"""Three things a classmate asked for, all of them about reading the thing.

1. A custom event could only last one of the round numbers in a dropdown.
   80 minutes was not among them, and neither was anything else real.
2. The start/end time sat on the LAST line of a block, inside the part that
   only fades in on hover -- so the one number everybody wants off a timetable
   needed a mouse, and on a phone was unreachable. The period letter had a
   whole line of its own above it.
3. The clock in the corner was hardcoded to 24h and ignored the 12h/24h switch,
   so the page contradicted itself.
"""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_rd_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5198"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="rd", ADMIN_PASSWORD="apw",
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

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
B - Organic Chem. - CH410 - 2 - Mr. Halvorsen - MC 205 - green
C - World Lit. - EN3011 - 1 - Ms. Whitfield - R 310 - blue
D - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow
E - French 2 - FR200 - 1 - Mr. Okafor - C 8 - purple
F - Robotics - CS301 - 1 - Ms. Patel - GH 015 - orange
G - Ceramics 1 - AR210 - 2 - Ms. Delgado - MC 240 - cyan
H - Concert Choir - MU220 - 1 - M. Roland - MC 118 - teal

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T19:20:00Z")   # 15:20 in Pittsburgh

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

        # ---- 1. an 80-minute event ----------------------------------------
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#addEvent")
        pg.click("#addEvent")
        row = pg.query_selector_all("#eventRows .evrow")[-1]
        dur = row.query_selector(".ev_dur")
        ck(dur.evaluate("e => e.tagName") == "INPUT",
           "the duration is typed, not picked from a fixed list")
        row.query_selector(".ev_name").fill("Robotics build session")
        row.query_selector(".ev_mode").select_option("weekly")
        for cb in row.query_selector_all(".ev_dow"):
            if cb.get_attribute("value") == "Wed":
                cb.check()
        row.query_selector(".ev_start").select_option("15:30")
        dur.fill("80")
        pg.click("#modeText")
        txt = pg.input_value("#rawText")
        ck("Wed - Robotics build session - 15:30-16:50" in txt,
           f"80 minutes comes out as 15:30-16:50 ({txt.splitlines()[-3:]})")

        # nonsense is clamped rather than written out as a broken line
        pg.click("#modeForm")
        pg.query_selector_all("#eventRows .evrow")[-1].query_selector(".ev_dur").fill("9999")
        pg.click("#modeText")
        ck("15:30-01:30" not in pg.input_value("#rawText"),
           "an absurd duration does not wrap past midnight")

        # ---- 2. the time is visible without hovering -----------------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(600)

        def first_class_block():
            return pg.evaluate("""() => {
                const b = [...document.querySelectorAll('#gbody .block')]
                    .find(e => (e.textContent||'').includes('Calculus'));
                if (!b) return null;
                const t = b.querySelector('.btime');
                const inExtra = !!(t && t.closest('.bextra'));
                return {text: b.textContent, time: t ? t.textContent : null,
                        inExtra, teachInExtra:
                          !!(b.querySelector('.bteach') &&
                             b.querySelector('.bteach').closest('.bextra'))};
            }""")

        info = first_class_block()
        ck(info and info["time"], "a class block has a time line")
        ck(info and not info["inExtra"],
           "and it is NOT in the hover-only part any more")
        ck(info and "A" in (info["time"] or ""),
           f"the period letter rides along on that line ({info['time']!r})")
        # The time comes FIRST and the letter after it, and the letter is not
        # bold: the time is what anybody actually reads off a timetable.
        t = info["time"] or ""
        ck(t.strip()[0].isdigit(), f"the time leads the line ({t!r})")
        ck(pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('Calculus'));
            const p = b.querySelector('.btime .bper');
            return p && parseInt(getComputedStyle(p).fontWeight,10) <= 500;
        }"""), "and the period letter is not bold")

        # A short last period cannot fit three lines. What it drops must be the
        # course code, never the time -- the time used to be the casualty.
        short = pg.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#gbody .col').forEach(col => {
                const bs = [...col.querySelectorAll('.block:not(.dayfull)')];
                const last = bs[bs.length - 1];
                if (!last) return;
                const t = last.querySelector('.btime'), n = last.querySelector('.bnum');
                out.push({name: (last.querySelector('.bname')||{}).textContent,
                          timeHidden: !t || t.classList.contains('hid'),
                          numHidden: !!(n && n.classList.contains('hid'))});
            });
            return out;
        }""")
        ck(short and not any(x["timeHidden"] for x in short),
           f"no block anywhere is left without its time ({short})")
        ck(info and info["teachInExtra"],
           "the teacher is what moved into the hover-only part instead")
        ck(info and "AM" in info["time"] or "PM" in info["time"],
           f"the time reads in the chosen format ({info['time']!r})")

        # no hover: the time must already be laid out and not clipped away
        vis = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('Calculus'));
            const t = b && b.querySelector('.btime');
            if (!t) return null;
            const r = t.getBoundingClientRect();
            return {h: r.height, hidden: getComputedStyle(t).display === 'none'};
        }""")
        ck(vis and not vis["hidden"] and vis["h"] > 4,
           f"and it is actually drawn on screen ({vis})")

        # ---- 2b. the time line never wraps ---------------------------------
        # It used to be ordinary wrapping text, so a narrow column broke it
        # wherever: "3:45 PM - 4:45 / PM Athletics", "School / event". The
        # ugly break was not the damage -- the wrapped tag became a SECOND
        # visual line, which pushed the line below it out of the block. So a
        # category label nobody needed cost you the course code.
        # Seven days across is the narrowest the desktop grid ever gets.
        pg.eval_on_selector("#daysSeg button[data-d='7']", "b => b.click()")
        pg.wait_for_timeout(700)
        pg.mouse.move(2, 2)          # a hovered block is allowed to wrap
        pg.wait_for_timeout(200)
        wrapped = pg.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(b => {
                const t = b.querySelector('.btime');
                if (!t || getComputedStyle(t).display === 'none') return;
                // a line box per rendered line: more than one means it wrapped
                if (t.getClientRects().length > 1)
                    out.push(((b.querySelector('.bname')||{}).textContent || '?')
                             + ' -> ' + t.textContent);
            });
            return out;
        }""")
        ck(not wrapped,
           f"no block's time line wraps, even seven days across ({wrapped[:3]})")

        # and the tag that does not fit is HIDDEN, not folded onto a new line
        tags = pg.evaluate("""() => {
            const all = [...document.querySelectorAll('#gbody .btime .bper')];
            return {total: all.length,
                    hidden: all.filter(p => p.classList.contains('hidw')).length,
                    anyBroken: all.some(p => p.getClientRects().length > 1)};
        }""")
        ck(tags["total"] > 0 and not tags["anyBroken"],
           f"and no period letter or category tag is split across lines ({tags})")

        # a full-height class block still gets all three of its visible lines
        three = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block:not(.dayfull)')]
                .filter(e => e.querySelector('.bnum') &&
                             e.getBoundingClientRect().height > 48);
            return b.map(e => ({name: (e.querySelector('.bname')||{}).textContent,
                                numHidden: e.querySelector('.bnum')
                                            .classList.contains('hid')}))
                    .filter(x => x.numHidden);
        }""")
        ck(not three,
           f"and a block tall enough for three lines still shows all three "
           f"({three[:3]})")

        # the tooltip carries everything, so a trimmed line is never the only copy
        tip = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('Calculus'));
            return b ? b.title : null;
        }""")
        ck(tip and "Calculus" in tip and ("AM" in tip or "PM" in tip),
           f"the block's tooltip has the whole of it ({tip!r})")

        pg.eval_on_selector("#daysSeg button[data-d='5']", "b => b.click()")
        pg.wait_for_timeout(500)

        # ---- 3. the corner clock follows 12h/24h ---------------------------
        pg.eval_on_selector("#fmtSeg button[data-f='24h']", "b => b.click()")
        pg.wait_for_timeout(300)
        t24 = pg.text_content("#cTime")
        ck(t24.strip().startswith("15:"), f"24h mode shows 15:xx ({t24!r})")
        ck("PM" not in t24, "with no AM/PM on it")

        pg.eval_on_selector("#fmtSeg button[data-f='12h']", "b => b.click()")
        pg.wait_for_timeout(300)
        t12 = pg.text_content("#cTime")
        ck(t12.strip().startswith("3:") and "PM" in t12,
           f"12h mode shows 3:xx PM ({t12!r})")
        ck(t12 != t24, "the switch actually changes the clock")

        # it must also stay right on the next tick, not revert
        pg.wait_for_timeout(1200)
        ck("PM" in pg.text_content("#cTime"),
           f"and stays that way as it ticks ({pg.text_content('#cTime')!r})")

        # the saved @pref should set it on load, without touching the buttons
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(1200)
        ck("PM" in pg.text_content("#cTime") or "AM" in pg.text_content("#cTime"),
           f"@pref time_format = 12h reaches the clock on load "
           f"({pg.text_content('#cTime')!r})")

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
