# -*- coding: utf-8 -*-
"""Text from a file somebody else wrote must not run as code.

Until the .ics importer existed, every string on the page came from the
student's own schedule or an admin's calendar file, and the innerHTML
interpolation in addBlock() was self-inflicted at worst. Importing a calendar
changes who the author is: a classmate forwards you "本科申请.ics", the SUMMARY
of one event is an <img onerror=...>, and it runs in YOUR session, with your
cookie.

Fixed at the render layer -- esc() on every innerHTML interpolation -- because
that covers schedule text, calendar files and imports at once, and does not
depend on remembering to sanitise the next input somebody adds.
"""
import os, re, subprocess, sys, tempfile, time, uuid
import urllib.request, urllib.parse, http.cookiejar
import html as HTML

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
sys.path.insert(0, APP_DIR)
import ics_import

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

WORK = tempfile.mkdtemp(prefix="ssa_xss_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5214"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="xss", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

BASE = "http://127.0.0.1:" + PORT
MAIL = "27doej@shadysideacademy.org"
BOOM = '<img src=x onerror="window.__pwned=1">'
SCHED = f"""Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - {BOOM} - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - <script>window.__pwned=1</script> - R 130 - yellow

@event Tue - {BOOM} - 17:30-18:30 - pink
@event 2026-09-11 - <b onmouseover="window.__pwned=1">deadline</b> - allday - red
@hw A - before - {BOOM} - 2026-09-08
@pref time_format = 12h
"""
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
    {"schedule_text": SCHED}).encode()))

# ---- the importer's own layer ----------------------------------------------
ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0",
    "BEGIN:VEVENT",
    "SUMMARY:<img src=x onerror=alert(1)>",
    "DTSTART;VALUE=DATE:20260915",
    "DTEND;VALUE=DATE:20260916",
    "END:VEVENT", "END:VCALENDAR", ""])
items, _sk, _c = ics_import.convert(ICS, "gray")
ck(items and "<" not in items[0]["line"] and ">" not in items[0]["line"],
   f"the importer strips angle brackets from a summary too -- two layers, "
   f"because the render layer is one edit away from someone forgetting "
   f"({items and items[0]['line']})")
ck(items and ics_import.validated_line(items[0]["line"]),
   "and what comes out still validates")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T15:00:00Z")
        pg.goto(BASE + "/login")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        def pwned():
            return pg.evaluate("() => !!window.__pwned")

        for view, click in [("week", None), ("month", '#viewSeg button[data-v="month"]'),
                            ("list", '#viewSeg button[data-v="list"]')]:
            pg.goto(BASE + "/app")
            pg.wait_for_selector("#gbody")
            pg.wait_for_timeout(700)
            if click:
                pg.click(click)
                pg.wait_for_timeout(900)
            ck(not pwned(), f"nothing ran in the {view} view")
            ck(pg.evaluate("() => document.querySelectorAll('#gbody img').length") == 0,
               f"and no injected <img> was built in the {view} view")

        # the loop above left it in the list, and the view is remembered
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody")
        pg.wait_for_timeout(600)
        pg.click('#viewSeg button[data-v="week"]')
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(800)
        # the text is still THERE, just as text -- escaping must not eat it
        shown = pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('onerror'));
            return b ? (b.querySelector('.bname')||{}).textContent : null;
        }""")
        ck(shown and "onerror" in shown,
           f"the course name is shown literally, not swallowed ({shown!r})")
        ck(pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#gbody .block')]
                .find(e => (e.textContent||'').includes('onerror'));
            return b ? b.querySelector('.bname').children.length : -1;
        }""") == 0,
           "and it is text inside .bname, not markup")

        # the legend, the all-day band and the click menu are the same story
        ck(pg.evaluate("() => document.querySelectorAll('.legend img').length") == 0,
           "the legend builds no markup out of a course name either")
        ck(not pwned(), "still nothing ran after all of that")

        # the chip itself is clipped out of a short block, so open its menu
        # the way a click would
        pg.evaluate("""() => {
            const el = document.querySelector('.hw[data-src]');
            openItemMenu(el, {clientX: 100, clientY: 100});
        }""")
        pg.wait_for_timeout(400)
        ck(pg.evaluate("() => document.querySelectorAll('.hwmenu img').length") == 0
           and not pwned(),
           "nor the little menu, which prints the item's own name")
        pg.keyboard.press("Escape")

        ck(not errs, f"no page errors: {errs[:3]}")

        # ---- and the whole way through an import --------------------------
        b2 = uuid.uuid4().hex
        body = (f"--{b2}\r\nContent-Disposition: form-data; name=\"color\"\r\n\r\n"
                f"gray\r\n".encode()
                + f"--{b2}\r\nContent-Disposition: form-data; name=\"ics\"; "
                  f"filename=\"evil.ics\"\r\nContent-Type: text/calendar\r\n\r\n".encode()
                + ICS.encode() + f"\r\n--{b2}--\r\n".encode())
        page = op.open(urllib.request.Request(
            BASE + "/edit/import-ics", body,
            {"Content-Type": f"multipart/form-data; boundary={b2}"})).read().decode()
        ck("<img src=x" not in page,
           "the preview page does not render the summary as markup")
        lines = [HTML.unescape(x) for x in
                 re.findall(r'name="line" value="([^"]*)"', page)]
        op.open(urllib.request.Request(
            BASE + "/edit/import-ics/confirm",
            urllib.parse.urlencode([("line", l) for l in lines]).encode()))
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(900)
        ck(not pwned() and pg.evaluate(
            "() => document.querySelectorAll('#gbody img,#allday img').length") == 0,
           "and an imported event is still only text on the page")
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
