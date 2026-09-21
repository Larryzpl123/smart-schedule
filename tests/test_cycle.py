"""Cycle days must match the SSA Daily WALK, and the admin anchor tool must work."""
import os, re, shutil, signal, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK=tempfile.mkdtemp(prefix="ssa_cy_"); CODE=os.path.join(WORK,"code")
shutil.copytree(APP_DIR,CODE,ignore=shutil.ignore_patterns("push","push.zip","__pycache__"))
env=dict(os.environ,DB_PATH=os.path.join(WORK,"d","schedule.db"),SECRET_KEY="x",ADMIN_PASSWORD="apw")
for k in ("SMTP_HOST","SMTP_USER","SMTP_PASS"): env.pop(k,None)
srv=subprocess.Popen([sys.executable,"-c","import app; app.app.run(port=5116,debug=False)"],
    cwd=CODE,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(3)
FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)

# straight from the WALK Larry pasted
WALK={"2026-08-25":None,   # Convocation - Day 0, must NOT be a cycle day
      "2026-08-26":1,"2026-08-27":2,"2026-08-28":3,"2026-08-31":4,
      "2026-09-01":5,"2026-09-02":6,"2026-09-03":7,"2026-09-04":8}
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg=b.new_page(viewport={"width":1500,"height":950}); errs=[]
        pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.goto("http://127.0.0.1:5116/register")
        pg.fill('input[name=email]',"27doej@shadysideacademy.org"); pg.fill('input[name=name]',"P Z")
        pg.fill('input[name=password]',"pw123456"); pg.click('button[type=submit]')
        pg.fill('input[name=email]',"27doej@shadysideacademy.org"); pg.fill('input[name=password]',"pw123456")
        pg.click('button[type=submit]'); pg.wait_for_load_state()
        pg.goto("http://127.0.0.1:5116/edit"); pg.click("#modeText")
        pg.fill("#rawText","P Z\n27 - Senior\n\n@term Fall 2026 = 08.25-01.15\n"
                "A - Test A - T1 - 1 - T - R1 - red - Ea\n\n@pref time_format = 24h")
        pg.click("#schedForm button[type=submit]"); pg.wait_for_load_state()

        def heads(datestr):
            pg.goto("http://127.0.0.1:5116/app"); pg.wait_for_timeout(1400)
            pg.evaluate("""async (ds)=>{const d=new Date(ds+'T12:00:00');
                weekStart=mondayOf(d); await ensureYearFor(weekStart); render();}""",datestr)
            pg.wait_for_timeout(600)
            return pg.evaluate("()=>[...document.querySelectorAll('#ghead > div')].slice(1)"
                               ".map(h=>h.textContent.trim())")
        print("\n[cycle days vs the SSA Daily WALK]")
        got={}
        for wk in ("2026-08-26","2026-09-02"):
            hs=heads(wk)
            mon=__import__("datetime").date.fromisoformat(wk)
            mon=mon-__import__("datetime").timedelta(days=mon.weekday())
            for i,h in enumerate(hs):
                got[str(mon+__import__("datetime").timedelta(days=i))]=h
        for date,want in sorted(WALK.items()):
            h=got.get(date,"")
            m=re.search(r"Day\s*(\d)",h)
            if want is None:
                ck(m is None, f"{date} Convocation: no cycle day  ({h!r})")
            else:
                ck(m and int(m.group(1))==want, f"{date} = Day {want}  (page says {h!r})")
        ck("Wedn" in got.get("2026-09-02",""), "Sep 2 uses the Wednesday late-start schedule (WALK says 9:35)")

        def served():
            import urllib.request
            return urllib.request.urlopen(
                "http://127.0.0.1:5116/api/calendar/2026-2027").read().decode()

        print("\n[admin anchor tool]")
        pg.goto("http://127.0.0.1:5116/admin/login")
        pg.fill('input[name=password]',"apw"); pg.click('button[type=submit]'); pg.wait_for_load_state()
        ck(pg.is_visible("summary:has-text('Fix the cycle day')"), "the tool is on the admin page")
        pg.click("summary:has-text('Fix the cycle day')")
        # scope to THAT panel: the admin page has other <details> on it now
        ck("2026-08-26 = Day 1" in pg.text_content(
               "details:has(summary:has-text('Fix the cycle day'))"),
           "existing anchors are listed")
        # pretend a snow day pushed everything: say Sep 3 is really Day 6
        pg.fill('input[name=date]',"2026-09-03")
        pg.select_option('select[name=day]',"6")
        pg.click("button:has-text('Save anchor')"); pg.wait_for_load_state(); pg.wait_for_timeout(400)
        ck("Anchor saved" in pg.text_content("body"), "save confirmed")
        # Stored in the database and laid over the file when it is served -- the
        # file itself is shipped by build_push.py and used to get overwritten.
        cal=served()
        ck("@anchor 2026-09-03 = 6" in cal, "served in the calendar the app reads")
        onfile=open(os.path.join(CODE,"calendar","2026-2027.txt"),encoding="utf-8").read()
        ck("@anchor 2026-09-03" not in onfile,
           "and not written into the file a deploy overwrites")
        h2=heads("2026-09-02")
        ck(re.search(r"Day\s*6", h2[3] or ""), f"Sep 3 now renders as Day 6 ({h2[3]!r})")
        ck(re.search(r"Day\s*4", h2[0] or ""), f"Aug 31 still Day 4 — a later anchor does not disturb earlier dates ({h2[0]!r})")
        # re-saving the same date replaces, never duplicates
        pg.goto("http://127.0.0.1:5116/admin"); pg.click("summary:has-text('Fix the cycle day')")
        pg.fill('input[name=date]',"2026-09-03"); pg.select_option('select[name=day]',"7")
        pg.click("button:has-text('Save anchor')"); pg.wait_for_load_state()
        cal=served()
        ck(cal.count("@anchor 2026-09-03")==1 and "@anchor 2026-09-03 = 7" in cal,
           "same date replaces rather than duplicating")
        ck(not errs, f"no JS errors ({errs[:3]})")
        b.close()
finally:
    srv.send_signal(signal.SIGTERM); srv.wait(timeout=10); shutil.rmtree(WORK,ignore_errors=True)
print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
