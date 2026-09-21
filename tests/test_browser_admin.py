"""Drive the admin export/restore pages in a real browser."""
import os
import json, os, subprocess, sys, tempfile, time, signal, shutil

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from werkzeug.security import generate_password_hash
WORK = tempfile.mkdtemp(prefix="ssa_ba_"); DBP = os.path.join(WORK,"d","schedule.db")
from cryptography.fernet import Fernet
KEY = Fernet.generate_key().decode()
env = dict(os.environ, DB_PATH=DBP, SECRET_KEY="x", ADMIN_PASSWORD="apw",
           BACKUP_KEY=KEY)
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k,None)
os.makedirs(os.path.join(WORK,"d"), exist_ok=True)
import sqlite3


sys.path.insert(0,APP_DIR)
subprocess.run([sys.executable,"-c","import app"], cwd=APP_DIR, env=env, capture_output=True)
con = sqlite3.connect(DBP)
for e,n in [("27a@shadysideacademy.org","Alice A"),("28b@shadysideacademy.org","Bob B"),
            ("29c@shadysideacademy.org","Cara C")]:
    con.execute("INSERT INTO users (email,name,username,class_year,password_hash,verified,"
                "schedule_text,is_public) VALUES (?,?,?,?,?,1,?,1)",
                (e,n,e.split("@")[0],e[:2],generate_password_hash("pw123456"),
                 f"{n}\n{e[:2]} - Junior\n\n@term Spring 2026 = 01.20-06.30\nA - Bio - SC1 - 1 - T - R1 - green\n"))
con.commit(); con.close()

srv = subprocess.Popen([sys.executable,"-c","import app; app.app.run(port=5113, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(accept_downloads=True)
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:5113/admin/login")
        pg.fill('input[name=password]',"apw"); pg.click('button[type=submit]')
        pg.wait_for_load_state()
        ck(pg.is_visible("text=Export all data"), "dashboard shows Export button")

        with pg.expect_download() as dl:
            pg.click("text=Export all data")
        bpath = os.path.join(WORK,"backup.json.enc"); dl.value.save_as(bpath)
        blob = open(bpath, "rb").read()
        # The download is encrypted now. A guessed admin password gets you this
        # file; without BACKUP_KEY (which lives in the WSGI file on the server)
        # it is nothing.
        ck(blob[:1] not in (b"{", b"["), "the downloaded file is not readable JSON")
        data = json.loads(Fernet(KEY.encode()).decrypt(blob).decode("utf-8"))
        ck(data["user_count"] == 3, f"downloaded backup has 3 users")
        ck(dl.value.suggested_filename.startswith("ssa-schedule-backup_"),
           f"filename = {dl.value.suggested_filename}")

        # simulate disaster + a divergent edit
        con = sqlite3.connect(DBP)
        con.execute("DELETE FROM users WHERE email='29c@shadysideacademy.org'")
        con.execute("UPDATE users SET schedule_text='CHANGED SINCE BACKUP' "
                    "WHERE email='27a@shadysideacademy.org'")
        con.commit(); con.close()

        pg.click("text=Restore from a backup")
        ck(pg.is_visible("text=Export first"), "restore page warns you to export first")
        pg.set_input_files('input[name=backup]', bpath)
        pg.click('button[type=submit]'); pg.wait_for_load_state()

        ck(pg.is_visible("text=Confirm what to restore"), "preview page shown")
        boxes = pg.eval_on_selector_all(".pickbox","n=>n.map(x=>({s:x.dataset.status,c:x.checked}))")
        ck(len(boxes)==2, f"2 actionable rows (1 new + 1 conflict), got {len(boxes)}")
        ck(any(x["s"]=="new" and x["c"] for x in boxes), "new row pre-ticked")
        ck(any(x["s"]=="conflict" and not x["c"] for x in boxes), "conflict row NOT pre-ticked")
        ck(pg.is_visible("text=schedule_text"), "names the field that would be overwritten")

        pg.click("text=Untick everything"); pg.wait_for_timeout(150)
        ck(not any(pg.eval_on_selector_all(".pickbox","n=>n.map(x=>x.checked)")), "'Untick everything' works")
        pg.click("text=Tick all conflicts"); pg.wait_for_timeout(150)
        st = pg.eval_on_selector_all(".pickbox","n=>n.map(x=>({s:x.dataset.status,c:x.checked}))")
        ck(all(x["c"] for x in st if x["s"]=="conflict") and not any(x["c"] for x in st if x["s"]=="new"),
           "'Tick all conflicts' ticks only conflicts")
        pg.click("text=Tick all new"); pg.wait_for_timeout(150)
        ck(all(x["c"] for x in pg.eval_on_selector_all(".pickbox","n=>n.map(x=>({c:x.checked}))")),
           "'Tick all new' adds the new rows on top")

        pg.on("dialog", lambda d: d.accept())
        pg.click("text=Write the ticked accounts"); pg.wait_for_load_state(); pg.wait_for_timeout(400)
        body = pg.text_content("body")
        ck("1 account(s) created, 1 overwritten" in body, f"result message correct")
        ck("pre-import_" in body, "tells you where the safety snapshot went")

        con = sqlite3.connect(DBP); con.row_factory = sqlite3.Row
        rows = {r["email"]: r["schedule_text"] for r in con.execute("SELECT email,schedule_text FROM users")}
        con.close()
        ck(len(rows)==3, "deleted account is back")
        ck("CHANGED SINCE BACKUP" not in rows["27a@shadysideacademy.org"], "conflict overwritten with backup version")
        ck("Cara C" in rows["29c@shadysideacademy.org"], "restored account has its schedule")
        snaps=[f for f in os.listdir(os.path.join(WORK,"d")) if f.startswith("pre-import_")]
        ck(len(snaps)==1, f"snapshot file on disk next to the DB: {snaps}")
        snap=json.load(open(os.path.join(WORK,"d",snaps[0]),encoding="utf-8"))
        ck(any("CHANGED SINCE BACKUP" in (u["schedule_text"] or "") for u in snap["users"]),
           "snapshot captured the pre-import state (so a wrong import is undoable)")
        ck(not errs, f"no JS errors ({errs[:3]})")
        b.close()
finally:
    srv.send_signal(signal.SIGTERM); srv.wait(timeout=10); shutil.rmtree(WORK, ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
