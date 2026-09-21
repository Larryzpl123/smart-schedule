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
