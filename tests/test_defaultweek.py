"""Larry opened the app on Sunday Sep 6 2026 and it showed the week of Aug 24,
the first week of the school year. "Today" did not fix it either.

Cause: the default week was chosen by asking whether today was in DAYMAP, and
DAYMAP only holds weekdays the calendar covers. That is false every Saturday,
every Sunday and every holiday, and the fallback was "jump to the first day of
the school year". So for most of every weekend the app pointed at August.
"""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_dw_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5185"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="dw", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
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
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - Ea

@pref time_format = 12h
"""

# EDT is UTC-4, so 15:00Z is 11:00 in Pittsburgh on all of these dates.
CASES = [
    # (real UTC instant, what Pittsburgh calls it,      expected week label)
    ("2026-09-06T15:00:00Z", "Sunday Sep 6",            "Sep 07"),
    ("2026-09-05T15:00:00Z", "Saturday Sep 5",          "Sep 07"),
    ("2026-09-07T15:00:00Z", "Labor Day (Mon, closed)", "Sep 07"),
    ("2026-09-09T15:00:00Z", "an ordinary Wednesday",   "Sep 07"),
    ("2026-09-11T15:00:00Z", "Friday Sep 11",           "Sep 07"),
]

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T15:00:00Z")

        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SEED)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        for instant, human, expect in CASES:
            pg.clock.install(time=instant)
            pg.goto(BASE + "/app")
            pg.wait_for_selector("#gbody .col")
            pg.wait_for_timeout(400)
            label = pg.text_content("#wkLabel")
            ck(expect in label, f"opening on {human} shows the week of {expect} ({label})")
            ck("Aug 24" not in label,
               f"...and NOT the first week of the school year ({label})")

            # the Today button has to agree with what the app opened on
            pg.click("#nextWk")
            pg.wait_for_timeout(300)
            pg.click("#todayBtn")
            pg.wait_for_timeout(500)
            ck(expect in pg.text_content("#wkLabel"),
               f"Today brings {human} back to {expect} ({pg.text_content('#wkLabel')})")

        # ---- outside the school year: fall back sensibly, not to a blank week ----
        # July belongs to the school year that just ended. Since 2026-27 is
        # already set up, that is what somebody opening the app wants.
        pg.clock.install(time="2026-07-15T15:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)
        lab = pg.text_content("#wkLabel")
        yr = pg.input_value("#yearSel")
        ck(yr == "2026-2027", f"in July it switches to the year about to start ({yr})")
        ck("Aug 24" in lab or "Aug 25" in lab,
           f"and opens on that year's first week ({lab})")

        # ...and if there is no later year, it shows where the loaded year
        # really ENDED, not the last cycle day before the immersive block
        pg.select_option("#yearSel", "2025-2026")
        pg.wait_for_timeout(600)
        pg.click("#todayBtn")
        pg.wait_for_timeout(600)

        # ---- a phone opens on today, not on Monday, but not on a dead weekend ----
        ph = b.new_page(viewport={"width": 390, "height": 844})
        ph.clock.install(time="2026-09-06T15:00:00Z")   # Sunday
        ph.goto(BASE + "/login")
        ph.fill('input[name=email]', "27doej@shadysideacademy.org")
        ph.fill('input[name=password]', "pw123456")
        ph.click('button[type=submit]')
        ph.wait_for_load_state()
        ph.goto(BASE + "/app")
        ph.wait_for_selector("#gbody .col")
        ph.wait_for_timeout(500)
        ck("Mon Sep 07" in ph.text_content("#wkLabel"),
           f"on a phone, Sunday opens on Monday the 7th ({ph.text_content('#wkLabel')})")

        ph.clock.install(time="2026-09-09T15:00:00Z")   # Wednesday
        ph.goto(BASE + "/app")
        ph.wait_for_selector("#gbody .col")
        ph.wait_for_timeout(500)
        ck("Wed Sep 09" in ph.text_content("#wkLabel"),
           f"and a weekday still opens on that day ({ph.text_content('#wkLabel')})")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
