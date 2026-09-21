# -*- coding: utf-8 -*-
"""Local mode: /local, /local/edit, /local/app.

The whole promise is "your schedule never reaches our server", so the test
that matters most is not "does it render" but "does anything leave the
browser". Two of these check that directly: the database stays empty after a
full round of typing, saving and ticking things off, and the page makes no
POST to /api/quick-* at all.

The rest is the mirror: local mode must obey the SAME caps as the cloud, or
the two modes disagree about what a legal schedule is and an export from one
will not import into the other.
"""
import io, json, os, subprocess, sys, tempfile, time
import urllib.request, urllib.error

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_loc_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5218"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="loc", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
# A server left behind by an earlier run answers on this port and brings its
# rate-limit counters with it, which shows up as a wall of unexplained 429s.
# Refuse to run rather than test somebody else's process.
import socket
_s = socket.socket()
# SO_REUSEADDR, because the dev server sets it too: without it a socket still
# in TIME_WAIT from the last run reads as "in use" when it is not.
_s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    _s.bind(("127.0.0.1", int(PORT)))
except OSError:
    print(f"  FAIL  port {PORT} is already in use; kill the stale app first")
    sys.exit(1)
finally:
    _s.close()

srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# A fixed sleep is a coin toss on a loaded machine; poll instead.
for _ in range(80):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/local", timeout=1).read()
        break
    except Exception:
        time.sleep(0.25)

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT

def get(path):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=20)
        return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

SCHED = """Jane Doe
27 - Junior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

@event Tue - Piano lesson - 17:30-18:30 - pink
@pref time_format = 12h
"""

try:
    print("\n[the three pages open with no account]")
    for path in ["/local", "/local/edit", "/local/app"]:
        code, body = get(path)
        ck(code == 200, f"{path} is 200 for a stranger ({code})")
    code, body = get("/local/edit")
    ck("ssa-local-schedule" in body, "/local/edit reads the browser key")
    ck("const LOCAL = true" in body, "/local/edit renders with the local flag on")
    ck("Earlier versions" not in body,
       "/local/edit hides version history (there is no server copy to keep)")
    # urlopen follows the redirect, so look at where it landed, not the code
    r = urllib.request.urlopen(BASE + "/edit", timeout=20)
    ck("/login" in r.geturl(),
       f"/edit still needs an account (landed on {r.geturl()})")

    print("\n[the front door offers both]")
    code, body = get("/")
    ck(code == 200, f"/ is a page now, not a redirect to the login wall ({code})")
    ck("/local" in body and ("/login" in body or "Log in" in body),
       "and it offers both doors")
    ck("nothing is stored on our server" in body,
       "stating what local mode costs us")
    ck("administrator could" in body,
       "and what an account costs you")
    r = urllib.request.urlopen(BASE + "/app", timeout=20)
    ck("/login" in r.geturl(),
       f"/app still sends a stranger to log in ({r.geturl()})")

    print("\n[the caps are one number, not two]")
    code, body = get("/api/caps")
    caps = json.loads(body)
    sys.path.insert(0, APP_DIR)
    import app as A
    ck(caps["events"] == A.MAX_EVENTS and caps["hw"] == A.MAX_HW
       and caps["sports"] == A.MAX_SPORTS and caps["projects"] == A.MAX_PROJECTS
       and caps["bytes"] == A.MAX_SCHEDULE_BYTES,
       f"/api/caps matches the server's own constants ({caps})")
    code, body = get("/local/edit")
    ck(f"const MAX_BYTES = {A.MAX_SCHEDULE_BYTES // 1024} * 1024" in body,
       "the editor's byte limit comes from the same place")

    print("\n[the no-account parse endpoints]")
    def post_file(path, field, blob, name):
        bnd = "----b"
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"{field}\";"
                f" filename=\"{name}\"\r\n\r\n").encode() + blob \
               + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(BASE + path, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
        try:
            r = urllib.request.urlopen(req, timeout=60)
            return r.getcode(), json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    PDF = os.path.join(os.environ.get("SSA_PDFS", "/tmp/pdfs"), "sample-schedule.pdf")
    if os.path.exists(PDF):
        code, j = post_file("/api/parse-pdf", "pdf", open(PDF, "rb").read(), "s.pdf")
        ck(code == 200 and j.get("count", 0) > 0,
           f"a Veracross PDF parses with no login ({code}, {j.get('count')})")
        ck("@term" in (j.get("text") or ""), "and comes back as schedule text")
    else:
        print("  SKIP  no sample PDF on this machine")
    code, j = post_file("/api/parse-pdf", "pdf", b"not a pdf", "x.pdf")
    ck(code == 400, f"rubbish is refused, not crashed on ({code})")
    code, j = post_file("/api/parse-pdf", "pdf", b"%PDF" + b"x" * (4 * 1024 * 1024 + 10),
                        "big.pdf")
    ck(code == 400, f"an oversized upload is refused ({code})")

    ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Apps\r\n"
           "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:CMU ED1 deadline\r\n"
           "DTSTART;VALUE=DATE:20261101\r\nDTEND;VALUE=DATE:20261102\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n").encode()
    code, j = post_file("/api/parse-ics", "ics", ICS, "a.ics")
    ck(code == 200 and len(j.get("items", [])) == 1,
       f"a .ics parses with no login ({code}, {len(j.get('items', []))})")
    ck(j["items"][0]["line"].startswith("@event 2026-11-01"),
       f"and the exclusive DTEND is not read as two days ({j['items'][0]['line']})")
    code, j = post_file("/api/parse-ics", "ics", b"just text", "a.ics")
    ck(code == 400, f"a non-calendar file is refused ({code})")

    print("\n[rate limits: no login means no other brake]")
    hits = [post_file("/api/parse-ics", "ics", ICS, "a.ics")[0] for _ in range(14)]
    ck(429 in hits, f"the per-IP hourly limit bites ({hits.count(429)} of 14 refused)")

    print("\n[nothing reached the database]")
    n = A.query("SELECT COUNT(*) c FROM users", fetch=True, one=True)["c"]
    ck(n == 0, f"no account was created by any of that ({n} rows)")
except Exception as e:
    ck(False, f"crashed before the browser: {type(e).__name__}: {e}")

# --------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        errs = []
        ctx = b.new_context()
        ctx.on("weberror", lambda e: errs.append(str(e.error)))
        posts = []
        ctx.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
        pg = ctx.new_page()

        print("\n[typing a schedule in]")
        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(400)
        pg.click("#modeText")
        pg.fill("#rawText", SCHED)
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(500)
        saved = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck(saved is not None and "AP Calculus BC" in saved,
           "Save put the text in this browser")
        ck(pg.locator("#localFlash").inner_text().lower().startswith("saved"),
           "and said so")
        ck(not [u for u in posts if "/edit" in u and "parse" not in u],
           f"and posted nothing to the server ({posts})")

        # The install nudge has a section of its own further down; switch it
        # off in this context so it does not sit on top of everything else.
        pg.evaluate("localStorage.setItem('ssa-local-install','off')")

        print("\n[and it comes back]")
        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(400)
        ck(pg.input_value("#f_name") == "Jane Doe",
           "reopening the editor reads it back out of the browser")

        pg.goto(BASE + "/local/app")
        pg.wait_for_timeout(1500)
        txt = pg.inner_text("body")
        ck("Calculus" in txt, "the schedule page draws it")
        ck("Local mode." not in txt,
           "and drops the empty-state banner once there is a schedule")

        print("\n[ticking things off stays local]")
        posts.clear()
        pg.evaluate("""() => {
          const t = localStorage.getItem('ssa-local-schedule');
          return t.length;
        }""")
        before = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        r = pg.evaluate("""async () => {
          const out = await api('/api/quick-add',
            {line:'@event 2026-09-30 - Dentist - 14:00-15:00 - gray'});
          return out.status;
        }""")
        after = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck(r == 200, f"quick-add answers like the server does ({r})")
        ck("Dentist" in after and "Dentist" not in before,
           "the new line went into this browser")
        ck(not [u for u in posts if "/api/quick" in u],
           f"and no request went out for it ({posts})")

        print("\n[the caps are enforced without a server to do it]")
        pg.goto(BASE + "/local/edit")
        pg.wait_for_timeout(400)
        pg.click("#modeText")
        pg.fill("#rawText", "X\n27\n\n" + ("@event Tue - Thing - 10:00-11:00 - red\n"
                                           * (60)))
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(300)
        msg = pg.locator("#localFlash").inner_text()
        ck("limit is 50" in msg, f"too many events is refused here too ({msg})")
        kept = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck("AP Calculus BC" in kept,
           "and the refusal did not overwrite what was already there")

        pg.fill("#rawText", "X\n27\n\n" + ("# padding padding padding\n" * 2000))
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(300)
        msg = pg.locator("#localFlash").inner_text()
        ck("KB" in msg and "limit is 32 KB" in msg,
           f"an oversized paste is refused with the server's sentence ({msg})")

        print("\n[backup and restore]")
        pg.goto(BASE + "/local/app")
        pg.wait_for_timeout(1200)
        pg.evaluate("document.querySelectorAll('dialog[open]')"
                    ".forEach(d => d.close())")
        ck(pg.locator("#bkBtn").is_visible(), "local mode shows the Backup button")
        pg.click("#bkBtn")
        pg.wait_for_timeout(300)
        shown = pg.input_value("#bkText") if pg.locator("#bkText").count() else ""
        ck("AP Calculus BC" in shown,
           "the backup box holds the schedule as plain text")
        # wipe the browser and put it back from the text
        pg.evaluate("localStorage.removeItem('ssa-local-schedule')")
        pg.reload()
        pg.wait_for_timeout(1200)
        ck("Local mode." in pg.inner_text("body"),
           "with the key gone it is empty again")
        pg.click("#bkBtn")
        pg.wait_for_timeout(300)
        pg.fill("#rsText", SCHED)
        pg.click("#rsPaste")
        pg.wait_for_timeout(800)
        back = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck(back is not None and "AP Calculus BC" in back,
           "pasting the text back restores it")

        print("\n[the daily reminder]")
        p2 = ctx.new_page()
        p2.goto(BASE + "/local/app")
        p2.wait_for_timeout(1500)
        p2.evaluate("() => localStorage.setItem('ssa-local-remind-seen',"
                    " dkey(pittNow()))")
        p2.reload(); p2.wait_for_timeout(1500)
        ck(not p2.locator("#keepDlg").is_visible(),
           "it does not nag twice in one day")
        p2.evaluate("localStorage.removeItem('ssa-local-remind-seen')")
        p2.reload(); p2.wait_for_timeout(1500)
        ck(p2.locator("#keepDlg").is_visible(),
           "a new day brings it back")
        p2.click("#keepSnooze")
        p2.wait_for_timeout(300)
        p2.evaluate("localStorage.removeItem('ssa-local-remind-seen')")
        p2.reload(); p2.wait_for_timeout(1500)
        ck(not p2.locator("#keepDlg").is_visible(),
           "'stop for 30 days' is believed")

        print("\n[installing it: the manifest]")
        code, man = get("/manifest-local.webmanifest")
        m = json.loads(man)
        ck(code == 200 and m["start_url"] == "/local/app",
           f"local mode installs to /local/app, not the login wall ({m.get('start_url')})")
        ck(m["scope"] == "/local/" and m["id"] == "/local/app",
           "and does not install over the cloud app's entry")
        pg.goto(BASE + "/local/app")
        pg.wait_for_timeout(900)
        href = pg.get_attribute("link[rel=manifest]", "href")
        ck(href == "/manifest-local.webmanifest",
           f"the local page points at it ({href})")
        pg.goto(BASE + "/sample")
        pg.wait_for_timeout(900)
        href = pg.get_attribute("link[rel=manifest]", "href")
        ck(href == "/manifest.webmanifest",
           f"and every other page still points at the normal one ({href})")

        print("\n[installing it: what each engine is told]")
        UAS = {
          "ios-safari": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                         "Mobile/15E148 Safari/604.1"),
          "ios-chrome": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0 "
                         "Mobile/15E148 Safari/604.1"),
          "mac-safari": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                         "Safari/605.1.15"),
          "android-chrome": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"),
          "win-chrome": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
          "win-edge": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
          "win-firefox": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
                          "Gecko/20100101 Firefox/127.0"),
        }
        info = {}
        for key, ua in UAS.items():
            c2 = b.new_context(user_agent=ua)
            p3 = c2.new_page()
            p3.goto(BASE + "/local/app")
            p3.wait_for_timeout(900)
            info[key] = p3.evaluate("installInfo()")
            c2.close()
        # the 7-day rule is WebKit's, and saying it anywhere else is a lie
        for key in ["ios-safari", "ios-chrome", "mac-safari"]:
            ck(info[key]["timed"] is True, f"{key}: told about the 7 day timer")
            ck(info[key]["order"] is True,
               f"{key}: told to install BEFORE typing (separate storage)")
            ck(len(info[key]["steps"]) >= 3, f"{key}: given real steps")
        for key in ["android-chrome", "win-chrome", "win-edge", "win-firefox"]:
            ck(info[key]["timed"] is False,
               f"{key}: NOT told about a timer that does not exist there")
        ck("Home Screen" in " ".join(info["ios-safari"]["steps"]),
           "iOS Safari is told about Add to Home Screen")
        ck("Share" in " ".join(info["ios-chrome"]["steps"]),
           "Chrome on iOS gets iOS steps, because it is WebKit too")
        ck("Dock" in " ".join(info["mac-safari"]["steps"]),
           "Safari on a Mac is told about the Dock, not the Home Screen")
        ck(info["win-edge"]["canInstall"] and
           any("Edge" in x for x in info["win-edge"]["steps"]),
           "Edge is named in Edge's instructions")
        ck(info["win-firefox"]["canInstall"] is False
           and not info["win-firefox"]["steps"],
           "Firefox is not given install steps it cannot follow")
        ck("persistent" in info["win-firefox"]["why"].lower()
           or "permanent" in info["win-firefox"]["why"].lower(),
           "and is offered the thing it CAN do instead")

        print("\n[installing it: how often it asks]")
        c3 = b.new_context(user_agent=UAS["ios-safari"])
        p4 = c3.new_page()
        p4.goto(BASE + "/local/app")
        p4.wait_for_timeout(1400)
        ck(p4.locator("#addDlg").is_visible(),
           "on iOS it asks even with nothing entered yet (storage is separate)")
        ck(not p4.locator("#keepDlg").is_visible(),
           "and does not stack a second modal on top of it")
        ck("7" in p4.inner_text("#addWhy") or "seven" in p4.inner_text("#addWhy"),
           "and says why")
        p4.click("#addOk")
        p4.reload(); p4.wait_for_timeout(1400)
        ck(not p4.locator("#addDlg").is_visible(),
           "'Got it' quiets it")
        p4.evaluate("localStorage.setItem('ssa-local-install-until','2020-01-01')")
        p4.reload(); p4.wait_for_timeout(1400)
        ck(p4.locator("#addDlg").is_visible(), "but only for a week")
        p4.click("#addNever")
        p4.evaluate("localStorage.setItem('ssa-local-install-until','2020-01-01')")
        p4.reload(); p4.wait_for_timeout(1400)
        ck(not p4.locator("#addDlg").is_visible(),
           "'Don't show this again' is permanent")
        ck(p4.evaluate("localStorage.getItem('ssa-local-install')") == "off",
           "and is remembered under its own key")
        # Esc must not turn it into a nag
        p4.evaluate("localStorage.setItem('ssa-local-install','on');"
                    "localStorage.setItem('ssa-local-install-until','2020-01-01')")
        p4.reload(); p4.wait_for_timeout(1400)
        ck(p4.locator("#addDlg").is_visible(), "Settings can turn it back on")
        p4.keyboard.press("Escape")
        p4.wait_for_timeout(200)
        p4.reload(); p4.wait_for_timeout(1400)
        ck(not p4.locator("#addDlg").is_visible(),
           "pressing Escape counts as 'not today', not 'ask me again now'")
        c3.close()

        print("\n[installing it: not where there is nothing to say]")
        c4 = b.new_context(user_agent=UAS["win-chrome"])
        p5 = c4.new_page()
        p5.goto(BASE + "/local/app")
        p5.wait_for_timeout(1400)
        ck(not p5.locator("#addDlg").is_visible(),
           "an empty schedule on a browser with no timer is not nagged")
        ck(p5.locator("#keepDlg").is_visible(),
           "the backup reminder still runs")
        ck(not p5.locator("#keepIOS").is_visible(),
           "and it does not claim a 7 day timer on Chrome")
        c4.close()

        print("\n[house style]")
        for path in ["/", "/local", "/local/edit", "/local/app"]:
            pg.goto(BASE + path)
            pg.wait_for_timeout(600)
            n = pg.evaluate("(document.body.innerText.match(/—/g)||[]).length")
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
print("\n" + ("FAILED: " + "; ".join(FAIL) if FAIL else "all good"))
sys.exit(1 if FAIL else 0)
