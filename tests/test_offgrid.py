"""Times off the 5-minute grid, and no browser autocomplete dropdowns.

An event made by double-clicking can be 32 minutes long, or start at 16:03.
- /edit used to refuse to save at all (the duration box had step=5, so 32 was
  "invalid" and the browser silently blocked the whole form), and its start
  dropdown had no 16:03, so a Form save moved the event to 06:00.
- The quick editor on the schedule page snapped 16:03 to its first option.
And the browser's "things you typed before" list must not pop up over the
name boxes (autocomplete=off)."""
import os, re, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_og_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5229"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ogtest", ADMIN_PASSWORD="apw",
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

@sport Fencing - Mon,Wed - 15:47-17:13 - cyan
@event 2026-11-13 - Odd one - 16:03-16:35 - pink
@event 2026-11-13 - Round one - 18:00-18:30 - gray

@pref time_format = 12h
"""

def norm(t):
    return (t or "").replace("\r\n", "\n")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-11-12T16:00:00Z")

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
        with pg.expect_navigation():
            pg.click('button[type=submit]')

        def mine():
            return norm(pg.evaluate("fetch('/api/my-schedule',{cache:'no-store'}).then(r=>r.text())"))

        # ---------------- /edit, Form mode ----------------
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        row = [r for r in pg.query_selector_all("#eventRows .evrow")
               if r.query_selector(".ev_name") and r.query_selector(".ev_name").input_value() == "Odd one"][0]
        ck(row.query_selector(".ev_start").input_value() == "16:03",
           f"the start dropdown shows 16:03, not its first option ({row.query_selector('.ev_start').input_value()})")
        ck(row.query_selector(".ev_dur").input_value() == "32", "the length box shows 32")
        valid = pg.evaluate("document.getElementById('schedForm').checkValidity()")
        ck(valid, "the form is valid with a 32-minute event in it (step 5 made it invalid)")
        # change something else and save from the FORM
        rr = [r for r in pg.query_selector_all("#eventRows .evrow")
              if r.query_selector(".ev_name") and r.query_selector(".ev_name").input_value() == "Round one"][0]
        rr.query_selector(".ev_name").fill("Round one, renamed")
        nav_ok = True
        try:
            with pg.expect_navigation(timeout=5000):
                pg.click('button[type=submit]')
        except Exception:
            nav_ok = False
        ck(nav_ok, "Save actually submits (it used to do nothing)")
        t = mine()
        ck("Round one, renamed" in t, "the change is saved")
        ck("@event 2026-11-13 - Odd one - 16:03-16:35 - pink" in t,
           f"and the 32-minute event comes through exactly ({[l for l in t.splitlines() if 'Odd one' in l]})")
        ck("@sport Fencing - Mon,Wed - 15:47-17:13 - cyan" in t,
           f"so does a sport at 15:47-17:13 ({[l for l in t.splitlines() if 'Fencing' in l]})")
        # typing an odd length works too
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        rr = [r for r in pg.query_selector_all("#eventRows .evrow")
              if r.query_selector(".ev_name") and r.query_selector(".ev_name").input_value().startswith("Round one")][0]
        rr.query_selector(".ev_dur").fill("47")
        with pg.expect_navigation(timeout=5000):
            pg.click('button[type=submit]')
        ck("Round one, renamed - 18:00-18:47" in mine(), "a 47-minute length typed in saves")
        # the 12h/24h relabel keeps 16:03
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        pg.evaluate("document.getElementById('f_timefmt').value='24h'; relabelTimes()")
        row = [r for r in pg.query_selector_all("#eventRows .evrow")
               if r.query_selector(".ev_name") and r.query_selector(".ev_name").input_value() == "Odd one"][0]
        ck(row.query_selector(".ev_start").input_value() == "16:03", "relabelling the times keeps 16:03")

        # ---------------- the quick editor on the schedule page ----------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        pg.evaluate("openEdit('event','2026-11-13 - Odd one - 16:03-16:35 - pink')")
        pg.wait_for_timeout(200)
        ck(pg.input_value("#qStart") == "16:03" and pg.input_value("#qDur") == "32",
           f"the quick editor opens at 16:03 for 32 min ({pg.input_value('#qStart')}, {pg.input_value('#qDur')})")
        pg.fill("#qName", "Odd one, edited")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        ck("@event 2026-11-13 - Odd one, edited - 16:03-16:35 - pink" in mine(),
           "saving it keeps 16:03-16:35 (it used to jump to the first time in the list)")
        pg.evaluate("document.getElementById('qDur').value='33'")
        ck(pg.evaluate("document.getElementById('qDur').checkValidity()"), "33 minutes is a valid length in the quick editor too")

        # ---------------- no autocomplete dropdowns ----------------
        ac = pg.evaluate("""() => ['qName','qNote','find'].map(id => document.getElementById(id).getAttribute('autocomplete'))""")
        ck(ac == ["off", "off", "off"], f"the quick editor's boxes and search say autocomplete=off ({ac})")
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        f = pg.evaluate("""() => ({form: document.getElementById('schedForm').getAttribute('autocomplete'),
            rows: Array.from(document.querySelectorAll('#schedForm input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=date]):not([type=number]):not([type=file])'))
              .filter(i => (i.getAttribute('autocomplete') || 'off') !== 'off').map(i => i.className || i.id)})""")
        ck(f["form"] == "off" and not f["rows"],
           f"the editor's form has autocomplete off and no box in it turns it back on ({f})")

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
