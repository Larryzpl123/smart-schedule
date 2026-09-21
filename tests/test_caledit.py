"""A deploy wiped every anchor added through /admin.

build_push.py ships calendar/ as code, but /admin/anchor and
/admin/community-on were writing into calendar/<year>.txt on the server. So
uploading push.zip overwrote them with the laptop's older copy -- which is
exactly what happened on 2026-09-06, taking out the anchor that re-synced the
rotation after two rain days.

Admin edits now live in the database and are laid over the file when it is
served, so the file stays free to edit locally and push.
"""
import os, shutil, sqlite3, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, urllib.error, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_ce_")
APP = os.path.join(WORK, "app")
shutil.copytree(APP_DIR, APP, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db"))
DB = os.path.join(WORK, "schedule.db")
PORT = "5192"
CAL = os.path.join(APP, "calendar", "2026-2027.txt")
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ce", ADMIN_PASSWORD="apw",
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

def admin():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(
        BASE + "/admin/login",
        urllib.parse.urlencode({"password": "apw"}).encode()))
    return op

def post(op, path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

def cal():
    return urllib.request.urlopen(BASE + "/api/calendar/2026-2027").read().decode()

file_before = open(CAL, encoding="utf-8").read()
srv = start()
op = admin()

# ---- an anchor added through /admin ----------------------------------------
post(op, "/admin/anchor", {"date": "2026-09-11", "day": "4"})
ck("@anchor 2026-09-11 = 4" in cal(), "an anchor added in /admin is served")
ck("@anchor 2026-09-11" not in open(CAL, encoding="utf-8").read(),
   "...without touching the calendar FILE (that is what got overwritten)")

# ---- and a community override ----------------------------------------------
post(op, "/admin/community-on", {"date": "2026-09-10", "label": "Class Meeting",
                                 "times": ""})
ck("@community_on 2026-09-10 = Class Meeting" in cal(),
   "a community override is served too")

# ---- the deploy that caused all this ---------------------------------------
# Overwrite calendar/ with the laptop's copy, exactly like unzipping push.zip.
stop(srv)
open(CAL, "w", encoding="utf-8").write(file_before)
srv = start()
body = cal()
ck("@anchor 2026-09-11 = 4" in body,
   "a push that overwrites calendar/ no longer wipes the anchor")
ck("@community_on 2026-09-10 = Class Meeting" in body,
   "or the community override")

# ---- editing one replaces it, it does not pile up --------------------------
op = admin()
post(op, "/admin/anchor", {"date": "2026-09-11", "day": "6"})
body = cal()
ck(body.count("@anchor 2026-09-11") == 1 and "= 6" in body,
   f"changing an anchor replaces it ({body.count('@anchor 2026-09-11')} lines)")

# ---- an admin edit beats the same date in the file -------------------------
existing = [l for l in file_before.split("\n") if l.strip().startswith("@anchor ")]
ck(bool(existing), f"the year file ships with anchors of its own ({len(existing)})")
if existing:
    d = existing[0].split()[1]
    was = existing[0].strip().split("=")[1].strip()
    other = "8" if was != "8" else "1"
    post(op, "/admin/anchor", {"date": d, "day": other})
    body = cal()
    ck(f"@anchor {d} = {other}" in body and f"@anchor {d} = {was}" not in body,
       f"an admin edit overrides the file's own line for {d} ({was} -> {other})")
    ck(body.count(f"@anchor {d}") == 1, "and does not leave both lines behind")

# ---- an empty label means "no community block that day" --------------------
post(op, "/admin/community-on", {"date": "2026-09-24", "label": "", "times": ""})
ck("@community_on 2026-09-24 =" in cal(),
   "clearing community time for a date still writes a line (empty = none)")

# ---- /admin shows what the app will actually see ---------------------------
home = op.open(BASE + "/admin").read().decode()
ck("2026-09-11" in home, "the admin page lists the anchors it stored")

# ---- and a snapshot actually contains them ---------------------------------
post(op, "/admin/backup-now", {})
import glob, json
baks = sorted(glob.glob(os.path.join(APP, "data", "backups", "*.json")) +
              glob.glob(os.path.join(WORK, "**", "backups", "*.json"), recursive=True))
if baks:
    data = json.load(open(baks[-1], encoding="utf-8"))
    got = data.get("calendar_edits") or []
    ck(any(e["date"] == "2026-09-11" for e in got),
       f"a backup carries the admin's calendar edits ({len(got)} of them)")
else:
    print("  SKIP  no backup file found to inspect")

# ---- they survive a restart ------------------------------------------------
stop(srv)
srv = start()
ck("@anchor 2026-09-11 = 6" in cal(), "and they survive a restart")

con = sqlite3.connect(DB)
n = con.execute("SELECT COUNT(*) FROM calendar_edits").fetchone()[0]
con.close()
ck(n >= 4, f"all of them are rows in calendar_edits ({n})")
stop(srv)

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
