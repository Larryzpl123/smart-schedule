"""The /sample page has to look like the app working, not like a timetable.

Two complaints from a classmate:
  - the colours were wrong. Shady Side colours a course cell by DEPARTMENT,
    and the sample had organic chemistry red and calculus blue, so anyone who
    knows their own PDF reads the demo as broken.
  - it showed only classes: no custom event, no homework. Half of what the app
    does was invisible.

The department colours are not invented here; pdf_import.SSA_SUBJECT_FILLS is
the measured ground truth, read off the real PDF's cell fills.
"""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


sys.path.insert(0, APP_DIR)
import pdf_import


WORK = tempfile.mkdtemp(prefix="ssa_sm_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5196"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="sm", ADMIN_PASSWORD="apw",
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

# subject -> colour, from the importer itself
DEPT = {subj: name for _fill, name, subj in pdf_import.SSA_SUBJECT_FILLS}
ck(DEPT.get("Math") == "red" and DEPT.get("Science") == "green",
   f"the importer says Math is {DEPT.get('Math')} and Science {DEPT.get('Science')}")

# what the sample claims, straight out of the file
import re
src = open(os.path.join(APP_DIR, "static", "app.html"), encoding="utf-8").read()
block = src[src.index(" student:\n`Jane Doe"):src.index("@pref time_format = 12h`", src.index(" student:"))]
lines = [l.strip() for l in block.split("\n")]

def colour_of(course):
    for l in lines:
        if re.match(r"^[A-H] - ", l) and course in l:
            return l.split(" - ")[6].strip()
    return None

for course, subject in [("AP Calculus BC", "Math"),
                        ("Organic Chem.", "Science"),
                        ("World Lit.", "English"),
                        ("U.S. History", "History"),
                        ("French 2", "World Language"),
                        ("Robotics", "Computer Science"),
                        ("Ceramics 1", "Art"),
                        ("Concert Choir", "Music")]:
    got = colour_of(course)
    ck(got == DEPT[subject],
       f"{course} is {subject}, so {DEPT[subject]} (sample says {got})")

ck(sum(1 for l in lines if l.startswith("@hw ")) >= 3,
   "the sample has homework in it")

# Nobody swims, runs and plays the piano at once. Checking one pair by hand
# missed it the first time (cross country was moved off Tue/Thu, and then the
# lesson collided with swimming and track instead), so check EVERY pair of the
# student's own timed things: same weekday, overlapping season, overlapping
# clock -- all three -- is the impossible case.
def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)

def season_overlaps(a, b):
    # (from, to) as MM.DD, either may be missing = all year; ranges may wrap
    def days(r):
        if not r[0] or not r[1]:
            return set(range(0, 1300))
        f, t = r
        alld = [f"{m:02d}.{d:02d}" for m in range(1, 13) for d in range(1, 32)]
        idx = {v: i for i, v in enumerate(alld)}
        if f <= t:
            return {i for v, i in idx.items() if f <= v <= t}
        return {i for v, i in idx.items() if v >= f or v <= t}
    return bool(days(a) & days(b))

timed = []          # (label, dows, start, end, season)
for l in lines:
    if l.startswith("@sport "):
        p = [x.strip() for x in l.replace("@sport", "").strip().split(" - ")]
        t = p[2].split("-")
        rng = p[3].split("-") if len(p) > 3 and "." in p[3] else ["", ""]
        timed.append((p[0], set(p[1].split(",")), mins(t[0]), mins(t[1]), tuple(rng)))
    elif l.startswith("@event "):
        p = [x.strip() for x in l.replace("@event", "").strip().split(" - ")]
        when = p[0]
        if not re.match(r"^[A-Za-z]{3}(,[A-Za-z]{3})*$", when.replace(" ", "")):
            continue                      # one-off and monthly: not a standing clash
        t = p[2].split("-")
        rng = p[3].split("-") if len(p) > 3 and "." in p[3] else ["", ""]
        timed.append((p[1], set(when.split(",")), mins(t[0]), mins(t[1]), tuple(rng)))

clashes = []
for i in range(len(timed)):
    for j in range(i + 1, len(timed)):
        a, b = timed[i], timed[j]
        if not (a[1] & b[1]):
            continue
        if a[2] >= b[3] or b[2] >= a[3]:
            continue
        if not season_overlaps(a[4], b[4]):
            continue
        clashes.append(f"{a[0]} vs {b[0]} on {sorted(a[1] & b[1])}")
ck(len(timed) >= 4, f"the sample has several standing commitments ({len(timed)})")
ck(not clashes, f"and none of them are in two places at once ({clashes})")

# And an event laid across a class period is both ugly and wrong: something due
# IN a class is homework, not an event.
one_offs = [l for l in lines if l.startswith("@event ") and l.split(" - ")[0].count("-") >= 2]
for l in one_offs:
    t = l.split(" - ")[2].split("-")[0]
    ck(t >= "15:15", f"a one-off event in the sample starts after class ({l})")
ck(sum(1 for l in lines if l.startswith("@event ")) >= 2,
   "and both a repeating event and a one-off one")

# A project is the one thing in the app you cannot work out from a week grid,
# so the demo has to have one, with pieces attached to real classes.
projs = [l for l in lines if l.startswith("@project ")]
ck(len(projs) == 1, f"the sample has a project ({projs})")
name = projs[0].replace("@project", "").split(" - ")[0].strip()
miles = [l for l in lines if l.startswith("@hw ") and name in l]
ck(len(miles) >= 3, f"with several milestones attached to it ({len(miles)})")
ck(any(l.rstrip().endswith("done") for l in miles)
   and not all(l.rstrip().endswith("done") for l in miles),
   "some done and some not, so the count on the band means something")
ck(any("{{" in l for l in lines if l.startswith(("@hw ", "@event "))),
   "dated ones use week placeholders, not a date that goes stale")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # Three different weeks: whatever week the page opens on, the dated
        # demo homework has to be in it. A hardcoded date would pass once.
        for instant, human in [("2026-09-09T15:00:00Z", "a Wednesday in September"),
                               ("2026-10-21T15:00:00Z", "a Wednesday in October"),
                               ("2027-04-14T15:00:00Z", "a Wednesday in April")]:
            pg.clock.install(time=instant)
            pg.goto(BASE + "/sample")
            pg.wait_for_selector("#gbody .col")
            pg.wait_for_timeout(700)
            body = pg.text_content("#gbody")
            ck("Piano lesson" in body, f"the repeating event shows on {human}")
            ck("Meet Ms. Rivera" in body, f"the one-off meeting shows on {human}")
            ck("Read Ch. 4" in body or "Problem set 3" in body
               or "Bring essay draft" in body, f"homework shows on {human}")
            ck("{{" not in body, f"no placeholder leaked onto the page on {human}")

        # and the colours actually reach the blocks
        pg.clock.install(time="2026-09-09T15:00:00Z")
        pg.goto(BASE + "/sample")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)
        chem = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('Organic Chem'));
            return b ? getComputedStyle(b).getPropertyValue('--blk').trim() : null;
        }""")
        calc = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('Calculus'));
            return b ? getComputedStyle(b).getPropertyValue('--blk').trim() : null;
        }""")
        ck(chem and calc and chem != calc,
           f"chemistry and calculus are drawn in different colours ({chem} vs {calc})")
        ck(chem == pg.evaluate("COLOR_MAP['green']"),
           f"chemistry is the green of the palette ({chem})")
        ck(calc == pg.evaluate("COLOR_MAP['red']"),
           f"calculus is the red ({calc})")

        ck("department colours" in (pg.text_content("body") or ""),
           "the banner says what is in the sample")
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
