"""Double-click to add, like a calendar app, plus the light/dark theme and the
"you already have 20" replace-the-oldest offer."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_qa_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5151"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="qatest", ADMIN_PASSWORD="apw",
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
# Sep 10 2026 is a Thursday, cycle Day 3. @day 3 = F,H,A,C,G,D -- so period A
# sits at 10:35-11:45 that day.
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - Ea

@pref time_format = 24h
"""

# The cap has moved twice (20 -> 50 for events). Read it, never repeat it.
import importlib
sys.path.insert(0, APP_DIR)
CAP = importlib.import_module("app").MAX_EVENTS

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-10T16:00:00Z")

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

        # ---------------- theme ----------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(400)
        # dark is the default (it was light for a day; people preferred dark)
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "dark",
           "dark is the default theme")
        dbg = pg.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('#gbody .block'))
                .find(x => x.querySelector('.bname').textContent === 'Game Design');
            return getComputedStyle(b).backgroundColor; }""")
        ck(dbg == "rgb(255, 176, 97)", f"dark keeps the old saturated colours ({dbg})")

        pg.click("#themeBtn")                      # -> light
        pg.wait_for_timeout(400)
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "light",
           "the switch flips to light")
        ck(pg.evaluate("localStorage.getItem('ssa-theme')") == "light",
           "the choice is remembered in localStorage")
        bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        ck("255, 255, 255" in bg, f"light theme really is a white page ({bg})")
        blockbg = pg.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('#gbody .block'))
                .find(x => x.querySelector('.bname').textContent === 'Game Design');
            return getComputedStyle(b).backgroundColor; }""")
        ck(blockbg == "rgb(249, 235, 227)",
           f"course blocks use the school PDF's own pastel fill ({blockbg})")
        ink = pg.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('#gbody .block'))
                .find(x => x.querySelector('.bname').textContent === 'Game Design');
            return getComputedStyle(b).color; }""")
        ck(ink == "rgb(31, 35, 40)", f"course-block text is near-black in light mode ({ink})")

        pg.reload()
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(300)
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "light",
           "the choice survives a reload")

        # ---------------- double-click empty space -> event ----------------
        col = pg.locator("#gbody .col").nth(3)      # Thursday Sep 10
        box = col.bounding_box()
        # 16:00 is well clear of every class block that day
        y = box["y"] + (16 * 60 - 8 * 60) * 1.05
        pg.mouse.dblclick(box["x"] + box["width"] / 2, y)
        pg.wait_for_timeout(300)
        ck(pg.is_visible("#quickDlg"), "double-clicking empty space opens the add dialog")
        ck(pg.input_value("#qStart") == "16:00",
           f"the start time comes from where you clicked ({pg.input_value('#qStart')})")
        ck(pg.text_content("#qDate") == "2026-09-10", "and the date from the column")
        pg.fill("#qName", "Meet Mr. B")
        # the duration is typed now, so any length is possible
        pg.fill("#qDur", "30")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("@event 2026-09-10 - Meet Mr. B - 16:00-16:30 - gray" in saved,
           f"the event was saved ({[l for l in saved.splitlines() if l.startswith('@event')]})")
        names = pg.eval_on_selector_all("#gbody .block .bname", "n=>n.map(x=>x.textContent)")
        ck("Meet Mr. B" in names, "and it appears on the grid without a page reload")

        # ---------------- double-click a class -> homework ----------------
        blk = pg.locator("#gbody .col").nth(3).locator(".block").filter(
            has_text="Game Design").first
        blk.dblclick()
        pg.wait_for_timeout(300)
        ck(pg.is_visible("#quickDlg"), "double-clicking a class opens the dialog")
        ck("Game Design" in pg.text_content("#qTitle"),
           f"the dialog names the class you clicked ({pg.text_content('#qTitle')})")
        ck(not pg.is_visible("#qTimeRow"), "no time picker for homework -- it follows the class")
        pg.fill("#qName", "Read ch.4")
        pg.select_option("#qWhen", "before")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("@hw A - before - Read ch.4 - 2026-09-10" in saved,
           f"homework saved against the right period ({[l for l in saved.splitlines() if l.startswith('@hw')]})")
        hw = pg.eval_on_selector_all("#gbody .hw", "n=>n.map(x=>x.textContent)")
        ck(any("Read ch.4" in h for h in hw), f"and it shows on the grid ({hw})")

        # a non-class block (Assembly/Club Time) has nothing to hang homework on
        fixed = pg.locator("#gbody .block.fixed").first
        if fixed.count():
            fixed.dblclick()
            pg.wait_for_timeout(250)
            ck(not pg.is_visible("#quickDlg"),
               "double-clicking Assembly/Club Time does nothing (no class to hang it on)")

        # ---------------- the 21st one ----------------
        bulk = SEED.rstrip("\n") + "\n\n" + "\n".join(
            f"@event 2026-10-{(d % 28) + 1:02d} - Filler {d} - 10:00-10:30 - gray"
            for d in range(1, CAP + 1)) + "\n"
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", bulk)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(400)
        col = pg.locator("#gbody .col").nth(3)
        box = col.bounding_box()
        pg.mouse.dblclick(box["x"] + box["width"] / 2, box["y"] + (17 * 60 - 8 * 60) * 1.05)
        pg.wait_for_timeout(300)
        pg.fill("#qName", "One too many")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        ck(pg.is_visible("#fullDlg"),
           f"at the cap ({CAP}), adding one more offers to drop the oldest")
        oldest = pg.text_content("#fullOldest")
        ck("Filler 1 " in oldest or oldest.endswith("Filler 1 - 10:00-10:30 - gray")
           or "Filler 1" in oldest,
           f"the dialog SHOWS which one would go ({oldest})")
        after_cancel = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck(after_cancel.count("@event") == CAP, "nothing was written before confirming")
        pg.click("#fullGo")
        pg.wait_for_timeout(700)
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("One too many" in saved, "confirming adds the new one")
        ck("Filler 1 -" not in saved, "and drops exactly the oldest one")
        ck(saved.count("@event") == CAP,
           f"still {CAP}, not {CAP + 1} ({saved.count('@event')})")
        ck("Filler 2 -" in saved and f"Filler {CAP} -" in saved,
           "the others are untouched")

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
