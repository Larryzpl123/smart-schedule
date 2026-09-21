"""The two hardening fixes from the review pass."""
import os, shutil, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_sec_")
APP = os.path.join(WORK, "app")
shutil.copytree(APP_DIR, APP, ignore=shutil.ignore_patterns("push","push.zip","__pycache__","*.db"))
env = dict(os.environ, DB_PATH=os.path.join(WORK,"d.db"), SECRET_KEY="s", ADMIN_PASSWORD="apw", FLASK_RUN_PORT="5175")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k,None)
srv = subprocess.Popen([sys.executable,"-c","import app; app.app.run(port=5175, debug=False)"],
                       cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
BASE="http://127.0.0.1:5175"

cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
r=op.open(urllib.request.Request(BASE+"/admin/login", urllib.parse.urlencode({"password":"apw"}).encode()))
sc = r.headers.get_all("Set-Cookie") or []
hist = [h for h in sc if "session" in h]
ck(any("SameSite=Lax" in h for h in hist), f"session cookie is SameSite=Lax ({hist})")
ck(any("HttpOnly" in h for h in hist), "session cookie is HttpOnly")

before = open(os.path.join(APP,"schedule.txt"),encoding="utf-8").read()
day1 = [l for l in before.split("\n") if l.startswith("@day 1")][0]
op.open(urllib.request.Request(BASE+"/admin/public",
        urllib.parse.urlencode({"remove": day1}).encode()))
after = open(os.path.join(APP,"schedule.txt"),encoding="utf-8").read()
ck(day1 in after, "the remove button cannot be pointed at a @day rotation line")
ck(before == after, "and nothing else changed either")

srv.terminate()
try: srv.wait(timeout=5)
except Exception: srv.kill()
print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL))+' FAILED'}")
sys.exit(1 if FAIL else 0)
