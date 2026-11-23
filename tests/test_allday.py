"""The all-day band lines up with the days under it.

A long all-day title ("Case Western EA decision (~12/19) - check
email/portal") used to widen its own column in the band -- grid columns are
1fr = minmax(auto, 1fr), so a nowrap label can push them -- and every pill
after it drifted off its day ("CMU decision" on Tuesday sat half on Monday).
And the band's "all day" label is left-aligned like the hour labels below."""
import os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_ad_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5235"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="adtest", ADMIN_PASSWORD="apw",
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
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea

@event 2026-12-15 - CMU decision - allday - pink
@event 2026-12-19 - Case Western EA decision (~12/19) – check email/portal - allday - pink
@event 2026-12-16 - A really quite long all-day title that will never fit in one narrow column - allday - gray

@pref time_format = 12h
"""

MEASURE = """() => {
  const heads = Array.from(document.querySelectorAll('#ghead .dcell')).map(c => c.getBoundingClientRect());
  const cells = Array.from(document.querySelectorAll('#allday .adcell')).map(c => c.getBoundingClientRect());
  const lab = document.querySelector('#allday .adlab'), tick = document.querySelector('.axis .tick');
  const pill = t => { const p = Array.from(document.querySelectorAll('#allday .pbar')).find(x => x.textContent.startsWith(t));
                      return p ? p.getBoundingClientRect() : null; };
  return {heads: heads.map(r => [r.left, r.right]), cells: cells.map(r => [r.left, r.right]),
          cmu: pill('CMU'), cwru: pill('Case Western'),
          labAlign: getComputedStyle(lab).textAlign, tickAlign: getComputedStyle(tick).textAlign}; }"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        errs = []
        for width, days in ((1280, "7"), (1280, "5"), (900, "7")):
            pg = b.new_page(viewport={"width": width, "height": 900})
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.clock.install(time="2026-12-14T15:00:00Z")
            pg.goto(BASE + "/local/app")
            pg.evaluate("([s, d]) => { localStorage.setItem('ssa-local-schedule', s);"
                        "localStorage.setItem('ssa-local-install','off');"
                        "localStorage.setItem('ssa-local-remind','off');"
                        "localStorage.setItem('ssa-days', d); }", [SEED, days])
            pg.reload()
            pg.wait_for_selector("#gbody .col", timeout=8000)
            pg.wait_for_timeout(400)
            m = pg.evaluate(MEASURE)
            tag = f"{width}px, {days}-day"
            off = [round(max(abs(h[0] - c[0]), abs(h[1] - c[1])), 1) for h, c in zip(m["heads"], m["cells"])]
            ck(len(m["cells"]) == len(m["heads"]) and max(off) <= 1.5,
               f"{tag}: every all-day cell sits exactly over its day (worst {max(off)}px off)")
            tue = m["heads"][1]
            ck(m["cmu"] and m["cmu"]["left"] >= tue[0] - 0.5 and m["cmu"]["right"] <= tue[1] + 0.5,
               f"{tag}: 'CMU decision' is inside Tuesday, not pushed onto Monday")
            if days == "7":
                sat = m["heads"][5]
                ck(m["cwru"] and m["cwru"]["left"] >= sat[0] - 0.5 and m["cwru"]["right"] <= sat[1] + 0.5,
                   f"{tag}: the long Case Western title stays in Saturday (cut short, full text on hover)")
            ck(m["labAlign"] in ("left", "start") and m["tickAlign"] in ("left", "start"),
               f"{tag}: 'all day' is left-aligned like the hour labels ({m['labAlign']} / {m['tickAlign']})")
            pg.close()
        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
