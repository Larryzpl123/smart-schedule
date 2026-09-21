"""Nobody can read anybody else's schedule. Not by search, not by URL.

History: schedules were readable by any other logged-in student by default.
The search box was hidden, which hid the problem rather than fixing it -- the
routes stayed live, so a student could still fetch any schedule by id. Then the
default was flipped to private. Now the whole lookup feature is gone: the box,
the index, and the per-student endpoint.
"""
import os, shutil, sqlite3, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, urllib.error, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_pv_")
APP = os.path.join(WORK, "app")
shutil.copytree(APP_DIR, APP, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db"))
DB = os.path.join(WORK, "schedule.db")
PORT = "5190"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="pv", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT

def start():
    p = subprocess.Popen(
        [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
        cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return p

def stop(p):
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()

def opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post(op, path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

def get(op, path):
    try:
        return op.open(BASE + path).read().decode(), 200
    except urllib.error.HTTPError as e:
        return "", e.code

def make(op, who, sched):
    post(op, "/register", {"email": who, "name": who.split("@")[0], "password": "pw123456"})
    post(op, "/login", {"email": who, "password": "pw123456"})
    post(op, "/edit", {"schedule_text": sched})

srv = start()

A_MAIL, B_MAIL = "27doej@shadysideacademy.org", "28smitha@shadysideacademy.org"
a, b = opener(), opener()
make(a, A_MAIL, "Jane Doe\n27 - Senior\n\n@term Fall 2026 = 08.25-01.15\n"
                "A - SECRET CLASS - N - 1 - T - R - red\n")
make(b, B_MAIL, "Sam Smith\n28 - Junior\n\n@term Fall 2026 = 08.25-01.15\n"
                "B - Other Class - N - 1 - T - R - blue\n")

con = sqlite3.connect(DB)
a_id = con.execute("SELECT id FROM users WHERE email=?", (A_MAIL,)).fetchone()[0]
flags = dict(con.execute("SELECT email, is_public FROM users").fetchall())
con.close()

ck(all(v == 0 for v in flags.values()),
   f"a brand-new account is private ({flags})")

# ---- the lookup feature is gone, not hidden --------------------------------
_, code = get(b, "/api/student-index")
ck(code == 404, f"there is no index of students any more (status {code})")

got, code = get(b, f"/api/student/{a_id}")
ck(code == 404 and "SECRET CLASS" not in got,
   f"and no way to fetch one by id (status {code})")

page, _ = get(b, "/app")
# (the file still explains in a comment what used to be here; what matters is
# that no element and no fetch remain)
ck('id="stuSearch"' not in page and 'PATHS.index' not in page
   and 'fetch("/api/student-index' not in page,
   "the page has no search box and never asks for the index")

# even flipping the old column back on must not bring it back
con = sqlite3.connect(DB)
con.execute("UPDATE users SET is_public=1")
con.commit()
con.close()
_, c1 = get(b, "/api/student-index")
_, c2 = get(b, f"/api/student/{a_id}")
ck(c1 == 404 and c2 == 404,
   f"setting is_public=1 in the database does not reopen it ({c1}/{c2})")

# ---- ...but the owner still sees their own ---------------------------------
mine, _ = get(a, "/api/my-schedule")
ck("SECRET CLASS" in mine, "the owner still sees their own schedule")
mine_b, _ = get(b, "/api/my-schedule")
ck("Other Class" in mine_b and "SECRET CLASS" not in mine_b,
   "and each account gets only its own")

# saving from /edit must not quietly rewrite the old column either
post(a, "/edit", {"schedule_text": "Jane Doe\n27 - Senior\n\n"
                  "@term Fall 2026 = 08.25-01.15\nA - SECRET CLASS - N - 1 - T - R - red\n"})
con = sqlite3.connect(DB)
still = con.execute("SELECT is_public FROM users WHERE email=?", (A_MAIL,)).fetchone()[0]
con.close()
ck(still == 1, "a save leaves the unused is_public column alone")

# ---- the one-off migration still behaves -----------------------------------
con = sqlite3.connect(DB)
con.execute("DELETE FROM app_meta WHERE key='privacy_default_off'")
con.commit()
con.close()
stop(srv)
srv = start()
con = sqlite3.connect(DB)
flags = dict(con.execute("SELECT email, is_public FROM users").fetchall())
con.close()
ck(all(v == 0 for v in flags.values()),
   f"a database from before the change is switched to private on first start ({flags})")

con = sqlite3.connect(DB)
con.execute("UPDATE users SET is_public=1 WHERE email=?", (A_MAIL,))
con.commit()
con.close()
stop(srv)
srv = start()
con = sqlite3.connect(DB)
after = con.execute("SELECT is_public FROM users WHERE email=?", (A_MAIL,)).fetchone()[0]
con.close()
ck(after == 1, "and it does not run again on every restart")
stop(srv)

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
