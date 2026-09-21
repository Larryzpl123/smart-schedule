"""Two things at the same time must both be visible.

Found in the sample: a 16:00 piano lesson was drawn on top of the 15:45 sports
block and simply erased it, and on a holiday it covered the whole no-school
wash. Blocks are absolutely positioned and all the same width, so whichever
was appended last won.

Overlapping blocks now share the column. The full-day wash is background and
keeps its width -- and, since it is background, double-clicking a holiday adds
an event there instead of hitting a wall.
"""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_ov_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5200"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ov", ADMIN_PASSWORD="apw",
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

# Wed Sep 9 2026. Three things collide in the afternoon, and one of them runs
# right through Labor Day (Mon Sep 7), which is a no-school day.
SEED = """Jane Doe
27 - Senior

@sport Cross Country - Mon,Tue,Wed,Thu,Fri - 15:45-17:15 - 08.25-11.05 - green

@project Model UN - 2026-09-07 - 2026-11-20 - blue

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red

@event Mon,Wed - Piano lesson - 16:00-17:00 - 08.25-06.30 - pink
@event Wed - Robotics build - 16:30-18:00 - 08.25-06.30 - orange
@event Tue - Long rehearsal - 15:00-18:00 - 08.25-06.30 - purple
@hw A - before - Position paper - 2026-09-09 - Model UN

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
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
        pg.wait_for_timeout(700)
        # park the pointer somewhere harmless: a hovered block widens itself,
        # and measuring one while the mouse happens to sit on it measures the
        # hover state instead of the layout
        pg.mouse.move(2, 2)
        pg.wait_for_timeout(200)

        def boxes(dayIndex):
            return pg.evaluate("""(i) => {
                const col = document.querySelectorAll('#gbody .col')[i];
                const cr = col.getBoundingClientRect();
                return [...col.querySelectorAll('.block')].map(e => {
                    const r = e.getBoundingClientRect();
                    return {name: (e.querySelector('.bname')||{}).textContent || '',
                            full: e.classList.contains('dayfull'),
                            left: Math.round(r.left - cr.left),
                            right: Math.round(cr.right - r.right),
                            width: Math.round(r.width),
                            colWidth: Math.round(cr.width),
                            z: getComputedStyle(e).zIndex};
                });
            }""", dayIndex)

        wed = {b["name"]: b for b in boxes(2)}
        for want in ["Cross Country", "Piano lesson", "Robotics build"]:
            ck(want in wed, f"{want} is drawn at all ({list(wed)})")

        if len(wed) >= 3:
            xc, pi, ro = wed["Cross Country"], wed["Piano lesson"], wed["Robotics build"]
            ck(xc["width"] < xc["colWidth"] * 0.9,
               f"the sport no longer takes the whole column ({xc['width']}/{xc['colWidth']})")
            ck(abs(xc["left"] - pi["left"]) > 10,
               f"the sport and the piano lesson are side by side "
               f"(left {xc['left']} vs {pi['left']})")
            # 15:45-17:15, 16:00-17:00 and 16:30-18:00 all overlap at 16:30
            lefts = sorted({xc["left"], pi["left"], ro["left"]})
            ck(len(lefts) == 3, f"all three get their own lane ({lefts})")
            widths = {xc["width"], pi["width"], ro["width"]}
            ck(max(widths) - min(widths) <= 2,
               f"and they share the width evenly ({sorted(widths)})")

        # ---- five at once: still legible, and readable in full on hover ----
        # Found by taking a screenshot and looking at it: at ~47px a block was
        # showing the course code and the time and no room for the name. And
        # the first attempt at the fix measured each block as it wrote the
        # widths, which reported the old width for all but the first one -- so
        # exactly one of five got the treatment. Measure after writing.
        narrow = pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[2];
            return [...col.querySelectorAll('.block')]
                .filter(e => e.getBoundingClientRect().width < 120)
                .map(e => ({name: (e.querySelector('.bname')||{}).textContent,
                            narrow: e.classList.contains('narrow'),
                            timeShown: e.querySelector('.btime')
                              ? getComputedStyle(e.querySelector('.btime')).display
                              : 'none'}));
        }""")
        ck(len(narrow) >= 3, f"the busy afternoon really is squeezed ({len(narrow)})")
        ck(narrow and all(n["narrow"] for n in narrow),
           f"EVERY squeezed block is marked narrow, not just the first "
           f"({[(n['name'], n['narrow']) for n in narrow]})")
        ck(all(n["timeShown"] == "none" for n in narrow),
           "and drops the time line, so the name has the room")

        # ---- opening one day gives the crowded afternoon real room ---------
        # Five things at 3:45 leave each block a fifth of a column and no
        # layout fixes that, so the day itself can take the whole grid.
        before = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .col')[2]
                        .querySelectorAll('.block')]
                .find(e => (e.textContent||'').includes('Piano'));
            return Math.round(b.getBoundingClientRect().width);
        }""")
        pg.evaluate("() => document.querySelectorAll('#ghead .dcell')[2].click()")
        pg.wait_for_timeout(500)
        cols = pg.eval_on_selector_all("#gbody .col", "c => c.length")
        ck(cols == 1, f"clicking the day heading shows that day alone ({cols} columns)")
        after = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('.block')]
                .find(e => (e.textContent||'').includes('Piano'));
            return Math.round(b.getBoundingClientRect().width);
        }""")
        ck(after > before * 3,
           f"and the squeezed block gets far more room ({before}px -> {after}px)")
        ck(pg.evaluate("""() => {
            const b = [...document.querySelectorAll('.block')]
                .find(e => (e.textContent||'').includes('Piano'));
            return !b.classList.contains('narrow')
                && getComputedStyle(b.querySelector('.btime')).display !== 'none';
        }"""), "so its time is readable again without hovering")
        ck("Sep 09" in pg.text_content("#ghead"),
           f"the heading still says which day it is ({pg.text_content('#ghead')[:60]!r})")

        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 5,
           "Escape goes back to the whole week")
        pg.evaluate("() => document.querySelectorAll('#ghead .dcell')[2].click()")
        pg.wait_for_timeout(400)
        pg.evaluate("() => document.querySelectorAll('#ghead .dcell')[0].click()")
        pg.wait_for_timeout(400)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 5,
           "and so does clicking the heading again")

        # ---- hovering must never SHRINK a block ---------------------------
        # A block taller than the hover min-height was being shrunk by hover,
        # which moved the pointer off it, which un-hovered it, which grew it
        # back: a flicker loop at the bottom edge.
        tall = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block:not(.dayfull)')]
                .sort((x, y) => y.getBoundingClientRect().height
                              - x.getBoundingClientRect().height)[0];
            const r = b.getBoundingClientRect();
            return {name: (b.querySelector('.bname')||{}).textContent,
                    h: Math.round(r.height),
                    x: Math.round(r.left + r.width / 2),
                    y: Math.round(r.top + r.height / 2)};
        }""")
        ck(tall and tall["h"] > 96,
           f"there is a block taller than the hover minimum ({tall})")
        pg.mouse.move(tall["x"], tall["y"])
        pg.wait_for_timeout(300)
        after = pg.evaluate("""(n) => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.querySelector('.bname')||{}).textContent === n);
            return {h: Math.round(b.getBoundingClientRect().height),
                    hovered: b.matches(':hover')};
        }""", tall["name"])
        ck(after["hovered"], "the pointer really is on it")
        ck(after["h"] >= tall["h"],
           f"hovering it does not make it smaller ({tall['h']} -> {after['h']})")
        pg.mouse.move(2, 2)
        pg.wait_for_timeout(200)

        # ---- the projects band lines up with the grid under it ------------
        # In the zoomed one-day view the band used a 48px label column while
        # the grid used 64px, so it sat 16px to the left and ran over its own
        # "projects" label.
        def bandOffsets():
            return pg.evaluate("""() => {
                const ad = document.getElementById('allday');
                if (!ad || ad.style.display === 'none') return null;
                const head = document.getElementById('ghead');
                const first = ad.children[1], col = head.children[1];
                if (!first || !col) return null;
                return {band: Math.round(first.getBoundingClientRect().left),
                        col: Math.round(col.getBoundingClientRect().left),
                        label: Math.round(ad.children[0].getBoundingClientRect().width)};
            }""")

        # the seed's project has a milestone this week, so the band is up
        wk = bandOffsets()
        ck(wk and abs(wk["band"] - wk["col"]) <= 1,
           f"in the week view the band starts where the first day does ({wk})")
        pg.evaluate("() => document.querySelectorAll('#ghead .dcell')[2].click()")
        pg.wait_for_timeout(600)
        zm = bandOffsets()
        ck(zm and abs(zm["band"] - zm["col"]) <= 1,
           f"and in the one-day view too ({zm})")
        ck(zm and zm["label"] == wk["label"],
           f"the label column keeps its width ({zm and zm['label']} vs {wk['label']})")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)

        # ---- 5 day / 7 day ------------------------------------------------
        # School never meets at the weekend, but sports, lessons and a
        # student's own events do, and the weekend used to be unreachable.
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 5,
           "weekdays only by default")
        pg.evaluate("() => document.querySelectorAll('#daysSeg button')[1].click()")
        pg.wait_for_timeout(500)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 7,
           "7 day shows Saturday and Sunday")
        head = pg.text_content("#ghead")
        ck("Sat" in head and "Sun" in head, f"...labelled as such ({head[:40]!r})")
        ck("NA" not in head and "Weekend" in head,
           "a weekend reads as a weekend, not as missing calendar data")
        ck("Sep 13" in pg.text_content("#wkLabel"),
           f"the week label runs to Sunday ({pg.text_content('#wkLabel')!r})")

        # it is remembered, like the theme
        pg.reload()
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 7,
           "and the choice survives a reload")
        pg.evaluate("() => document.querySelectorAll('#daysSeg button')[0].click()")
        pg.wait_for_timeout(400)
        ck(pg.eval_on_selector_all("#gbody .col", "c => c.length") == 5,
           "switching back works too")

        # a class with nothing beside it still uses the whole column
        calc = next((b for b in boxes(2) if "Calculus" in b["name"]), None)
        ck(calc and calc["width"] > calc["colWidth"] * 0.9,
           f"a class with no clash still spans the column ({calc})")

        # ---- Labor Day: the wash stays whole, the event sits on it ---------
        mon = boxes(0)
        full = next((b for b in mon if b["full"]), None)
        ck(full, f"Monday is a no-school day with a full-day block ({[b['name'] for b in mon]})")
        ck(full and full["width"] > full["colWidth"] * 0.9,
           f"the no-school wash keeps the full width ({full})")
        ck(full and full["z"] in ("0", "auto"),
           f"and sits behind everything else (z-index {full['z']})")
        piano_mon = next((b for b in mon if "Piano" in b["name"]), None)
        ck(piano_mon, "the piano lesson still happens on a holiday")
        ck(piano_mon and piano_mon["width"] < piano_mon["colWidth"] * 0.99,
           f"...drawn on the day, not instead of it ({piano_mon})")

        # ---- double-clicking a holiday adds an event ----------------------
        pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[0];
            const full = col.querySelector('.block.dayfull');
            const r = full.getBoundingClientRect();
            full.dispatchEvent(new MouseEvent('dblclick', {bubbles: true,
                clientX: r.left + r.width/2, clientY: r.top + 200}));
        }""")
        pg.wait_for_timeout(400)
        ck(pg.is_visible("#quickDlg") or pg.is_visible("dialog[open]"),
           "double-clicking a holiday opens the add-event box")

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
