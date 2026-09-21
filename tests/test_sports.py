"""Sports: three seasons, pickers instead of typing.

A classmate's complaints, all real:
  - the days of the week had to be typed as a string ("Mon,Thu")
  - there was no colour picker
  - there was ONE sport box per semester, and there are three seasons, so a
    winter sport could not be entered at all -- and winter crosses January,
    which no single semester covers either.

Sports are now per student rather than per term, entered as rows with day
checkboxes, time dropdowns and a colour, and their season range may wrap
around New Year.
"""
import os, re, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_sp_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5194"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="sp", ADMIN_PASSWORD="apw",
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

# Fall, winter (crossing New Year) and spring, all at once -- the thing that
# was impossible before.
SEED = """Jane Doe
27 - Senior

@sport Cross Country - Mon,Wed - 15:45-17:15 - 08.25-11.05 - green
@sport Swimming - Mon,Thu - 15:45-17:15 - 11.16-02.20 - cyan
@sport Track - Tue,Thu - 16:00-17:30 - 03.02-05.20 - orange

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea

@term Spring 2027 = 01.19-06.30
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # ---- enter three sports through the form, no typing of weekdays ----
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#addSport")
        ck(pg.is_visible("#addSport"), "there is a sports section of its own")
        ck(not pg.is_visible(".t_sport"),
           "and no single sport text box buried in the term card")

        for i in range(3):
            pg.click("#addSport")
        rows = pg.query_selector_all("#sportRows .sprow")
        ck(len(rows) == 3, f"three sports fit ({len(rows)} rows)")

        data = [("Cross Country", ["Mon", "Wed"], "15:45", "17:15",
                 "2024-08-25", "2024-11-05", "green"),
                ("Swimming", ["Mon", "Thu"], "15:45", "17:15",
                 "2024-11-16", "2024-02-20", "cyan"),
                ("Track", ["Tue", "Thu"], "16:00", "17:30",
                 "2024-03-02", "2024-05-20", "orange")]
        for row, (nm, dows, st, en, fr, to, col) in zip(rows, data):
            row.query_selector(".sp_name").fill(nm)
            for cb in row.query_selector_all(".sp_dow"):
                if cb.get_attribute("value") in dows:
                    cb.check()
            row.query_selector(".sp_start").select_option(st)
            row.query_selector(".sp_end").select_option(en)
            row.query_selector(".sp_from").fill(fr)
            row.query_selector(".sp_to").fill(to)
            row.query_selector(".sp_color").select_option(col)

        ck(len(rows[0].query_selector_all(".sp_dow")) == 5,
           "days of the week are checkboxes, not a string to type")
        ck(rows[0].query_selector(".sp_color").evaluate(
            "e => e.tagName") == "SELECT", "the colour is a dropdown")
        ck(rows[0].query_selector(".sp_start").evaluate(
            "e => e.tagName") == "SELECT", "so are the times")

        # what the form would write
        pg.click("#modeText")
        txt = pg.input_value("#rawText")
        for want in ["@sport Cross Country - Mon,Wed - 15:45-17:15 - 08.25-11.05 - green",
                     "@sport Swimming - Mon,Thu - 15:45-17:15 - 11.16-02.20 - cyan",
                     "@sport Track - Tue,Thu - 16:00-17:30 - 03.02-05.20 - orange"]:
            ck(want in txt, f"the form writes {want[7:30]}...")
        first_term = txt.index("@term")
        ck(all(txt.index(l) < first_term for l in
               ["@sport Cross Country", "@sport Swimming", "@sport Track"]),
           "sports are written above the terms, where a PDF re-import keeps them")

        # ---- and read back into the pickers, not a raw box -----------------
        pg.fill("#rawText", SEED)
        pg.click("#modeForm")
        rows = pg.query_selector_all("#sportRows .sprow")
        ck(len(rows) == 3, f"loading text back gives three rows ({len(rows)})")
        ck(not pg.query_selector_all("#sportRows .sprow.raw"),
           "none of them fell back to a raw text box")
        ck(rows[1].query_selector(".sp_name").input_value() == "Swimming",
           "the second one is Swimming")
        ck(rows[1].query_selector(".sp_from").input_value().endswith("11-16"),
           "with its season start read back into the date picker")

        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # ---- what the app draws --------------------------------------------
        def sports_on(day):
            pg.goto(BASE + "/app")
            pg.wait_for_selector("#gbody .col")
            pg.wait_for_timeout(300)
            pg.evaluate(f"""() => {{
                const d = new Date('{day}T12:00:00');
                weekStart = mondayOf(d);
                focusDay = (d.getDay() + 6) % 7;
                render();
            }}""")
            pg.wait_for_timeout(300)
            # only that day's column -- the grid draws the whole week, so
            # asking the grid at large would answer for Monday every time
            return pg.evaluate("""(i) => {
                const col = document.querySelectorAll('#gbody .col')[i];
                return col ? [...col.querySelectorAll('.block')]
                                .map(e => (e.textContent || '').trim()) : [];
            }""", (__import__("datetime").date.fromisoformat(day).weekday()))

        got = " | ".join(sports_on("2026-09-14"))     # a Monday in the fall
        ck("Cross Country" in got, f"a fall sport shows in September ({got[:90]})")
        ck("Swimming" not in got, "and the winter one does not")

        got = " | ".join(sports_on("2026-12-14"))     # a Monday in December
        ck("Swimming" in got, f"the winter sport shows in December ({got[:90]})")

        # January: still winter, but a different semester -- this is exactly
        # what a term-scoped sport could not express.
        got = " | ".join(sports_on("2027-02-01"))     # a Monday in February
        ck("Swimming" in got,
           f"and still in February, on the far side of New Year ({got[:90]})")
        ck("Cross Country" not in got, "with the fall season over")

        got = " | ".join(sports_on("2027-03-09"))     # a Tuesday in March
        ck("Track" in got, f"the spring sport shows in March ({got[:90]})")
        ck("Swimming" not in got, "and winter has ended")

        # a Friday: nothing is on
        got = " | ".join(sports_on("2026-09-18"))
        ck("Cross Country" not in got, "nothing on a day the sport does not meet")

        ck(not errs, f"no page errors: {errs[:3]}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

# ---- the server's own cap --------------------------------------------------
import urllib.request, urllib.parse, http.cookiejar
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))
too_many = "Jane Doe\n27 - Senior\n\n" + "".join(
    f"@sport S{i} - Mon - 15:45-17:15 - cyan\n" for i in range(9))
r = op.open(urllib.request.Request(
    BASE + "/edit", urllib.parse.urlencode({"schedule_text": too_many}).encode()))
body = r.read().decode()
ck("limit is 8" in body, "posting 9 sports straight to the server is refused")
mine = op.open(BASE + "/api/my-schedule").read().decode()
ck("S8" not in mine, "and the refused save changed nothing")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
