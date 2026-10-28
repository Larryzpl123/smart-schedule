"""No block may show a half-cut line. Anything that doesn't fit wholly is
hidden, and hovering brings it all back. Checked on the Convocation day,
where 25-minute blocks are the tightest case."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_cl_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5138"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="cltest", ADMIN_PASSWORD="apw",
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

# Larry's real shape: a senior with all eight periods, so Convocation draws
# eight 25-minute blocks -- the case in the screenshot.
SCHEDULE = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange
B - Art Core - AR100 - 2 - Ms. Delgado - BC Studio - cyan
C - French 3 - FR300 - 1 - Mr. Okafor - R 310 - purple
D - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green
E - Playwriting - EN424A - 1 - Ms. Ferraro - R 110 - blue
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red
G - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green
H - Revolution in Ancient Rome - HI445 - 1 - Mr. Lindqvist - R 205 - yellow

@pref time_format = 12h
"""

MEASURE = """() => {
  const out = [];
  document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(blk => {
    const cs = getComputedStyle(blk);
    const padT = parseFloat(cs.paddingTop), padB = parseFloat(cs.paddingBottom);
    const avail = blk.clientHeight - padT - padB;
    const kids = Array.from(blk.children).filter(e => !e.classList.contains('bextra'));
    if (!kids.length) return;
    const base = kids[0].offsetTop;
    // A clamped name whose overflow is still visible paints the lines it
    // "cut" underneath its own box. Its offsetHeight looks fine; only the
    // overflow tells you the hidden lines are still on screen.
    const nm = kids[0];
    if (nm.scrollHeight > nm.clientHeight + 1 && getComputedStyle(nm).overflowY === 'visible')
      out.push({block: nm.textContent.slice(0, 24), line: 'bname (clamped, overflow visible)',
                overflowPx: nm.scrollHeight - nm.clientHeight});
    kids.forEach(el => {
      if (el.classList.contains('hid')) return;          // hidden = fine
      const over = (el.offsetTop + el.offsetHeight) - (base + avail);
      // 1.5px matches the renderer's sub-pixel grace: that much runs into the
      // padding and is invisible. A genuinely half-cut line overflows by ~6px+.
      if (over > 1.5) out.push({
        block: blk.querySelector('.bname').textContent.slice(0, 24),
        line: el.className, overflowPx: Math.round(over * 10) / 10 });
    });
  });
  return out;
}"""

# A block whose TIMES overlap nothing else in its column must be drawn full
# width. Patch 43 judged overlap by the stretched (22px) height, so two
# back-to-back slots -- 10:05-10:15 then 10:15-10:50 on Oct 30, 08:15-08:25
# then 08:25-08:35 on Convocation day -- were squeezed side by side as if they
# clashed. Returns the offenders.
SOLO = """() => {
  const out = [];
  document.querySelectorAll('#gbody .col').forEach(col => {
    const cw = col.getBoundingClientRect().width;
    const bs = Array.from(col.querySelectorAll('.block:not(.dayfull)'))
      .map(el => ({el, s: +el.dataset.start, e: +el.dataset.end}))
      .filter(b => Number.isFinite(b.s) && Number.isFinite(b.e));
    bs.forEach(b => {
      const clash = bs.some(c => c !== b && c.s < b.e && b.s < c.e);
      const share = b.el.getBoundingClientRect().width / cw;
      if (!clash && share < 0.8) out.push({name: b.el.querySelector('.bname').textContent.slice(0, 24),
                                           share: Math.round(share * 100)});
    });
  });
  return out; }"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-08-25T16:00:00Z")

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

        # ---- Convocation day: eight 25-minute blocks ----
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(250)

        bad = pg.evaluate(MEASURE)
        ck(not bad, f"nothing is left half-cut on Convocation day ({bad[:3]})")
        solo = pg.evaluate(SOLO)
        ck(not solo, f"Convocation day: blocks whose times clash with nothing are full width ({solo[:3]})")
        tens = pg.evaluate("""() => Array.from(document.querySelectorAll('#gbody .block:not(.dayfull)'))
          .filter(b => +b.dataset.end - +b.dataset.start === 10).length""")
        # (this test user is a senior, so only the 08:15-08:25 slot shows; the
        # back-to-back pair is grades 9-11 -- covered by the 10-minute section below)
        ck(tens >= 1, f"...and that day really has a 10-minute slot to test ({tens})")

        # something IS being hidden -- otherwise the test proves nothing
        hidden = pg.eval_on_selector_all("#gbody .block .hid", "n => n.length")
        ck(hidden > 0, f"lines that didn't fit were actually hidden (n={hidden})")

        # the course name always survives
        empty = pg.evaluate("""() => Array.from(
            document.querySelectorAll('#gbody .block:not(.dayfull)'))
            .filter(b => { const n = b.querySelector('.bname');
                           return !n || n.classList.contains('hid') ||
                                  !n.textContent.trim(); }).length""")
        ck(empty == 0, f"every block still shows its name ({empty} blank blocks)")

        # hover restores everything
        first = pg.locator("#gbody .block:not(.dayfull)").first
        first.hover()
        pg.wait_for_timeout(350)
        shown = pg.evaluate("""() => {
            const b = document.querySelector('#gbody .block:not(.dayfull)');
            return Array.from(b.children)
                .filter(e => !e.classList.contains('bextra'))
                .every(e => e.offsetHeight > 0 && getComputedStyle(e).display !== 'none');
        }""")
        ck(shown, "hovering brings the hidden lines back")

        # ---- a normal week: 50-minute blocks have room for more ----
        pg.clock.install(time="2026-09-08T16:00:00Z")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(250)
        bad2 = pg.evaluate(MEASURE)
        ck(not bad2, f"nothing half-cut on a normal school day either ({bad2[:3]})")
        vis = pg.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('#gbody .block:not(.dayfull)'))
              .find(x => x.querySelector('.bname').textContent === 'Game Design');
            return b ? Array.from(b.children)
              .filter(e => !e.classList.contains('bextra') && !e.classList.contains('hid'))
              .map(e => e.className) : null; }""")
        ck(vis and len(vis) >= 2,
           f"a full-length class still shows more than just its name ({vis})")

        # ---- narrow window: text re-wraps, clipping must be redone ----
        pg.set_viewport_size({"width": 700, "height": 900})
        pg.wait_for_timeout(500)
        bad3 = pg.evaluate(MEASURE)
        ck(not bad3, f"still nothing half-cut after a resize ({bad3[:3]})")

        # ---- a 10-minute slot with something starting right after it ----
        # Oct 30: "Student Meeting" 10:05-10:15, then the Immersives Fair at
        # 10:15. A block is never drawn under MIN_BLOCK_PX (22px), so the 10px
        # meeting reached 11px into the Fair -- whose times do not overlap, so
        # layoutOverlaps() put both full width and the Fair was painted over
        # the meeting's name, cutting it in half.
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SCHEDULE.replace("@pref",
            "@event 2026-09-08 - Student Meeting with the Head of School - 15:30-15:40 - gray\n"
            "@event 2026-09-08 - Fair right after - 15:40-16:15 - gray\n"
            "@event 2026-09-08 - Lone ten - 17:00-17:10 - gray\n"
            "@event 2026-09-08 - Gap ten - 17:30-17:40 - gray\n"
            "@event 2026-09-08 - After gap - 17:45-18:00 - gray\n"
            "@event 2026-09-08 - Clash one - 19:00-20:00 - gray\n"
            "@event 2026-09-08 - Clash two - 19:30-20:30 - gray\n@pref"))
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        bad4 = pg.evaluate(MEASURE)
        ck(not bad4, f"no line half-cut on a day with 10-minute blocks ({bad4[:3]})")
        solo4 = pg.evaluate(SOLO)
        ck(not solo4, f"back-to-back blocks are NOT put side by side ({solo4[:3]})")
        g = pg.evaluate("""() => {
          const out = {};
          document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(b => {
            const n = b.querySelector('.bname'), t = n ? n.textContent : '';
            const k = t.startsWith('Student Meeting') ? 'meet' : t === 'Fair right after' ? 'fair'
                    : t === 'Lone ten' ? 'lone' : t === 'Gap ten' ? 'gap' : t === 'After gap' ? 'after'
                    : t === 'Clash one' ? 'c1' : t === 'Clash two' ? 'c2' : null;
            if (!k) return;
            const r = b.getBoundingClientRect(), nr = n.getBoundingClientRect(),
                  col = b.closest('.col').getBoundingClientRect();
            out[k] = {l: r.left, r: r.right, t: r.top, b: r.bottom,
                      nameInside: nr.top >= r.top - 0.5 && nr.bottom <= r.bottom + 0.5,
                      h: r.height, micro: b.classList.contains('micro'),
                      share: Math.round(r.width / col.width * 100)};
          });
          return out; }""")
        m, f, lone = g.get("meet"), g.get("fair"), g.get("lone")
        hit = (m and f and m["l"] < f["r"] and f["l"] < m["r"]
               and m["t"] < f["b"] and f["t"] < m["b"])
        ck(m and f and not hit,
           f"the 10-minute block and the one starting right after it are not "
           f"drawn on top of each other ({m}, {f})")
        ck(m and m["nameInside"], f"its name sits wholly inside its own box ({m})")
        ck(lone and lone["share"] > 80,
           f"a 10-minute block with nothing after it keeps the full width ({lone})")
        ck(m and f and m["share"] > 80 and f["share"] > 80,
           f"the 10-minute block and the one right after it are both full width ({m and m['share']}, {f and f['share']})")
        ck(m and m["micro"] and m["b"] <= f["t"] + 0.5,
           f"with no room to stretch it keeps its true height and goes .micro ({m})")
        ck(lone and not lone["micro"] and lone["h"] >= 21.5,
           f"with empty space below, a 10-minute block is stretched to readable size ({lone})")
        ga, af = g.get("gap"), g.get("after")
        ck(ga and af and ga["b"] <= af["t"] + 0.5 and ga["h"] > 11 and af["share"] > 80,
           f"a 5-minute gap: stretched only as far as the next block, never into it ({ga}, {af})")
        c1, c2 = g.get("c1"), g.get("c2")
        ck(c1 and c2 and c1["share"] < 60 and c2["share"] < 60,
           f"blocks whose times really clash are still side by side ({c1 and c1['share']}, {c2 and c2['share']})")
        pg.hover("#gbody .block >> text=Student Meeting")
        pg.wait_for_timeout(250)
        hov = pg.evaluate("""() => { const b = document.querySelector('#gbody .block:hover');
          return b ? Math.round(b.getBoundingClientRect().height) : 0; }""")
        ck(hov > 40, f"hovering it opens it out to full size ({hov}px)")
        pg.mouse.move(0, 0)
        pg.set_viewport_size({"width": 400, "height": 900})
        pg.wait_for_timeout(600)
        bad5 = pg.evaluate(MEASURE)
        ck(not bad5, f"and nothing half-cut at phone width, where the long name wraps ({bad5[:3]})")

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
