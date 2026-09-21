"""Theme everywhere, delete-by-double-click, the phone layout, the PWA bits,
and the logged-out /sample page."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_ui_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5162"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="uitest", ADMIN_PASSWORD="apw",
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
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - Ea

@event 2026-09-10 - Meet Mr. B - 15:30-16:00 - gray
@hw A - before - Read ch.4 - 2026-09-10

@pref time_format = 24h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-10T16:00:00Z")

        pg.goto(BASE + "/register")
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=name]', "Jane Doe")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.fill('input[name=email]', "27doej@shadysideacademy.org")
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SEED)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # ---------- dark is the default again ----------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(400)
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "dark",
           "dark is the default now")

        # ---------- the theme follows you off the schedule page ----------
        pg.click("#themeBtn")           # -> light
        pg.wait_for_timeout(300)
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#f_name")
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "light",
           "/edit picks up the light theme instead of snapping back to dark")
        ebg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        ck("255, 255, 255" in ebg, f"/edit really is light ({ebg})")
        ck(pg.is_visible("#themeBtn"), "and it has its own switch")
        pg.click("#themeBtn")
        pg.wait_for_timeout(200)
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "dark",
           "switching from /edit works")
        pg.goto(BASE + "/admin/login")
        ck(pg.evaluate("document.documentElement.getAttribute('data-theme')") == "dark",
           "the admin pages follow the same choice")

        # ---------- double-click again to EDIT ----------
        # It used to delete on the spot. Creating and destroying with the same
        # gesture is a trap, so the gesture opens an editor and Delete is a
        # button inside it -- one deliberate click further away.
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(400)
        ev = pg.locator("#gbody .block").filter(has_text="Meet Mr. B").first
        ck(ev.count() == 1, "my own event is on the grid")
        pg.on("dialog", lambda d: d.accept())
        ev.dblclick()
        pg.wait_for_timeout(500)
        ck(pg.is_visible("#quickDlg") and pg.input_value("#qName") == "Meet Mr. B",
           "double-clicking my own event opens it for editing")
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("Meet Mr. B" in saved, "and nothing is deleted just by looking at it")
        pg.click("#qDelete")
        pg.wait_for_timeout(700)
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("Meet Mr. B" not in saved, "Delete inside the editor removes it")
        gone = pg.locator("#gbody .block").filter(has_text="Meet Mr. B").count()
        ck(gone == 0, "and it disappears from the grid")

        hw = pg.locator("#gbody .hw").first
        ck(hw.count() == 1, "the homework marker is there")
        hw.dblclick()
        pg.wait_for_timeout(500)
        ck(pg.is_visible("#quickDlg"), "a homework marker opens the same editor")
        pg.click("#qDelete")
        pg.wait_for_timeout(700)
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("@hw" not in saved, "and can be deleted from there")

        # a CLASS must not be deletable this way -- it opens the homework dialog
        cls = pg.locator("#gbody .block").filter(has_text="Game Design").first
        cls.dblclick()
        pg.wait_for_timeout(300)
        ck(pg.is_visible("#quickDlg"), "double-clicking a class still adds homework, never deletes the class")
        pg.click("#qCancel")

        # ---------- phone ----------
        ph = b.new_page(viewport={"width": 390, "height": 844})
        ph.clock.install(time="2026-09-10T16:00:00Z")
        ph.goto(BASE + "/login")
        ph.fill('input[name=email]', "27doej@shadysideacademy.org")
        ph.fill('input[name=password]', "pw123456")
        ph.click('button[type=submit]')
        ph.wait_for_load_state()
        ph.goto(BASE + "/app")
        ph.wait_for_selector("#gbody .col")
        ph.wait_for_timeout(600)
        ncols = ph.eval_on_selector_all("#gbody .col", "n=>n.length")
        ck(ncols == 1, f"a phone shows one day, not five ({ncols})")
        ck("Thu Sep 10" in ph.text_content("#wkLabel"),
           f"it opens on today and the label names the day ({ph.text_content('#wkLabel')})")
        # no horizontal overflow -- the usual mobile smell
        ovf = ph.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ck(ovf <= 1, f"the page doesn't scroll sideways on a phone (overflow {ovf}px)")
        # nothing spills out of its block
        over = ph.evaluate("""()=>{const out=[];
          document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(b=>{
            const cs=getComputedStyle(b);
            const avail=b.clientHeight-parseFloat(cs.paddingTop)-parseFloat(cs.paddingBottom)+1.5;
            const kids=Array.from(b.children).filter(e=>!e.classList.contains('bextra')&&!e.classList.contains('hid'));
            if(!kids.length)return; const base=kids[0].offsetTop;
            kids.forEach(e=>{if(e.offsetTop+e.offsetHeight-(base+avail)>1.5)
              out.push(b.querySelector('.bname').textContent.slice(0,16));});});
          return out;}""")
        ck(not over, f"no block overflows on a phone ({over})")
        # the arrows step one day
        ph.click("#nextWk")
        ph.wait_for_timeout(400)
        ck("Fri Sep 11" in ph.text_content("#wkLabel"),
           f"the arrow moves one day, not one week ({ph.text_content('#wkLabel')})")
        # ...and rolls into the next week off the end
        ph.click("#nextWk")
        ph.wait_for_timeout(400)
        ck("Mon Sep 14" in ph.text_content("#wkLabel"),
           f"past Friday it rolls into Monday ({ph.text_content('#wkLabel')})")
        # tapping a block expands it (no hover on a touch screen)
        ph.click("#todayBtn")
        ph.wait_for_timeout(400)
        blk = ph.locator("#gbody .block:not(.dayfull)").first
        blk.click()
        ph.wait_for_timeout(300)
        ck(ph.eval_on_selector_all("#gbody .block.open", "n=>n.length") == 1,
           "tapping a block expands it")

        # ---------- PWA ----------
        man = ph.evaluate("""async () => {
            const r = await fetch('/manifest.webmanifest');
            return {ok: r.ok, type: r.headers.get('content-type'), body: await r.json()}; }""")
        ck(man["ok"] and man["body"]["start_url"] == "/app",
           f"the manifest is served from the root ({man['ok']}, {man.get('type')})")
        ck(len(man["body"]["icons"]) >= 2, "with icons")
        sw = ph.evaluate("fetch('/sw.js').then(r=>r.ok)")
        ck(sw, "the service worker is served from the root (so its scope is the whole site)")
        icon = ph.evaluate("fetch('/static/icons/icon-192.png').then(r=>r.ok)")
        ck(icon, "the icons are actually there")

        # ---------- /sample, logged out ----------
        anon = b.new_context()
        sp = anon.new_page()
        sp.clock.install(time="2026-09-10T16:00:00Z")
        sp.goto(BASE + "/sample")
        sp.wait_for_selector("#gbody .col", timeout=8000)
        sp.wait_for_timeout(500)
        blocks = sp.eval_on_selector_all("#gbody .block .bname", "n=>n.map(x=>x.textContent)")
        ck(len(blocks) > 3, f"the sample shows a real week without logging in ({blocks[:4]})")
        ck(any("Sample schedule" in t for t in
               sp.eval_on_selector_all("div", "n=>n.map(x=>x.textContent.slice(0,40))")),
           "and says clearly that it's a sample")
        ck(sp.locator("a[href='/register']").count() >= 1, "with a way to sign up from there")
        # a logged-out visitor must not reach anyone's data
        me = sp.evaluate("fetch('/api/my-schedule').then(r=>r.status)")
        ck(me in (302, 401, 403, 200) and "Jane Doe" not in
           sp.evaluate("fetch('/api/my-schedule').then(r=>r.text()).catch(()=>'')"),
           f"the sample page can't read a real account's schedule (status {me})")

        # the status line is a developer note; a visitor should never see it
        st = sp.text_content("#status").strip()
        ck(st == "", f"the sample page shows no developer status text (got {st!r})")
        # and because it reads the real calendar, the cycle days are the real ones
        cyc = sp.eval_on_selector_all(".dcell .dcyc", "n=>n.map(x=>x.textContent).filter(Boolean)")
        ck(any("Day" in c for c in cyc),
           f"the sample shows real cycle days, not a phase-shifted guess ({cyc})")
        # no em dashes anywhere a student can read them
        dashes = sp.evaluate("(document.body.innerText.match(/\u2014/g)||[]).length")
        ck(dashes == 0, f"no em dashes on the sample page ({dashes})")

        lg = anon.new_page()
        lg.goto(BASE + "/login")
        ck(lg.locator("a[href='/sample']").count() >= 1, "the login page links to the sample")

        # ...and none on the pages a logged-in student uses either
        for path in ["/app", "/edit"]:
            pg.goto(BASE + path)
            pg.wait_for_timeout(500)
            n = pg.evaluate("(document.body.innerText.match(/\u2014/g)||[]).length")
            ck(n == 0, f"no em dashes on {path} ({n})")
            # The \uXXXX escapes only work inside a JS string. Written straight
            # into HTML they render as six literal characters, which is exactly
            # what shipped on the Backup button and what nothing was checking.
            # textContent, not innerText: half of these live in a <dialog>
            # that is closed until somebody presses a button.
            raw = pg.evaluate("(() => { const c = document.body.cloneNode(true); c.querySelectorAll('script,style').forEach(n => n.remove()); return (c.textContent.match(/\\\\u[0-9a-fA-F]{4}/g)||[]).length; })()")
            ck(raw == 0, f"no unrendered \\uXXXX escapes on {path} ({raw})")

        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
