"""When pdfplumber is absent the app must say SO, not blame the student's file."""
import os
import io, json, os, sys, tempfile, shutil, subprocess

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
# The Veracross PDFs these check against are real student schedules and
# are not in this repository. Without them there is nothing to parse, so
# skip rather than report a failure that is not one.
import glob as _g, os as _o, sys as _s
_dir = _o.environ.get("SSA_PDFS", "/tmp/pdfs")
if not _g.glob(_o.path.join(_dir, "*.pdf")):
    print("  SKIP  no sample PDFs (set SSA_PDFS to a folder of them)")
    _s.exit(0)


WORK=tempfile.mkdtemp(prefix="ssa_pm_"); CODE=os.path.join(WORK,"code")
shutil.copytree(APP_DIR,CODE,ignore=shutil.ignore_patterns("push","push.zip","__pycache__"))
# make "import pdfplumber" fail, exactly like a server where nobody ran pip
open(os.path.join(CODE,"blockpdf.py"),"w").write(
    "import sys, importlib.abc\n"
    "class B(importlib.abc.MetaPathFinder):\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'pdfplumber':\n"
    "            raise ImportError(\"No module named 'pdfplumber'\")\n"
    "        return None\n"
    "sys.meta_path.insert(0, B())\n")

PROBE = '''
%(block)s
import json, io, os
import app
app.app.config["TESTING"]=True
c=app.app.test_client()
from werkzeug.security import generate_password_hash



app.query("INSERT INTO users (email,name,username,password_hash,verified) VALUES (?,?,?,?,1)",
  ("27doej@shadysideacademy.org","P","27doej",generate_password_hash("pw123456")))
uid=app.query("SELECT id FROM users WHERE email=?",("27doej@shadysideacademy.org",),fetch=True,one=True)["id"]
with c.session_transaction() as s: s["user_id"]=uid
blob=%(blob)s
r=c.post("/edit/import-pdf",data={"pdf":(io.BytesIO(blob),"s.pdf")},content_type="multipart/form-data")
c.post("/admin/login",data={"password":"apw"},follow_redirects=True)
adm=c.get("/admin").data.decode()
print("@@"+json.dumps({"status":r.status_code,"body":json.loads(r.data),
                       "admin_ok":"Admin dashboard" in adm,
                       "admin_warns":"PDF import is not working" in adm,
                       "backend":app._pdf_backend_problem()}))
'''
def run(block, blob, tag):
    w=tempfile.mkdtemp()
    env=dict(os.environ,DB_PATH=os.path.join(w,"d","schedule.db"),SECRET_KEY="x",ADMIN_PASSWORD="apw")
    for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k,None)
    src=PROBE % {"block": block, "blob": blob}
    r=subprocess.run([sys.executable,"-c",src],cwd=CODE,env=env,capture_output=True,text=True)
    shutil.rmtree(w,ignore_errors=True)
    line=[l for l in r.stdout.splitlines() if l.startswith("@@")]
    if not line:
        print(f"   [{tag}] stderr:", r.stderr[-500:]); return {}
    return json.loads(line[-1][2:])

GOOD='open(os.environ.get("SSA_PDFS","/tmp/pdfs") + "/sample-schedule.pdf","rb").read()'

print("\n[pdfplumber missing]")
g=run("import blockpdf", GOOD, "missing")
ck(g.get("status")==503, f"503, not 400 (got {g.get('status')})")
err=g.get("body",{}).get("error","")
ck("pdfplumber" in err, f"names pdfplumber: {err[:80]!r}")
ck("pip3 install --user pdfplumber" in err, "gives the exact command")
ck("Send it to Larry" not in err, "does NOT tell the student to blame their file")
ck(g.get("admin_ok") is True, "admin page rendered")
ck(g.get("admin_warns") is True, f"admin banner shown (backend said: {str(g.get('backend'))[:60]!r})")

print("\n[pdfplumber present — the very PDF that failed]")
g2=run("pass", GOOD, "present")
ck(g2.get("status")==200, f"200 (got {g2.get('status')})")
ck(g2.get("body",{}).get("count")==8, f"8 courses ({g2.get('body',{}).get('count')})")
ck(g2.get("admin_warns") is False, "no admin banner when it works")
ck(g2.get("backend") is None, "backend reports no problem")

print("\n[a genuinely broken PDF still names the reason]")
g3=run("pass", 'b"%PDF-1.4 total nonsense"', "broken")
ck(g3.get("status")==400, f"400 (got {g3.get('status')})")
e3=g3.get("body",{}).get("error","")
ck("Could not read that PDF" in e3 and "(" in e3, f"carries the underlying reason: {e3[:110]!r}")
ck("pdfplumber" not in e3, "and does not misreport it as a missing install")

shutil.rmtree(WORK,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
