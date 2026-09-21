"""⑧⑩⑫⑬⑭⑥⑪: custom events, homework, public events, community overrides,
special (Convocation) days, and the server-side caps.

Frontend behaviour is checked in a real browser against the real Flask app,
because the parse/render code is the thing that actually has to be right.
"""
import os, re, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_ev_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5133"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="evtest", ADMIN_PASSWORD="apw",
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

# Fall 2026 runs 08.25-01.15. Sep 8 2026 = cycle day 1 (anchors: 08-26=1, 09-04=8,
# Sep 7 is Labor Day). Day 1 period A = 08:15-09:05, E = 13:20-14:10.
SCHEDULE = """Jane Doe
27 - Junior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - orange

@event 2026-09-08 - Meet Mr. Bennett - 15:30-16:00 - gray
@event Tue - Piano - 16:30-17:30 - 09.01-12.20 - pink
@event monthly Thu#1 - Form Meeting - 11:00-11:30 - pink
@hw A - before - Read ch.4 - 2026-09-08
@hw E - during - Finish draft - 2026-09-08

@pref time_format = 24h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        # 2026-09-08 12:00 EDT = 16:00 UTC (pittNow re-expresses it either way)
        pg.clock.install(time="2026-09-08T16:00:00Z")

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
        pg.fill("#rawText", SCHEDULE)
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("@event monthly Thu#1" in saved, "@event/@hw lines survive a save")

        # ---------- ⑧ events render ----------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        names = pg.eval_on_selector_all(
            "#gbody .block .bname", "n=>n.map(x=>x.textContent)")
        ck("Meet Mr. Bennett" in names, f"one-off @event drawn on its date ({names})")
        ck("Piano" in names, "weekly @event drawn on its weekday (Tue Sep 8)")
        # ⑭ monthly: Thu#1 of Sept 2026 is Sep 3 -- NOT in the Sep 7-11 week
        ck("Form Meeting" not in names,
           "monthly Thu#1 is NOT drawn in a week that has no first-Thursday")

        # ---------- ⑭ the monthly rule itself ----------
        mres = pg.evaluate("""() => {
          const d = s => new Date(s + "T12:00:00");
          return {
            first:  matchesMonthly("Thu#1", d("2026-09-03")),
            notfirst: matchesMonthly("Thu#1", d("2026-09-10")),
            second: matchesMonthly("Thu#2", d("2026-09-10")),
            last:   matchesMonthly("Thu#-1", d("2026-09-24")),
            notlast: matchesMonthly("Thu#-1", d("2026-09-17")),
            fifteenth: matchesMonthly("15", d("2026-09-15")),
            notfifteenth: matchesMonthly("15", d("2026-09-16")),
            wrongdow: matchesMonthly("Thu#1", d("2026-09-01"))
          };
        }""")
        ck(mres["first"] and not mres["notfirst"], "Thu#1 = first Thursday only")
        ck(mres["second"], "Thu#2 = second Thursday")
        ck(mres["last"] and not mres["notlast"], "Thu#-1 = last Thursday (Sep 24, not Sep 17)")
        ck(mres["fifteenth"] and not mres["notfifteenth"], "monthly 15 = the 15th")
        ck(not mres["wrongdow"], "Thu#1 doesn't match a Tuesday")

        # ---------- ⑩ homework ----------
        hw = pg.eval_on_selector_all("#gbody .hw", "n=>n.map(x=>x.textContent)")
        ck(any("Read ch.4" in h for h in hw), f"'before' homework drawn ({hw})")
        ck(any("Finish draft" in h for h in hw), "'during' homework drawn")
        during_cls = pg.eval_on_selector_all(
            "#gbody .hw", "n=>n.map(x=>x.className)")
        ck(any("during" in c for c in during_cls), "'during' homework styled differently")
        # It must be attached to the right class at that class's real time on
        # THIS day: period A sits at 08:15 on cycle day 1. (The chip lives
        # inside its class block now, so ask which block owns it rather than
        # measuring a pixel offset.)
        owner = pg.evaluate("""() => {
            const chip = Array.from(document.querySelectorAll('#gbody .hw'))
                .find(e => e.textContent.includes('Read ch.4'));
            if (!chip) return null;
            const blk = chip.closest('.block');
            return blk ? {period: blk.dataset.period,
                          start: Number(blk.dataset.start),
                          name: blk.querySelector('.bname').textContent} : null; }""")
        ck(owner and owner["period"] == "A" and owner["start"] == 8 * 60 + 15,
           f"'before' homework hangs on period A at its real 08:15 slot ({owner})")

        b.close()
except ImportError:
    print("  SKIP  playwright not installed")

# ---------- ⑬ server-side caps (the part a form count can't enforce) ----------
import urllib.request, urllib.parse, http.cookiejar
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

post("/login", {"email": "27doej@shadysideacademy.org", "password": "pw123456"})

def save(text):
    post("/edit", {"schedule_text": text})
    return op.open(BASE + "/api/my-schedule").read().decode()

good = "Jane Doe\n27 - Junior\n\n@term Fall 2026 = 08.25-01.15\nA - X - N - 1 - T - R - red\n"
save(good)
ck("A - X - N" in op.open(BASE + "/api/my-schedule").read().decode(), "baseline save works")

# The caps live on the server and have moved twice; read them rather than
# repeating the numbers here, which is exactly the mistake the /edit form made.
import importlib, sys as _sys
_sys.path.insert(0, APP_DIR)
_app = importlib.import_module("app")

too_many_ev = good + "\n" + "\n".join(
    f"@event 2026-09-{(d % 28) + 1:02d} - E{d} - 10:00-10:30 - gray"
    for d in range(1, _app.MAX_EVENTS + 2))
after = save(too_many_ev)
ck(f"E{_app.MAX_EVENTS + 1}" not in after and "A - X - N" in after,
   f"one over the cap ({_app.MAX_EVENTS + 1}) refused, old schedule untouched")

ok_ev = good + "\n" + "\n".join(
    f"@event 2026-09-{(d % 28) + 1:02d} - E{d} - 10:00-10:30 - gray"
    for d in range(1, _app.MAX_EVENTS + 1))
after = save(ok_ev)
ck(f"E{_app.MAX_EVENTS}" in after,
   f"exactly {_app.MAX_EVENTS} events is accepted")
ck(_app.MAX_HW == 60, f"the homework cap is 60 ({_app.MAX_HW})")
ck(_app.MAX_EVENTS == 50, f"and events went to 50 ({_app.MAX_EVENTS})")
too_many_hw = good + "\n" + "\n".join(
    f"@hw A - before - H{d} - 2026-10-{(d % 28) + 1:02d}" for d in range(1, _app.MAX_HW + 2))
after = save(too_many_hw)
ck(f"H{_app.MAX_HW + 1}" not in after,
   f"one over the cap ({_app.MAX_HW + 1}) is refused by the server")

over = _app.MAX_SCHEDULE_BYTES + 1000
after = save(good + "\n" + "x" * over)
ck(len(after) < over, f"a schedule over {_app.MAX_SCHEDULE_BYTES // 1024} KB is refused")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
