# -*- coding: utf-8 -*-
"""@retime: a normal cycle day on different times (2026-10-16 Pep Rally).

The school's email gave two timetables for one day, Cycle Day 4, and said the
choice between them would be made at 8am. The app already had calendar
"special" days (Convocation), but those DROP OUT of the rotation: marking the
16th special would have made it a day with no cycle number and shifted every
day after it by one -- the exact silent-wrong failure of patch 36.

So a retimed day keeps its cycle number and its classes and only moves them:
  @retime 2026-10-16 = Pep Rally (outdoor)
  @special Pep Rally (outdoor) | 08:15-09:00 | period 1 | ALL
  ...
"period N" is the student's own class in the Nth period of THAT day's
rotation, and the binding lives in the shared config so the 8am switch is one
word, edited from a phone, with no deploy.
"""
import os, shutil, socket, subprocess, sys, tempfile, time, urllib.request

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

OUTDOOR = """
@special Pep Rally (outdoor) | 08:15-09:00 | period 1 | ALL
@special Pep Rally (outdoor) | 09:05-09:55 | period 2 | ALL
@special Pep Rally (outdoor) | 10:00-10:50 | period 3 | ALL
@special Pep Rally (outdoor) | 10:55-11:40/11:25-12:10 | period 4 | ALL
@special Pep Rally (outdoor) | 12:15-13:05 | period 5 | ALL
@special Pep Rally (outdoor) | 13:10-13:55 | period 6 | ALL
@special Pep Rally (outdoor) | 14:05-15:00 | MS/SS Spirit Event, Farrell Stadium | ALL
"""
RAIN = """
@special Pep Rally (rain) | 08:15-09:05 | period 1 | ALL
@special Pep Rally (rain) | 09:10-10:00 | period 2 | ALL
@special Pep Rally (rain) | 10:05-10:50 | Pep Rally, Rauh Theatre | ALL
@special Pep Rally (rain) | 10:55-11:45 | period 3 | ALL
@special Pep Rally (rain) | 11:50-12:40/12:25-13:15 | period 4 | ALL
@special Pep Rally (rain) | 13:20-14:10 | period 5 | ALL
@special Pep Rally (rain) | 14:15-15:00 | period 6 | ALL
"""
def block(variant):
    return f"\n@retime 2026-10-16 = Pep Rally ({variant})\n" + OUTDOOR + RAIN

# ---------------------------------------------------------------------------
print("\n[the admin's save check]")
WORK = tempfile.mkdtemp(prefix="ssa_rt_")
os.environ.update(DB_PATH=os.path.join(WORK, "db", "s.db"), SECRET_KEY="rt",
                  ADMIN_PASSWORD="apw")
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    os.environ.pop(k, None)
sys.path.insert(0, APP_DIR)
_cwd = os.getcwd(); os.chdir(APP_DIR)
import app as A
os.chdir(_cwd)
base = open(os.path.join(APP_DIR, "schedule.txt"), encoding="utf-8").read()

e, w = A.check_shared(base)
ck(not e, f"the current shared file still passes ({e})")
e, w = A.check_shared(base + block("outdoor"))
ck(not e, f"both pep rally timetables pass ({e})")
ck(not any("@retime" in x for x in w), f"and @retime is a known directive ({w})")
e, w = A.check_shared(base + block("indoor"))
ck(any("Did you mean" in x and "Pep Rally (rain)" in x for x in e),
   f"a misspelt variant is REFUSED, with the right names suggested ({e})")
e, w = A.check_shared(base + OUTDOOR.replace("10:55-11:40/11:25-12:10",
                                             "10:55-11:40/11:25-12:10/12:00-12:30"))
ck(any("two lunch tracks" in x for x in e), "three lunch tracks are refused")
e, w = A.check_shared(base + OUTDOOR.replace("| period 6 |", "| period 9 |"))
ck(any("no period 9" in x for x in e), "a period that cannot exist is refused")
e, w = A.check_shared(base + "\n@retime 2026-13-40 = Pep Rally (outdoor)\n" + OUTDOOR)
ck(any("not a real date" in x for x in e), "an impossible date is refused")

# ---------------------------------------------------------------------------
CODE = os.path.join(WORK, "code")
shutil.copytree(APP_DIR, CODE, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db", "tests"))
SHARED = os.path.join(CODE, "schedule.txt")
def set_shared(variant):
    with open(SHARED, "w", encoding="utf-8") as fh:
        fh.write(base + (block(variant) if variant else ""))
set_shared("outdoor")

PORT = "5219"
_s = socket.socket(); _s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    _s.bind(("127.0.0.1", int(PORT)))
except OSError:
    print(f"  FAIL  port {PORT} in use"); sys.exit(1)
finally:
    _s.close()
env = dict(os.environ, DB_PATH=os.path.join(WORK, "d2", "s.db"), SECRET_KEY="rt2",
           ADMIN_PASSWORD="apw")
srv = subprocess.Popen([sys.executable, "-c",
                        f"import app; app.app.run(port={PORT}, debug=False)"],
                       cwd=CODE, env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:" + PORT
for _ in range(80):
    try:
        urllib.request.urlopen(BASE + "/local", timeout=1).read(); break
    except Exception:
        time.sleep(0.25)

def student(e_track):
    # Day 4 is B C G E F H. E is the midday period with two lunch tracks.
    return f"""Jane Doe
27 - Junior

@term Fall 2026 = 08.25-01.15
B - Bio B - BI100 - 1 - Ms. B - R1 - green - Ea
C - Chem C - CH100 - 1 - Ms. C - R2 - blue - Ea
G - Geo G - GE100 - 1 - Ms. G - R3 - red - Ea
E - Eng E - EN100 - 1 - Ms. E - R4 - yellow - {e_track}
F - Fr F - FR100 - 1 - Ms. F - R5 - purple - Ea
H - Hist H - HI100 - 1 - Ms. H - R6 - orange - Ea
@pref time_format = 24h
"""

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        # 09:30 Pittsburgh on Friday the 16th
        pg.clock.install(time="2026-10-16T13:30:00Z")

        def load(e_track="La"):
            pg.goto(BASE + "/local/app")
            pg.evaluate("""s => { localStorage.setItem('ssa-local-schedule', s);
                                  localStorage.setItem('ssa-local-install', 'off');
                                  localStorage.setItem('ssa-local-remind', 'off'); }""",
                        student(e_track))
            pg.reload()
            pg.wait_for_timeout(1500)

        def day(iso):
            y, m, d = map(int, iso.split("-"))
            return pg.evaluate(f"""() => {{
              const s = daySpecs(new Date({y}, {m-1}, {d}));
              const hm = x => String(Math.floor(x/60)).padStart(2,'0') + ':' + String(x%60).padStart(2,'0');
              return {{cycle: DAYMAP['{iso}'] && DAYMAP['{iso}'].cycle,
                       blocks: s.blocks.filter(b => b.kind === 'class' || b.kind === 'fixed')
                                       .map(b => [b.name, hm(b.start) + '-' + hm(b.end)])}};
            }}""")

        print("\n[outdoor plan, a late-lunch student]")
        load("La")
        r = day("2026-10-16")
        got = dict(r["blocks"])
        ck(r["cycle"] == 4, f"it is still Cycle Day 4 ({r['cycle']})")
        want = {"Bio B": "08:15-09:00", "Chem C": "09:05-09:55", "Geo G": "10:00-10:50",
                "Eng E": "11:25-12:10", "Fr F": "12:15-13:05", "Hist H": "13:10-13:55",
                "MS/SS Spirit Event, Farrell Stadium": "14:05-15:00"}
        bad = {k: (v, got.get(k)) for k, v in want.items() if got.get(k) != v}
        ck(not bad, f"every block on the email's times (wrong: {bad})")
        ck("Assembly" not in got,
           f"and the usual Friday Assembly is not drawn on top of it ({list(got)})")
        hdr = pg.evaluate("""() => [...document.querySelectorAll('#ghead .dcell')]
             .map(c => c.innerText.replace(/\\s+/g,' ')).find(t => t.includes('Oct 16')) || ''""")
        ck("Pep Rally (outdoor)" in hdr and "Day 4" in hdr,
           f"the day header says which plan it is showing ({hdr!r})")

        print("\n[the days around it are untouched]")
        r15 = dict(day("2026-10-15")["blocks"])
        ck(any(v.startswith("08:15-09:05") for v in r15.values()),
           f"Thursday the 15th keeps normal times ({r15})")
        ck(pg.evaluate("DAYMAP['2026-10-20'].cycle") == 5,
           "Tuesday the 20th is still Day 5: retiming did not shift the rotation")

        print("\n[an early-lunch student gets the other track]")
        load("Ea")
        got = dict(day("2026-10-16")["blocks"])
        ck(got.get("Eng E") == "10:55-11:40", f"Eng E on the early track ({got.get('Eng E')})")

        print("\n[the 8am switch: one word in the shared config]")
        set_shared("rain")
        load("La")
        r = day("2026-10-16"); got = dict(r["blocks"])
        want = {"Bio B": "08:15-09:05", "Chem C": "09:10-10:00",
                "Pep Rally, Rauh Theatre": "10:05-10:50", "Geo G": "10:55-11:45",
                "Eng E": "12:25-13:15", "Fr F": "13:20-14:10", "Hist H": "14:15-15:00"}
        bad = {k: (v, got.get(k)) for k, v in want.items() if got.get(k) != v}
        ck(not bad, f"every block on the rain plan's times (wrong: {bad})")
        ck(r["cycle"] == 4, "still Day 4")

        print("\n[nothing set: an ordinary Day 4]")
        set_shared(None)
        load("La")
        got = dict(day("2026-10-16")["blocks"])
        ck(got.get("Bio B") == "08:15-09:05" and got.get("Geo G") == "10:35-11:45",
           f"without @retime it is the normal timetable ({got})")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    srv.terminate()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
