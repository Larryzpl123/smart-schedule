"""The classmate's complaint: adding an event meant typing the raw text format.
Now it's dropdowns. This builds an event and a homework item entirely by
choosing options -- never typing the format -- and checks the saved text, then
checks that reloading turns the text back into the right dropdown state."""
import os, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


WORK = tempfile.mkdtemp(prefix="ssa_ef_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5142"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="eftest", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

SCHED_LATE = """Jane Doe\n27 - Junior\n\n@term Fall 2026 = 08.25-01.15\nA - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red\n\n@event Tue - Late rehearsal - 22:00-23:00 - purple\n@pref time_format = 24h\n"""

FAIL = []
def ck(c, l):
    print(("  PASS  " if c else "  FAIL  ") + l)
    if not c:
        FAIL.append(l)

BASE = "http://127.0.0.1:" + PORT
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - red - Ea
E - Playwriting - EN424A - 1 - Ms. Ferraro - R 110 - orange - La

@pref time_format = 12h
"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1400, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

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

        # ---------- build a WEEKLY event with only clicks ----------
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#addEvent")
        pg.click("#addEvent")
        row = pg.locator("#eventRows .evrow").first
        row.locator(".ev_mode").select_option("weekly")
        row.locator(".ev_dow[value=Mon]").check()
        row.locator(".ev_dow[value=Wed]").check()
        row.locator(".ev_name").fill("Piano")
        row.locator(".ev_start").select_option("16:00")
        row.locator(".ev_dur").fill("60")
        row.locator(".ev_color").select_option("pink")

        # ---------- and a MONTHLY (nth weekday) one ----------
        pg.click("#addEvent")
        row2 = pg.locator("#eventRows .evrow").nth(1)
        row2.locator(".ev_mode").select_option("nth")
        row2.locator(".ev_nth").select_option("1")
        row2.locator(".ev_dowsel").select_option("Thu")
        row2.locator(".ev_name").fill("Form Meeting")
        row2.locator(".ev_start").select_option("10:05")
        row2.locator(".ev_dur").fill("25")

        # ---------- homework: pick the class from a dropdown ----------
        pg.click("#addHw")
        hw = pg.locator("#hwRows .hwrow").first
        opts = hw.locator(".hw_per option").all_text_contents()
        ck(any("A: Game Design" in o for o in opts),
           f"the class dropdown names the actual courses ({opts[:3]})")
        hw.locator(".hw_per").select_option("E")
        hw.locator(".hw_when").select_option("before")
        hw.locator(".hw_name").fill("Read ch.4")
        hw.locator(".hw_date").fill("2026-09-10")

        # what would be saved?
        txt = pg.evaluate("formToText()")
        ck("@event Mon,Wed - Piano - 16:00-17:00 - pink" in txt,
           f"weekly event built from dropdowns ({[l for l in txt.split(chr(10)) if l.startswith('@event')]})")
        ck("@event monthly Thu#1 - Form Meeting - 10:05-10:30 - gray" in txt,
           "monthly-by-weekday event built from dropdowns")
        ck("@hw E - before - Read ch.4 - 2026-09-10" in txt,
           f"homework built from dropdowns ({[l for l in txt.split(chr(10)) if l.startswith('@hw')]})")

        # duration really drives the end time
        row.locator(".ev_dur").fill("90")
        txt2 = pg.evaluate("formToText()")
        ck("Piano - 16:00-17:30" in txt2, "changing the duration moves the end time")

        # save it for real
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        saved = pg.evaluate("fetch('/api/my-schedule').then(r=>r.text())")
        ck("@event Mon,Wed - Piano - 16:00-17:30 - pink" in saved, "saved to the server")

        # ---------- reload: text must come back as dropdown state ----------
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        st = pg.evaluate("""() => {
            const r = document.querySelectorAll('#eventRows .evrow')[0];
            return {mode: r.querySelector('.ev_mode').value,
                    dows: Array.from(r.querySelectorAll('.ev_dow')).filter(c=>c.checked).map(c=>c.value),
                    name: r.querySelector('.ev_name').value,
                    start: r.querySelector('.ev_start').value,
                    dur: r.querySelector('.ev_dur').value,
                    color: r.querySelector('.ev_color').value};
        }""")
        ck(st["mode"] == "weekly" and st["dows"] == ["Mon", "Wed"] and st["name"] == "Piano"
           and st["start"] == "16:00" and st["dur"] == "90" and st["color"] == "pink",
           f"a saved event reloads into the right dropdowns ({st})")

        st2 = pg.evaluate("""() => {
            const r = document.querySelectorAll('#eventRows .evrow')[1];
            return {mode: r.querySelector('.ev_mode').value,
                    nth: r.querySelector('.ev_nth').value,
                    dow: r.querySelector('.ev_dowsel').value};
        }""")
        ck(st2["mode"] == "nth" and st2["nth"] == "1" and st2["dow"] == "Thu",
           f"the monthly event reloads as 1st/Thu ({st2})")

        # round-trip must not corrupt anything
        again = pg.evaluate("formToText()")
        for line in ["@event Mon,Wed - Piano - 16:00-17:30 - pink",
                     "@event monthly Thu#1 - Form Meeting - 10:05-10:30 - gray",
                     "@hw E - before - Read ch.4 - 2026-09-10"]:
            ck(line in again, f"round-trips unchanged: {line}")

        # ---------- an odd hand-written line is preserved, not eaten ----------
        pg.click("#modeText")
        cur = pg.input_value("#rawText")
        pg.fill("#rawText", cur.replace("@pref", "@event Day 3 - Weird custom thing - 09:00-09:30 - red\n\n@pref"))
        pg.click("#modeForm")
        pg.wait_for_timeout(300)
        raws = pg.eval_on_selector_all("#eventRows .evrow.raw input", "n=>n.map(x=>x.value)")
        ck(any("Weird custom thing" in v for v in raws),
           f"an unparseable line survives as an editable raw row ({raws})")
        back = pg.evaluate("formToText()")
        ck("@event Day 3 - Weird custom thing - 09:00-09:30 - red" in back,
           "and it comes back out of the form untouched")

        # the class dropdown must be labelled on LOAD too, not only after a click
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#hwRows .hwrow")
        pg.wait_for_timeout(200)
        loaded = pg.eval_on_selector_all("#hwRows .hwrow .hw_per option",
                                         "n=>n.map(x=>x.textContent)")
        ck(any("Game Design" in o for o in loaded),
           f"course names are in the dropdown on page load, not just after +Add ({loaded[:3]})")

        ck(not errs, f"no page errors: {errs}")

        # ---- every N days --------------------------------------------------
        # "First Tuesday of the month" was always there (Monthly (weekday),
        # 1st, Tue). "Every 90 days" was not, and it is not the same thing --
        # it falls on no weekday and on no day of the month.
        pg.goto(BASE + "/edit"); pg.wait_for_selector("#addEvent")
        pg.click("#addEvent")
        row = pg.query_selector_all("#eventRows .evrow")[-1]
        opts = [o.get_attribute("value") for o in row.query_selector_all(".ev_mode option")]
        ck("everyN" in opts, f"the form offers every-N-days ({opts})")
        row.query_selector(".ev_name").fill("Renew hosting")
        row.query_selector(".ev_mode").select_option("everyN")
        pg.wait_for_timeout(300)
        row = pg.query_selector_all("#eventRows .evrow")[-1]
        row.query_selector(".ev_everyn").fill("90")
        row.query_selector(".ev_everyfrom").fill("2026-09-07")
        row.query_selector(".ev_start").select_option("19:00")
        row.query_selector(".ev_dur").fill("15")
        pg.click("#modeText")
        txt2 = pg.input_value("#rawText")
        ck("@event every 90 from 2026-09-07 - Renew hosting - 19:00-19:15" in txt2,
           f"and writes it: {[l for l in txt2.splitlines() if 'Renew' in l]}")
        pg.click("#modeForm"); pg.wait_for_timeout(400)
        got = [r for r in pg.query_selector_all("#eventRows .evrow")
               if r.query_selector(".ev_name")
               and r.query_selector(".ev_name").input_value() == "Renew hosting"]
        ck(got and got[0].query_selector(".ev_mode").input_value() == "everyN",
           "reading it back gives the picker, not a raw line")
        ck(got and got[0].query_selector(".ev_everyn").input_value() == "90",
           "with the interval kept")

        # ---- the form's caps come from the server -------------------------
        # MAX_HW went to 60 when project milestones arrived and this file still
        # said 20, so the form refused the 21st milestone that the server would
        # happily have taken. The numbers are rendered in now, not copied.
        import importlib, sys as _sys
        _sys.path.insert(0, APP_DIR)
        _app = importlib.import_module("app")
        caps = pg.evaluate("""() => ({events: MAX_EVENTS, hw: MAX_HW,
                                      sports: MAX_SPORTS, projects: MAX_PROJECTS})""")
        ck(caps["events"] == _app.MAX_EVENTS and caps["hw"] == _app.MAX_HW
           and caps["sports"] == _app.MAX_SPORTS
           and caps["projects"] == _app.MAX_PROJECTS,
           f"the form agrees with the server ({caps} vs "
           f"{_app.MAX_EVENTS}/{_app.MAX_HW}/{_app.MAX_SPORTS}/{_app.MAX_PROJECTS})")
        page = pg.content()
        ck("Max 20." not in page and "(max 20)" not in page,
           "and the help text does not still say 20")

        # ---- the season window used to be read and then thrown away -------
        # The row PARSED "09.01-12.20" off the line and then wrote the line
        # back without it. Open /edit with a term-long piano lesson in it,
        # press Save without touching anything, and the lesson now runs all
        # year. Nobody reported it because nothing on screen said the window
        # was there at all.
        pg.goto(BASE + "/edit")
        pg.click("#modeText")
        pg.fill("#rawText", SEED.replace(
            "@pref time_format = 12h",
            "@event Mon,Wed - Term piano - 16:00-17:00 - 09.01-12.20 - pink\n"
            "@pref time_format = 12h"))
        pg.click('button[type=submit]')
        pg.wait_for_load_state()
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        row = [r for r in pg.query_selector_all("#eventRows .evrow")
               if r.query_selector(".ev_name")
               and r.query_selector(".ev_name").input_value() == "Term piano"][0]
        ck(row.query_selector(".ev_from").input_value().endswith("-09-01")
           and row.query_selector(".ev_to").input_value().endswith("-12-20"),
           "the form shows the event's season window")
        pg.click("#modeText")
        txt3 = pg.input_value("#rawText")
        ck("@event Mon,Wed - Term piano - 16:00-17:00 - 09.01-12.20 - pink" in txt3,
           f"and writes it straight back out: "
           f"{[l for l in txt3.splitlines() if 'Term piano' in l]}")

        # ---- all day ------------------------------------------------------
        pg.click("#modeForm"); pg.wait_for_timeout(400)
        pg.click("#addEvent")
        row = pg.query_selector_all("#eventRows .evrow")[-1]
        row.query_selector(".ev_name").fill("UC deadline")
        row.query_selector(".ev_date").fill("2026-11-30")
        row.query_selector(".ev_allday").check()
        row.query_selector(".ev_color").select_option("red")
        ck(row.query_selector(".ev_start").is_hidden(),
           "ticking all-day takes the time pickers away rather than leaving "
           "them up and ignored")
        pg.click("#modeText")
        txt4 = pg.input_value("#rawText")
        ck("@event 2026-11-30 - UC deadline - allday - red" in txt4,
           f"and the line says allday: "
           f"{[l for l in txt4.splitlines() if 'UC deadline' in l]}")
        pg.click("#modeForm"); pg.wait_for_timeout(400)
        row = [r for r in pg.query_selector_all("#eventRows .evrow")
               if r.query_selector(".ev_name")
               and r.query_selector(".ev_name").input_value() == "UC deadline"][0]
        ck(row.query_selector(".ev_allday").is_checked(),
           "reading it back gives the tick, not a raw line")

        # ---- the form's time dropdowns follow its own Time format setting --
        pg.goto(BASE + "/edit")
        pg.wait_for_selector("#eventRows .evrow")
        def formTimes():
            return pg.evaluate("""() => {
                const sel = document.querySelector('.ev_start');
                const o = [...sel.options];
                return {labels: o.map(x => x.textContent),
                        values: o.map(x => x.value)};
            }""")
        pg.select_option("#f_timefmt", "12h")
        pg.wait_for_timeout(300)
        f12 = formTimes()
        ck("4:00 PM" in f12["labels"] and "16:00" not in f12["labels"],
           f"12-hour on this page relabels the row's time dropdown too -- a "
           f"form offering 16:00 while the grid says 4:00 PM is two apps "
           f"({f12['labels'][:3]})")
        ck("16:00" in f12["values"], "the value stays 24-hour")
        pg.select_option("#f_timefmt", "24h")
        pg.wait_for_timeout(300)
        f24 = formTimes()
        ck("16:00" in f24["labels"] and f24["values"] == f12["values"],
           "and switching back relabels without touching the values")

        # ---- the dropdown must offer every hour the grid can draw ----------
        # Larry had a 10pm event and the latest option was 7pm. The grid was
        # never the problem: fitAxis() stretches to AX_CEIL (23:00), so it
        # would have drawn the thing happily. Only the list of options said no,
        # which is the app refusing to accept something it can already display.
        vals = f24["values"]
        ck(vals[0] == "06:00",
           f"the form starts at the earliest hour the grid draws ({vals[0]})")
        ck(vals[-1] == "23:00",
           f"and ends at the latest one ({vals[-1]})")
        ck("22:00" in vals, "22:00 is selectable")

        # and the two lists must not drift apart: the grid decides, the form
        # follows. Read the grid's own limits rather than trusting a literal.
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(1200)
        lim = pg.evaluate("({floor: AX_FLOOR, ceil: AX_CEIL, "
                          "first: QTIMES[0], last: QTIMES[QTIMES.length-1]})")
        def hhmm(mins):
            return f"{mins // 60:02d}:{mins % 60:02d}"
        ck(lim["first"] == hhmm(lim["floor"]) and lim["last"] == hhmm(lim["ceil"]),
           f"the grid's own dialog covers exactly its axis range ({lim})")
        ck(vals[0] == hhmm(lim["floor"]) and vals[-1] == hhmm(lim["ceil"]),
           f"and /edit offers the same range as the grid ({vals[0]}-{vals[-1]} "
           f"vs {lim['first']}-{lim['last']})")

        # end to end: a late event entered on /edit actually appears
        pg.goto(BASE + "/edit")
        pg.wait_for_timeout(500)
        pg.click("#modeText")
        pg.fill("#rawText", SCHED_LATE)
        pg.click("#schedForm button[type=submit]")
        pg.wait_for_timeout(800)
        pg.goto(BASE + "/app")
        pg.wait_for_timeout(1500)
        drawn = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('#gbody .block').forEach(el => {
            if ((el.textContent || '').includes('Late rehearsal'))
              out.push({end: +el.dataset.end, h: el.getBoundingClientRect().height});
          });
          return {hits: out, axEnd: AX_END};
        }""")
        ck(len(drawn["hits"]) >= 1,
           f"a 22:00 event drawn on the grid ({drawn})")
        ck(drawn["axEnd"] >= 23 * 60,
           f"and the axis stretched to hold it ({drawn['axEnd']} minutes)")
        ck(all(x["h"] > 4 for x in drawn["hits"]),
           f"with real height, not clipped to nothing ({drawn['hits']})")

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
