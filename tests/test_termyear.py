"""The two year bugs Larry hit:

  1. Dec 2025 rendered his Fall 2026 classes. The @term ranges are bare MM.DD,
     so 12.10 is inside BOTH "Fall 2025 = 08.26-01.16" and
     "Fall 2026 = 08.25-01.15" and whichever came first in the file won.
  2. Importing last year's Spring PDF filed it as this year's Spring. The PDF
     has no year in it at all -- only "SS Semester 2" and a grade like "11th" --
     so the year has to come from the student's class year plus that grade.
"""
import os, json, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_ty_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5141"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="tytest", ADMIN_PASSWORD="apw",
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

# Larry's actual text, three terms, Fall 2026 listed FIRST (that ordering is
# what exposed the bug).
SCHEDULE = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - red - Ea
H - Revolution in Ancient Rome - HI445 - 1 - Mr. Lindqvist - R 205 - indigo - Ea

@term Spring 2026 = 01.20-06.30
A - AP Calculus BC - MA560 - 1 - Ms. Yam2 - R 202 - red - La
H - Organic Chem. - CH410 - 2 - Mr. Halvorsen - MC 208 - green - La - days 3,7

@term Fall 2025 = 08.26-01.16
A - AP Calculus BC - MA560 - 1 - Ms. Yam2 - R 202 - red - La
E - U.S. History - HI300 - 5 - Mr. Bennett - R 135 - yellow - Ea

@pref time_format = 12h
"""

def names_in_week(pg, iso_utc, year_id):
    pg.clock.install(time=iso_utc)
    pg.goto(BASE + "/app")
    pg.wait_for_selector("#gbody .col", timeout=8000)
    pg.select_option("#yearSel", year_id)
    pg.wait_for_timeout(400)
    return pg.eval_on_selector_all("#gbody .block .bname", "n=>n.map(x=>x.textContent)")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-03T18:00:00Z")

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
        pg.fill("#rawText", SCHEDULE)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # --- 1. Dec 2025 must show the Fall 2025 classes ---
        dec25 = names_in_week(pg, "2025-12-10T17:00:00Z", "2025-2026")
        ck("U.S. History" in dec25,
           f"Dec 2025 shows Fall 2025 classes ({dec25})")
        ck("Game Design" not in dec25 and "Revolution in Ancient Rome" not in dec25,
           "Dec 2025 does NOT show the Fall 2026 classes")

        # --- 2. Dec 2026 must show the Fall 2026 classes ---
        dec26 = names_in_week(pg, "2026-12-09T17:00:00Z", "2026-2027")
        ck("Game Design" in dec26, f"Dec 2026 shows Fall 2026 classes ({dec26})")
        ck("U.S. History" not in dec26, "Dec 2026 does NOT show Fall 2025's classes")

        # --- 3. Spring still resolves correctly (same school year, other half) ---
        mar26 = names_in_week(pg, "2026-03-10T17:00:00Z", "2025-2026")
        ck("Organic Chem." in mar26, f"Mar 2026 shows Spring 2026 classes ({mar26})")
        ck("U.S. History" not in mar26, "Mar 2026 is not still on Fall 2025")

        # --- 4. the helper itself ---
        h = pg.evaluate("""() => ({
            fall:   termYearStart("Fall 2025"),
            spring: termYearStart("Spring 2026"),
            none:   termYearStart("All Year"),
            dec:    schoolYearStart(new Date(2025,11,10)),
            mar:    schoolYearStart(new Date(2026,2,10)),
            aug:    schoolYearStart(new Date(2026,7,1)),
            jul:    schoolYearStart(new Date(2026,6,31))
        })""")
        ck(h["fall"] == 2025 and h["spring"] == 2025,
           f"Fall 2025 and Spring 2026 are the same school year ({h})")
        ck(h["none"] is None, "a label with no year returns null (falls back to dates)")
        ck(h["dec"] == 2025 and h["mar"] == 2025, "Dec 2025 and Mar 2026 are both 2025-26")
        ck(h["aug"] == 2026 and h["jul"] == 2025, "the school year flips in August")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

# ---------- 2. PDF import must land in the right YEAR ----------
sys.path.insert(0, APP_DIR)
import app as APP
ck(APP.school_year_for("27", 11) == "2025-2026",
   f"a class-of-27 student in 11th grade was in 2025-2026 (got {APP.school_year_for('27', 11)})")
ck(APP.school_year_for("27", 12) == "2026-2027", "...and in 12th grade, 2026-2027")
ck(APP.school_year_for("28", 11) == "2026-2027", "class of 28, 11th grade -> 2026-2027")
ck(APP.school_year_for("", 11) is None, "no class year -> None (fall back to current)")
ck(APP.school_year_for("27", None) is None, "no grade on the PDF -> None")
ck(APP.school_year_for("27", 99) is None, "a nonsense grade is rejected")

# and through the real route, with the real PDF: it says 11th + Semester 2,
# so it must import as Spring 2026, NOT Spring 2027.
PDF = os.path.join(os.environ.get("SSA_PDFS", "/tmp/pdfs"), "sample-schedule.pdf")
if os.path.exists(PDF):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
        {"email": "27doej@shadysideacademy.org", "password": "pw123456"}).encode()))
    bnd = "----y"
    with open(PDF, "rb") as f:
        blob = f.read()
    pre = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"pdf\"; "
           f"filename=\"s.pdf\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    req = urllib.request.Request(BASE + "/edit/import-pdf",
                                 pre + blob + f"\r\n--{bnd}--\r\n".encode())
    req.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    r = json.loads(op.open(req).read().decode())
    ck(r.get("term") == "Spring 2026",
       f"last year's Spring PDF imports as Spring 2026, not 2027 (got {r.get('term')})")
    ck("@term Spring 2026 = 01.20-06.30" in r.get("text", ""),
       "the merged text carries the right term line")
else:
    print("  SKIP  sample PDF not present")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
