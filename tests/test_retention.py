"""Snapshots are capped by size, not only by count.

Every snapshot holds every account, so "keep the newest 30" grows with the
school: at roughly 2.9 KB of schedule text per student that is about 1.3 MB a
snapshot for 300 students and 2.6 MB for 600, so 30 kept copies would be 39 MB
and 77 MB of a small hosting quota. The count still applies; whichever limit
bites first wins.

Also: school-wide @public events never expire, and after a couple of years the
admin page is a list of homecomings nobody remembers. That is tidiness, not
disk, so it is a warning rather than anything automatic.
"""
import os, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


sys.path.insert(0, APP_DIR)
WORK = tempfile.mkdtemp(prefix="ssa_rt_")
os.environ["DB_PATH"] = os.path.join(WORK, "schedule.db")
os.environ["SECRET_KEY"] = "rt"
os.environ["ADMIN_PASSWORD"] = "apw"
os.environ["BACKUP_DIR"] = os.path.join(WORK, "backups")
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    os.environ.pop(k, None)
os.chdir(APP_DIR)
import app


FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BAK = os.environ["BACKUP_DIR"]
os.makedirs(BAK, exist_ok=True)

def make_files(n, size):
    for f in os.listdir(BAK):
        os.remove(os.path.join(BAK, f))
    for i in range(n):
        p = os.path.join(BAK, f"auto_2026-01-{i+1:02d}_000000_auto.json")
        with open(p, "wb") as fh:
            fh.write(b"x" * size)
        os.utime(p, (1000000 + i * 60, 1000000 + i * 60))   # oldest first
    return sorted(os.listdir(BAK))

def kept():
    return sorted(os.listdir(BAK))

# ---- the count limit still works on its own --------------------------------
make_files(10, 1000)
app._prune(os.path.join(BAK, "auto_*.json"), keep=4)
ck(len(kept()) == 4, f"the count limit keeps 4 of 10 ({len(kept())})")
ck(kept()[-1].startswith("auto_2026-01-10"),
   f"and keeps the NEWEST ones ({kept()})")

# ---- the size limit bites first when files are big -------------------------
make_files(10, 1_000_000)                       # 10 MB in total
app._prune(os.path.join(BAK, "auto_*.json"), keep=30, max_bytes=3_500_000)
n = len(kept())
total = sum(os.path.getsize(os.path.join(BAK, f)) for f in kept())
ck(n == 3, f"a 3.5 MB cap keeps 3 of ten 1 MB snapshots ({n})")
ck(total <= 3_500_000, f"and the folder really is under the cap ({total} bytes)")
ck(kept()[-1].startswith("auto_2026-01-10"), f"newest kept ({kept()})")

# ---- one snapshot bigger than the whole cap is still kept ------------------
# Deleting the only backup because it is large is worse than being over quota.
make_files(1, 9_000_000)
app._prune(os.path.join(BAK, "auto_*.json"), keep=30, max_bytes=1_000_000)
ck(len(kept()) == 1, "the newest snapshot is never deleted for being too big")

# ---- and the real path uses both -------------------------------------------
ck(app.BACKUP_KEEP == 30 and app.BACKUP_MAX_MB == 40,
   f"defaults: {app.BACKUP_KEEP} files or {app.BACKUP_MAX_MB} MB, "
   f"whichever comes first")

for f in os.listdir(BAK):
    os.remove(os.path.join(BAK, f))
app.write_backup("auto")
ck(len(kept()) == 1, "a real snapshot still gets written after all that")

# ---- stale school-wide events are pointed out, not deleted -----------------
base = open(os.path.join(APP_DIR, "schedule.txt"), encoding="utf-8").read()
old_line = "@public 2019-10-17 - Homecoming 2019 - 12:00-16:00 - ALL - pink"
new_line = ("@public " + time.strftime("%Y-%m-%d")
            + " - Tailgate - 17:00-19:00 - ALL - pink")

errs, warns = app.check_shared(base + "\n" + new_line + "\n")
ck(not errs, f"a fresh event is fine ({errs})")
ck(not any("more than a year old" in w for w in warns),
   f"and raises nothing about age ({warns})")

errs, warns = app.check_shared(base + "\n" + old_line + "\n" + new_line + "\n")
ck(not errs, "an old event is not an error -- it still works")
ck(any("more than a year old" in w and "2019-10-17" in w for w in warns),
   f"but the admin is told which one is stale ({warns})")

# ---- the disk report on the admin page is measured, not assumed ------------
# The 40 MB cap was first sized against the built-in sample's 2.9 KB schedule.
# A real one with events and homework on it is bigger, and the projection has
# to follow the real average rather than that number.
import sqlite3
app.ensure_schema()
big = "Jane Doe\n27 - Senior\n" + ("A - Something - X - 1 - T - R - red\n" * 200)
con = sqlite3.connect(os.environ["DB_PATH"])
for i in range(5):
    con.execute("INSERT INTO users (email,name,password_hash,schedule_text) "
                "VALUES (?,?,?,?)", (f"{i}@shadysideacademy.org", "N", "x", big))
con.commit(); con.close()

r = app.storage_report()
ck(not r.get("error"), f"the report does not blow up ({r.get('error')})")
ck(r["users"] == 5, f"it counts the accounts ({r['users']})")
ck(abs(r["avg_text"] - len(big)) < 50,
   f"the average schedule size is measured off the real rows "
   f"({r['avg_text']} vs {len(big)})")

# ...and off the rows that HAVE a schedule. On the live server 5 of 11
# accounts were empty, so the average came out 557 bytes when the six real
# ones averaged about a thousand -- and the projection underneath asks what
# 600 accounts WITH schedules would cost. Half the answer to the wrong
# question.
con = sqlite3.connect(os.environ["DB_PATH"])
for i in range(5):
    con.execute("INSERT INTO users (email,name,password_hash,schedule_text) "
                "VALUES (?,?,?,?)", (f"empty{i}@shadysideacademy.org", "N", "x", ""))
con.commit(); con.close()
r2 = app.storage_report()
ck(r2["users"] == 10 and r2["filled"] == 5,
   f"five more accounts, none of them with a schedule ({r2['users']}, {r2['filled']})")
ck(abs(r2["avg_text"] - r["avg_text"]) < 2,
   f"and the average does not move -- empty rows are not schedules of size "
   f"zero, they are accounts that have not filled one in "
   f"({r2['avg_text']} vs {r['avg_text']})")
ck(r["per_user"] > r["avg_text"], "and the per-account figure adds the rest of the row")
projected = {p["users"]: p for p in r["projected"]}
ck(projected[600]["db_mb"] > projected[100]["db_mb"] * 5,
   "the projection scales with the number of accounts")
ck(projected[600]["kept_mb"] <= r["cap_mb"] + 0.01,
   f"and never projects more snapshot disk than the cap allows "
   f"({projected[600]['kept_mb']} vs {r['cap_mb']})")
ck(r["worst_mb"] < r["quota_mb"],
   f"worst case still fits the quota ({r['worst_mb']} of {r['quota_mb']} MB)")

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
