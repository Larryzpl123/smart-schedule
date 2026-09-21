"""Migration guard + smoke test that nothing existing broke."""
import os
import io, os, subprocess, sys, tempfile, textwrap

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)

# ---- migration guard: DB_PATH set, but old DB still in the code dir --------
print("\n[guard] forgotten-migration warning")
work = tempfile.mkdtemp(prefix="ssa_guard_")
code = os.path.join(work, "code"); import shutil; shutil.copytree(APP_DIR, code)
open(os.path.join(code, "schedule.db"), "wb").write(b"")      # pretend old DB
env = dict(os.environ, DB_PATH=os.path.join(work, "data", "schedule.db"),
           SECRET_KEY="x", ADMIN_PASSWORD="")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k, None)
out = subprocess.run([sys.executable, "-c", "import app"], cwd=code, env=env,
                     capture_output=True, text=True)
ck(out.returncode == 0, "app still imports (warning, not a crash)")
ck("EMPTY database is about to be created" in out.stdout, "loud warning printed")
ck("cp '" in out.stdout, "warning tells you the exact cp command")

# quiet when there is nothing to warn about
os.remove(os.path.join(code, "schedule.db"))
out2 = subprocess.run([sys.executable, "-c", "import app"], cwd=code, env=env,
                      capture_output=True, text=True)
ck("EMPTY database" not in out2.stdout, "no false alarm when the old DB is gone")

# default (no DB_PATH) still behaves exactly as before
env3 = {k:v for k,v in env.items() if k != "DB_PATH"}
out3 = subprocess.run([sys.executable, "-c",
    "import app; print('DB=', app.DB)"], cwd=code, env=env3, capture_output=True, text=True)
ck("DB= schedule.db" in out3.stdout, "unset DB_PATH still defaults to schedule.db")
shutil.rmtree(work, ignore_errors=True)

# ---- smoke: the normal student flow still works end to end ----------------
print("\n[smoke] existing flows unaffected")
work2 = tempfile.mkdtemp(prefix="ssa_smoke_")
os.environ["DB_PATH"] = os.path.join(work2, "d", "schedule.db")
os.environ["SECRET_KEY"] = "x"; os.environ["ADMIN_PASSWORD"] = "apw"
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): os.environ.pop(k, None)
sys.path.insert(0, APP_DIR); os.chdir(APP_DIR)
import app as A
A.app.config["TESTING"]=True
c = A.app.test_client()

r = c.post("/register", data={"email":"27testx@shadysideacademy.org",
    "name":"Test X","password":"pw123456"}, follow_redirects=True)
ck(b"Account created" in r.data, "dev-mode register works (no email configured)")
r = c.post("/login", data={"email":"27testx@shadysideacademy.org",
    "password":"pw123456"}, follow_redirects=True)
ck(r.status_code == 200, "login works")

r = c.get("/edit")
b = r.data.decode()
ck(r.status_code == 200, "/edit renders")
ck("Download (.txt)" in b and "Load .txt" in b and "Import my Veracross PDF" in b,
   "edit page shows the download / .txt / PDF buttons")
ck("modeForm" in b and "addTerm" in b, "form/text mode editor still intact")
ck(b.count("<script") == b.count("</script>"), "no broken script tags")

txt = "Test X\n27 - Junior\n\n@term Spring 2026 = 01.20-06.30\nA - Bio - SC101 - 1 - Dr. Q - S 1 - green\n"
c.post("/edit", data={"schedule_text": txt, "is_public":"on"})
ck(c.get("/api/my-schedule").data.decode() == txt, "save + /api/my-schedule unchanged")
ck(c.get("/edit/export").data.decode() == txt, "export matches what /api/my-schedule serves")
# The student lookup was removed outright -- box and endpoints together.
ck(c.get("/api/student-index").status_code == 404, "the student index is gone")
ck(c.get("/api/student/1").status_code == 404, "and so is fetching one by id")
ck(c.get("/api/schedule-shared").status_code == 200, "shared schedule.txt still served")
ck(c.get("/api/calendar/2025-2026").status_code == 200, "calendar still served")

# admin pages render
c.post("/admin/login", data={"password":"apw"})
ck(b"Export all data" in c.get("/admin").data, "admin dashboard shows Export button")
ck(c.get("/admin/import").status_code == 200, "admin import page renders")
ck(c.get("/admin/shared").status_code == 200, "admin shared editor still renders")

import shutil as sh; sh.rmtree(work2, ignore_errors=True)
print("\n" + "="*60)
print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: " + "; ".join(FAIL))
print("="*60)
sys.exit(1 if FAIL else 0)
