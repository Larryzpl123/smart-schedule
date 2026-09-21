"""Round 3 of Larry's bug reports:
  1. admin had no way to add a school-wide event (the @public format existed,
     the form did not).
  2. immersive days rendered blank for anyone without an @immersive line, and
     the last day of 2026-27 was showing as a normal school day.
  3. homework markers were nearly invisible.
"""
import os, shutil, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_f3_")
APP = os.path.join(WORK, "app")
shutil.copytree(APP_DIR, APP, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db"))
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5170"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="f3test", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

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

# A student who only ever uploaded a PDF: no @immersive line anywhere.
NO_IMMERSIVE = """Jane Doe
27 - Senior

@term Spring 2027 = 01.19-06.30
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - Ea

@pref time_format = 24h
"""
# Homework only draws on a day when that period actually meets, so the test
# picks a real date rather than guessing: @day 1/3/5/7 all contain A and F.

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1400, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2027-01-20T17:00:00Z")

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
        pg.fill("#rawText", NO_IMMERSIVE)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # ---------- 2a. an immersive week, with NO @immersive line ----------
        # 2027-05-12..14 is marked immersive in calendar/2026-2027.txt
        pg.clock.install(time="2027-05-12T15:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(500)
        wed = pg.evaluate("""() => {
            const cols = document.querySelectorAll('#gbody .col');
            // Wed 2027-05-12 is the third column of that week
            return Array.from(cols[2].querySelectorAll('.block .bname')).map(n => n.textContent); }""")
        ck(len(wed) > 0,
           f"an immersive day is no longer blank for someone with no @immersive line ({wed})")
        ck(any("Session" in w or "Community" in w or "Advisory" in w or "Assembly" in w
               for w in wed),
           f"it shows the school-wide immersive blocks ({wed})")
        hint = pg.evaluate("""() => Array.from(document.querySelectorAll('#gbody .btime'))
            .map(n => n.textContent).filter(t => /immersive course/i.test(t)).length""")
        ck(hint > 0, "and tells them to add their immersive course in Edit")

        # ---------- 2b. the last day of 2026-27 ----------
        pg.clock.install(time="2027-06-03T15:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(500)
        head = pg.evaluate("""() => {
            const cells = document.querySelectorAll('.dcell');
            const out = [];
            cells.forEach(c => out.push({
                num: (c.querySelector('.dnum')||{}).textContent,
                cyc: (c.querySelector('.dcyc')||{}).textContent,
                name: (c.querySelector('.dname')||{}).textContent}));
            return out; }""")
        last = [h for h in head if h["num"] and "Jun 03" in h["num"]]
        ck(last and last[0]["cyc"] == "",
           f"the last day no longer eats a cycle day ({last})")
        ck(last and "Last Day" in last[0]["name"], f"and is still named ({last})")
        cols = pg.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#gbody .col').forEach(c =>
              out.push(Array.from(c.querySelectorAll('.block .bname')).map(n=>n.textContent)));
            return out; }""")
        # Jun 3 2027 is a Thursday -> 4th column
        ck("Game Design" not in cols[3] and "Linear Algebra" not in cols[3],
           f"and no longer shows a full day of classes ({cols[3]})")

        # ---------- 3. homework markers must be legible ----------
        pg.clock.install(time="2027-01-20T17:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(500)
        day = pg.evaluate("""() => {
            const cols = document.querySelectorAll('#gbody .col');
            const cells = document.querySelectorAll('.dcell');
            for (let i = 0; i < cols.length; i++) {
              const cyc = (cells[i].querySelector('.dcyc')||{}).textContent || "";
              const m = cyc.match(/Day (\\d)/);
              if (m && [1,3,5,7].includes(+m[1])) return cols[i].dataset.k;
            }
            return null; }""")
        ck(day is not None, f"found a day carrying both period A and F ({day})")
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", NO_IMMERSIVE.rstrip()
                + f"\n\n@hw A - before - Read ch.4 - {day}\n"
                + f"@hw F - during - Finish the proof - {day}\n")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(500)
        chips = pg.evaluate("""() => Array.from(document.querySelectorAll('#gbody .hw')).map(e => {
            const cs = getComputedStyle(e);
            return {text: e.textContent, bg: cs.backgroundColor, color: cs.color,
                    weight: cs.fontWeight, size: parseFloat(cs.fontSize),
                    during: e.classList.contains('during')}; })""")
        ck(len(chips) == 2, f"both homework markers render ({len(chips)})")

        def lum(rgb):
            n = [int(x) / 255 for x in rgb[rgb.index("(")+1:rgb.index(")")].split(",")[:3]]
            n = [c/12.92 if c <= 0.03928 else ((c+0.055)/1.055) ** 2.4 for c in n]
            return 0.2126*n[0] + 0.7152*n[1] + 0.0722*n[2]
        for c in chips:
            ratio = (max(lum(c["bg"]), lum(c["color"])) + 0.05) / \
                    (min(lum(c["bg"]), lum(c["color"])) + 0.05)
            kind = "during" if c["during"] else "before"
            ck(ratio >= 4.5,
               f"{kind} chip has readable contrast ({ratio:.1f}:1, needs 4.5)")
            ck("rgba(0, 0, 0, 0)" not in c["bg"],
               f"{kind} chip has a solid background, not a translucent wash")
            ck(int(c["weight"]) >= 600, f"{kind} chip text is bold ({c['weight']})")
            ck(c["size"] >= 10, f"{kind} chip is big enough to read ({c['size']}px)")

        # the "during" chip sits on a coloured class block, so check it against
        # that block rather than against the page
        ok = pg.evaluate("""() => {
            const chip = document.querySelector('#gbody .hw.during');
            if (!chip) return null;
            const r = chip.getBoundingClientRect();
            const blocks = Array.from(document.querySelectorAll('#gbody .block'));
            const under = blocks.find(b => { const q = b.getBoundingClientRect();
                return r.top >= q.top - 2 && r.bottom <= q.bottom + 2 &&
                       r.left >= q.left - 2 && r.right <= q.right + 2; });
            return under ? under.querySelector('.bname').textContent : null; }""")
        ck(ok == "Linear Algebra",
           f"the 'during' chip sits inside its own class block ({ok})")

        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

# ---------- 1. admin can add and remove a school-wide event ----------
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

post("/admin/login", {"password": "apw"})
page = op.open(BASE + "/admin").read().decode()
ck("School-wide event" in page, "the admin page has a school-wide event form")

post("/admin/public", {"date": "2026-09-12", "label": "UPA All Academy Tailgate",
                       "start": "17:00", "end": "19:00", "grades": "ALL",
                       "color": "pink"})
shared = open(os.path.join(APP, "schedule.txt"), encoding="utf-8").read()
ck("@public 2026-09-12 - UPA All Academy Tailgate - 17:00-19:00 - ALL - pink" in shared,
   "adding one writes the right line into schedule.txt")
page = op.open(BASE + "/admin").read().decode()
ck("UPA All Academy Tailgate" in page, "and it is listed back on the page")

# validation actually refuses bad input rather than writing a broken line
before = open(os.path.join(APP, "schedule.txt"), encoding="utf-8").read()
post("/admin/public", {"date": "nope", "label": "X", "start": "17:00",
                       "end": "19:00", "grades": "ALL", "color": "pink"})
post("/admin/public", {"date": "2026-09-13", "label": "Has - a hyphen",
                       "start": "17:00", "end": "19:00", "grades": "ALL", "color": "pink"})
post("/admin/public", {"date": "2026-09-14", "label": "Bad time", "start": "5pm",
                       "end": "19:00", "grades": "ALL", "color": "pink"})
post("/admin/public", {"date": "2026-09-15", "label": "Bad grades", "start": "17:00",
                       "end": "19:00", "grades": "seniors", "color": "pink"})
after = open(os.path.join(APP, "schedule.txt"), encoding="utf-8").read()
ck(before == after, "a bad date, a hyphen in the name, a bad time and bad grades are all refused")

post("/admin/public", {"remove": "@public 2026-09-12 - UPA All Academy Tailgate - 17:00-19:00 - ALL - pink"})
shared = open(os.path.join(APP, "schedule.txt"), encoding="utf-8").read()
ck("UPA All Academy Tailgate" not in shared, "and it can be removed again")

# a student must not be able to reach these
cj2 = http.cookiejar.CookieJar()
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))
try:
    r = op2.open(urllib.request.Request(BASE + "/admin/public",
        urllib.parse.urlencode({"date": "2026-09-12", "label": "Sneaky",
                                "start": "17:00", "end": "19:00"}).encode()))
    body, code = r.read().decode(), r.status
except urllib.error.HTTPError as e:
    body, code = "", e.code
shared = open(os.path.join(APP, "schedule.txt"), encoding="utf-8").read()
ck("Sneaky" not in shared, f"a non-admin cannot add a school-wide event (status {code})")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
