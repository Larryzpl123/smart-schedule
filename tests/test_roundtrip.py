"""Round-trip test: export -> wipe -> import -> compare. Plus DB_PATH checks."""
import os
import io, json, os, shutil, sqlite3, sys, tempfile

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_rt_")
DATA = os.path.join(WORK, "data")          # DB deliberately OUTSIDE the code dir
os.makedirs(DATA, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(DATA, "schedule.db")
os.environ["ADMIN_PASSWORD"] = "admin-pw-for-test"
# The export is encrypted now; with no key it is refused outright rather than
# quietly falling back to plain text, so the round trip needs one.
from cryptography.fernet import Fernet as _F
os.environ["BACKUP_KEY"] = _F.generate_key().decode()
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("SMTP_HOST", None); os.environ.pop("SMTP_USER", None); os.environ.pop("SMTP_PASS", None)

sys.path.insert(0, APP_DIR); os.chdir(APP_DIR)
import app as A
from werkzeug.security import generate_password_hash


FAIL = []
def ck(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: FAIL.append(label)

# ---------------------------------------------------------------- seed data
SEED = [
    ("27doej@shadysideacademy.org", "Jane Doe", "27doej", "27", 11,
     "Jane Doe\n27 - Junior\n\n@term Spring 2026 = 01.20-06.30\nA - AP Calculus BC - MA560 - 1 - Mrs. Yam - R 202 - red\nD - Robotics - CS301 - 1 - Ms. Wang - GH 031 - orange\n\n@pref time_format = 12h\n", 1, 1),
    ("28smithj@shadysideacademy.org", "Jane Smith", "28smithj", "28", 10,
     "Jane Smith\n28 - Sophomore\n\n@term Fall 2025 = 08.26-01.16\nB - Chemistry - SC220 - 2 - Dr. Lee - S 14 - teal\n", 1, 1),
    ("29chenx@shadysideacademy.org", "Xu Chen", "29chenx", "29", 9,
     "", 0, 0),   # unverified, no schedule, not searchable -- edge case
    ("27oyang-li@shadysideacademy.org", "Ouyang Li", "27oyang-li", "27", None,
     "Ouyang Li\n27 - Junior\n\n@term Spring 2026 = 01.20-06.30\nC - 中文 5 - CH500 - 1 - 王老师 - L 7 - purple\n", 1, 1),  # unicode + NULL grade
]
for email, name, uname, cy, grade, sched, ver, pub in SEED:
    A.query("INSERT INTO users (email,name,username,class_year,grade,password_hash,"
            "verified,schedule_text,is_public) VALUES (?,?,?,?,?,?,?,?,?)",
            (email, name, uname, cy, grade, generate_password_hash("pw123456"),
             ver, sched, pub))

def snapshot():
    rows = A.query(f"SELECT {', '.join(A.BACKUP_COLUMNS)} FROM users ORDER BY email", fetch=True)
    return [{c: r[c] for c in A.BACKUP_COLUMNS} for r in rows]

before = snapshot()
print(f"\nseeded {len(before)} users into {os.environ['DB_PATH']}")

A.app.config["TESTING"] = True
c = A.app.test_client()

# ---------------------------------------------------------------- ② DB_PATH
print("\n[2] DB_PATH")
ck(os.path.exists(os.environ["DB_PATH"]), "DB created at DB_PATH, outside the code dir")
ck(not os.path.exists(os.path.join(APP_DIR, "schedule.db")), "no stray schedule.db in the code dir")

# ---------------------------------------------------------------- admin auth
print("\n[auth] export/import must be admin-only")
ck(c.get("/admin/export").status_code in (302, 401, 403), "/admin/export blocked when not admin")
ck(c.get("/admin/import").status_code in (302, 401, 403), "/admin/import blocked when not admin")
ck(c.post("/admin/import/apply").status_code in (302, 401, 403), "/admin/import/apply blocked when not admin")
c.post("/admin/login", data={"password": "admin-pw-for-test"})
ck(c.get("/admin/export").status_code == 200, "admin can export after login")

# ---------------------------------------------------------------- ① export
print("\n[1a] export")
r = c.get("/admin/export")
ck("attachment" in r.headers.get("Content-Disposition", ""), "export downloads as attachment")
ck(r.data[:1] not in (b"{", b"["),
   "and what it hands over is not readable without the key -- a guessed "
   "admin password must not be a way to read everyone's schedule")
backup = json.loads(_F(os.environ["BACKUP_KEY"].encode()).decrypt(r.data).decode("utf-8"))
ck(backup["user_count"] == len(SEED), f"export has all {len(SEED)} users")
ck(all("verify_code" not in u for u in backup["users"]), "verify_code NOT leaked into the backup")
ck(any("中文" in (u["schedule_text"] or "") for u in backup["users"]), "unicode survives export")
ck(all(u["password_hash"] for u in backup["users"]), "password hashes present (accounts stay usable)")
raw = json.dumps(backup, ensure_ascii=False, indent=2)

# ---------------------------------------------------------- wipe + restore
print("\n[1b] wipe the database, then restore from the file")
A.query("DELETE FROM users")
ck(A.query("SELECT COUNT(*) n FROM users", fetch=True, one=True)["n"] == 0, "database is empty")

r = c.post("/admin/import", data={"backup": (io.BytesIO(raw.encode()), "backup.json")},
           content_type="multipart/form-data")
body = r.data.decode()
ck(r.status_code == 200 and "Confirm what to restore" in body, "preview page renders")
ck(body.count('name="apply"') == len(SEED), f"all {len(SEED)} rows offered as new")

r = c.post("/admin/import/apply", data={"apply": [u["email"] for u in backup["users"]]},
           follow_redirects=True)
ck(r.status_code == 200, "apply succeeded")
after = snapshot()
ck(len(after) == len(before), f"row count restored ({len(after)}/{len(before)})")
ck(after == before, "every field byte-identical after round trip")
if after != before:
    for a, b in zip(after, before):
        for k in a:
            if a[k] != b[k]: print(f"      DIFF {b['email']}.{k}: {b[k]!r} -> {a[k]!r}")
ck(os.path.exists(os.path.join(DATA, "schedule.db")), "DB still at DB_PATH")
snaps = [f for f in os.listdir(DATA) if f.startswith("pre-import_")]
ck(len(snaps) == 1, f"pre-import safety snapshot written ({snaps})")

# ---------------------------------------------------------- conflict handling
print("\n[1c] conflicts are not applied unless ticked")
A.query("UPDATE users SET schedule_text=? WHERE email=?",
        ("EDITED SINCE THE BACKUP\n", SEED[0][0]))
r = c.post("/admin/import", data={"backup": (io.BytesIO(raw.encode()), "backup.json")},
           content_type="multipart/form-data")
body = r.data.decode()
ck("conflict" in body and "schedule_text" in body, "conflict is shown with the changed field")
ck(body.count('name="apply"') == 1, "only the conflicting row is tickable (rest identical)")

r = c.post("/admin/import/apply", data={}, follow_redirects=True)   # tick nothing
live = A.query("SELECT schedule_text FROM users WHERE email=?", (SEED[0][0],), fetch=True, one=True)
ck(live["schedule_text"] == "EDITED SINCE THE BACKUP\n", "ticking nothing changes nothing")

r = c.post("/admin/import", data={"backup": (io.BytesIO(raw.encode()), "backup.json")},
           content_type="multipart/form-data")
c.post("/admin/import/apply", data={"apply": [SEED[0][0]]}, follow_redirects=True)
live = A.query("SELECT schedule_text FROM users WHERE email=?", (SEED[0][0],), fetch=True, one=True)
ck(live["schedule_text"] == SEED[0][5], "ticking the conflict restores the backup version")

# ---------------------------------------------------------- bad input
print("\n[1d] bad files are rejected, not half-applied")
for label, blob in [("not JSON", b"hello world"),
                    ("wrong format tag", json.dumps({"format": "something-else"}).encode()),
                    ("wrong version", json.dumps({"format": A.BACKUP_FORMAT, "version": 99, "users": []}).encode()),
                    ("record with no email", json.dumps({"format": A.BACKUP_FORMAT, "version": 1,
                        "users": [{"name": "x", "password_hash": "h"}]}).encode()),
                    ("record with no password hash", json.dumps({"format": A.BACKUP_FORMAT, "version": 1,
                        "users": [{"email": "a@b.c"}]}).encode())]:
    n_before = A.query("SELECT COUNT(*) n FROM users", fetch=True, one=True)["n"]
    r = c.post("/admin/import", data={"backup": (io.BytesIO(blob), "bad.json")},
               content_type="multipart/form-data", follow_redirects=True)
    n_after = A.query("SELECT COUNT(*) n FROM users", fetch=True, one=True)["n"]
    ck(n_after == n_before and "Confirm what to restore" not in r.data.decode(),
       f"rejected: {label}")

# ---------------------------------------------------------- student export
print("\n[1e] student can download their own schedule")
s = A.app.test_client()
ck(s.get("/edit/export").status_code in (302, 401), "/edit/export blocked when logged out")
uid = A.query("SELECT id FROM users WHERE email=?", (SEED[0][0],), fetch=True, one=True)["id"]
with s.session_transaction() as sess:
    sess["user_id"] = uid
r = s.get("/edit/export")
ck(r.status_code == 200, "logged-in student gets 200")
ck("attachment" in r.headers.get("Content-Disposition", ""), "downloads as attachment")
ck("27doej" in r.headers.get("Content-Disposition", ""), "filename uses their username")
ck(r.data.decode("utf-8") == SEED[0][5], "downloaded .txt is byte-identical to what's stored")

# a student must NOT reach admin backup routes
ck(s.get("/admin/export").status_code in (302, 401, 403), "student cannot hit /admin/export")

print("\n" + "=" * 60)
print(f"{'ALL PASSED' if not FAIL else str(len(FAIL)) + ' FAILED: ' + '; '.join(FAIL)}")
print("=" * 60)
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAIL else 0)
