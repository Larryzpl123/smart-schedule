"""Click the new Resend button and look at the admin cap banner for real."""
import os, subprocess, sys, tempfile, time, signal, shutil, sqlite3

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK=tempfile.mkdtemp(prefix="ssa_brl_"); DBP=os.path.join(WORK,"d","schedule.db")
env=dict(os.environ,DB_PATH=DBP,SECRET_KEY="x",ADMIN_PASSWORD="apw",
         MAX_CODES_PER_DAY="3",CODE_TTL_SECONDS="900",
         SMTP_HOST="fake",SMTP_USER="fake",SMTP_PASS="fake")   # is_enabled() -> True
# stub the actual send so nothing tries real SMTP
os.makedirs(os.path.join(WORK,"code")); shutil.copytree(APP_DIR,os.path.join(WORK,"code","app"),
    ignore=shutil.ignore_patterns("push","push.zip","__pycache__"))
APP=os.path.join(WORK,"code","app")
open(os.path.join(APP,"runner.py"),"w").write(
 "import mailer\n"
 "_log=open(%r,'a')\n"
 "mailer.send_code=lambda to,c:(_log.write(to+' '+c+chr(10)),_log.flush(),(True,'sent'))[2]\n"
 "import app\n"
 "app.app.run(port=5114,debug=False)\n" % os.path.join(WORK,"sent.log"))
open(os.path.join(WORK,"sent.log"),"w").close()
srv=subprocess.Popen([sys.executable,"runner.py"],cwd=APP,env=env,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(3)
FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
def sent(): return [l for l in open(os.path.join(WORK,"sent.log")) if l.strip()]
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg=b.new_page(); errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())

        pg.goto("http://127.0.0.1:5114/register")
        pg.fill('input[name=email]',"27doej@shadysideacademy.org")
        pg.fill('input[name=name]',"P Z"); pg.fill('input[name=password]',"pw123456")
        pg.click('button[type=submit]'); pg.wait_for_load_state()
        ck("/verify" in pg.url, "register lands on the verify page")
        ck(len(sent())==1, "1 code emailed")
        ck(pg.is_visible("text=Codes expire 15 minutes"), "page tells the user codes expire")

        ck(pg.is_visible("button:has-text('Resend code')"), "Resend is a button now, not a link")
        pg.click("button:has-text('Resend code')"); pg.wait_for_load_state(); pg.wait_for_timeout(300)
        ck(len(sent())==2, f"clicking Resend sends a new code ({len(sent())})")
        ck(pg.is_visible("text=New code sent"), "confirmation shown")

        # a plain reload must NOT fire another send (the whole point of POST)
        n=len(sent()); pg.goto(pg.url); pg.wait_for_timeout(300)
        ck(len(sent())==n, "reloading the verify page does not send another code")

        # 3rd send hits the global cap (MAX=3)
        pg.click("button:has-text('Resend code')"); pg.wait_for_load_state(); pg.wait_for_timeout(300)
        ck(len(sent())==3, "3rd send goes out")
        pg.click("button:has-text('Resend code')"); pg.wait_for_load_state(); pg.wait_for_timeout(300)
        ck(len(sent())==3, "4th is refused — daily cap held")
        ck(pg.is_visible("text=daily limit"), "user is told the site hit its daily limit")

        # a wrong code
        pg.fill('input[name=code]',"000000"); pg.click("button[type=submit]")
        pg.wait_for_load_state()
        ck(pg.is_visible("text=didn't match"), "wrong code rejected")

        # admin banner + reset
        pg.goto("http://127.0.0.1:5114/admin/login")
        pg.fill('input[name=password]',"apw"); pg.click('button[type=submit]'); pg.wait_for_load_state()
        ck(pg.is_visible("text=Daily email cap reached"), "admin sees the cap banner")
        pg.click("button:has-text('Reset counter')"); pg.wait_for_load_state(); pg.wait_for_timeout(300)
        ck(pg.is_visible("text=Daily email counter reset"), "reset confirmed")
        ck(pg.is_visible("text=sent in the last 24h"), "banner is back to the normal counter line")
        ck(not errs, f"no JS errors ({errs[:3]})")
        b.close()
finally:
    srv.send_signal(signal.SIGTERM); srv.wait(timeout=10); shutil.rmtree(WORK,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
