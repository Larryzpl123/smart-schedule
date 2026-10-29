"""Print this week: My week / School only, as a Table or the Grid.

What is checked is what ends up on paper -- a snapshot of the page taken at
the moment window.print() is called, and real PDFs from Chromium's print
pipeline (one landscape Letter page) -- and that the screen is put back
exactly as it was afterwards. Both an account and local mode."""
import os, re, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_pr_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5223"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="prtest", ADMIN_PASSWORD="apw",
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
# Week of Mon Oct 26 2026: Wed Oct 28 is a no-classes day in the calendar.
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
B - Art Core - AR100 - 2 - Ms. Delgado - BC Studio - cyan - Ea
C - French 3 - FR300 - 1 - Mr. Okafor - R 310 - purple - Ea
D - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
E - Playwriting - EN424A - 1 - Ms. Ferraro - R 110 - blue - La
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - La
G - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
H - Revolution in Ancient Rome - HI445 - 1 - Mr. Lindqvist - R 205 - yellow - Ea

@project History paper - 2026-10-20 - 2026-11-06 - blue
  > thesis still open
@event 2026-10-29 - Dentist - 16:00-17:00 - pink
  > bring the insurance card
@event 2026-10-29 - Late rehearsal - 20:00-21:30 - gray
@hw C - before - Read ch.4 - 2026-10-30
  > p.112-140, annotate

@pref time_format = 12h
"""

# Stand-in for window.print(): records what the page IS at that moment, which
# is exactly what the browser would put on paper.
HOOK = """() => { window.__snaps = []; window.print = () => {
  const m = document.querySelector('main'), t = document.getElementById('printTable');
  window.__snaps.push({
    cls: document.documentElement.className,
    theme: document.documentElement.getAttribute('data-theme'),
    title: document.getElementById('printTitle').textContent,
    table: t.innerText, tableShown: getComputedStyle(t).display !== 'none',
    gridShown: getComputedStyle(document.querySelector('.grid-wrap')).display !== 'none',
    dcells: document.querySelectorAll('#ghead .dcell').length,
    cols: document.querySelectorAll('#gbody .col').length,
    toolbar: getComputedStyle(document.querySelector('.toolbar')).display,
    imgs: t.querySelectorAll('img').length,
    zoom: parseFloat(m.style.zoom || '1'), h: m.getBoundingClientRect().height,
    blocks: Array.from(document.querySelectorAll('#gbody .block .bname')).map(b => b.textContent)
  }); }; }"""

def pages(pdf_bytes):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf_bytes))

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-10-29T13:30:00Z")

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
        with pg.expect_navigation():
            pg.click('button[type=submit]')

        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(400)
        pg.evaluate(HOOK)

        def run(what, layout, notes=True):
            pg.click("#printBtn")
            pg.wait_for_timeout(150)
            pg.check(f"input[name=prWhat][value={what}]")
            pg.check(f"input[name=prLayout][value={layout}]")
            if pg.is_visible("#prNotes"):
                pg.set_checked("#prNotes", notes)
            pg.click("#printGo")
            pg.wait_for_timeout(900)
            return pg.evaluate("window.__snaps[window.__snaps.length-1]")

        # ---------------- the dialog ----------------
        pg.click("#printBtn")
        pg.wait_for_timeout(150)
        wk = pg.text_content("#printWeek")
        ck("Oct 26" in wk and "Oct 30" in wk, f"the dialog says which week it prints ({wk!r})")
        pg.check("input[name=prWhat][value=school]")
        ck(not pg.is_visible("#prNotes"), "no notes option for School only (nothing personal to note)")
        pg.check("input[name=prWhat][value=mine]")
        pg.check("input[name=prLayout][value=grid]")
        ck(not pg.is_visible("#prNotes"), "no notes option for the grid")
        pg.check("input[name=prLayout][value=table]")
        ck(pg.is_visible("#prNotes"), "notes option for My week as a table")
        pg.click("#printCancel")

        # ---------------- My week, table ----------------
        s = run("mine", "table")
        t = s["table"]
        ck(s["tableShown"] and not s["gridShown"] and s["toolbar"] == "none",
           f"on paper: the table, no grid, no toolbar ({s['tableShown']}, {s['gridShown']}, {s['toolbar']})")
        ck(s["theme"] == "light", f"printed in the light theme ({s['theme']})")
        ck("Jane Doe" in s["title"] and "Oct 26" in s["title"], f"titled with the name and the week ({s['title']!r})")
        for d in ["Mon Oct 26", "Tue Oct 27", "Wed Oct 28", "Thu Oct 29", "Fri Oct 30"]:
            if d not in t:
                ck(False, f"day header {d} missing")
        ck("Day 1" in t, "cycle days are on it")
        ck("No Classes" in t, "the no-classes day says so")
        ck("Game Design" in t and "Linear Algebra" in t, "classes are on it")
        ck("GH 043" in t, "with rooms")
        ck("Dentist" in t and "bring the insurance card" in t, "events and their notes")
        ck("Due: Read ch.4" in t and "p.112-140" in t, "homework, with its note")
        ck("Ongoing" in t and "History paper" in t and "due Nov 6" in t,
           "a project running through the whole week is mentioned once")
        ck(t.count("History paper") == 1, f"...only once ({t.count('History paper')})")
        ck("Late rehearsal" in t, "an evening event is on it")
        s2 = run("mine", "table", notes=False)
        ck("Dentist" in s2["table"] and "insurance" not in s2["table"] and "p.112-140" not in s2["table"],
           "unticking Include notes leaves the notes off")

        # ---------------- School only, table ----------------
        s = run("school", "table")
        t = s["table"]
        ck("Senior School" in s["title"] and "Jane" not in s["title"], f"school title, nobody's name ({s['title']!r})")
        leaks = [x for x in ["Game Design", "Linear Algebra", "GH 043", "Rivera", "Dentist", "Read ch.4",
                             "History paper", "Late rehearsal", "insurance"] if x in t]
        ck(not leaks, f"School only has nothing personal on it ({leaks})")
        ck("Periods:" in t and "Day 1" in t and "No Classes" in t,
           "it still has the cycle days, the period order and the no-classes day")
        ck(re.search(r"Assembly|Community|Club", t) is not None, f"and the school's own blocks ({t[:200]!r})")

        # ---------------- My week, grid -- from a phone-width window ----------------
        pg.set_viewport_size({"width": 400, "height": 900})
        pg.wait_for_timeout(500)
        s = run("mine", "grid")
        ck(s["gridShown"] and not s["tableShown"], "the grid this time")
        ck(s["dcells"] == 5 and s["cols"] == 5,
           f"a phone still prints the whole week, not its one-day view ({s['dcells']} days)")
        ck(s["h"] * s["zoom"] <= 740, f"scaled to one page ({s['h']:.0f}px x {s['zoom']})")
        ck(s["zoom"] < 1, f"...which with a 9:30pm event means scaled down ({s['zoom']})")
        ck("Game Design" in s["blocks"] and "Dentist" in s["blocks"], "with the classes and events")
        s = run("school", "grid")
        ck(s["dcells"] == 5 and "Game Design" not in s["blocks"] and "Dentist" not in s["blocks"],
           f"School only grid: no personal blocks ({s['blocks'][:8]})")
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.wait_for_timeout(400)

        # ---------------- put back exactly as it was ----------------
        pg.evaluate("VIEW='list'; render()")
        pg.wait_for_timeout(500)
        view_before = pg.evaluate("localStorage.getItem('ssa-view')")
        s = run("school", "grid")
        st = pg.evaluate("""() => ({cls: document.documentElement.className,
            theme: document.documentElement.getAttribute('data-theme'), view: VIEW,
            ev: (STU.events || []).length, list: document.querySelectorAll('.lrow').length,
            zoom: document.querySelector('main').style.zoom, lsview: localStorage.getItem('ssa-view'),
            title: document.getElementById('printTitle').textContent,
            prefs: localStorage.getItem('ssa-print')})""")
        ck("printing" not in st["cls"] and st["theme"] == "dark" and st["zoom"] == "",
           f"afterwards: no print classes, dark theme back, no zoom ({st})")
        ck(st["view"] == "list" and st["list"] > 0 and st["lsview"] == view_before,
           f"...the list view it was on, and the saved view untouched ({st['view']}, {st['lsview']})")
        ck(st["ev"] == 2 and not st["title"], "...and the student's own schedule, not the school-only copy")
        ck('"school"' in (st["prefs"] or "") and '"grid"' in (st["prefs"] or ""),
           f"the last choice is remembered for next time ({st['prefs']})")

        # ---------------- Cmd+P: the browser's own print ----------------
        pg.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        cp = pg.evaluate("document.documentElement.className")
        ck("printing" in cp and "print-grid" in cp, f"Cmd+P gets the same printout with the last choices ({cp})")
        pg.evaluate("window.dispatchEvent(new Event('afterprint'))")
        ck("printing" not in pg.evaluate("document.documentElement.className"), "...and is put back after")

        # ---------------- real PDFs: one landscape page each ----------------
        pg.evaluate("VIEW='week'; render()")
        for what in ("mine", "school"):
            for lay in ("table", "grid"):
                pg.evaluate("o => printSetup(o)", {"what": what, "layout": lay, "notes": True})
                pdf = pg.pdf(prefer_css_page_size=True, print_background=True)
                pg.evaluate("printRestore()")
                n = pages(pdf)
                ck(n == 1, f"{what} / {lay}: one page of PDF ({n})")
        # Letter landscape: 792 x 612 pt
        pg.evaluate("o => printSetup(o)", {"what": "mine", "layout": "table", "notes": True})
        pdf = pg.pdf(prefer_css_page_size=True)
        pg.evaluate("printRestore()")
        ck(b"/MediaBox [0 0 792 612]" in pdf, "Letter, landscape")

        # ---------------- a note is text on paper too ----------------
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SEED.replace("bring the insurance card", '<img src=x onerror="window.__pwn=1">'))
        with pg.expect_navigation():
            pg.click('button[type=submit]')
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        pg.evaluate(HOOK)
        s = run("mine", "table")
        ck(s["imgs"] == 0 and not pg.evaluate("window.__pwn||0") and "<img" in s["table"],
           "a note with HTML in it prints as text")

        # ---------------- local mode: no account needed ----------------
        lp = b.new_page(viewport={"width": 1280, "height": 900})
        lp.on("pageerror", lambda e: errs.append(str(e)))
        lp.clock.install(time="2026-10-29T13:30:00Z")
        lp.goto(BASE + "/local/app")
        lp.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        lp.reload()
        lp.wait_for_selector("#gbody .col", timeout=8000)
        lp.wait_for_timeout(400)
        lp.evaluate(HOOK)
        lp.click("#printBtn")
        lp.wait_for_timeout(150)
        lp.check("input[name=prWhat][value=mine]")
        lp.check("input[name=prLayout][value=table]")
        lp.click("#printGo")
        lp.wait_for_timeout(900)
        ls = lp.evaluate("window.__snaps[window.__snaps.length-1]")
        ck("Game Design" in ls["table"] and "Dentist" in ls["table"] and "Jane Doe" in ls["title"],
           "local mode prints the same way, no account involved")
        ck("printing" not in lp.evaluate("document.documentElement.className"), "...and restores")

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
