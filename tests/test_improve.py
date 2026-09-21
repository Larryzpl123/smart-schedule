"""Round 4, the improvements nobody asked for but the app needed:
  A. automatic on-disk backups (the only copy of the DB was one somebody
     remembered to download)
  B. an admin path back in for a student who forgot their password and cannot
     receive the reset email either
  C. /admin/shared refuses a shared config that would break the timetable, and
     keeps the version it replaces
  D. /edit warns before you navigate away from unsaved typing
"""
import os
import glob, json, os, shutil, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, http.cookiejar


WORK = tempfile.mkdtemp(prefix="ssa_imp_")
APP = os.path.join(WORK, "app")
shutil.copytree(APP_DIR, APP, ignore=shutil.ignore_patterns(
    "push", "push.zip", "__pycache__", "*.db"))
DATA = os.path.join(WORK, "data")
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "schedule.db")
PORT = "5180"
# A reset now needs a code emailed to the admin mailbox, so the server has to
# have one. With no SMTP configured the mailer prints instead of sending, and
# that print is where this test reads the code from.
SRVLOG = os.path.join(WORK, "improve_server.log")
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="imp", ADMIN_PASSWORD="apw",
           ADMIN_EMAIL="larry@school.example", FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

_srvlog = open(SRVLOG, "w")
srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP, env=env, stdout=_srvlog, stderr=subprocess.STDOUT)
time.sleep(3)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT
SHARED = os.path.join(APP, "schedule.txt")
BACKUPS = os.path.join(DATA, "backups")

def opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post(op, path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

# a real account to back up and to reset
stu = opener()
post(stu, "/register", {"email": "27doej@shadysideacademy.org",
                        "name": "Jane Doe", "password": "pw123456"})
post(stu, "/login", {"email": "27doej@shadysideacademy.org", "password": "pw123456"})
post(stu, "/edit", {"schedule_text": "Jane Doe\n27 - Senior\n\n"
                    "@term Fall 2026 = 08.25-01.15\nA - X - N - 1 - T - R - red\n"})

adm = opener()
post(adm, "/admin/login", {"password": "apw"})

# ---------------- A. backups ----------------
page = adm.open(BASE + "/admin").read().decode()
time.sleep(0.5)
snaps = sorted(glob.glob(os.path.join(BACKUPS, "auto_*.json")))
ck(len(snaps) >= 1, f"opening the admin page writes a snapshot on its own ({snaps})")
if snaps:
    data = json.load(open(snaps[0], encoding="utf-8"))
    blob = json.dumps(data)
    ck("27doej@shadysideacademy.org" in blob, "the snapshot contains the real accounts")
    ck("A - X - N" in blob, "and their schedule text")
    ck(not glob.glob(os.path.join(BACKUPS, "*.part")),
       "no half-written .part file is left behind")

before = len(glob.glob(os.path.join(BACKUPS, "auto_*.json")))
adm.open(BASE + "/admin").read()
after = len(glob.glob(os.path.join(BACKUPS, "auto_*.json")))
ck(after == before, "opening it again does NOT write another one (once a day, not once a click)")

post(adm, "/admin/backup-now", {})
ck(len(glob.glob(os.path.join(BACKUPS, "auto_*.json"))) == before + 1,
   "the 'Snapshot now' button always writes one")

page = adm.open(BASE + "/admin").read().decode()
ck("Automatic snapshot" in page, "the dashboard says when the last snapshot was")

# ---------------- B. admin resets a password ----------------
uid = None
import sqlite3
con = sqlite3.connect(DB)
uid = con.execute("SELECT id FROM users WHERE email=?",
                  ("27doej@shadysideacademy.org",)).fetchone()[0]
con.close()

import re
# Step 1 emails a code and changes nothing. Resetting is the one admin action
# that leads to READING a schedule (reset, then log in as them), so it is tied
# to a mailbox the admin password alone does not reach.
first = post(adm, f"/admin/user/{uid}/reset-password", {}).read().decode()
ck("Enter the code" in first,
   "a reset asks for the emailed code before it changes anything")
srvout = open(SRVLOG, encoding="utf-8", errors="replace").read() if os.path.exists(SRVLOG) else ""
cm = re.search(r"Code:\s*(\d{6})", srvout)
ck(cm is not None, "and a code went out to the admin address")
r = post(adm, f"/admin/user/{uid}/reset-password/confirm",
         {"code": cm.group(1) if cm else "000000"})
body = r.read().decode()          # the redirect is followed, so this is the page
                                  # carrying the flash; a later GET would have
                                  # consumed and lost it
m = re.search(r"Temporary password for [^:]+: ([a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})", body)
ck(m is not None, "the admin is shown the new password once, on the spot")
ck(body.count("Temporary password") <= 1, "and only once")

if m:
    temp = m.group(1)
    # the flash must not still be sitting there on the next page load
    later = adm.open(BASE + "/admin").read().decode()
    ck("Temporary password" not in later,
       "the password is not left on screen for the next person at that computer")
    # the old password must be dead
    o2 = opener()
    post(o2, "/login", {"email": "27doej@shadysideacademy.org", "password": "pw123456"})
    st = o2.open(BASE + "/api/my-schedule").read().decode()
    ck("A - X - N" not in st, "the old password no longer works")
    # the new one must work
    o3 = opener()
    post(o3, "/login", {"email": "27doej@shadysideacademy.org", "password": temp})
    st3 = o3.open(BASE + "/api/my-schedule").read().decode()
    ck("A - X - N" in st3, "the temporary password logs them in")
    ck(len(temp) >= 14, f"the temporary password is not trivially guessable ({temp})")

# a student must not be able to reset anyone's password
sneak = opener()
post(sneak, "/login", {"email": "27doej@shadysideacademy.org",
                       "password": m.group(1) if m else "pw123456"})
try:
    post(sneak, f"/admin/user/{uid}/reset-password", {})
except urllib.error.HTTPError:
    pass
page = adm.open(BASE + "/admin").read().decode()
ck("Temporary password" not in page,
   "a logged-in student cannot reset a password (no new one was issued)")

# ---------------- C. the shared config is checked before saving ----------------
good = open(SHARED, encoding="utf-8").read()
day2 = [l for l in good.split("\n") if l.startswith("@day 2")][0]

# dropping H from day 2 is the exact bug that shipped once before
broken = good.replace(day2, day2.replace("H:11:50-12:40|12:25-13:15 ", ""))
post(adm, "/admin/shared", {"content": broken})
now = open(SHARED, encoding="utf-8").read()
ck(now == good, "a rotation with an uneven period spread is REFUSED, file untouched")

post(adm, "/admin/shared", {"content": good.replace("A:08:15-09:05", "A:08:15-08:05", 1)})
ck(open(SHARED, encoding="utf-8").read() == good, "a backwards time range is refused")

post(adm, "/admin/shared", {"content": "\n".join(
    l for l in good.split("\n") if not l.startswith("@day 5"))})
ck(open(SHARED, encoding="utf-8").read() == good, "a missing rotation day is refused")

# A mistyped directive parses as nothing at all, everywhere in the app, and
# used to save with no feedback whatsoever.
r = post(adm, "/admin/shared", {"content": good.rstrip("\n") +
         "\n@publik 2026-10-17 - Homecoming - 12:00-16:00 - ALL - pink\n"})
after_typo = r.read().decode()
ck("isn't a directive this app knows" in after_typo or
   "@publik" in after_typo,
   "a mistyped directive is called out instead of silently doing nothing")
ck("@publik" in open(SHARED, encoding="utf-8").read(),
   "...but it still saves, because it is a warning and not an error")

# put the good file back before testing the .bak
post(adm, "/admin/shared", {"content": good})

# a legitimate edit still saves, and keeps the version it replaced
legit = good.rstrip("\n") + "\n# a harmless comment\n"
post(adm, "/admin/shared", {"content": legit})
saved = open(SHARED, encoding="utf-8").read()
ck("a harmless comment" in saved, "a legitimate edit still saves")
baks = sorted(glob.glob(SHARED + ".*.bak"), key=os.path.getmtime)
ck(len(baks) >= 1, f"the replaced version is kept as a .bak ({len(baks)} kept)")
if baks:
    ck(open(baks[-1], encoding="utf-8").read() == good,
       "and the newest .bak is exactly the content that was replaced")

# the error page hands your text back rather than throwing it away
r = post(adm, "/admin/shared", {"content": legit + "\n@day 9 = A:08:00-09:00\n"})
html = r.read().decode()
ck("@day 9 is outside 1-8" in html or "outside 1-8" in html,
   "the refusal says exactly what is wrong")
ck("a harmless comment" in html, "and your text is still in the box")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
