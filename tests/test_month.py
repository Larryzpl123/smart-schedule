"""A month at a glance, from the data that already exists.

A classmate asked for month and year views showing "task progress". There is
no task model in this app -- @hw is one due date, @event is one time block --
so a year of progress bars would be a year of nothing. A month of the data
that DOES exist is useful today: which days have no school, which have a
special schedule, when homework is due, what is on after school.

Both views must agree about what falls on a day, which is why itemsOn() is one
function and not two copies of the matching rules.
"""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_mo_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5202"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="mo", ADMIN_PASSWORD="apw",
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

@sport Cross Country - Tue,Thu - 15:45-17:15 - 08.25-11.05 - green

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
B - Organic Chem. - CH410 - 2 - Mr. Halvorsen - MC 205 - green

@event Mon - Piano lesson - 16:00-17:00 - 08.25-06.30 - pink
@event 2026-09-16 - Dentist - 14:00-15:00 - gray
@hw A - before - Read Ch. 4 - 2026-09-09
@hw B - during - Lab report due - 2026-09-09

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T15:00:00Z")

        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SEED)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)
        ck(pg.is_visible("#viewSeg"), "there is a Week / Month switch")

        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
        pg.wait_for_timeout(900)

        cells = pg.eval_on_selector_all("#gbody .mcell", "c => c.length")
        ck(cells in (35, 42), f"a month of whole weeks ({cells} cells)")
        ck(pg.text_content("#wkLabel").strip() == "September 2026",
           f"the label names the month ({pg.text_content('#wkLabel')!r})")
        head = pg.text_content("#ghead")
        # the text is "Mon"; the CSS uppercases it on screen
        ck(head.startswith("Mon") and "Sun" in head,
           f"seven columns, Monday first ({head!r})")

        def cell(day):
            return pg.evaluate("""(d) => {
                const c = [...document.querySelectorAll('#gbody .mcell')]
                  .find(e => !e.classList.contains('other')
                        && e.querySelector('.mnum').textContent === String(d));
                return c ? {text: c.textContent,
                            noschool: c.classList.contains('noschool'),
                            today: c.classList.contains('today'),
                            chips: [...c.querySelectorAll('.mchip')].map(x=>x.textContent),
                            hw: [...c.querySelectorAll('.mchip.hw')].map(x=>x.textContent)} : null;
            }""", day)

        c9 = cell(9)
        ck(c9 and len(c9["hw"]) == 2,
           f"both pieces of homework show on the 9th ({c9 and c9['hw']})")
        ck(c9 and any("Read Ch. 4" in x for x in c9["hw"]), "by name")
        ck(c9 and c9["today"], "and today is marked")
        ck(c9 and "Day 2" in c9["text"], f"the cycle day is there ({c9['text'][:40]!r})")

        c7 = cell(7)
        ck(c7 and c7["noschool"], "Labor Day is shaded as a no-school day")
        ck(c7 and "Labor Day" in c7["text"], f"and named ({c7['text'][:40]!r})")
        ck(c7 and any("Piano" in x for x in c7["chips"]),
           "a Monday lesson still shows on a holiday")

        c16 = cell(16)
        ck(c16 and any("Dentist" in x for x in c16["chips"]),
           f"a one-off event lands on its date ({c16 and c16['chips']})")
        c8 = cell(8)
        ck(c8 and any("Cross Country" in x for x in c8["chips"]),
           f"a sport shows on the days it meets ({c8 and c8['chips']})")
        c9chips = c9["chips"] if c9 else []
        ck(not any("Cross Country" in x for x in c9chips),
           f"and not on the days it does not ({c9chips})")

        # "Wednesday" under every Wednesday is thirty words of nothing
        ck(pg.text_content("#gbody").count("Wednesday") == 0,
           "an ordinary Wednesday is not labelled as an event")

        # arrows move by month, Today comes back
        pg.click("#nextWk")
        pg.wait_for_timeout(700)
        ck(pg.text_content("#wkLabel").strip() == "October 2026",
           f"the arrow steps a month ({pg.text_content('#wkLabel')!r})")
        pg.click("#todayBtn")
        pg.wait_for_timeout(700)
        ck(pg.text_content("#wkLabel").strip() == "September 2026",
           f"Today comes back to this month ({pg.text_content('#wkLabel')!r})")

        # ---- flipping Week/Month must be a fixed point ---------------------
        # It was not: the month was taken from the week's MONDAY, and the Monday
        # of the week of Sep 7 2026 in the 5-day view is fine, but coming back
        # landed on the Monday of the week containing today (a Sunday), which is
        # Aug 31 -- so the next flip read that as August, and every round trip
        # slid another month into the past.
        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[0].click()")
        pg.wait_for_timeout(600)
        week0 = pg.text_content("#wkLabel")
        seen = []
        for _ in range(4):
            pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
            pg.wait_for_timeout(500)
            seen.append(pg.text_content("#wkLabel").strip())
            pg.evaluate("() => document.querySelectorAll('#viewSeg button')[0].click()")
            pg.wait_for_timeout(500)
            seen.append(pg.text_content("#wkLabel").strip())
        ck(all(x == "September 2026" for x in seen[0::2]),
           f"the month stays put however often you flip ({seen[0::2]})")
        ck(all(x == seen[1] for x in seen[1::2]) and seen[1] == week0.strip(),
           f"and so does the week ({week0.strip()!r} then {seen[1::2]})")

        # The report came from a Sunday, which is the worst case: Sunday's own
        # Monday is in the previous month more often than any other day's.
        pg.clock.install(time="2026-09-06T15:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(1000)
        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[0].click()")
        pg.wait_for_timeout(600)
        sun = []
        for _ in range(3):
            pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
            pg.wait_for_timeout(500)
            sun.append(pg.text_content("#wkLabel").strip())
            pg.evaluate("() => document.querySelectorAll('#viewSeg button')[0].click()")
            pg.wait_for_timeout(500)
            sun.append(pg.text_content("#wkLabel").strip())
        ck(all(x == "September 2026" for x in sun[0::2]),
           f"opened on a Sunday, the month still stays put ({sun[0::2]})")
        ck(all(x == sun[1] for x in sun[1::2]),
           f"and the week does too ({sun[1::2]})")

        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
        pg.wait_for_timeout(600)

        # clicking a day opens it in the week view
        pg.evaluate("""() => {
            const c = [...document.querySelectorAll('#gbody .mcell')]
              .find(e => !e.classList.contains('other')
                    && e.querySelector('.mnum').textContent === '16');
            c.click();
        }""")
        pg.wait_for_timeout(900)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 1,
           "clicking a day opens that day on its own")
        ck("Sep 16" in pg.text_content("#ghead"),
           f"the right day ({pg.text_content('#ghead')[:40]!r})")
        ck("Dentist" in pg.text_content("#gbody"),
           "with the event that was in the month cell")

        # the choice is remembered
        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
        pg.wait_for_timeout(700)
        pg.reload()
        pg.wait_for_timeout(1200)
        ck(pg.eval_on_selector_all("#gbody .mcell", "c => c.length") > 0,
           "the month view survives a reload")

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
