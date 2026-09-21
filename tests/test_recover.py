# -*- coding: utf-8 -*-
"""Undo, earlier versions, and search.

Four things that all exist for the same reason: the app now holds real
deadlines rather than a timetable you could reconstruct from a PDF.

  - Deleting was a confirm() and then gone. A confirm is answered in half a
    second and regretted a minute later.
  - The only safety net was the whole-database snapshot, which runs when an
    ADMIN opens /admin. A student who pasted over their schedule on Tuesday
    and noticed on Thursday might have nothing behind them -- and could not
    have reached it if there were.
  - Fifty events and an imported college calendar later, "where did I put
    that" is a real question whose answer was "page through the weeks".
"""
import os, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, http.cookiejar, json

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
sys.path.insert(0, APP_DIR)

WORK = tempfile.mkdtemp(prefix="ssa_rec_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5215"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="rec", ADMIN_PASSWORD="apw",
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
MAIL = "27doej@shadysideacademy.org"
HEAD = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

"""
TAIL = "@pref time_format = 12h\n"
V1 = HEAD + "@event Tue - Piano lesson - 17:30-18:30 - pink\n" + TAIL
V2 = V1.replace("Piano lesson", "Piano lesson with Mrs. Adams")
V3 = HEAD + TAIL                                   # the wreck

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))

def save(text):
    op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
        {"schedule_text": text}).encode()))

def mine():
    return op.open(BASE + "/api/my-schedule").read().decode()

def history():
    return json.loads(op.open(BASE + "/api/my-history").read().decode())

# ---------------------------------------------------------------- history --
ck(history()["versions"] == [], "a new account has no earlier versions")
save(V1)
ck(history()["versions"] == [],
   "and the FIRST save records none either -- there was nothing there before")
save(V2)
h = history()["versions"]
ck(len(h) == 1 and "Piano lesson -" in h[0]["text"],
   f"the second save keeps what was there before it ({len(h)})")
save(V2)
ck(len(history()["versions"]) == 1,
   "saving the same thing again does not add a rung")
save(V3)
h = history()["versions"]
ck(len(h) == 2, f"wrecking it keeps the good one ({len(h)})")
ck("Adams" in h[0]["text"] and "Adams" not in mine(),
   "the newest rung is the version from just before the wreck")
ck(h[0]["events"] == 1 and h[0]["hw"] == 0,
   f"and the list says what is in it without opening it ({h[0]})")
ck(h[0]["changed"] > 0, "and how far it is from what is there now")

# it is a LIST, not a restore button: nothing was written by looking
ck("Adams" not in mine(), "looking at history changes nothing")

# ...but NOT the grid's single-line endpoints. A version per checkbox tick
# would fill the ladder with states nobody wants to go back to, and every one
# of those changes is already undoable on its own terms.
save(HEAD + "@hw A - before - Tick me - 2026-09-08\n" + TAIL)
before = len(history()["versions"])
op.open(urllib.request.Request(BASE + "/api/quick-add",
    json.dumps({"kind": "event",
                "line": "Wed - Club - 15:30-16:30 - cyan"}).encode(),
    {"Content-Type": "application/json"}))
op.open(urllib.request.Request(BASE + "/api/quick-done",
    json.dumps({"line": "A - before - Tick me - 2026-09-08", "done": True}).encode(),
    {"Content-Type": "application/json"}))
op.open(urllib.request.Request(BASE + "/api/quick-delete",
    json.dumps({"kind": "event",
                "line": "Wed - Club - 15:30-16:30 - cyan"}).encode(),
    {"Content-Type": "application/json"}))
ck(len(history()["versions"]) == before,
   f"adding, ticking and deleting one line on the grid cut no rung at all "
   f"({before} -> {len(history()['versions'])})")
ck("Club" not in mine(), "though they really did change the schedule")
# ...and a real save still does
save(HEAD + "@event Mon - Real change - 09:00-09:30 - gray\n" + TAIL)
ck(len(history()["versions"]) == before + 1,
   "while a Save on /edit does -- that is the change that loses a lot at once")

# ---- the ladder is spaced, not just the last N changes --------------------
import sqlite3
for i in range(20):
    save(HEAD + f"@event Mon - Filler {i} - 09:00-09:30 - gray\n" + TAIL)
h = history()["versions"]
import app as _app
ck(len(h) <= _app.HISTORY_KEEP,
   f"the ladder is capped ({len(h)} <= {_app.HISTORY_KEEP})")
ck(len(h) >= _app.HISTORY_RECENT,
   f"and holds at least the most recent few ({len(h)})")
# Fake an older day on the oldest rows, then push one more change through.
# The year is 2020 on purpose: the first version of this used 2026-09-<20-n>,
# which quietly started colliding with the real "today" once the clock passed
# the 20th, and then every rung looked like the same day.
con = sqlite3.connect(DB)
ids = [r[0] for r in con.execute(
    "SELECT id FROM schedule_history ORDER BY id DESC").fetchall()]
for n, i in enumerate(ids[_app.HISTORY_RECENT:]):
    con.execute("UPDATE schedule_history SET saved_at=? WHERE id=?",
                (f"2020-01-{max(1, 20 - n):02d} 08:00:00", i))
con.commit(); con.close()
save(HEAD + "@event Mon - One more - 09:00-09:30 - gray\n" + TAIL)
h = history()["versions"]
days = [v["saved_at"][:10] for v in h]
ck(len(set(days)) > 1,
   f"the ladder spreads over days rather than being twelve rungs of the last "
   f"ten minutes -- 'five minutes ago' five times is no use on Thursday "
   f"({sorted(set(days))})")

# ---- and it is private ----------------------------------------------------
cj2 = http.cookiejar.CookieJar()
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))
op2.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": "28roea@shadysideacademy.org", "name": "Roe",
     "password": "pw123456"}).encode()))
op2.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": "28roea@shadysideacademy.org", "password": "pw123456"}).encode()))
other = json.loads(op2.open(BASE + "/api/my-history").read().decode())
ck(other["versions"] == [],
   "somebody else's account sees their own history, which is empty, and not "
   "one line of Jane's")
# urllib follows the redirect to the login page, so check the BODY: whatever
# comes back, it must not be somebody's schedule.
try:
    anon = urllib.request.urlopen(BASE + "/api/my-history").read().decode()
except Exception as e:
    anon = str(e)
ck("Adams" not in anon and '"versions"' not in anon,
   f"and logged out it serves the login page, not a schedule "
   f"({anon[:60]!r})")

# ---------------------------------------------------------------- browser --
save(V1.replace("@pref", "@hw A - before - Read Ch. 4 - 2026-09-08\n"
                         "@hw E - before - Essay outline - 2026-10-20\n@pref"))
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-08T15:00:00Z")   # Tue, 11:00 EDT
        pg.goto(BASE + "/login")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(900)

        # ---- undo ------------------------------------------------------
        pg.on("dialog", lambda d: d.accept())
        pg.evaluate("""() => {
            const el = document.querySelector('.hw[data-src]');
            openItemMenu(el, {clientX: 100, clientY: 100});
        }""")
        pg.wait_for_timeout(300)
        pg.click(".hwmenu button:last-child")        # Delete
        pg.wait_for_timeout(1200)
        ck("Read Ch. 4" not in (op.open(BASE + "/api/my-schedule").read().decode()),
           "deleting really deletes")
        undo = pg.evaluate("""() => {
            const el = document.getElementById('undo');
            return {shown: el.style.display, text: (el.textContent||'').trim()};
        }""")
        ck(undo["shown"] == "flex" and "Undo" in undo["text"],
           f"and an Undo is offered ({undo})")
        pg.click("#undo button")
        pg.wait_for_timeout(1200)
        back = op.open(BASE + "/api/my-schedule").read().decode()
        ck("Read Ch. 4" in back,
           "which puts the line back exactly as it was")
        ck(back.count("Read Ch. 4") == 1, "once, not twice")
        ck(pg.evaluate("""() => document.getElementById('undo').style.display"""
                       ) == "none", "and the offer goes away once taken")

        # ---- search -----------------------------------------------------
        pg.keyboard.press("/")
        pg.wait_for_timeout(900)
        ck(pg.evaluate("() => VIEW") == "list",
           "'/' from anywhere goes to the list")
        ck(pg.evaluate("() => document.activeElement.id") == "find",
           "with the cursor in the box")
        pg.fill("#find", "outline")
        pg.wait_for_timeout(900)
        body = pg.text_content("#gbody")
        ck("Essay outline" in body,
           "typing finds a milestone in October -- past the end of the "
           "two-month window it was showing")
        ck("Piano lesson" not in body, "and nothing that does not match")
        ck("results for" in body or "result for" in body,
           f"with a count at the top")
        ck("whole year" in body,
           "and it says that a search overrides the range and the filter, "
           "rather than quietly changing two controls behind you")

        pg.fill("#find", "Rivera")
        pg.wait_for_timeout(900)
        body = pg.text_content("#gbody")
        ck("AP Calculus BC" in body,
           "searching a teacher's name finds their classes, even though the "
           "list was set to deadlines only")
        # ...but ONCE. Ungrouped, this returned a hundred and five rows all
        # saying "AP Calculus BC" -- correct, and useless.
        rows = pg.evaluate("""() => [...document.querySelectorAll('.lrow .lname')]
            .filter(r => r.textContent === 'AP Calculus BC').length""")
        ck(rows == 1,
           f"and once, not once per lesson for the whole year ({rows} rows). "
           f"The homework due in that class is a separate result, which is "
           f"right -- it is a different thing that happens to match")
        ck("this year" in body,
           f"with how many times it comes round instead ({body[:160]!r})")

        pg.fill("#find", "zzzznothing")
        pg.wait_for_timeout(900)
        ck("Nothing" in pg.text_content("#gbody"), "and says so when there is nothing")

        pg.press("#find", "Escape")
        pg.wait_for_timeout(900)
        ck(pg.evaluate("() => SEARCH") == "" and
           "Piano lesson" not in pg.text_content("#gbody"),
           "Escape clears it and the list goes back to what it was")

        ck(not errs, f"no page errors: {errs[:3]}")
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
