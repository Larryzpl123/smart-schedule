"""The day was 08:00-18:00 no matter what you had in it.

Both time pickers offer 07:00-19:00, so a 7pm rehearsal could be entered,
saved, counted against the cap -- and never drawn. That is not a missing
preference, it is the app accepting input it will not show.

No setting: the axis grows to fit what this student actually has, and shrinks
back when they remove it. A floor and a ceiling stop one 2am entry from
squashing the school day into nothing.
"""
import os, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, http.cookiejar

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_ax_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5208"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ax", ADMIN_PASSWORD="apw",
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
BASE_SCHED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

@pref time_format = 12h
"""

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))

def save(text):
    op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
        {"schedule_text": text}).encode()))

save(BASE_SCHED)

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

        def axis():
            pg.goto(BASE + "/app")
            pg.wait_for_selector("#gbody .col")
            pg.wait_for_timeout(800)
            return pg.evaluate("""() => {
                const ticks = [...document.querySelectorAll('#gbody .axis .tick')]
                    .map(t => t.textContent.trim());
                return {start: AX_START, end: AX_END, ticks: ticks,
                        firstTick: ticks[0], lastTick: ticks[ticks.length - 1],
                        bodyH: Math.round(document.getElementById('gbody')
                                 .getBoundingClientRect().height)};
            }""")

        def visible(name):
            return pg.evaluate("""(n) => {
                const b = [...document.querySelectorAll('#gbody .block')]
                    .find(e => (e.querySelector('.bname')||{}).textContent === n);
                if (!b) return null;
                const r = b.getBoundingClientRect();
                const g = document.getElementById('gbody').getBoundingClientRect();
                return {top: Math.round(r.top - g.top),
                        bottom: Math.round(r.bottom - g.top),
                        gridH: Math.round(g.height)};
            }""", name)

        a0 = axis()
        ck(a0["start"] == 480 and a0["end"] == 1080,
           f"a schedule inside school hours keeps 08:00-18:00 ({a0['start']}, {a0['end']})")

        # ---- a 7pm rehearsal ----------------------------------------------
        save(BASE_SCHED + "@event Wed - Orchestra - 19:00-20:15 - purple\n")
        a1 = axis()
        ck(a1["end"] >= 20 * 60 + 15,
           f"an evening event pushes the end of the day out ({a1['end']})")
        ck(a1["start"] == 480, f"and leaves the morning alone ({a1['start']})")
        v = visible("Orchestra")
        ck(v and v["top"] >= 0 and v["bottom"] <= v["gridH"] + 2,
           f"the block is inside the grid, not off the bottom ({v})")
        ck("8:00 PM" in a1["ticks"] or "20:00" in a1["ticks"],
           f"and the hour labels go that far ({a1['ticks'][-3:]})")

        # ---- an early one --------------------------------------------------
        save(BASE_SCHED + "@event Wed - Orchestra - 19:00-20:15 - purple\n"
                          "@event Tue - Swim practice - 06:30-07:45 - cyan\n")
        a2 = axis()
        ck(a2["start"] <= 6 * 60 + 30,
           f"an early one pulls the start back ({a2['start']})")
        v = visible("Swim practice")
        ck(v and v["top"] >= -1,
           f"and that block is not clipped off the top ({v})")
        ck(a2["bodyH"] > a0["bodyH"],
           f"the grid really is taller than the plain one ({a2['bodyH']} vs {a0['bodyH']})")

        # ---- and back again ------------------------------------------------
        save(BASE_SCHED)
        a3 = axis()
        ck(a3["start"] == 480 and a3["end"] == 1080,
           f"removing them puts the day back to 08:00-18:00 ({a3['start']}, {a3['end']})")

        # ---- one absurd entry must not squash the school day ---------------
        save(BASE_SCHED + "@event Wed - Night shift - 02:00-03:00 - gray\n")
        a4 = axis()
        ck(a4["start"] >= 6 * 60,
           f"the axis stops at the floor rather than following a 2am entry "
           f"({a4['start']})")

        # ---- the now-line still lands in the right place -------------------
        save(BASE_SCHED + "@event Wed - Orchestra - 19:00-20:15 - purple\n")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(800)
        bar = pg.evaluate("""() => {
            const bar = document.querySelector('#gbody .nowbar');
            if (!bar) return null;
            const g = document.getElementById('gbody').getBoundingClientRect();
            const r = bar.getBoundingClientRect();
            // 11:00 in Pittsburgh, on an axis that now runs 08:00-21:00
            return {frac: (r.top - g.top) / g.height,
                    want: (11 * 60 - AX_START) / (AX_END - AX_START)};
        }""")
        ck(bar and abs(bar["frac"] - bar["want"]) < 0.01,
           f"the now-line is placed against the new axis, not the old one ({bar})")

        ck(not errs, f"no page errors: {errs[:3]}")

        # ---- the duration box fits three digits ----------------------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)
        pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[0];
            const r = col.getBoundingClientRect();
            col.dispatchEvent(new MouseEvent('dblclick', {bubbles: true,
                clientX: r.left + 20, clientY: r.top + 200}));
        }""")
        pg.wait_for_timeout(400)
        fits = pg.evaluate("""() => {
            const el = document.getElementById('qDur');
            el.value = '180';
            const dlg = document.getElementById('quickDlg');
            const row = document.getElementById('qTimeRow');
            const cs = getComputedStyle(dlg);
            const d = dlg.getBoundingClientRect();
            // the dialog's inner edge, inside its own padding and border
            const innerRight = d.right - parseFloat(cs.paddingRight)
                                       - parseFloat(cs.borderRightWidth);
            return {scroll: el.scrollWidth, client: el.clientWidth,
                    boxRight: Math.round(el.getBoundingClientRect().right),
                    rowRight: Math.round(row.getBoundingClientRect().right),
                    rowScroll: row.scrollWidth, rowClient: row.clientWidth,
                    innerRight: Math.round(innerRight)};
        }""")
        ck(fits["scroll"] <= fits["client"] + 1,
           f"a three-digit duration is not cut off ({fits})")
        # ...and the box itself, spinner arrows and all, is inside the dialog.
        # Putting the all-day checkbox in this row squeezed the duration box
        # until its up/down arrows were sliced off at the edge -- the number
        # fitted, the control did not.
        ck(fits["boxRight"] <= fits["innerRight"] + 1
           and fits["rowRight"] <= fits["innerRight"] + 1,
           f"and the whole box, spinner arrows included, is inside the "
           f"dialog ({fits})")
        ck(fits["rowScroll"] <= fits["rowClient"] + 1,
           f"the time row does not overflow itself ({fits})")

        # ---- all day takes the time row away rather than leaving it dead ---
        allday = pg.evaluate("""() => {
            const cb = document.getElementById('qAllDay');
            cb.checked = true; cb.dispatchEvent(new Event('change'));
            const hidden = document.getElementById('qTimeRow').style.display;
            cb.checked = false; cb.dispatchEvent(new Event('change'));
            return {hidden: hidden,
                    back: document.getElementById('qTimeRow').style.display};
        }""")
        ck(allday["hidden"] == "none" and allday["back"] != "none",
           f"ticking all day hides the time row and unticking brings it back "
           f"({allday})")

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
