"""A semester-long project, with milestones that land on a real class period.

The ask: "US History paper is a whole-semester project, but prospectus,
sources, outline, rough draft and final draft are each due in a particular
class". The hard half of that already worked -- @hw hangs on a period LETTER
and the app resolves where that letter sits on that day, rotation and lunch
track and all. What was missing was the container.

So: @project is a name and two dates, milestones are ordinary @hw lines that
name it, and progress is COUNTED from those rather than stored -- a typed
percentage and a list of ticks disagree the first time anyone edits one, and
then the bar lies.
"""
import os, json, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, urllib.error, http.cookiejar

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_pr_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5204"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="pr", ADMIN_PASSWORD="apw",
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
MAIL = "27doej@shadysideacademy.org"

# E is U.S. History. Sep 8 2026 is cycle day 1, Sep 11 is day 4.
SEED = """Jane Doe
27 - Senior

@project US History Paper - 2026-09-07 - 2027-01-15 - yellow

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

@hw E - before - Prospectus - 2026-09-08 - US History Paper - done
@hw E - before - Sources - 2026-09-11 - US History Paper
@hw E - before - Outline - 2026-10-02 - US History Paper
@hw A - before - Ordinary homework - 2026-09-09

@pref time_format = 12h
"""

def login():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
        {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
    op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
        {"email": MAIL, "password": "pw123456"}).encode()))
    return op

op = login()
op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
    {"schedule_text": SEED}).encode()))

# ---- the limits moved, because a milestone IS an @hw line -------------------
sys.path.insert(0, APP_DIR)
import app as appmod
ck(appmod.MAX_HW >= 30,
   f"the homework limit leaves room for several projects ({appmod.MAX_HW})")
ck(appmod.MAX_PROJECTS == 12, f"projects are capped too ({appmod.MAX_PROJECTS})")

too_many = SEED + "".join(
    f"@project P{i} - 2026-09-07 - 2026-10-07 - blue\n" for i in range(12))
r = op.open(urllib.request.Request(BASE + "/edit",
    urllib.parse.urlencode({"schedule_text": too_many}).encode()))
ck("limit is 12" in r.read().decode(), "13 projects is refused by the server")
ck("US History Paper" in op.open(BASE + "/api/my-schedule").read().decode()
   and "P11" not in op.open(BASE + "/api/my-schedule").read().decode(),
   "and the refused save changed nothing")

# ---- ticking one off, from the schedule page --------------------------------
def post_json(path, payload):
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.loads(op.open(req).read().decode()), 200
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code

body, code = post_json("/api/quick-done",
                       {"line": "E - before - Sources - 2026-09-11 - US History Paper",
                        "done": True})
ck(body.get("ok"), f"a milestone can be ticked off without opening the form ({body})")
txt = op.open(BASE + "/api/my-schedule").read().decode()
ck("Sources - 2026-09-11 - US History Paper - done" in txt,
   f"and the line says so afterwards")

body, code = post_json("/api/quick-done",
                       {"line": "E - before - Sources - 2026-09-11 - US History Paper - done",
                        "done": False})
txt = op.open(BASE + "/api/my-schedule").read().decode()
ck(body.get("ok") and "Sources - 2026-09-11 - US History Paper\n" in txt,
   f"unticking works and does not leave two 'done's ({body})")

body, code = post_json("/api/quick-done", {"line": "E - before - Nope - 2026-09-11"})
ck(code == 404, f"a line that isn't there is refused, not half-applied ({code})")

# put it back to done for the browser half
post_json("/api/quick-done",
          {"line": "E - before - Sources - 2026-09-11 - US History Paper", "done": True})

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
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(700)

        # ---- the band, not a block ----------------------------------------
        band = pg.evaluate("""() => {
            const ad = document.getElementById('allday');
            if (!ad || ad.style.display === 'none') return null;
            return {bars: [...ad.querySelectorAll('.pbar')]
                      .map(b => b.textContent.trim()).filter(Boolean),
                    cols: ad.children.length};
        }""")
        ck(band, "a project with a milestone this week gets a band above the grid")
        ck(band and any("US History Paper" in t for t in band["bars"]),
           f"named on the band ({band and band['bars']})")
        ck(band and any("2/3" in t for t in band["bars"]),
           f"with progress counted from its milestones ({band and band['bars']})")
        # a milestone chip inside a class block mentions the project, which is
        # fine; what must not exist is a BLOCK named after the project, since
        # a project has no time of day to put one at
        ck(not pg.evaluate("""() => [...document.querySelectorAll('#gbody .block .bname')]
              .some(e => (e.textContent||'').includes('US History Paper'))"""),
           "and it is NOT drawn as a block in the day (it has no time of day)")

        # ---- but a quiet semester-long project is not permanent wallpaper --
        # Larry's point: a project that runs all semester sits on top of the
        # most-used view every single week and stops being read. It shows when
        # it has something to say this week, and the month view carries the
        # whole span.
        pg.evaluate("() => { for (let i=0;i<6;i++) nextWk.click(); }")
        pg.wait_for_timeout(1200)
        quiet = pg.evaluate("""() => {
            const ad = document.getElementById('allday');
            const wk = document.getElementById('wkLabel').textContent;
            return {shown: ad && ad.style.display !== 'none', week: wk};
        }""")
        ck(quiet and not quiet["shown"],
           f"a week with no milestone and no deadline shows no band ({quiet})")
        # every segment of a bar is the same height, labelled or not
        pg.click("#todayBtn")
        pg.wait_for_timeout(900)
        hs = pg.eval_on_selector_all("#allday .pbar",
            "b => b.filter(x => x.style.visibility !== 'hidden')"
            "     .map(x => Math.round(x.getBoundingClientRect().height))")
        ck(hs and len(set(hs)) == 1,
           f"the bar is the same thickness all the way across ({hs})")

        # ---- milestones still land on the right class ---------------------
        where = pg.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#gbody .col').forEach(col => {
                col.querySelectorAll('.hw').forEach(h => {
                    const blk = h.closest('.block');
                    out.push({day: col.dataset.k, text: h.textContent.trim(),
                              inside: blk ? (blk.querySelector('.bname')||{}).textContent : null,
                              done: h.classList.contains('done')});
                });
            });
            return out;
        }""")
        sources = next((x for x in where if "Sources" in x["text"]), None)
        ck(sources and sources["day"] == "2026-09-11",
           f"a milestone is on its own date ({sources})")
        ck(sources and sources["inside"] == "U.S. History",
           f"inside the class it is due in ({sources and sources['inside']})")
        ck(sources and sources["done"], "and shows as done")
        ck(sources and "US History Paper" in sources["text"],
           f"the chip names its project ({sources and sources['text']!r})")
        plain = next((x for x in where if "Ordinary" in x["text"]), None)
        ck(plain and not plain["done"] and "·" not in plain["text"],
           f"homework with no project is unchanged ({plain})")

        # ---- one click, two choices ---------------------------------------
        pg.eval_on_selector("#gbody .hw", "e => e.click()")
        pg.wait_for_timeout(400)
        menu = pg.text_content(".hwmenu") if pg.is_visible(".hwmenu") else ""
        ck("Mark" in menu and "Delete" in menu,
           f"clicking a milestone offers done and delete ({menu!r})")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        ck(not pg.is_visible(".hwmenu"), "Escape closes it")

        # actually tick one through the menu
        pg.evaluate("""() => {
            const h = [...document.querySelectorAll('#gbody .hw')]
                .find(e => e.textContent.includes('Ordinary'));
            h.click();
        }""")
        pg.wait_for_timeout(400)
        pg.evaluate("""() => [...document.querySelectorAll('.hwmenu button')]
            .find(b => b.textContent.includes('Mark done')).click()""")
        pg.wait_for_timeout(900)
        after = op.open(BASE + "/api/my-schedule").read().decode()
        ck("Ordinary homework - 2026-09-09" in after and "done" in
           [l for l in after.split("\n") if "Ordinary" in l][0],
           f"the menu really writes it: {[l for l in after.split(chr(10)) if 'Ordinary' in l]}")

        # ---- the month view shows the same project ------------------------
        pg.evaluate("() => document.querySelectorAll('#viewSeg button')[1].click()")
        pg.wait_for_timeout(900)
        names = pg.eval_on_selector_all("#gbody .mchip.pname",
                                        "b => b.map(x => x.textContent.trim())")
        ck(any("US History Paper" in t for t in names),
           f"the month names the project ({names})")
        ck(any("2/3" in t for t in names) or any("3/3" in t for t in names),
           f"with the same count as the week view ({names})")
        ck(len(names) == 1,
           f"named once in the grid, not on every Monday ({names})")
        spans = pg.evaluate("""() => [...document.querySelectorAll('#gbody .mcell')]
            .filter(c => c.querySelector('.prail')).length""")
        ck(spans > 20, f"the rail really spans the month, not one day ({spans} cells)")
        rails = pg.eval_on_selector_all("#gbody .prail",
            "r => r.map(x => Math.round(x.getBoundingClientRect().height))")
        ck(rails and len(set(rails)) == 1,
           f"and it is the same thickness on every one of them ({sorted(set(rails))})")

        ck(not errs, f"no page errors: {errs[:3]}")

        # ---- and the form can build one without touching the text mode ----
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#addProj")
        rows = pg.eval_on_selector_all("#projRows .prrow", "r => r.length")
        ck(rows == 1, f"the saved project comes back as a row ({rows})")
        ck(pg.input_value("#projRows .pr_name") == "US History Paper",
           "with its name")
        sel = pg.eval_on_selector_all("#hwRows .hw_proj option",
                                      "o => o.map(x => x.value)")
        ck("US History Paper" in sel,
           f"and every homework row can be attached to it ({sel})")
        picked = pg.eval_on_selector_all("#hwRows .hw_proj", "s => s.map(x => x.value)")
        ck(picked.count("US History Paper") == 3,
           f"the three milestones point at it ({picked})")
        ticked = pg.eval_on_selector_all("#hwRows .hw_done", "c => c.map(x => x.checked)")
        ck(ticked.count(True) == 3, f"and the ticks survived the round trip ({ticked})")

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
