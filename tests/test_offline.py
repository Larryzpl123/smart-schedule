"""Local mode with no internet, and with no count caps.

Real service worker, real offline (the browser's own offline switch), real
reloads: the app and the editor open offline, show the school calendar from
the last visit online and say how old it is, and take edits; online again,
a calendar change made on the server comes through by itself. The account
app is never cached. And local mode has no 200-event ceiling -- only the
browser-storage one."""
import http.cookiejar, os, subprocess, sys, tempfile, time, urllib.parse, urllib.request

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_off_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5225"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="offtest", ADMIN_PASSWORD="apw",
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
sys.path.insert(0, APP_DIR)
import importlib
_app = importlib.import_module("app")

SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
C - French 3 - FR300 - 1 - Mr. Okafor - R 310 - purple - Ea
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - La

@event 2026-10-29 - Dentist - 16:00-17:00 - pink

@pref time_format = 12h
"""

def admin_anchor(date, day):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(BASE + "/admin/login",
                                   urllib.parse.urlencode({"password": "apw"}).encode()))
    op.open(urllib.request.Request(BASE + "/admin/anchor",
                                   urllib.parse.urlencode({"date": date, "day": day}).encode()))

THU = """() => { const c = Array.from(document.querySelectorAll('#ghead .dcell'))
    .find(x => x.textContent.includes('Oct 29')); return c ? c.querySelector('.dcyc').textContent : null; }"""
CACHED = """() => caches.open('ssa-local-v1').then(c => c.keys()).then(ks => ks.map(r => new URL(r.url).pathname))"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-10-29T13:30:00Z")

        # ---------------- first visit, online ----------------
        pg.goto(BASE + "/local/app")
        pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.evaluate("navigator.serviceWorker.ready")
        pg.reload()                                   # now controlled by the worker
        pg.wait_for_selector("#gbody .col", timeout=8000)
        ck(pg.evaluate("!!navigator.serviceWorker.controller"), "the page is under the service worker")
        online_day = pg.evaluate(THU)
        ck(online_day == "Day 3", f"online: Thu Oct 29 is Day 3 ({online_day})")
        cached = []
        for _ in range(40):
            cached = pg.evaluate(CACHED)
            if {"/local/edit", "/local", "/api/calendar/2025-2026", "/api/calendar/2026-2027",
                "/api/schedule-shared", "/local/app"} <= set(cached):
                break
            pg.wait_for_timeout(250)
        ck({"/local/edit", "/local", "/api/calendar/2025-2026", "/api/calendar/2026-2027",
            "/api/schedule-shared", "/local/app"} <= set(cached),
           f"everything local mode needs is saved, including the editor and the other year it did not open ({sorted(cached)})")
        ck(not [p for p in cached if p in ("/app", "/edit", "/api/my-schedule", "/api/me")],
           "nothing personal or account-side in it")

        # ---------------- offline ----------------
        ctx.set_offline(True)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(pg.evaluate(THU) == "Day 3", f"offline: the app opens, with the right cycle day ({pg.evaluate(THU)})")
        ck(pg.locator("#gbody .block", has_text="Dentist").count() > 0
           and pg.locator("#gbody .block", has_text="Game Design").count() > 0,
           "...and the schedule on it")
        st = pg.text_content("#status") or ""
        ck("Offline" in st and "as of" in st, f"it says it is offline and how old the calendar is ({st!r})")
        ck("Embedded data" not in st, "it is the saved copy, not the stale one baked into the page")

        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(600)
        ck(pg.locator("#schedForm").count() == 1, "offline: the editor opens too (never visited this session -- saved ahead)")
        pg.click("#modeText")
        txt = pg.input_value("#rawText")
        ck("Dentist" in txt, "with the schedule in it")
        pg.fill("#rawText", txt.replace("@pref", "@event 2026-10-30 - Offline edit - 12:00-12:30 - gray\n@pref"))
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(400)
        ck("Offline edit" in (pg.evaluate("localStorage.getItem('ssa-local-schedule')") or ""), "an edit saves offline")
        # an import says why it cannot work, instead of "TypeError: Failed to fetch"
        pg.set_input_files("#icsFile", files=[{"name": "x.ics", "mimeType": "text/calendar",
                                               "buffer": b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"}])
        pg.wait_for_timeout(600)
        im = pg.text_content("#icsBox") or ""
        ck("needs the internet" in im, f"an import offline says it needs the internet ({im!r})")

        pg.goto(BASE + "/local/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(pg.locator("#gbody .block", has_text="Offline edit").count() > 0, "back in the app, offline, the edit is there")

        # ---------------- online again: the server's change comes through ----------------
        ctx.set_offline(False)
        admin_anchor("2026-10-29", "7")
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(pg.evaluate(THU) == "Day 7", f"online: a calendar change made on the server shows straight away ({pg.evaluate(THU)})")
        st = pg.text_content("#status") or ""
        ck("Offline" not in st, f"and it no longer says offline ({st!r})")
        ctx.set_offline(True)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(pg.evaluate(THU) == "Day 7", f"...and the saved copy was updated with it ({pg.evaluate(THU)})")
        ctx.set_offline(False)

        # ---------------- no count caps in local mode ----------------
        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(400)
        ck("No limit here." in (pg.text_content("body") or ""), "the editor says there is no limit")
        n = _app.MAX_EVENTS + 100
        many = SEED.replace("@pref", "".join(f"@event 2026-11-{(d % 28) + 1:02d} - Many {d} - 09:00-09:30 - gray\n"
                                            for d in range(n)) + "@pref")
        pg.click("#modeText")
        pg.fill("#rawText", many)
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(400)
        saved = pg.evaluate("localStorage.getItem('ssa-local-schedule')") or ""
        ck(saved.count("@event") >= n, f"{n} events save in local mode (the account cap is {_app.MAX_EVENTS})")
        pg.goto(BASE + "/local/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        r = pg.evaluate("localApi('/api/quick-add',{kind:'event',line:'2026-11-03 - One more - 10:00-10:30 - gray'})")
        ck(r["status"] == 200 and not r["body"].get("full"), f"and quick-add never offers to drop the oldest ({r['status']})")
        big = SEED + "# " + ("x" * 1100 * 1024) + "\n"
        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(300)
        pg.click("#modeText")
        pg.evaluate("t => document.getElementById('rawText').value = t", big)
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(400)
        msg = pg.locator("#localFlash").inner_text()
        ck(f"limit is {_app.LOCAL_MAX_BYTES // 1024} KB" in msg,
           f"the one ceiling left is the browser's storage ({msg[:90]!r})")

        # ---------------- the account app is never cached ----------------
        ap = ctx.new_page()
        ap.goto(BASE + "/register")
        ap.fill('input[name=email]', "27doej@shadysideacademy.org")
        ap.fill('input[name=name]', "Jane Doe")
        ap.fill('input[name=password]', "pw123456")
        ap.click('button[type=submit]')
        ap.fill('input[name=email]', "27doej@shadysideacademy.org")
        ap.fill('input[name=password]', "pw123456")
        ap.click('button[type=submit]')
        ap.wait_for_load_state()
        ap.goto(BASE + "/app")
        ap.wait_for_timeout(1200)
        ap.reload()
        ap.wait_for_timeout(1200)
        cached = ap.evaluate(CACHED)
        ck(not [p for p in cached if p in ("/app", "/edit", "/api/my-schedule", "/api/me", "/login")],
           f"using the account app saves nothing of it ({[p for p in cached if not p.startswith('/local')]})")
        ck(_app.MAX_EVENTS == 200, f"and the account cap is still {_app.MAX_EVENTS}")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    # even when a step above blows up: an orphaned server keeps the port, and
    # the NEXT run then quietly tests whatever code that old server was running
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
