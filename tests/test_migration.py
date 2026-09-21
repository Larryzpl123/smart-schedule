"""The live DB has the OLD schema. Does the new app.py upgrade it in place
without losing anything?"""
import os
import json, os, shutil, sqlite3, subprocess, sys, tempfile

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from werkzeug.security import generate_password_hash


FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
WORK=tempfile.mkdtemp(prefix="ssa_mig_"); os.makedirs(os.path.join(WORK,"d"))
DBP=os.path.join(WORK,"d","schedule.db")

# --- build a database with the EXACT pre-change schema (from the old app.py) --
OLD_SCHEMA = """CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT,
    username      TEXT,
    class_year    TEXT,
    grade         INTEGER,
    password_hash TEXT NOT NULL,
    verified      INTEGER DEFAULT 0,
    verify_code   TEXT,
    schedule_text TEXT DEFAULT '',
    is_public     INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now','localtime')))"""
con=sqlite3.connect(DBP); con.execute(OLD_SCHEMA)
SCHED="Jane Doe\r\n27 - Junior\r\n\r\n@term Spring 2026 = 01.20-06.30\r\nA - AP Calculus BC - MA560 - 1 - Mrs. Yam - R 202 - red\r\n"
PWH=generate_password_hash("realpassword")
con.execute("INSERT INTO users (email,name,username,class_year,grade,password_hash,verified,"
            "schedule_text,is_public,created_at) VALUES (?,?,?,?,?,?,1,?,1,?)",
  ("27doej@shadysideacademy.org","Jane Doe","27doej","27",11,PWH,SCHED,"2026-08-01 09:00:00"))
con.execute("INSERT INTO users (email,name,username,password_hash,verified,verify_code,schedule_text)"
            " VALUES (?,?,?,?,0,'424242','')",
  ("28mid@shadysideacademy.org","Mid Verify","28mid",generate_password_hash("pw123456")))
con.commit()
cols_before={r[1] for r in con.execute("PRAGMA table_info(users)")}
tabs_before={r[0] for r in con.execute("select name from sqlite_master where type='table'")}
con.close()
ck("code_expires" not in cols_before, "starting DB has NO code_expires (like production)")
ck("rate_limits" not in tabs_before, "starting DB has NO rate_limits table")

# --- start the new app against it ------------------------------------------
env=dict(os.environ,DB_PATH=DBP,SECRET_KEY="x",ADMIN_PASSWORD="apw")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS","MAX_CODES_PER_DAY","CODE_TTL_SECONDS"): env.pop(k,None)
probe=r'''
import app, json
from werkzeug.security import check_password_hash
app.app.config["TESTING"]=True
c=app.app.test_client()
u=app.query("SELECT * FROM users WHERE email='27doej@shadysideacademy.org'",fetch=True,one=True)
r=c.post("/login",data={"email":"27doej@shadysideacademy.org","password":"realpassword"},follow_redirects=True)
with c.session_transaction() as s: logged = "user_id" in s
print(json.dumps({
  "cols": [x["name"] for x in app.query("PRAGMA table_info(users)",fetch=True)],
  "tables": [x["name"] for x in app.query("select name from sqlite_master where type='table'",fetch=True)],
  "n": app.query("SELECT COUNT(*) c FROM users",fetch=True,one=True)["c"],
  "sched": u["schedule_text"], "created": u["created_at"], "grade": u["grade"],
  "hash_ok": check_password_hash(u["password_hash"],"realpassword"),
  "login_ok": logged,
  "sched_api": c.get("/api/my-schedule").data.decode(),
}))
'''
r=subprocess.run([sys.executable,"-c",probe],cwd=APP_DIR,env=env,capture_output=True,text=True)
try: g=json.loads(r.stdout.strip().splitlines()[-1])
except Exception: g={}; print("      stderr:",r.stderr[-800:])

ck("code_expires" in g.get("cols",[]), "code_expires column was ADDED")
ck("rate_limits" in g.get("tables",[]), "rate_limits table was created")
ck(g.get("n")==2, f"both accounts still there ({g.get('n')})")
ck(g.get("sched")==SCHED, "schedule text byte-identical, CRLF and all")
ck(g.get("created")=="2026-08-01 09:00:00", "created_at preserved")
ck(g.get("grade")==11, "grade preserved")
ck(g.get("hash_ok") is True, "password hash still verifies")
ck(g.get("login_ok") is True, "the existing account can still log in")
ck(g.get("sched_api")==SCHED, "/api/my-schedule serves the same text")

# --- second start must be a no-op (not a crash, not a duplicate column) -----
r2=subprocess.run([sys.executable,"-c",probe],cwd=APP_DIR,env=env,capture_output=True,text=True)
try: g2=json.loads(r2.stdout.strip().splitlines()[-1])
except Exception: g2={}; print("      stderr2:",r2.stderr[-500:])
ck(r2.returncode==0, "starting a second time doesn't crash (ALTER is guarded)")
ck(g2.get("cols",[]).count("code_expires")==1, "column added exactly once")
ck(g2.get("n")==2, "still 2 accounts")

# --- a user caught mid-verification when the deploy lands ------------------
probe2=r'''
import app, json
app.app.config["TESTING"]=True
c=app.app.test_client()
r=c.post("/verify",data={"email":"28mid@shadysideacademy.org","code":"424242"},follow_redirects=True)
b=r.data.decode()
print(json.dumps({"expired": "expired" in b, "verified":
  app.query("SELECT verified FROM users WHERE email='28mid@shadysideacademy.org'",fetch=True,one=True)["verified"]}))
'''
r3=subprocess.run([sys.executable,"-c",probe2],cwd=APP_DIR,env=env,capture_output=True,text=True)
try: g3=json.loads(r3.stdout.strip().splitlines()[-1])
except Exception: g3={}; print("      stderr3:",r3.stderr[-500:])
ck(g3.get("expired") is True, "a code issued BEFORE the deploy is treated as expired, not accepted")
ck(g3.get("verified")==0, "...and the account is not verified by it (they just resend)")

shutil.rmtree(WORK,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
