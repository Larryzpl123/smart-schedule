# -*- coding: utf-8 -*-
"""The list, and the all-day band it needed.

A week grid answers "where am I at 10:05". It is bad at "what is coming and
what have I already missed": a deadline three weeks out is three page-turns
away, and an overdue one is behind you where nobody looks.

Two things had to exist first:
  - all-day events. An imported deadline has no time of day. Drawing it as a
    block would either invent one or fill the column, so it goes in the band
    above the grid, next to the projects.
  - daySpecs(). The list must not re-derive "does this meet today" -- that is
    exactly how a repeating event ends up in one view and missing from another.
"""
import os, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, http.cookiejar

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_ls_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5213"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ls", ADMIN_PASSWORD="apw",
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
SCHED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - AP Calculus BC - MA560 - 1 - Ms. Rivera - R 210 - red
E - U.S. History - HI300 - 5 - Mr. Bennett - R 130 - yellow

@sport Cross Country - Mon,Wed - 15:45-17:15 - 08.25-11.05 - cyan
@event Tue - Piano lesson - 17:30-18:30 - pink
@event 2026-09-30 - Dentist - 14:00-15:00 - gray
@event 2026-09-08 - SAT registration closes - allday - orange
@event 2026-09-11 - CMU ED1 deadline - allday - red
@event 2026-11-30 - UC deadline - allday - red
@project History Paper - 2026-09-07 - 2026-11-20 - yellow
@hw E - before - Prospectus - 2026-09-17 - History Paper
@hw A - before - Overdue thing - 2026-08-31
@hw E - before - Sources - 2026-09-10 - History Paper
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

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install(time="2026-09-09T15:00:00Z")     # Wed 9 Sep, 11:00 EDT
        pg.goto(BASE + "/login")
        pg.fill('input[name=email]', MAIL)
        pg.fill('input[name=password]', "pw123456")
        pg.click('button[type=submit]')
        pg.wait_for_load_state()

        # ---- the all-day band, in the week view ------------------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col")
        pg.wait_for_timeout(800)
        band = pg.evaluate("""() => {
            const ad = document.getElementById('allday');
            const bars = [...ad.querySelectorAll('.pbar')]
                .filter(b => b.style.visibility !== 'hidden');
            return {disp: ad.style.display,
                    label: (ad.querySelector('.adlab')||{}).textContent,
                    texts: bars.map(b => b.textContent.trim()).filter(Boolean),
                    ev: [...ad.querySelectorAll('.pbar.adev')].map(b => b.dataset.src)};
        }""")
        ck("CMU ED1 deadline" in " ".join(band["texts"]),
           f"an all-day event shows in the band above the grid ({band['texts']})")
        ck(band["label"] == "all day",
           f"and the band says so rather than still saying 'projects' ({band['label']})")
        ck(any("CMU ED1" in (s or "") for s in band["ev"]),
           "the pill carries its own source line, so it can be clicked")
        ck(pg.evaluate("""() => [...document.querySelectorAll('#gbody .block')]
              .some(b => (b.textContent||'').includes('CMU ED1'))""") is False,
           "and it is NOT drawn as a block in the hour grid -- it has no hour")

        # a deadline in November is not this week, so it is not in the band
        ck("UC deadline" not in " ".join(band["texts"]),
           "an all-day event in another week stays out of this week's band")

        # ---- two deadlines on different days share a line ----------------
        # One row per item made the band as tall as the number of all-day
        # things in the week, nearly all of it empty: Tuesday's pill sat on
        # the first line and Friday's on the second, so they stepped down the
        # band and read as a rendering fault. Larry reported it with a
        # screenshot of exactly that.
        pack = pg.evaluate("""() => {
            const ad = document.getElementById('allday');
            const pills = [...ad.querySelectorAll('.pbar.adev')];
            const cells = [...ad.querySelectorAll('.adcell')];
            return {tops: pills.map(b => Math.round(b.getBoundingClientRect().top)),
                    texts: pills.map(b => b.textContent.trim()),
                    perCell: cells.map(c => c.children.length)};
        }""")
        ck(len(pack["tops"]) == 2,
           f"both of this week's deadlines are drawn ({pack['texts']})")
        ck(len(set(pack["tops"])) == 1,
           f"and they sit on the SAME line, because a one-day thing on Tuesday "
           f"and one on Friday do not need a line each ({pack['tops']})")
        # a project really does need its own line -- it is a bar across the
        # week, not a pill -- so two lines here, not three
        ck(set(pack["perCell"]) == {2},
           f"so the band is a project line plus ONE deadline line, not one "
           f"line per deadline ({pack['perCell']})")

        # ---- the list --------------------------------------------------
        pg.click('#viewSeg button[data-v="list"]')
        pg.wait_for_timeout(900)
        ck(pg.evaluate("()=>document.getElementById('gbody').className") == "lview",
           "the List button switches the grid for a list")
        body = pg.text_content("#gbody")
        ck("Overdue" in body and "Overdue thing" in body,
           "unticked homework with a date behind you is at the top, under Overdue")
        ck("Prospectus" in body, "and the milestone that is coming is in it")
        ck("CMU ED1 deadline" in body, "so is the all-day deadline")
        ck("UC deadline" not in body,
           "a deadline past the end of the two-month window is not in it")
        ck("Piano lesson" not in body,
           "but not the standing weekly lesson: 'Due' means things with a "
           "deadline, not the routine")
        ck("Cross Country" not in body, "nor practice")
        ck("Dentist" in body, "a one-off appointment IS a thing with a date")

        # homework whose period does not meet that day
        ck("no class that day" in body,
           "homework dated to a day its own period does not meet is still "
           "listed -- the grid has nowhere to draw it, but a list must not "
           "silently lose something you are due to hand in")

        # ---- everything -------------------------------------------------
        pg.click('#scopeSeg button[data-p="all"]')
        pg.wait_for_timeout(700)
        body = pg.text_content("#gbody")
        ck("Piano lesson" in body and "Cross Country" in body
           and "AP Calculus BC" in body,
           "Everything adds the classes, the practice and the weekly lesson")

        pg.click('#scopeSeg button[data-p="due"]')
        pg.wait_for_timeout(500)

        # ---- the range --------------------------------------------------
        pg.select_option("#spanSel", "14")
        pg.wait_for_timeout(700)
        body = pg.text_content("#gbody")
        ck("UC deadline" not in body,
           "two weeks does not reach a November deadline")
        ck("Prospectus" in body, "but does reach next week's milestone")
        pg.select_option("#spanSel", "400")
        pg.wait_for_timeout(1200)
        ck("UC deadline" in pg.text_content("#gbody"), "All reaches it again")

        # the setting is remembered, like the theme and the view
        pg.reload()
        pg.wait_for_timeout(1200)
        ck(pg.evaluate("()=>[VIEW,LSPAN,LSCOPE]") == ["list", 400, "due"],
           "the view, the range and the filter all survive a reload")
        ck(pg.evaluate("""()=>{
             const v=document.querySelector('#viewSeg button[data-v="list"]');
             return v.classList.contains('on')
               && document.getElementById('spanSel').style.display!=='none'
               && document.getElementById('daysSeg').style.display==='none';}"""),
           "and the toolbar comes back matching them -- the remembered view "
           "used to be applied to the grid but not to the buttons")

        # ---- one click opens the menu, exactly like a block -------------
        pg.select_option("#spanSel", "60")
        pg.wait_for_timeout(700)
        pg.click('.lrow[data-srckind="hw"]')
        pg.wait_for_timeout(300)
        ck(pg.evaluate("()=>!!document.querySelector('.hwmenu')"),
           "one click on a list row opens the same little menu")
        ck("Mark done" in (pg.text_content(".hwmenu") or ""),
           "with the same options")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)

        # ---- Pick date in the list moves the LIST ------------------------
        # It used to move weekStart, which the list is not built out of, so
        # "Go" did nothing at all in this view.
        pg.click("#pickBtn")
        pg.wait_for_timeout(300)
        pg.select_option("#dlgY", "2026")
        pg.select_option("#dlgM", "10")          # November
        pg.wait_for_timeout(200)
        pg.select_option("#dlgD", "1")
        pg.click("#dlgGo")
        pg.wait_for_timeout(900)
        ck("Nov" in (pg.text_content("#wkLabel") or ""),
           f"picking 1 November starts the list there "
           f"({pg.text_content('#wkLabel')!r})")
        ck("UC deadline" in pg.text_content("#gbody"),
           "so a November deadline is on screen without changing the range")
        ck("Overdue" not in pg.text_content("#gbody"),
           "and there is no Overdue heading when the window does not start "
           "at today -- 'overdue' would mean nothing there")
        pg.click("#todayBtn")
        pg.wait_for_timeout(900)
        ck("Overdue" in pg.text_content("#gbody"), "Today brings it back")

        # ---- and the week view still works after all that ---------------
        pg.click('#viewSeg button[data-v="week"]')
        pg.wait_for_timeout(800)
        ck(pg.evaluate("()=>document.querySelectorAll('#gbody .col').length") >= 5,
           "going back to the week draws the columns again")
        ck(pg.evaluate("()=>document.getElementById('ghead').style.display") != "none",
           "and the day headings come back")
        ck(not errs, f"no page errors anywhere: {errs[:3]}")

        # ---- an all-day event can be made from the popup ------------------
        # The format, the band, the list and the importer can all say "all
        # day"; if the double-click popup cannot, then in practice the only
        # way to get one is to import a file or hand-edit text.
        pg.evaluate("""() => {
            const col = document.querySelectorAll('#gbody .col')[2];
            const r = col.getBoundingClientRect();
            col.dispatchEvent(new MouseEvent('dblclick', {bubbles: true,
                clientX: r.left + 20, clientY: r.top + 200}));
        }""")
        pg.wait_for_timeout(400)
        pg.fill("#qName", "Passport expires")
        pg.check("#qAllDay")
        pg.wait_for_timeout(200)
        ck(pg.is_hidden("#qStart"),
           "ticking all day takes the time pickers off the popup")
        pg.select_option("#qColor", "red")
        pg.click("#qSave")
        pg.wait_for_timeout(1200)
        saved = op.open(BASE + "/api/my-schedule").read().decode()
        line = [l for l in saved.split("\n") if "Passport expires" in l]
        ck(line and " - allday - red" in line[0],
           f"and the line it writes says allday ({line})")
        ck(pg.evaluate("""() => [...document.querySelectorAll('#allday .pbar')]
              .some(b => (b.textContent||'').includes('Passport'))"""),
           "and it appears in the band straight away, not on the hour grid")

        # ---- the .ics export --------------------------------------------
        out = pg.evaluate("""() => {
            const y = YEARS.find(v => v.id === curYear.id);
            return buildICS({from: new Date(y.startYear,7,1),
                             to: new Date(y.startYear+1,6,31),
                             calName:'T', classes:true, hw:true, events:true,
                             sports:true, projects:true, school:false});
        }""")
        t = out["text"]
        lines = t.split("\r\n")
        ck(t.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in t,
           "the export is a calendar")
        ck("\r\n" in t, "with CRLF line endings, as RFC 5545 requires")
        ck(all(len(l.encode("utf-8")) <= 75 for l in lines),
           "and every line folded to 75 OCTETS -- a Chinese summary is three "
           "bytes a glyph, and folding on character count makes lines Apple "
           "Calendar rejects")
        ck(t.count("BEGIN:VEVENT") == t.count("END:VEVENT") == out["count"],
           f"every VEVENT is closed ({out['count']})")

        def field(summary, key):
            for blk in t.split("BEGIN:VEVENT")[1:]:
                if "SUMMARY:" + summary in blk:
                    for l in blk.split("\r\n"):
                        if l.startswith(key):
                            return l
            return None

        # A class at 08:15 Pittsburgh is 12:15Z in September (EDT, -4) and
        # 13:15Z in January (EST, -5). One fixed offset for the whole year
        # would put every class an hour out for half of it, and a schedule
        # that is right until November is worse than one that is never right.
        starts = sorted({l[len("DTSTART:"):] for l in lines
                         if l.startswith("DTSTART:") and l.endswith("Z")})
        ck(all(x.endswith("Z") for x in starts) and len(starts) > 50,
           f"timed events go out as absolute instants, so no client has to "
           f"guess what TZID=America/New_York means without a VTIMEZONE "
           f"block ({len(starts)})")
        from datetime import datetime as _dt, timezone as _tz
        try:
            from zoneinfo import ZoneInfo as _Z
            ny = _Z("America/New_York")
            local = {}
            wday = {}                      # (hour, minute) -> is it a Wednesday
            for blk in t.split("BEGIN:VEVENT")[1:]:
                if "SUMMARY:AP Calculus BC" not in blk:
                    continue
                for l in blk.split("\r\n"):
                    if l.startswith("DTSTART:") and l.endswith("Z"):
                        v = l[len("DTSTART:"):]
                        u = _dt.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=_tz.utc)
                        n = u.astimezone(ny)
                        local.setdefault(v[:6], []).append((n.hour, n.minute, n.weekday() == 2))
            months = sorted(local)
            edt = [m for m in months if m in ("202609", "202610")]
            est = [m for m in months if m in ("202612", "202701")]
            ck(edt and est,
               f"the export spans both sides of the November clock change "
               f"({months})")
            # Period A moves round the rotation, so a month holds several
            # start times -- but a full month holds ALL of them, so the set
            # from the EDT side and the set from the EST side have to be the
            # same set. A fixed offset would shift one of them by an hour.
            warm = set().union(*[set(local[m]) for m in edt])
            cold = set().union(*[set(local[m]) for m in est])
            # The first version asked for cold <= warm: every start time seen in
            # winter must also have been seen in autumn. That holds only by the
            # luck of the rotation -- which Wednesdays period A happens to land
            # on -- and one no-class day in October (10/28) shifted the
            # rotation and broke it without anything being wrong.
            # What DST actually threatens is a time an hour off. So: every
            # exported start, on both sides of the change, must be a real start
            # time for ITS kind of day, read from the bell schedule itself.
            import re as _re
            sched = open(os.path.join(APP_DIR, "schedule.txt"), encoding="utf-8").read()
            hm = lambda x: tuple(int(y) for y in x.split(":"))
            normal_ok, wed_ok = set(), set()
            for line in sched.splitlines():
                if line.startswith("@day"):
                    for tok in line.split("=", 1)[1].split():
                        for tr in tok.split(":", 1)[1].split("|"):
                            normal_ok.add(hm(tr.split("-")[0]))
                elif line.startswith("@wed"):
                    wed_ok.add(hm(line.split("=>")[1].strip().split("-")[0]))
            bad = sorted((h, m, "Wed" if w else "") for (h, m, w) in warm | cold
                         if (h, m) not in (wed_ok if w else normal_ok))
            ck(len(warm) > 1 and len(cold) > 1 and not bad,
               f"and it reads back at real bell times on both sides of it "
               f"(off the bell schedule: {bad})")
        except ImportError:
            print("  SKIP  zoneinfo not available for the DST check")

        ck(field("UC deadline", "DTSTART;VALUE=DATE") == "DTSTART;VALUE=DATE:20261130",
           f"an all-day event goes out as a DATE ({field('UC deadline','DTSTART')})")
        ck(field("UC deadline", "DTEND;VALUE=DATE") == "DTEND;VALUE=DATE:20261201",
           "with the exclusive end the format asks for, so it is ONE day in "
           "every client")
        ck(field("History Paper", "DTSTART;VALUE=DATE") == "DTSTART;VALUE=DATE:20260907"
           and field("History Paper", "DTEND;VALUE=DATE") == "DTEND;VALUE=DATE:20261121",
           "a project is one multi-day all-day event")
        ck(field("AP Calculus BC", "LOCATION") == "LOCATION:R 210",
           "a class carries its room")

        # A UID that changes between exports makes a second copy of every
        # lesson in the phone's calendar on the second download.
        again = pg.evaluate("""() => {
            const y = YEARS.find(v => v.id === curYear.id);
            return buildICS({from: new Date(y.startYear,7,1),
                             to: new Date(y.startYear+1,6,31),
                             calName:'T', classes:true, hw:true, events:true,
                             sports:true, projects:true, school:false}).text;
        }""")
        uids = lambda s: [l for l in s.split("\r\n") if l.startswith("UID:")]
        ck(uids(t) == uids(again) and len(set(uids(t))) == len(uids(t)),
           "UIDs are stable between exports and unique within one, so a "
           "second download updates the phone rather than doubling it")

        # what the checkboxes actually do
        few = pg.evaluate("""() => {
            const y = YEARS.find(v => v.id === curYear.id);
            return buildICS({from: new Date(y.startYear,7,1),
                             to: new Date(y.startYear+1,6,31),
                             calName:'T', classes:false, hw:false, events:true,
                             sports:false, projects:false, school:false});
        }""")
        ck("AP Calculus" not in few["text"] and "Cross Country" not in few["text"]
           and "Piano lesson" in few["text"],
           "unticking Classes really leaves the classes out")
        ck(few["count"] < out["count"], "and the file is smaller for it")

        # the dialog is reachable and says what it is
        pg.click("#icsBtn")
        pg.wait_for_timeout(300)
        dlg = pg.text_content("#icsDlg")
        ck("copy" in dlg and "not a subscription" in dlg,
           "the download dialog says plainly that the file will not update itself")
        pg.click("#icsCancel")
        ck(not errs, f"still no page errors: {errs[:3]}")
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
