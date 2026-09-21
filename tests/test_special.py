"""⑥ per-date community override, ⑫ @public school-wide events,
⑪ Convocation-style 'special' day type."""
import os, shutil, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_sp_")
SRC = APP_DIR
APP = os.path.join(WORK, "app")           # a copy, so we can edit shared config
shutil.copytree(SRC, APP, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db"))
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5134"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="sptest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

# ⑥: make Thu Sep 10 a Class Meeting with its own longer slot, and blank out
# Fri Sep 11's community block entirely.
with open(os.path.join(APP, "calendar", "2026-2027.txt"), "a", encoding="utf-8") as f:
    f.write("\n@community_on 2026-09-10 = Class Meeting - 10:05-10:45\n"
            "@community_on 2026-09-11 = \n")

# ⑫: a school-wide event and a seniors-only one, both in the shared config.
with open(os.path.join(APP, "schedule.txt"), "a", encoding="utf-8") as f:
    f.write("\n@public 2026-09-09 - Homecoming - 12:00-16:00 - ALL - pink\n"
            "@public 2026-09-09 - Senior Photos - 16:00-17:00 - 12 - teal\n")

srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT
SCHEDULE = """Jane Doe
27 - Junior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
G - World Lit. - EN3011 - 1 - Ms. Whitfield - R 310 - yellow

@pref time_format = 24h
"""

def week_of(pg, iso_utc):
    pg.clock.install(time=iso_utc)
    pg.goto(BASE + "/app")
    pg.wait_for_selector("#gbody .col", timeout=8000)

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-08T16:00:00Z")

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

        # ---- week of Sep 7-11 ----
        week_of(pg, "2026-09-08T16:00:00Z")
        cols = pg.evaluate("""() => Array.from(document.querySelectorAll('#gbody .col'))
            .map(c => Array.from(c.querySelectorAll('.block .bname')).map(n => n.textContent))""")
        # Mon 7 = Labor Day, Tue 8, Wed 9, Thu 10, Fri 11
        tue, wed, thu, fri = cols[1], cols[2], cols[3], cols[4]

        # ⑥ override wins over the day-of-week map
        ck("Class Meeting" in thu and "Club Time" not in thu,
           f"Thu Sep 10 shows Class Meeting instead of Club Time ({thu})")
        times = pg.evaluate("""() => {
            const c = document.querySelectorAll('#gbody .col')[3];
            const b = Array.from(c.querySelectorAll('.block'))
                .find(x => x.querySelector('.bname').textContent === 'Class Meeting');
            return b ? b.style.height : null; }""")
        ck(times and abs(float(times.replace("px", "")) - 40 * 1.05) < 1.5,
           f"the override's own 10:05-10:45 slot is used, not the usual 25 min ({times})")

        # an empty override removes the block for that day
        ck("Assembly" not in fri and "Club Time" not in fri,
           f"Fri Sep 11's community block removed by an empty override ({fri})")
        # untouched days still follow the weekday map
        ck("Advisory" in tue, f"Tue still shows its usual Advisory ({tue})")

        # ⑫ public events, grade-filtered
        ck("Homecoming" in wed, f"@public ALL event shows for a junior ({wed})")
        ck("Senior Photos" not in wed, "@public 12-only event hidden from a junior")

        # ---- ⑪ Convocation week (Aug 25 2026) ----
        week_of(pg, "2026-08-25T16:00:00Z")
        head = pg.eval_on_selector_all("#gbody .col", "c=>c.length")
        conv = pg.evaluate("""() => {
            const cols = document.querySelectorAll('#gbody .col');
            // Aug 25 2026 is a Tuesday -> second column
            return Array.from(cols[1].querySelectorAll('.block')).map(b => ({
                name: b.querySelector('.bname').textContent,
                per: (b.querySelector('.btime')||{}).textContent || "",
                top: parseFloat(b.style.top)}));
        }""")
        cnames = [c["name"] for c in conv]
        ck("Convocation - Senior Quad or Mellon Gym" in cnames,
           f"Convocation block drawn ({cnames})")
        ck("Designated Rooms" in cnames, "grade 9,10,11 block shows for a junior")
        ck("Report to Hillman Terrace" not in cnames,
           "seniors-only Hillman Terrace block hidden from a junior")
        # first-of-day: day 1's first period is A -> the student's own A class
        ck(any(c["name"] == "AP Calculus BC" and "Day 1" in c["per"] for c in conv),
           f"'first-of-day 1' resolved to the student's period-A class ({conv})")
        # day 7's first period is C -- student has no C, so nothing is drawn
        ck(not any("Day 7" in c["per"] for c in conv),
           "a first-of-day slot for a period the student doesn't have is skipped")
        # @day 2 starts with G, and G is this student's World Lit. -- so the
        # letter is read off the rotation table, not from a hardcoded list.
        ck(any(c["name"] == "World Lit." and "G \u00b7 Day 2" in c["per"] for c in conv),
           "day 2's first period (G) resolves through @day, not a hardcoded list")
        ck(len([c for c in conv if "Day" in c["per"]]) == 2,
           "only the two rotation days whose first period the student actually has are drawn")

        # a special day must not eat a cycle day: Aug 26 is still Day 1
        cyc = pg.eval_on_selector_all("#gbody ~ * .dcyc, .dcell .dcyc",
                                      "n=>n.map(x=>x.textContent)")
        ck("Day 1" in cyc, f"Aug 26 is still cycle Day 1 -- Convocation consumed none ({cyc})")

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
