"""The toolbar never shows a wrong switch while the page is still arriving.

app.html is ~245KB; over a real connection it comes in pieces and the browser
paints the toolbar before the main script (further down) has run. A 7-day
user saw "5 day" -- the markup's default -- for a moment on about a third of
refreshes; a light-theme user could get a dark flash, a compact-toolbar user
the full one. Here a small proxy sends the page in two pieces with a pause
between, and every frame is sampled: whenever the toolbar is visible its
switches must already match what was saved."""
import os, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
WORK = tempfile.mkdtemp(prefix="ssa_ch_")
PORT, PROXY = "5245", 5246
env = dict(os.environ, DB_PATH=os.path.join(WORK, "d", "schedule.db"), SECRET_KEY="chtest",
           ADMIN_PASSWORD="apw", FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
srv = subprocess.Popen([sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False, threaded=True)"],
                       cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

UP = "http://127.0.0.1:" + PORT
class Slow(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        try:
            r = urllib.request.urlopen(UP + self.path, timeout=10)
            code, body, hdrs = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            code, body, hdrs = e.code, e.read(), e.headers
        self.send_response(code)
        for k, v in hdrs.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        cut = body.find(b'<div class="status"') if self.path.split("?")[0] == "/local/app" else -1
        if cut > 0:                       # everything up to just past the toolbar, then a pause
            self.wfile.write(body[:cut]); self.wfile.flush(); time.sleep(1.5)
            self.wfile.write(body[cut:])
        else:
            self.wfile.write(body)
px = ThreadingHTTPServer(("127.0.0.1", PROXY), Slow)
threading.Thread(target=px.serve_forever, daemon=True).start()

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = f"http://127.0.0.1:{PROXY}"
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
"""
SAMPLER = """(() => { window.__fr = []; const snap = () => {
  const tb = document.querySelector('.toolbar'), ds = document.getElementById('daysSeg');
  const on = s => { const b = document.querySelector(s + ' button.on'); return b ? b.textContent.trim() : null; };
  if (tb && ds && document.body) window.__fr.push({shown: getComputedStyle(tb).visibility !== 'hidden',
      days: on('#daysSeg'), view: on('#viewSeg'), theme: document.documentElement.getAttribute('data-theme'),
      compact: document.documentElement.classList.contains('tb-compact'),
      bg: getComputedStyle(document.body).backgroundColor,
      b5: getComputedStyle(document.querySelector('#daysSeg button[data-d="5"]')).backgroundColor,
      b7: getComputedStyle(document.querySelector('#daysSeg button[data-d="7"]')).backgroundColor});
  if (window.__fr.length < 600) requestAnimationFrame(snap); };
  requestAnimationFrame(snap); })();"""

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        errs = []
        for compact in (False, True):
            tag = "compact toolbar" if compact else "full toolbar"
            ctx = b.new_context(viewport={"width": 1280, "height": 800})
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE + "/local")
            pg.evaluate("([s, c]) => { localStorage.setItem('ssa-local-schedule', s);"
                        "localStorage.setItem('ssa-local-install','off'); localStorage.setItem('ssa-local-remind','off');"
                        "localStorage.setItem('ssa-days','7'); localStorage.setItem('ssa-view','week');"
                        "localStorage.setItem('ssa-theme','light');"
                        "if (c) localStorage.setItem('ssa-toolbar','compact'); }", [SEED, compact])
            pg.add_init_script(SAMPLER)
            pg.goto(BASE + "/local/app", wait_until="commit")
            pg.wait_for_selector("#gbody .col", timeout=15000)
            pg.wait_for_timeout(300)
            fr = pg.evaluate("window.__fr")
            early = [f for f in fr if f["days"]]
            ck(len(early) > 20, f"{tag}: the page really did arrive in pieces ({len(fr)} frames sampled)")
            shown = [f for f in fr if f["shown"]]
            bad = [f for f in shown if f["days"] != "7 day" or f["view"] != "Week" or f["compact"] != compact]
            ck(shown and not bad, f"{tag}: whenever the toolbar is visible it says 7 day / Week "
                                  f"({len(bad)} of {len(shown)} visible frames wrong{', e.g. ' + str(bad[0]) if bad else ''})")
            # what is actually painted, not just the class: the buttons fade
            # colour over .15s, and that fade from "5 day" played on screen
            fin = fr[-1]
            fade = [f for f in shown if (f["b5"], f["b7"]) != (fin["b5"], fin["b7"])]
            ck(not fade, f"{tag}: and its colours are already final -- no fade from 5 day to 7 day "
                         f"({len(fade)} visible frames mid-fade{', e.g. ' + str((fade[0]['b5'], fade[0]['b7'])) if fade else ''})")
            dark = [f for f in fr if f["theme"] != "light"]
            ck(not dark, f"{tag}: light theme from the first frame ({len(dark)} frames without it)")
            ck(fr and fr[-1]["shown"], f"{tag}: and the toolbar is visible once loaded")
            ctx.close()
        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    px.shutdown()
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()
print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
