"""Edit my schedule > On this device, the 12h/24h button, and the compact toolbar.

- The 12h / 24h button writes "@pref time_format" (account and local, the
  same text either way) instead of changing the view until the next load.
- Saving in Form mode keeps every other @pref line.
- The per-browser settings live in the editor now; the reminders and the
  install / storage help still work from there.
- Toolbar style: every option showing (default) or one button per switch."""
import json, os, re, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_dev_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5227"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="devtest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - La

@event 2026-10-29 - Dentist - 16:00-17:00 - pink

@pref background = #112233
@pref time_format = 12h
@pref highlight_class = #ffaa00
"""
def norm(t):
    return "\n".join(l.rstrip() for l in (t or "").replace("\r\n", "\n").split("\n")).strip()

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-10-29T13:30:00Z")

        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        def save_text(t):
            pg.goto(BASE + "/edit")
            pg.click("#modeText")
            pg.fill("#rawText", t)
            with pg.expect_navigation():
                pg.click('button[type=submit]')

        def mine():
            return norm(pg.evaluate("fetch('/api/my-schedule',{cache:'no-store'}).then(r=>r.text())"))

        def seg_on(sel):
            return pg.evaluate(f"(document.querySelector('{sel} button.on')||{{}}).textContent||''").strip()

        save_text(SEED)

        # ---------------- 12h / 24h sticks ----------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(seg_on("#fmtSeg") == "12h", "starts on the file's 12h")
        pg.click("#fmtSeg button[data-f='24h']")
        pg.wait_for_timeout(600)
        t = mine()
        ck(t.count("@pref time_format") == 1 and "@pref time_format = 24h" in t,
           "clicking 24h writes the @pref line (one of it)")
        ck("@pref background = #112233" in t and "@pref highlight_class = #ffaa00" in t,
           "the other @pref lines are untouched")
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(seg_on("#fmtSeg") == "24h" and pg.locator("#gbody .block", has_text="16:00").count() > 0,
           "and it is still 24h after a reload (it used to snap back)")
        r = pg.evaluate("""fetch('/api/pref',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({key:'background',value:'#000'})}).then(r=>r.status)""")
        ck(r == 400, f"only time_format can be set this way ({r})")
        # a failed save says so instead of pretending
        pg.route("**/api/pref", lambda route: route.abort())
        pg.click("#fmtSeg button[data-f='12h']")
        pg.wait_for_timeout(500)
        st = pg.text_content("#status") or ""
        ck("nothing was saved" in st.lower() or "only changed on this screen" in st.lower(),
           f"offline / failed: it says the change was not saved ({st!r})")
        pg.unroute("**/api/pref")

        # ---------------- form mode keeps the other @pref lines ----------------
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        ck(pg.locator("#f_timefmt").get_attribute("type") == "hidden",
           "no Time format control in the editor any more (one control, on the schedule page)")
        with pg.expect_navigation():
            pg.click('button[type=submit]')          # save straight from the FORM
        t = mine()
        ck("@pref background = #112233" in t and "@pref highlight_class = #ffaa00" in t,
           "saving in Form mode keeps @pref background / highlight (it used to drop them)")
        ck("@pref time_format = 24h" in t and t.count("@pref time_format") == 1,
           "and the time format as it was")

        # ---------------- server and local mode write the same text ----------------
        save_text(SEED)
        pg.evaluate("""fetch('/api/pref',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({key:'time_format',value:'24h'})})""")
        pg.wait_for_timeout(300)
        server = mine()
        lp = ctx.new_page()
        lp.on("pageerror", lambda e: errs.append(str(e)))
        lp.clock.install(time="2026-10-29T13:30:00Z")
        lp.goto(BASE + "/local/app")
        lp.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        lp.reload()
        lp.wait_for_selector("#gbody .col", timeout=8000)
        lp.click("#fmtSeg button[data-f='24h']")
        lp.wait_for_timeout(400)
        local = norm(lp.evaluate("localStorage.getItem('ssa-local-schedule')"))
        ck(server == local, "local mode's 24h button writes exactly what the server does"
           + ("" if server == local else f"\n--- server\n{server}\n--- local\n{local}"))
        no_pref = "Jane Doe\n27 - Senior\n\n@event 2026-10-29 - X - 10:00-10:30 - gray\n\n"
        lp.evaluate("s=>localStorage.setItem('ssa-local-schedule',s)", no_pref)
        r = lp.evaluate("localApi('/api/pref',{key:'time_format',value:'24h'})")
        lt = lp.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck(r["status"] == 200 and lt.rstrip("\n").endswith("@pref time_format = 24h"),
           "a file with no @pref line gets one at the end")

        # ---------------- On this device (local) ----------------
        lp.goto(BASE + "/local/edit")
        lp.wait_for_timeout(400)
        ck(lp.locator("#device").is_visible() and lp.locator("#devRemind").is_visible()
           and lp.locator("#devInstall").is_visible(), "local: reminders are in On this device")
        ck(not lp.is_checked("#devRemind") and not lp.is_checked("#devInstall"),
           "they show what this browser has (both were turned off)")
        lp.check("#devRemind")
        ck(lp.evaluate("localStorage.getItem('ssa-local-remind')") == "on",
           "ticking writes the key the schedule page reads")
        lp.evaluate("localStorage.removeItem('ssa-local-remind-seen')")
        lp.goto(BASE + "/local/app")
        lp.wait_for_timeout(1500)
        ck(lp.locator("#keepDlg").is_visible(), "...so the daily reminder comes back")
        ck((lp.get_attribute("#keepBackup", "href") or "").endswith("/local/edit#device"),
           "and its 'Back it up now' goes to the editor's backup")
        lp.evaluate("document.getElementById('keepDlg').close()")
        lp.goto(BASE + "/local/edit#device")           # the link, followed from where it is
        lp.wait_for_timeout(300)
        lp.click("a[href='/local/app#install']")
        lp.wait_for_timeout(1500)
        ck(lp.locator("#addDlg").is_visible(), "'How do I install it' opens the install help")
        lp.wait_for_timeout(300)
        store = lp.text_content("#setStore") or ""
        ck("Storage is" in store, f"which now also says how safe the storage is ({store!r})")

        # ---------------- compact toolbar ----------------
        def visible_buttons(page, sel):
            return page.evaluate(f"""Array.from(document.querySelectorAll('{sel} button'))
                .filter(b => b.offsetParent !== null).map(b => b.textContent.trim())""")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        ck(visible_buttons(pg, "#viewSeg") == ["Week", "Month", "List"],
           "default toolbar: every option showing")
        w_seg = pg.evaluate("['#viewSeg','#daysSeg','#fmtSeg'].map(s=>document.querySelector(s).getBoundingClientRect().width).reduce((a,b)=>a+b)")
        pg.goto(BASE + "/edit#device")
        pg.wait_for_timeout(300)
        ck(pg.is_checked("input[name=devToolbar][value=segmented]"), "the editor shows the default picked")
        pg.check("input[name=devToolbar][value=compact]")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        ck(visible_buttons(pg, "#viewSeg") == ["Week"] and visible_buttons(pg, "#daysSeg") == ["5 day"],
           f"compact: one button per switch ({visible_buttons(pg, '#viewSeg')}, {visible_buttons(pg, '#daysSeg')})")
        w_cmp = pg.evaluate("['#viewSeg','#daysSeg','#fmtSeg'].map(s=>document.querySelector(s).getBoundingClientRect().width).reduce((a,b)=>a+b)")
        ck(w_cmp < w_seg * 0.6, f"and it takes much less room ({w_cmp:.0f}px vs {w_seg:.0f}px)")
        ck("Month" in (pg.get_attribute("#viewSeg button.on", "title") or ""),
           "the button says what a click will switch to")
        seq = []
        for _ in range(3):
            pg.click("#viewSeg button.on")
            pg.wait_for_timeout(400)
            seq.append((visible_buttons(pg, "#viewSeg")[0], pg.evaluate("VIEW")))
        ck(seq == [("Month", "month"), ("List", "list"), ("Week", "week")],
           f"click, click, click: Month, List, back to Week ({seq})")
        pg.click("#daysSeg button.on")
        pg.wait_for_timeout(400)
        ck(visible_buttons(pg, "#daysSeg") == ["7 day"] and pg.locator("#ghead .dcell").count() == 7,
           "5 day -> 7 day, and the grid follows")
        before = seg_on("#fmtSeg")
        pg.click("#fmtSeg button.on")
        pg.wait_for_timeout(600)
        after = seg_on("#fmtSeg")
        ck(before != after and f"@pref time_format = {after}" in mine(),
           f"12h/24h in one click, and it is saved too ({before} -> {after})")
        pg.goto(BASE + "/edit#device")
        pg.check("input[name=devToolbar][value=segmented]")
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        ck(len(visible_buttons(pg, "#viewSeg")) == 3, "and back to every option showing")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
