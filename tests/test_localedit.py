"""Local mode, edited the way people actually edit: by double-clicking.

The double-click editor was gated on "logged in", so in local mode adding or
editing an event by double-click said "Log in first" -- since local mode
existed. Its tests called localApi() directly and never made the gesture.
This one makes the gestures: double-click empty grid -> new event (with a
note), double-click it -> edit, double-click a class -> homework. And the
sample page stays read-only."""
import os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_le_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5231"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="letest", ADMIN_PASSWORD="apw",
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
B - Art Core - AR100 - 2 - Ms. Delgado - BC Studio - cyan - Ea
C - French 3 - FR300 - 1 - Mr. Okafor - R 310 - purple - Ea
D - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
E - Playwriting - EN424A - 1 - Ms. Ferraro - R 110 - blue - La
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - La
G - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
H - Revolution in Ancient Rome - HI445 - 1 - Mr. Lindqvist - R 205 - yellow - Ea

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1280, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-11-17T14:00:00Z")      # Tue Nov 17, 9am in Pittsburgh
        pg.goto(BASE + "/local/app")
        pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(400)
        saved = lambda: pg.evaluate("localStorage.getItem('ssa-local-schedule')") or ""

        # ---- double-click empty grid: a new event ----
        col = pg.locator("#gbody .col[data-k='2026-11-17']")
        box = col.bounding_box()
        pg.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] - 25)
        pg.wait_for_timeout(300)
        st = pg.text_content("#status") or ""
        ck(pg.evaluate("document.getElementById('quickDlg').open"),
           f"double-clicking empty grid opens the new-event box ({st!r})")
        ck("Log in" not in st, "and does not say 'Log in first'")
        pg.fill("#qName", "Piano")
        pg.fill("#qNote", "bring the Bach")
        pg.click("#qSave")
        pg.wait_for_timeout(500)
        s = saved()
        ck("- Piano - " in s and "  > bring the Bach" in s, "it is added to this browser, note and all")
        ck(pg.locator("#gbody .block", has_text="Piano").count() > 0, "and drawn")

        # ---- double-click it: edit ----
        pg.locator("#gbody .block", has_text="Piano").first.dblclick()
        pg.wait_for_timeout(300)
        ck(pg.evaluate("document.getElementById('quickDlg').open") and pg.input_value("#qName") == "Piano",
           "double-clicking it opens the editor")
        ck(pg.input_value("#qNote") == "bring the Bach", "with its note")
        pg.fill("#qName", "Piano lesson")
        pg.click("#qSave")
        pg.wait_for_timeout(500)
        s = saved()
        ck("- Piano lesson - " in s and "- Piano - " not in s, "editing saves to this browser")

        # ---- one click, then "Edit..." in the menu ----
        pg.locator("#gbody .block", has_text="Piano lesson").first.click()
        pg.wait_for_timeout(200)
        pg.locator(".hwmenu button", has_text="Edit").click()
        pg.wait_for_timeout(300)
        ck(pg.evaluate("document.getElementById('quickDlg').open") and pg.input_value("#qName") == "Piano lesson",
           f"click, then Edit in the menu, opens the editor too ({pg.text_content('#status')!r})")
        pg.click("#qCancel")

        # ---- double-click a class: homework ----
        cls = pg.locator("#gbody .col[data-k='2026-11-17'] .block[data-period]").first
        cname = cls.get_attribute("data-cname")
        cls.dblclick()
        pg.wait_for_timeout(300)
        ck(pg.evaluate("document.getElementById('quickDlg').open")
           and cname in (pg.text_content("#qTitle") or ""),
           f"double-clicking a class opens homework for it ({pg.text_content('#qTitle')!r})")
        pg.fill("#qName", "Read ch.4")
        pg.click("#qSave")
        pg.wait_for_timeout(500)
        ck("@hw " in saved() and "Read ch.4 - 2026-11-17" in saved(), "and the homework is added")

        # ---- a name with " - " in it (the field separator) ----
        box = pg.locator("#gbody .col[data-k='2026-11-18']").bounding_box()
        pg.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + box["height"] - 25)
        pg.wait_for_timeout(300)
        pg.fill("#qName", "Case Western - check email/portal")
        pg.click("#qSave")
        pg.wait_for_timeout(500)
        s = saved()
        ck("- Case Western \u2013 check email/portal - " in s,
           f"a name typed with ' - ' is saved with an en dash, not split into fields "
           f"({[l for l in s.splitlines() if 'Case Western' in l]})")
        pg.reload(); pg.wait_for_selector("#gbody .col", timeout=8000); pg.wait_for_timeout(300)
        ck(pg.locator("#gbody .block", has_text="Case Western").count() > 0,
           "and the event is still there after a reload (it used to vanish)")
        pg.goto(BASE + "/local/edit")
        pg.wait_for_selector("#eventRows .evrow")
        pg.click("#addEvent")
        row = pg.query_selector_all("#eventRows .evrow")[-1]
        row.query_selector(".ev_name").fill("Pick up - Mom")
        row.query_selector(".ev_date").fill("2026-11-19")
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(400)
        ck("- Pick up \u2013 Mom - " in saved(), "same rule in Edit my schedule")
        pg.goto(BASE + "/local/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)

        # ---- the sample page is still look-only ----
        sp = b.new_page(viewport={"width": 1280, "height": 1000})
        sp.goto(BASE + "/sample")
        sp.wait_for_selector("#gbody .col", timeout=8000)
        sp.wait_for_timeout(400)
        c2 = sp.locator("#gbody .col").first.bounding_box()
        sp.mouse.dblclick(c2["x"] + c2["width"] / 2, c2["y"] + c2["height"] - 25)
        sp.wait_for_timeout(300)
        ck(not sp.evaluate("document.getElementById('quickDlg').open"), "the sample page stays read-only")

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
