"""Drive the real /edit page in Chromium: does 'Load from a .txt file' actually
work, and does it survive the form<->text mode sync?"""
import os, subprocess, sys, tempfile, time, signal, shutil

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from werkzeug.security import generate_password_hash


WORK = tempfile.mkdtemp(prefix="ssa_br_")
DB = os.path.join(WORK, "d", "schedule.db")
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="brtest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT="5111")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k, None)

srv = subprocess.Popen([sys.executable, "-c",
    "import app; app.app.run(port=5111, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)

ORIGINAL = """Jane Doe
27 - Junior

@term Spring 2026 = 01.20-06.30
A - AP Calculus BC - MA560 - 1 - Mrs. Yam - R 202 - red
D - Robotics - CS301 - 1 - Ms. Wang - GH 031 - orange
@immersive Your Move - IM327 - Mr. Solomon - MC 110 - indigo

@pref time_format = 12h"""

UPLOAD = """Jane Doe
27 - Junior

@term Spring 2026 = 01.20-06.30
B - AP Physics C - SC480 - 2 - Dr. Nolan - S 21 - blue
F - 中文 5 - CH500 - 1 - 王老师 - L 7 - purple
@sport Swimming - Mon,Thu - 15:45-17:15 - 09.01-11.30 - cyan

@pref time_format = 24h"""

try:
    from playwright.sync_api import sync_playwright
    tf = os.path.join(WORK, "27doej_schedule.txt")
    open(tf, "w", encoding="utf-8").write(UPLOAD)

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page()
        errs, four04 = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: four04.append(r.url) if r.status == 404 else None)
        LF = lambda t: t.replace("\r\n", "\n").strip()

        pg.goto("http://127.0.0.1:5111/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')

        # seed a schedule through the real Text-mode UI
        pg.goto("http://127.0.0.1:5111/edit")
        pg.click("#modeText")
        pg.fill("#rawText", ORIGINAL)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck(LF(saved) == LF(ORIGINAL), "text-mode save round-trips through the browser")
        ck("\r\n" in saved, "browser stores CRLF (pre-existing HTML form behaviour)")

        # ---- the actual new feature ----
        pg.goto("http://127.0.0.1:5111/edit")
        ck(pg.is_visible("#importBtn"), "'Load .txt' button is visible")
        ck(pg.is_visible("text=Download (.txt)"), "'Download (.txt)' link is visible")

        pg.set_input_files("#importFile", tf)
        pg.wait_for_timeout(700)

        ck(pg.is_visible("#textPane"), "switched to Text mode after loading")
        raw = pg.input_value("#rawText")
        ck(LF(raw) == LF(UPLOAD),
           "raw text is EXACTLY the uploaded file (not regenerated from the form)")
        ck("中文 5" in raw and "王老师" in raw, "unicode preserved through FileReader")
        ck("Loaded 27doej_schedule.txt" in pg.text_content("#importStatus"),
           "status line names the loaded file")

        # nothing saved yet -- this is the promise made on the page
        still = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck(LF(still) == LF(ORIGINAL), "loading a file does NOT save (still the old schedule)")

        # the form pane must have been rebuilt from the uploaded file too
        pg.click("#modeForm")
        pg.wait_for_timeout(300)
        vals = pg.eval_on_selector_all(".periodrow .c_name", "n=>n.map(x=>x.value)")
        ck(vals[1] == "AP Physics C", f"form pane rebuilt: period B = {vals[1]!r}")
        ck(vals[5] == "中文 5", f"form pane rebuilt: period F = {vals[5]!r}")
        ck(vals[0] == "" and vals[3] == "", "old periods A and D cleared from the form")
        ck(pg.input_value("#f_timefmt") == "24h", "@pref time_format picked up from the file")
        # Sports moved out of the term card into rows of their own -- three
        # seasons never fit in one box per semester.
        sport = pg.eval_on_selector_all("#sportRows .sp_name", "n=>n.map(x=>x.value)")
        ck(sport and "Swimming" in sport, f"@sport line carried into the form ({sport})")

        # now actually save and confirm it lands
        pg.click('button[type=submit]')
        pg.wait_for_load_state(); pg.wait_for_timeout(400)
        final = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("AP Physics C" in final and "中文 5" in final, "Save persists the imported schedule")
        ck("AP Calculus BC" not in final, "old schedule replaced")

        # and the download link serves it back
        with pg.expect_download() as dl:
            pg.click("text=Download (.txt)")
        path = dl.value.path()
        got = open(path, encoding="utf-8").read()
        ck("AP Physics C" in got and "中文 5" in got, "download returns the saved schedule")
        ck(dl.value.suggested_filename.startswith("27doej_schedule_"),
           f"download filename = {dl.value.suggested_filename}")

        ck(not errs, f"no JS errors ({errs[:3]})")
        ck(not four04, f"no 404s ({set(four04)})")
        b.close()
finally:
    srv.send_signal(signal.SIGTERM); srv.wait(timeout=10)
    shutil.rmtree(WORK, ignore_errors=True)

print("\n" + "="*60)
print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: " + "; ".join(FAIL))
print("="*60)
sys.exit(1 if FAIL else 0)
