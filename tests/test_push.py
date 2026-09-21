"""Does build_push.py actually protect the live site? Simulate a real deploy."""
import os
import json, os, shutil, sqlite3, subprocess, sys, tempfile, zipfile

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from werkzeug.security import generate_password_hash


FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
PY=sys.executable

# ---------------------------------------------------------------- build
print("\n[build] what lands in push.zip")
subprocess.run([PY,"build_push.py"],cwd=APP_DIR,capture_output=True)
names = zipfile.ZipFile(os.path.join(APP_DIR, "push.zip")).namelist()
ck(all(n.startswith("push/") for n in names), "everything is under push/")
ck("push/app.py" in names, "app.py included")
ck("push/static/app.html" in names, "static/app.html included")
ck("push/templates/admin_import.html" in names, "NEW template admin_import.html included")
ck("push/templates/admin_import_preview.html" in names, "NEW template admin_import_preview.html included")
ck(len([n for n in names if n.startswith("push/calendar/")])==2, "both calendars included")
ck(not any(n.endswith(".db") for n in names), "no .db")
ck("push/schedule.txt" not in names, "schedule.txt EXCLUDED (admin edits it on the server)")
ck("push/wsgi_pythonanywhere.py" not in names, "wsgi (secrets) excluded")
ck(not any(n.endswith(".md") for n in names), "docs excluded")
ck(not any("samples" in n for n in names), "sample PDFs excluded")

# ---------------------------------------------------------------- guards
print("\n[guards] does it refuse to ship data?")
sand = tempfile.mkdtemp(prefix="ssa_push_")
proj = os.path.join(sand,"proj"); shutil.copytree(APP_DIR,proj,
    ignore=shutil.ignore_patterns("push","push.zip","__pycache__"))
open(os.path.join(proj,"schedule.db"),"wb").write(b"fake db")
r=subprocess.run([PY,"build_push.py"],cwd=proj,capture_output=True,text=True)
ck(r.returncode==0 and "schedule.db" not in "".join(
    zipfile.ZipFile(os.path.join(proj,"push.zip")).namelist()),
   "a stray schedule.db next to app.py is not picked up")

# force one in, confirm the guard actually fires
os.makedirs(os.path.join(proj,"push"),exist_ok=True)
src=open(os.path.join(proj,"build_push.py"),encoding="utf-8").read()
open(os.path.join(proj,"build_push.py"),"w",encoding="utf-8").write(
    src.replace('FILES = [', 'FILES = [\n    "schedule.db",'))
r=subprocess.run([PY,"build_push.py"],cwd=proj,capture_output=True,text=True)
ck(r.returncode!=0 and "REFUSING TO BUILD" in (r.stdout+r.stderr),
   "guard aborts the build if a .db ever gets in")
open(os.path.join(proj,"build_push.py"),"w",encoding="utf-8").write(src)

# missing-template detector
shutil.copy(os.path.join(proj,"templates","admin_import.html"), os.path.join(sand,"stash.html"))
os.remove(os.path.join(proj,"templates","admin_import.html"))
r=subprocess.run([PY,"build_push.py"],cwd=proj,capture_output=True,text=True)
ck("admin_import.html" in r.stdout and "NOT in push/" in r.stdout,
   "warns when app.py renders a template that isn't in the zip")
shutil.copy(os.path.join(sand,"stash.html"), os.path.join(proj,"templates","admin_import.html"))

# opt-in
r=subprocess.run([PY,"build_push.py","--with-shared"],cwd=proj,capture_output=True,text=True)
ck("push/schedule.txt" in zipfile.ZipFile(os.path.join(proj,"push.zip")).namelist(),
   "--with-shared does include schedule.txt")
ck("WILL overwrite" in r.stdout, "--with-shared warns that it overwrites the server copy")

# ---------------------------------------------------------------- deploy sim
print("\n[deploy] unzip over a live server and see what survives")
srv  = os.path.join(sand,"home"); code = os.path.join(srv,"schedule-backend")
data = os.path.join(srv,"data"); os.makedirs(data,exist_ok=True)
# old code = the ORIGINAL files, before today's changes
shutil.copytree("/mnt/user-data/uploads/Smart Schedule", code)
# live server state
LIVE_SHARED = "@community time = 10:05-10:30\n@dismiss normal = 15:00\n# EDITED BY ADMIN IN THE BROWSER\n"
open(os.path.join(code,"schedule.txt"),"w",encoding="utf-8").write(LIVE_SHARED)
open(os.path.join(code,"server_only.txt"),"w",encoding="utf-8").write("server-only file\n")
open(os.path.join(code,"HANDOFF.md"),"w",encoding="utf-8").write("# old doc\n")
dbp = os.path.join(data,"schedule.db")
con=sqlite3.connect(dbp)
con.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,
  name TEXT,username TEXT,class_year TEXT,grade INTEGER,password_hash TEXT NOT NULL,verified INTEGER DEFAULT 0,
  verify_code TEXT,schedule_text TEXT DEFAULT '',is_public INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime')))""")
for i in range(7):
    con.execute("INSERT INTO users (email,name,username,class_year,password_hash,verified,schedule_text)"
                " VALUES (?,?,?,?,?,1,?)",(f"2{i}s{i}@shadysideacademy.org",f"Student {i}",f"2{i}s{i}","27",
                generate_password_hash("pw123456"),f"Student {i}\n27 - Junior\n\n@term Spring 2026 = 01.20-06.30\nA - Bio - SC1 - 1 - T - R1 - green\n"))
con.commit(); con.close()
before_db = open(dbp,"rb").read()

# the documented deploy commands, run for real
subprocess.run([PY,"build_push.py"],cwd=APP_DIR,capture_output=True)
shutil.copy(os.path.join(APP_DIR, "push.zip"), os.path.join(srv,"push.zip"))
subprocess.run(["unzip","-o","-q","push.zip","-d","push_tmp"],cwd=srv,check=True)
subprocess.run(f'cp -r push_tmp/push/* schedule-backend/',cwd=srv,shell=True,check=True)
shutil.rmtree(os.path.join(srv,"push_tmp")); os.remove(os.path.join(srv,"push.zip"))

ck(open(os.path.join(code,"schedule.txt"),encoding="utf-8").read()==LIVE_SHARED,
   "admin's live schedule.txt survived the deploy untouched")
ck(open(dbp,"rb").read()==before_db, "database byte-identical after deploy")
ck(os.path.exists(os.path.join(code,"templates","admin_import.html")), "new templates landed")
ck("DB_PATH" in open(os.path.join(code,"app.py"),encoding="utf-8").read(), "new app.py landed")
ck(open(os.path.join(code,"server_only.txt"),encoding="utf-8").read()=="server-only file\n",
   "files that exist only on the server were left alone")
ck(os.path.exists(os.path.join(code,"HANDOFF.md")), "docs already on the server not deleted")

# does the deployed site actually work?
from cryptography.fernet import Fernet as _F
_KEY = _F.generate_key().decode()
env=dict(os.environ,DB_PATH=dbp,SECRET_KEY="x",ADMIN_PASSWORD="apw",BACKUP_KEY=_KEY)
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k,None)
probe = r'''
import app, json, os
from cryptography.fernet import Fernet
app.app.config["TESTING"]=True
c=app.app.test_client()
n=app.query("SELECT COUNT(*) n FROM users",fetch=True,one=True)["n"]
c.post("/admin/login",data={"password":"apw"})
# the export is encrypted now, so the probe has to open it the way Larry would
b=json.loads(Fernet(os.environ["BACKUP_KEY"].encode())
             .decrypt(c.get("/admin/export").data).decode())
sh=c.get("/api/schedule-shared").data.decode()
print(json.dumps({"users":n,"export":b["user_count"],"import_page":c.get("/admin/import").status_code,
                  "shared_ok":"EDITED BY ADMIN" in sh,"home":c.get("/admin").status_code}))
'''
r=subprocess.run([PY,"-c",probe],cwd=code,env=env,capture_output=True,text=True)
try: got=json.loads(r.stdout.strip().splitlines()[-1])
except Exception: got={}; print("      probe stderr:",r.stderr[-500:])
ck(got.get("users")==7, f"deployed app sees all 7 accounts ({got.get('users')})")
ck(got.get("export")==7, "export works on the deployed app")
ck(got.get("import_page")==200, "restore page works on the deployed app")
ck(got.get("shared_ok") is True, "app serves the admin's live schedule.txt, not a pushed one")
ck(got.get("home")==200, "admin dashboard renders")
ck(not os.path.exists(os.path.join(code,"schedule.db")), "no stray DB recreated in the code folder")

shutil.rmtree(sand,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
