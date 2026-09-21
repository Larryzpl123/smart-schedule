"""Double-clicking your own event opens an editor, not a delete.

Larry's point: creating with a double-click is natural, destroying with the
same gesture is not. The two things you want to do to something you just made
are adjust it and remove it, and only removal was reachable without opening
the whole edit form. So the gesture opens a small editor and Delete is a
button inside it.
"""
import os, json, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, urllib.error, http.cookiejar

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_em_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5206"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="em", ADMIN_PASSWORD="apw",
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
SEED = """Jane Doe
27 - Senior

@project US History Paper - 2026-09-07 - 2027-01-15 - yellow

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

@event 2026-09-09 - Dentist - 14:00-15:00 - gray
@event Mon,Wed - Piano lesson - 16:00-17:00 - 08.25-06.30 - pink
@hw E - before - Sources - 2026-09-11 - US History Paper

@pref time_format = 12h
"""

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
    {"schedule_text": SEED}).encode()))

def mine():
    return op.open(BASE + "/api/my-schedule").read().decode()

def post_json(path, payload):
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.loads(op.open(req).read().decode()), 200
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code

# ---- the endpoint on its own ------------------------------------------------
body, code = post_json("/api/quick-edit", {
    "kind": "event", "line": "2026-09-09 - Dentist - 14:00-15:00 - gray",
    "newline": "2026-09-10 - Dentist - 14:30-15:30 - teal"})
ck(body.get("ok"), f"an event line can be replaced ({body})")
txt = mine()
ck("2026-09-10 - Dentist - 14:30-15:30 - teal" in txt
   and "2026-09-09 - Dentist" not in txt, "the old line is gone, the new one is there")

body, code = post_json("/api/quick-edit", {
    "kind": "event", "line": "2026-09-09 - Dentist - 14:00-15:00 - gray",
    "newline": "2026-09-11 - Dentist - 14:30-15:30 - teal"})
ck(code == 404, f"editing a line that is no longer there is refused ({code})")

# a bad edit must not be able to smuggle past the limits
body, code = post_json("/api/quick-edit", {
    "kind": "event", "line": "2026-09-10 - Dentist - 14:30-15:30 - teal",
    "newline": "2026-09-10 - " + ("x" * 40000) + " - 14:30-15:30 - teal"})
ck(code == 400 and "KB" in (body.get("error") or ""),
   f"an edit still goes through the size check ({code}, {str(body)[:60]})")
ck("x" * 100 not in mine(), "and nothing was written")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T15:00:00Z")

        pg.goto(BASE + "/login")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)

        def dbl(name):
            pg.evaluate("""(n) => {
                const b = [...document.querySelectorAll('#gbody .block')]
                    .find(e => (e.querySelector('.bname')||{}).textContent === n);
                b.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
            }""", name)
            pg.wait_for_timeout(400)

        # ---- a repeating event ------------------------------------------
        dbl("Piano lesson")
        ck(pg.is_visible("#quickDlg"), "double-clicking my own event opens the editor")
        ck(pg.input_value("#qName") == "Piano lesson",
           f"with the name filled in ({pg.input_value('#qName')!r})")
        ck(pg.input_value("#qStart") == "16:00",
           f"and the time ({pg.input_value('#qStart')!r})")
        ck(pg.is_visible("#qDelete"), "Delete is a button inside it, not the gesture")
        rep = pg.text_content("#qRepeat")
        ck("repeats" in rep and "Mon,Wed" in rep,
           f"a repeating one says so before you change it ({rep!r})")

        pg.fill("#qName", "Piano with Mr. K")
        pg.select_option("#qStart", "16:30")
        pg.select_option("#qColor", "purple")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        txt = mine()
        ck("@event Mon,Wed - Piano with Mr. K - 16:30-17:30 - 08.25-06.30 - purple" in txt,
           f"saving rewrites that one line and keeps its shape: "
           f"{[l for l in txt.split(chr(10)) if 'Piano' in l]}")
        ck(txt.count("Piano") == 1, "and does not leave the old one behind")

        # ---- a one-off can move day -------------------------------------
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(700)
        pg.evaluate("() => { for (let i=0;i<0;i++) nextWk.click(); }")
        dbl("Dentist")
        # the dialog now carries the same shapes /edit does, so a one-off's
        # date lives in the shape row with the rest of them
        ck(pg.input_value("#qMode") == "once", "a one-off says so")
        ck(pg.is_visible("#qOnDate"), "and offers its date")
        ck(pg.input_value("#qOnDate") == "2026-09-10",
           f"set to the day it is on ({pg.input_value('#qOnDate')!r})")
        pg.fill("#qOnDate", "2026-09-11")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        ck("2026-09-11 - Dentist" in mine(), "and moving it works")

        # ---- homework: same gesture, same editor -------------------------
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(800)
        pg.evaluate("""() => {
            const h = [...document.querySelectorAll('#gbody .hw')]
                .find(e => e.textContent.includes('Sources'));
            h.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
        }""")
        pg.wait_for_timeout(500)
        ck(pg.is_visible("#quickDlg") and pg.input_value("#qName") == "Sources",
           "double-clicking a milestone opens it too")
        ck(pg.is_visible("#qPerRow"), "with the class it hangs on")
        ck(pg.input_value("#qPer") == "E", f"set to E ({pg.input_value('#qPer')!r})")
        pg.select_option("#qPer", "A")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        txt = mine()
        ck("@hw A - before - Sources - 2026-09-11 - US History Paper" in txt,
           f"moving it to another class keeps it attached to its project: "
           f"{[l for l in txt.split(chr(10)) if 'Sources' in l]}")

        # ---- and Delete still exists, one deliberate click deeper --------
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(800)
        pg.on("dialog", lambda d: d.accept())
        dbl("Dentist")
        pg.click("#qDelete")
        pg.wait_for_timeout(900)
        ck("Dentist" not in mine(), "Delete inside the editor removes it")

        # ---- one rule: one click = menu, two clicks = editor ---------------
        # A homework chip had a menu and an event block did not, so the same
        # click meant different things depending on what you hit.
        # put the milestone back on E: it was moved to A a moment ago, and A
        # does not meet on that rotation day, so there is no chip to click
        post_json("/api/quick-edit", {
            "kind": "hw",
            "line": "A - before - Sources - 2026-09-11 - US History Paper",
            "newline": "E - before - Sources - 2026-09-11 - US History Paper"})
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(900)
        pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.querySelector('.bname')||{}).textContent.includes('Piano'));
            b.click();
        }""")
        pg.wait_for_timeout(400)
        menu = pg.text_content(".hwmenu") if pg.is_visible(".hwmenu") else ""
        ck("Edit" in menu and "Delete" in menu,
           f"one click on my own event opens the same menu ({menu!r})")
        ck("Mark done" not in menu,
           "with no 'done' on an event -- only homework can be finished")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)

        pg.evaluate("""() => {
            const h = [...document.querySelectorAll('#gbody .hw')][0];
            h.click();
        }""")
        pg.wait_for_timeout(400)
        hmenu = pg.text_content(".hwmenu")
        ck("Mark done" in hmenu or "Mark not done" in hmenu,
           f"one click on homework offers done as well ({hmenu!r})")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)

        # ---- and the editor is not a dead end for done --------------------
        pg.evaluate("""() => {
            const h = [...document.querySelectorAll('#gbody .hw')][0];
            h.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
        }""")
        pg.wait_for_timeout(500)
        ck(pg.is_visible("#qDone"), "the editor can tick homework off too")
        before = mine()
        pg.click("#qDone")
        pg.wait_for_timeout(900)
        ck(mine() != before and "done" in
           [l for l in mine().split("\n") if "Sources" in l][0],
           f"and it really writes it: {[l for l in mine().split(chr(10)) if 'Sources' in l]}")

        # ---- the dialog is centred, not pinned to the corner --------------
        # `*{margin:0}` at the top of the file had been killing the auto
        # margins that centre a <dialog>, so every one of them opened top-left.
        pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.querySelector('.bname')||{}).textContent.includes('Piano'));
            b.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
        }""")
        pg.wait_for_timeout(500)
        box = pg.evaluate("""() => {
            const d = document.getElementById('quickDlg');
            const r = d.getBoundingClientRect();
            return {left: Math.round(r.left), top: Math.round(r.top),
                    w: Math.round(r.width), h: Math.round(r.height),
                    vw: window.innerWidth, vh: window.innerHeight};
        }""")
        cx = box["left"] + box["w"] / 2
        cy = box["top"] + box["h"] / 2
        ck(abs(cx - box["vw"] / 2) < 20,
           f"the editor is horizontally centred ({cx} vs {box['vw']/2})")
        ck(abs(cy - box["vh"] / 2) < 20,
           f"and vertically ({cy} vs {box['vh']/2})")

        # ---- the popup can say everything /edit can -----------------------
        # It used to offer a one-off at a fixed time and nothing else, while
        # the form could write weekly, monthly-by-date, monthly-by-weekday, a
        # date window and any duration. Two ways to say the same thing, one of
        # them poorer, reads as a missing feature.
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)
        pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[1];
            const r = col.getBoundingClientRect();
            col.dispatchEvent(new MouseEvent('dblclick', {bubbles: true,
                clientX: r.left + 20, clientY: r.top + 380}));
        }""")
        pg.wait_for_timeout(500)
        ck(pg.is_visible("#quickDlg"), "double-clicking empty space still adds")
        modes = pg.eval_on_selector_all("#qMode option", "o => o.map(x => x.value)")
        ck(modes == ["once", "weekly", "dom", "nth", "everyN"],
           f"every repeat shape the form has ({modes})")
        ck(pg.input_value("#qMode") == "once" and pg.is_visible("#qOnDate"),
           "starting as a one-off on the day you clicked")

        pg.fill("#qName", "Robotics build")
        pg.select_option("#qMode", "weekly")
        pg.wait_for_timeout(300)
        dows = pg.eval_on_selector_all("#qShape .qDow", "c => c.map(x => x.value)")
        ck(dows == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
           f"weekly gives day checkboxes, weekend included ({dows})")
        ck(pg.is_visible("#qRangeRow"),
           "and a repeating one can be limited to a date window")
        pg.evaluate("""() => [...document.querySelectorAll('#qShape .qDow')]
            .filter(c => c.value === 'Tue' || c.value === 'Thu')
            .forEach(c => c.checked = true)""")
        pg.select_option("#qStart", "16:00")
        pg.fill("#qDur", "80")                    # not a round number in any list
        pg.fill("#qFrom", "2024-09-01")
        pg.fill("#qTo", "2024-11-30")
        pg.select_option("#qColor", "teal")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        txt = mine()
        ck("@event Tue,Thu - Robotics build - 16:00-17:20 - 09.01-11.30 - teal" in txt,
           f"the popup writes the full line: "
           f"{[l for l in txt.split(chr(10)) if 'Robotics' in l]}")

        # and it reads that back into the same widgets
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(800)
        dbl("Robotics build")
        ck(pg.input_value("#qMode") == "weekly", "reopening it says weekly")
        picked = pg.eval_on_selector_all("#qShape .qDow",
                                         "c => c.filter(x => x.checked).map(x => x.value)")
        ck(picked == ["Tue", "Thu"], f"with the right days ticked ({picked})")
        ck(pg.input_value("#qDur") == "80", f"the typed duration ({pg.input_value('#qDur')!r})")
        ck(pg.input_value("#qFrom").endswith("09-01")
           and pg.input_value("#qTo").endswith("11-30"),
           f"and the window ({pg.input_value('#qFrom')}, {pg.input_value('#qTo')})")

        # changing the shape outright works too
        pg.select_option("#qMode", "nth")
        pg.wait_for_timeout(300)
        pg.select_option("#qNth", "1")
        pg.select_option("#qNthDow", "Thu")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        ck("@event monthly Thu#1 - Robotics build" in mine(),
           f"a weekly event can become a monthly one: "
           f"{[l for l in mine().split(chr(10)) if 'Robotics' in l]}")

        # ---- every N days --------------------------------------------------
        # Larry: "every 30 days" is not "monthly 30" -- a renewal on a 90-day
        # cycle, or anything on a 10-day one, falls on no weekday and on no day
        # of the month. (First Tuesday of the month already worked: that is
        # Monthly (weekday), nth=1, Tue.)
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)
        pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[0];
            const r = col.getBoundingClientRect();
            col.dispatchEvent(new MouseEvent('dblclick', {bubbles: true,
                clientX: r.left + 20, clientY: r.top + 300}));
        }""")
        pg.wait_for_timeout(500)
        modes = pg.eval_on_selector_all("#qMode option", "o => o.map(x => x.value)")
        ck("everyN" in modes, f"the popup offers it ({modes})")
        pg.fill("#qName", "Renew hosting")
        pg.select_option("#qMode", "everyN")
        pg.wait_for_timeout(300)
        pg.fill("#qEveryN", "14")
        pg.fill("#qEveryFrom", "2026-09-09")
        pg.select_option("#qStart", "19:00")
        pg.fill("#qDur", "15")
        pg.click("#qSave")
        pg.wait_for_timeout(900)
        ck("@event every 14 from 2026-09-09 - Renew hosting - 19:00-19:15" in mine(),
           f"and writes it: {[l for l in mine().split(chr(10)) if 'Renew' in l]}")

        def onDay(day):
            pg.evaluate("""(d) => {
                const t = new Date(d + 'T12:00:00');
                weekStart = mondayOf(t); focusDay = (t.getDay() + 6) % 7;
                render();
            }""", day)
            pg.wait_for_timeout(400)
            return pg.evaluate("""(d) => {
                const col = [...document.querySelectorAll('#gbody .col')]
                    .find(c => c.dataset.k === d);
                return col ? col.textContent.includes('Renew hosting') : null;
            }""", day)

        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)
        # every 14 days from a Wednesday, so every occurrence is a Wednesday
        # and the 5-day grid can show it
        ck(onDay("2026-09-09") is True, "it lands on the anchor day")
        ck(onDay("2026-09-23") is True, "and a fortnight later")
        ck(onDay("2026-10-07") is True, "and a fortnight after that")
        ck(onDay("2026-09-16") is False, "and not on the week in between")
        ck(onDay("2026-09-08") is False, "nor before it starts")

        # reopening reads it back into the same widgets
        pg.evaluate("""() => {
            const t = new Date('2026-09-23T12:00:00');
            weekStart = mondayOf(t); render();
        }""")
        pg.wait_for_timeout(500)
        dbl("Renew hosting")
        ck(pg.input_value("#qMode") == "everyN", "reopening says every N days")
        ck(pg.input_value("#qEveryN") == "14" and
           pg.input_value("#qEveryFrom") == "2026-09-09",
           f"with the interval and the anchor "
           f"({pg.input_value('#qEveryN')}, {pg.input_value('#qEveryFrom')})")
        pg.click("#qCancel")

        # ---- the time dropdown speaks whichever clock the page is set to ---
        # It only ever said "17:30" while the grid said "5:30 PM". One
        # dropdown, not an hour select beside an AM/PM select -- that pairing
        # is how you get 12:30 AM meaning noon.
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)
        def qtimes():
            return pg.evaluate("""() => {
                const st = document.getElementById('qStart');
                const o = [...st.options];
                return {labels: o.map(x => x.textContent),
                        values: o.map(x => x.value),
                        is12: IS12};
            }""")
        pg.click('#fmtSeg button[data-f="12h"]')
        pg.wait_for_timeout(400)
        t12 = qtimes()
        ck("5:30 PM" in t12["labels"] and "17:30" not in t12["labels"],
           f"in 12-hour mode the list reads 5:30 PM, not 17:30 "
           f"({t12['labels'][-8:]})")
        ck("12:00 PM" in t12["labels"] and "12:00 AM" not in t12["labels"],
           "noon is 12:00 PM and there is no midnight in the school-day range")
        ck("17:30" in t12["values"],
           "but the VALUE is still 24-hour: that is what goes into the line, "
           "and it must not depend on what the reader is looking at")

        pg.click('#fmtSeg button[data-f="24h"]')
        pg.wait_for_timeout(400)
        t24 = qtimes()
        ck("17:30" in t24["labels"] and "5:30 PM" not in t24["labels"],
           f"switching to 24-hour relabels the same list ({t24['labels'][-6:]})")
        ck(t24["values"] == t12["values"],
           "and changes not one value")

        # a selection survives the relabel
        pg.click('#fmtSeg button[data-f="12h"]')
        pg.wait_for_timeout(300)
        kept = pg.evaluate("""() => {
            const st = document.getElementById('qStart');
            st.value = '16:00';
            document.querySelector('#fmtSeg button[data-f="24h"]').click();
            const a = st.value;
            document.querySelector('#fmtSeg button[data-f="12h"]').click();
            return {after24: a, after12: st.value,
                    label: st.options[st.selectedIndex].textContent};
        }""")
        ck(kept["after24"] == "16:00" and kept["after12"] == "16:00"
           and kept["label"] == "4:00 PM",
           f"and what you had picked stays picked across the switch ({kept})")

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
