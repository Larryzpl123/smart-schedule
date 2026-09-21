"""Reproduce the bug: a student whose OS/browser clock is set to China time
sees the schedule land on the wrong day. Fixed via pittNow() in app.html,
which always re-expresses the real instant in America/New_York civil time
regardless of the browser's configured timezone. This test sets the browser
context to Asia/Shanghai and picks a real UTC instant that is Wed evening in
Pittsburgh but already Thu morning in Shanghai -- exactly the failure mode
Larry described ("变成周五")."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_tz_")
DB = os.path.join(WORK, "d", "schedule.db")
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="tztest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT="5126")
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-c", "import app; app.app.run(port=5126, debug=False)"],
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

# 2026-09-10T01:30:00Z: US-Eastern (EDT, UTC-4) = Wed 2026-09-09 21:30 local.
#                       Asia/Shanghai (UTC+8)   = Thu 2026-09-10 09:30 local.
# A student whose device thinks it's in Shanghai should still see the grid
# on Sep 9 (Wed, cycle day 2 -- period A meets 13:20-14:10 that day), not
# jump to Sep 10.
REAL_UTC_INSTANT = "2026-09-10T01:30:00Z"

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        ctx = b.new_context(timezone_id="Asia/Shanghai", locale="zh-CN")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time=REAL_UTC_INSTANT)

        pg.goto("http://127.0.0.1:5126/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        pg.goto("http://127.0.0.1:5126/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SCHEDULE)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        pg.goto("http://127.0.0.1:5126/app")
        pg.wait_for_selector("#gbody .col.today", timeout=8000)

        # sanity: the browser really does think it's Shanghai-time Thursday
        browser_date = pg.evaluate("new Date().toString()")
        ck("2026" in browser_date, f"clock installed ({browser_date})")

        # the "today" header cell must say Sep 9 (Wed), not Sep 10 (Thu)
        dnum = pg.eval_on_selector(".dcell.today .dnum", "el => el.textContent")
        dow = pg.eval_on_selector(".dcell.today .dow", "el => el.textContent")
        ck("Sep 09" in dnum, f"today column shows Sep 09 (Pittsburgh date), not Shanghai's Sep 10 (got {dnum!r})")
        ck("WED" in dow.upper(), f"today column shows Wednesday, not Shanghai's Thursday (got {dow!r})")

        # header digital clock must show Pittsburgh time (21:30ish), not 09:30 Shanghai time
        ctime = pg.eval_on_selector("#cTime", "el => el.textContent")
        ck(ctime.startswith("21:3"), f"header clock shows Pittsburgh time ~21:30, not Shanghai's 09:30 (got {ctime!r})")

        # the now-bar should be positioned for 21:30 Pittsburgh time -- past AX_END (18:00),
        # so it should NOT be drawn at all (no bar during a time the campus is closed)
        bar_count = pg.locator(".col.today .nowbar").count()
        ck(bar_count == 0, f"no now-bar drawn for 21:30 (after AX_END=18:00) (got {bar_count})")

        # cycle day must be the real Pittsburgh one: Sep 9 = day 2 (Sep8=day1)
        cyc = pg.eval_on_selector(".dcell.today .dcyc", "el => el.textContent")
        ck("Day 2" in cyc, f"cycle day computed from Pittsburgh's Sep 9, not Shanghai's Sep 10 (got {cyc!r})")

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
