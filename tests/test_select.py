"""The dropdown arrow on selects (the year picker...) sits inside the box.

The browser's own arrow was jammed against the right edge. Selects draw their
own chevron, inset from the edge, with room for it on phones too (the narrow
layout's padding rule used to reset it)."""
import os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
WORK = tempfile.mkdtemp(prefix="ssa_sel_")
PORT = "5237"
env = dict(os.environ, DB_PATH=os.path.join(WORK, "d", "schedule.db"), SECRET_KEY="seltest",
           ADMIN_PASSWORD="apw", FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
srv = subprocess.Popen([sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
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
"""
PROBE = """() => { const e = document.getElementById('yearSel'), c = getComputedStyle(e);
  const m = /calc\\(100% - ([\\d.]+)px\\)/.exec(c.backgroundPosition);
  return {app: c.appearance, pr: parseFloat(c.paddingRight), img: c.backgroundImage.startsWith('url("data:image/svg'),
          inset: m ? parseFloat(m[1]) : 0}; }"""
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        errs = []
        for w in (1280, 400):
            pg = b.new_page(viewport={"width": w, "height": 900})
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE + "/local/app")
            pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                        "localStorage.setItem('ssa-local-install','off');"
                        "localStorage.setItem('ssa-local-remind','off');}", SEED)
            pg.reload()
            pg.wait_for_selector("#yearSel", timeout=8000)
            r = pg.evaluate(PROBE)
            ck(r["app"] == "none" and r["img"], f"{w}px: the year picker draws its own chevron ({r})")
            ck(r["inset"] >= 8, f"{w}px: the chevron is inset from the right edge ({r['inset']}px)")
            ck(r["pr"] >= 24, f"{w}px: and the text leaves room for it ({r['pr']}px)")
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
