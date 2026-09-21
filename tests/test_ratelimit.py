"""Task 3: do the limits actually block, and do they survive a restart?"""
import os, sqlite3, subprocess, sys, tempfile, time, shutil, json

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_rl_"); DBP = os.path.join(WORK,"d","schedule.db")
os.environ.update(DB_PATH=DBP, SECRET_KEY="x", ADMIN_PASSWORD="apw",
                  MAX_CODES_PER_DAY="6", CODE_TTL_SECONDS="2")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): os.environ.pop(k,None)
sys.path.insert(0,APP_DIR); os.chdir(APP_DIR)
import app as A, mailer


SENT=[]
mailer.is_enabled = lambda: True                       # pretend SMTP is on
mailer.send_code  = lambda to,c: (SENT.append((to,c)), (True,"sent"))[1]

FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)
A.app.config["TESTING"]=True
def C(): return A.app.test_client()
def reg(c,email,pw="pw123456"):
    return c.post("/register",data={"email":email,"name":"T","password":pw},follow_redirects=True)
def code_for(email):
    return A.query("SELECT verify_code FROM users WHERE email=?",(email,),fetch=True,one=True)["verify_code"]

E1="27aaa@shadysideacademy.org"

# ------------------------------------------------------------ sendcode 5/hr
print("\n[sendcode] 5 per hour per address")
c=C()
oks=[b"emailed you a 6-digit code" in reg(c,E1).data for _ in range(5)]
ck(all(oks), "first 5 register/send attempts go through")
ck(len(SENT)==5, f"5 emails actually sent ({len(SENT)})")
r=reg(c,E1)
ck(b"Too many codes" in r.data, "6th is blocked")
ck(len(SENT)==5, "and no 6th email was sent")

print("\n[sendcode] a blocked attempt must not change stored state")
hash_before=A.query("SELECT password_hash FROM users WHERE email=?",(E1,),fetch=True,one=True)["password_hash"]
reg(c,E1,pw="attacker-password")
hash_after=A.query("SELECT password_hash FROM users WHERE email=?",(E1,),fetch=True,one=True)["password_hash"]
ck(hash_before==hash_after, "blocked register did NOT overwrite the password hash")

# ------------------------------------------------------------ code expiry
print("\n[expiry] codes stop working after CODE_TTL_SECONDS (2s here)")
A.clear_limit("sendcode",E1)
SENT.clear(); reg(c,E1); code=code_for(E1)
r=c.post("/verify",data={"email":E1,"code":code},follow_redirects=True)
ck(b"Verified" in r.data, "a fresh code verifies")

E2="27bbb@shadysideacademy.org"
reg(c,E2); code2=code_for(E2)
time.sleep(2.2)
r=c.post("/verify",data={"email":E2,"code":code2},follow_redirects=True)
ck(b"has expired" in r.data, "the same code 2s later is rejected as expired")
ck(A.query("SELECT verified FROM users WHERE email=?",(E2,),fetch=True,one=True)["verified"]==0,
   "account stayed unverified")

print("\n[expiry] a row from before this feature (code_expires NULL) is not valid forever")
A.query("UPDATE users SET verify_code='123456', code_expires=NULL WHERE email=?",(E2,))
A.clear_limit("verifycode",E2)
r=c.post("/verify",data={"email":E2,"code":"123456"},follow_redirects=True)
ck(b"expired" in r.data, "NULL code_expires is treated as expired, not as valid")

# ------------------------------------------------------------ verifycode 10/15min
print("\n[verifycode] 10 guesses per 15 min")
A.clear_limit("verifycode",E2); A.clear_limit("sendcode",E2)
A.query("UPDATE users SET verify_code='999999', code_expires=? WHERE email=?",(time.time()+9999,E2))
blocked=None
for i in range(12):
    r=c.post("/verify",data={"email":E2,"code":f"{i:06d}"},follow_redirects=True)
    if b"Too many attempts" in r.data: blocked=i; break
ck(blocked==10, f"blocked on guess #11 (0-indexed {blocked})")
r=c.post("/verify",data={"email":E2,"code":"999999"},follow_redirects=True)
ck(b"Verified" not in r.data, "even the CORRECT code is refused while rate-limited")

# ------------------------------------------------------------ login 8/15min
print("\n[login] 8 per 15 min, cleared on success")
E3="27ccc@shadysideacademy.org"
A.query("INSERT INTO users (email,name,username,password_hash,verified) VALUES (?,?,?,?,1)",
        (E3,"C","27ccc",__import__("werkzeug.security",fromlist=["x"]).generate_password_hash("rightpw")))
c3=C()
for i in range(7):
    c3.post("/login",data={"email":E3,"password":"wrong"},follow_redirects=True)
r=c3.post("/login",data={"email":E3,"password":"rightpw"},follow_redirects=True)
ck(b"Too many login" not in r.data, "8th attempt (correct password) still allowed")
with c3.session_transaction() as s: logged=("user_id" in s)
ck(logged, "logged in")
ck(A.query("SELECT COUNT(*) c FROM rate_limits WHERE action='login' AND who=?",(E3,),
           fetch=True,one=True)["c"]==0, "successful login cleared the counter")
c4=C()
outs=[c4.post("/login",data={"email":E3,"password":"wrong"},follow_redirects=True) for _ in range(9)]
ck(b"Too many login" in outs[-1].data, "9th consecutive wrong password is blocked")

# ------------------------------------------------------------ admin 8/15min
print("\n[adminlogin] 8 per 15 min")
ca=C()
for _ in range(8): ca.post("/admin/login",data={"password":"nope"},follow_redirects=True)
r=ca.post("/admin/login",data={"password":"apw"},follow_redirects=True)
ck(b"Too many attempts" in r.data, "correct admin password blocked once the limit is hit")
A.clear_limit("adminlogin","127.0.0.1"); A.clear_limit("adminlogin","unknown")
r=ca.post("/admin/login",data={"password":"apw"},follow_redirects=True)
ck(b"Admin dashboard" in r.data, "works again after the counter is cleared")

# ------------------------------------------------------------ global daily cap
print("\n[global cap] rotating addresses must NOT get around it (MAX=6 here)")
A.query("DELETE FROM rate_limits"); SENT.clear()
cg=C(); results=[]
for i in range(9):
    results.append(reg(cg,f"27z{i:02d}@shadysideacademy.org").data)   # 9 DIFFERENT addresses
ck(len(SENT)==6, f"exactly 6 emails sent, then it stopped ({len(SENT)})")
ck(b"daily limit" in results[6], "7th address is told the site hit its daily limit")
ck(all(b"daily limit" in r for r in results[6:]), "and every one after that too")
ck(A.codes_sent_today()==6, "admin-visible counter reads 6")

print("\n[global cap] admin can see it and reset it")
ca=C(); A.clear_limit("adminlogin","127.0.0.1"); A.clear_limit("adminlogin","unknown")
ca.post("/admin/login",data={"password":"apw"})
b=ca.get("/admin").data
ck(b"Daily email cap reached" in b, "admin dashboard shows the cap banner")
ca.post("/admin/reset-send-cap",follow_redirects=True)
ck(A.codes_sent_today()==0, "reset button zeroes the counter")
SENT.clear(); reg(C(),"27zz9@shadysideacademy.org")
ck(len(SENT)==1, "sending works again after the reset")

# ------------------------------------------------------------ survives restart
print("\n[restart] this is why the counters are in SQLite, not in memory")
A.query("DELETE FROM rate_limits")
for _ in range(4): reg(C(),E1) if False else None
cR=C()
for _ in range(4): cR.post("/login",data={"email":E3,"password":"wrong"},follow_redirects=True)
n_before=A.query("SELECT COUNT(*) c FROM rate_limits WHERE action='login'",fetch=True,one=True)["c"]
ck(n_before==4, f"4 failed logins recorded ({n_before})")
probe='''
import app, json
n=app.query("SELECT COUNT(*) c FROM rate_limits WHERE action='login'",fetch=True,one=True)["c"]
print(json.dumps({"n":n}))
'''
r=subprocess.run([sys.executable,"-c",probe],cwd=APP_DIR,
                 env=dict(os.environ),capture_output=True,text=True)
got=json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
ck(got.get("n")==4, f"a FRESH process still sees all 4 (in-memory would read 0) -> {got.get('n')}")

# ------------------------------------------------------------ resend is POST
print("\n[resend] must not be reachable by GET")
ck(C().get("/verify/resend?email="+E1).status_code==405, "GET /verify/resend -> 405")
A.query("DELETE FROM rate_limits")
A.query("UPDATE users SET verified=0 WHERE email=?",(E2,))
SENT.clear()
C().post("/verify/resend",data={"email":E2},follow_redirects=True)
ck(len(SENT)==1, "POST /verify/resend does send")
cS=C()
for _ in range(6): cS.post("/verify/resend",data={"email":E2},follow_redirects=True)
ck(len(SENT)==5, f"resend obeys the same 5/hour cap ({len(SENT)})")

# ------------------------------------------------------------ housekeeping
print("\n[housekeeping]")
A.query("INSERT INTO rate_limits (action,who,ts) VALUES ('old','x',?)",(time.time()-90000,))
A.sweep_rate_limits()
ck(A.query("SELECT COUNT(*) c FROM rate_limits WHERE action='old'",fetch=True,one=True)["c"]==0,
   "sweep drops rows older than the longest window")
ck("code_expires" not in A.BACKUP_COLUMNS, "code_expires is not written into backups")
b=json.loads(A.json.dumps(A.build_backup())) if False else A.build_backup()
ck(all("code_expires" not in u and "verify_code" not in u for u in b["users"]),
   "export really contains neither the code nor its expiry")

shutil.rmtree(WORK,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
