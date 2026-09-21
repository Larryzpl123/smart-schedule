"""Verify the auto-refreshing current-time bar: updateNowBar() runs every
60s and on tab-visibility/focus regain, moving only the .nowbar + toggling
.now on blocks -- NOT rebuilding the whole grid (checked via a marker we
stash on the grid body that a real render() would wipe out)."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from werkzeug.security import generate_password_hash


WORK = tempfile.mkdtemp(prefix="ssa_nb_")
DB = os.path.join(WORK, "d", "schedule.db")
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="nbtest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT="5122")
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-c", "import app; app.app.run(port=5122, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

SCHEDULE = """Jane Doe
27 - Junior

@term Fall 2026 = 08.26-01.20
A - AP Calculus BC - MA560 - 1 - Mrs. Yam - R 202 - red

@pref time_format = 24h"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # Anchor the fake clock to a real school day/time before any page script runs,
        # so init()'s first render() sees a normal Tuesday during class hours.
        # app.html now always re-expresses "now" in America/New_York (pittNow()), so the
        # clock must be a real UTC instant, not a bare local-looking string: 08:20 EDT on
        # 2026-09-08 is 12:20 UTC (EDT = UTC-4, no DST transition nearby).
        pg.clock.install(time="2026-09-08T12:20:00Z")  # Tue = cycle day 1; period A meets 08:15-09:05 Pittsburgh time

        pg.goto("http://127.0.0.1:5122/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # seed a schedule through the real Text-mode UI (mirrors test_browser.py)
        pg.goto("http://127.0.0.1:5122/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SCHEDULE)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        pg.goto("http://127.0.0.1:5122/app")
        pg.wait_for_selector("#gbody .col.today", timeout=8000)

        # --- 1. initial render placed a bar at ~2:03pm ---
        top0 = pg.eval_on_selector(".col.today .nowbar", "el => el.style.top")
        ck(top0 not in (None, ""), f"initial nowbar rendered (top={top0!r})")
        blk_now0 = pg.locator(".col.today .block.now").count()
        ck(blk_now0 == 1, f"AP Calculus BC (08:15-09:05) highlighted at 08:20 (got {blk_now0} .now blocks)")

        # tag the *actual nowbar DOM node* so we can tell whether later ticks reuse it
        # (updateNowBar) or a full render() replaced it with a freshly-created one.
        pg.evaluate("document.querySelector('.col.today .nowbar').dataset.identity = 'orig'")

        # --- 2. advance the fake clock 1 minute and fire the interval tick ourselves
        #        (jump forward instead of waiting 60s of wall-clock) ---
        pg.clock.fast_forward(60_000)
        top1 = pg.eval_on_selector(".col.today .nowbar", "el => el.style.top")
        ck(top1 != top0, f"nowbar moved after 60s tick ({top0!r} -> {top1!r})")
        identity1 = pg.eval_on_selector(".col.today .nowbar", "el => el.dataset.identity")
        ck(identity1 == "orig", "60s tick used updateNowBar() (same DOM node moved), not a full render()")

        # --- 3. the .now highlight should still track the moved bar (still within 08:15-09:05) ---
        blk_now = pg.locator(".col.today .block.now").count()
        ck(blk_now == 1, f"exactly one block carries .now at 08:21 (got {blk_now})")

        # --- 4. jump clock forward hours (simulating a backgrounded/suspended tab,
        #        during which the browser throttles/pauses setInterval) then fire
        #        visibilitychange -- the bar must snap to the correct spot immediately,
        #        without waiting for another 60s tick. ---
        pg.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        pg.clock.fast_forward(2 * 60 * 60_000)  # +2h while "hidden" -> 10:21 (a gap between B and C)
        pg.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        top2 = pg.eval_on_selector(".col.today .nowbar", "el => el.style.top")
        expected_top = round((10 * 60 + 21 - 8 * 60) * 1.05, 2)  # 10:21, AX_START=8:00, 1.05 px/min
        got = float(str(top2).replace("px", ""))
        ck(abs(got - expected_top) < 0.6, f"visibilitychange wake-up snapped bar correctly (top={got}, expected~{expected_top})")
        identity2 = pg.eval_on_selector(".col.today .nowbar", "el => el.dataset.identity")
        ck(identity2 == "orig", "visibility wake-up used updateNowBar() (same DOM node), not a full render()")

        # --- 5. day rollover: nowbar must move to the (new) today column, which
        #        requires a real render() -- confirm updateNowBar() detects this
        #        and falls back correctly instead of leaving a stale bar. ---
        pg.clock.fast_forward(24 * 60 * 60_000)  # exactly +24h -> same time next day, still school hours
        pg.evaluate("window.dispatchEvent(new Event('focus'))")
        identity3 = pg.eval_on_selector(".col.today .nowbar", "el => el.dataset.identity")
        ck(identity3 != "orig", "day rollover triggers a real render() (nowbar node was replaced)")

        ck(not errs, f"no page errors: {errs}")

        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL))+' FAILED'}")
sys.exit(1 if FAIL else 0)
