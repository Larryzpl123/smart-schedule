"""A refresh paints the header and the year in one go.

On a slow connection the year box used to show 2025-26 (its first option)
until the school file had loaded, "Edit my schedule / Log out" appeared on
their own after the calendar, and the clock last of all, shoving them left.
Every animation frame of a deliberately slow load is sampled here: the year
must never be anything but the current one, the links never without the
clock, and a 24h student's clock must not start in 12h and flip."""
import os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
WORK = tempfile.mkdtemp(prefix="ssa_fp_")
PORT = "5239"
env = dict(os.environ, DB_PATH=os.path.join(WORK, "d", "schedule.db"), SECRET_KEY="fptest",
           ADMIN_PASSWORD="apw", FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
srv = subprocess.Popen([sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
                       cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea

@pref time_format = 24h
"""
# one snapshot per frame, from the first frame the page has a body
SAMPLER = """(() => { window.__fr = []; const snap = () => {
  const y = document.getElementById('yearSel'), n = document.getElementById('acctNav'),
        d = document.getElementById('cDate'), t = document.getElementById('cTime');
  if (y && n && d) window.__fr.push({year: y.value, nav: getComputedStyle(n).display !== 'none',
        clock: !!d.textContent, time: t ? t.textContent : ''});
  if (window.__fr.length < 400) requestAnimationFrame(snap); };
  requestAnimationFrame(snap); })();"""

def slow(route):
    time.sleep(0.7)
    route.continue_()

def load(pg, path):
    pg.goto(BASE + path, wait_until="commit")
    pg.wait_for_selector("#gbody .col", timeout=15000)
    pg.wait_for_timeout(300)
    return pg.evaluate("window.__fr")

def check(tag, fr):
    years = sorted({f["year"] for f in fr if f["year"]})
    ck(fr and years == ["2026-2027"], f"{tag}: the year box never shows anything but 2026-27 ({years}, {len(fr)} frames)")
    split = [f for f in fr if f["nav"] != f["clock"]]
    ck(not split, f"{tag}: the links and the clock appear in the same frame ({len(split)} frames with one but not the other)")

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        errs = []

        # ---- account ----
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-11-17T15:00:00Z")
        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.add_init_script(SAMPLER)
        pg.route("**/*.txt", slow)
        pg.route("**/api/**", slow)
        check("account", load(pg, "/app"))
        ctx.close()

        # ---- local, a 24h student ----
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-11-17T15:00:00Z")
        pg.goto(BASE + "/local/app")
        pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.add_init_script(SAMPLER)
        pg.route("**/*.txt", slow)
        fr = load(pg, "/local/app")
        check("local", fr)
        times = [f["time"] for f in fr if f["time"]]
        ck(times and not any(("AM" in t or "PM" in t) for t in times),
           f"local: a 24h student's clock is 24h from its first frame ({times[:1]}...)")
        # no @pref: back to 12h, the browser's 24h memory is only a guess
        pg.evaluate("s=>localStorage.setItem('ssa-local-schedule',s)", SEED.replace("@pref time_format = 24h", ""))
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=15000)
        pg.wait_for_timeout(300)
        ck("M" in (pg.text_content("#cTime") or ""), f"without @pref the clock settles on 12h ({pg.text_content('#cTime')!r})")
        ctx.close()

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()
print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
