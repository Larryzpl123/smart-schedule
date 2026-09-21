# -*- coding: utf-8 -*-
"""Calendars in and out.

Larry's own 本科申请.ics is the acceptance test for the importer, because it is
what a real file looks like rather than what a tidy one does: Apple's export,
folded lines, \; and \\, escapes, Chinese summaries, twelve all-day deadlines
whose DTEND is the NEXT morning, three timed events in Asia/Shanghai and
America/New_York, VALARM blocks with their own DESCRIPTION and UID, and two
VTIMEZONE blocks whose RRULEs are not the events' RRULEs.

Every one of those is a way to get it wrong:
  - read DTEND as inclusive and every deadline becomes two days long
  - miss the VALARM nesting and the summary becomes "UC 两天后截止"
  - miss the VTIMEZONE nesting and you import a 1989 daylight-saving rule
  - ignore TZID and a 09:00 Beijing call lands in the middle of second period
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

# ---------------------------------------------------------------- the file --
# Written out here rather than read from a path, so the test travels with the
# repo. It is a trimmed copy of the real one, with every awkward shape kept.
REAL = "\r\n".join([
 "BEGIN:VCALENDAR",
 "CALSCALE:GREGORIAN",
 "PRODID:-//Apple Inc.//macOS 15.7.7//EN",
 "VERSION:2.0",
 "X-WR-CALNAME:本科申请",
 "BEGIN:VTIMEZONE",
 "TZID:America/New_York",
 "BEGIN:DAYLIGHT",
 "DTSTART:20070311T020000",
 "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
 "TZNAME:EDT",
 "TZOFFSETFROM:-0500",
 "TZOFFSETTO:-0400",
 "END:DAYLIGHT",
 "END:VTIMEZONE",
 "BEGIN:VTIMEZONE",
 "TZID:Asia/Shanghai",
 "BEGIN:STANDARD",
 "DTSTART:19890917T020000",
 "RRULE:FREQ=YEARLY;UNTIL=19910914T170000Z;BYMONTH=9;BYDAY=3SU",
 "TZOFFSETFROM:+0900",
 "TZOFFSETTO:+0800",
 "END:STANDARD",
 "END:VTIMEZONE",
 "BEGIN:VEVENT",
 "DESCRIPTION:四篇 PIQ 提交\;与 ED 撞期\\,别拖到 RD。",
 "DTEND;VALUE=DATE:20261201",
 "DTSTAMP:20260907T220602Z",
 "DTSTART;VALUE=DATE:20261130",
 "SUMMARY:★ UC 系统截止(11:59pm PT)",
 "UID:larry-app-12@zhong",
 "BEGIN:VALARM",
 "ACTION:DISPLAY",
 "DESCRIPTION:UC 两天后截止",
 "TRIGGER:-P2D",
 "UID:076BE846-AEB2-4EFB-9137-044667491711",
 "END:VALARM",
 "END:VEVENT",
 "BEGIN:VEVENT",
 "DTEND;VALUE=DATE:20261102",
 "DTSTAMP:20260907T220602Z",
 "DTSTART;VALUE=DATE:20261101",
 "SUMMARY:★★ CMU Dietrich ED1 截止(按 11/1 完成)",
 "UID:larry-app-05@zhong",
 "END:VEVENT",
 "BEGIN:VEVENT",
 "DESCRIPTION:UCAS 需要 predicted grades\\, personal statement\\, 和 referen",
 " ce。三校共用一份文书\;这一行是折行过的。",
 "DTEND;VALUE=DATE:20270114",
 "DTSTART;VALUE=DATE:20270113",
 "SUMMARY:UCAS 主截止 18:00 UK(UCL / Edinburgh / KCL)",
 "UID:larry-app-13@zhong",
 "END:VEVENT",
 "BEGIN:VEVENT",
 "DTEND;TZID=Asia/Shanghai:20270112T093000",
 "DTSTART;TZID=Asia/Shanghai:20270112T090000",
 "SUMMARY:Profile in Courage",
 "UID:larry-app-20@zhong",
 "END:VEVENT",
 "BEGIN:VEVENT",
 "DTEND;TZID=America/New_York:20260908T093000",
 "DTSTART;TZID=America/New_York:20260908T090000",
 "SUMMARY:Register Oct SAT",
 "UID:larry-app-21@zhong",
 "END:VEVENT",
 "END:VCALENDAR", ""])

items, skipped, calname = ics_import.convert(REAL, "pink")
by = {it["name"]: it for it in items}

ck(calname == "本科申请", f"the calendar's own name is read ({calname})")
ck(len(items) == 5 and not skipped,
   f"all five events came through and none were dropped ({len(items)}, {skipped})")

uc = by.get("★ UC 系统截止(11:59pm PT)")
ck(uc is not None, "the Chinese summary survives, stars and brackets and all")
ck(uc and uc["line"] == "@event 2026-11-30 - ★ UC 系统截止(11:59pm PT) - allday - pink",
   f"a one-day deadline is one all-day event on ITS day ({uc and uc['line']})")
ck(uc and uc["kind"] == "event",
   "not a project: DTEND 12-01 on a DTSTART 11-30 event is the exclusive end "
   "of one day, not a second day")

ck(any("两天后" in it["name"] for it in items) is False,
   "the VALARM's own DESCRIPTION did not become anybody's summary")
ck(not any(it["date"] < "2000-01-01" for it in items),
   "no 1989 daylight-saving rule from inside VTIMEZONE turned into an event")

cmu = by.get("★★ CMU Dietrich ED1 截止(按 11/1 完成)")
ck(cmu and cmu["line"].startswith("@event 2026-11-01 "),
   f"the CMU deadline lands on 11-01 ({cmu and cmu['line']})")

# 09:00 Beijing (UTC+8) on 12 Jan is 20:00 the previous evening in Pittsburgh
# (UTC-5). Getting this wrong puts a video call in the middle of second period.
pic = by.get("Profile in Courage")
ck(pic and pic["line"] == "@event 2027-01-11 - Profile in Courage - 20:00-20:30 - pink",
   f"an Asia/Shanghai event is converted to Pittsburgh time ({pic and pic['line']})")
ck(pic and pic["note"] and "Asia/Shanghai" in pic["note"],
   "and it says so, rather than moving somebody's meeting silently")

sat = by.get("Register Oct SAT")
ck(sat and sat["line"] == "@event 2026-09-08 - Register Oct SAT - 09:00-09:30 - pink",
   f"an America/New_York event is left exactly where it was ({sat and sat['line']})")
ck(sat and not sat["note"], "with no note, because nothing moved")

ck(all(ics_import.validated_line(it["line"]) for it in items),
   "every proposed line passes the server's own grammar check")

# ---------------------------------------------------------------- shapes ----
def one(text):
    it, sk, _ = ics_import.convert(text, "gray")
    return (it[0] if it else None), (sk[0] if sk else None)

def wrap(*body):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0"] + list(body) + ["END:VCALENDAR", ""])

def vevent(*props):
    return "\r\n".join(["BEGIN:VEVENT"] + list(props) + ["END:VEVENT"])

it, _ = one(wrap(vevent("SUMMARY:Spring break", "DTSTART;VALUE=DATE:20270315",
                        "DTEND;VALUE=DATE:20270320")))
ck(it and it["kind"] == "project"
   and it["line"] == "@project Spring break - 2027-03-15 - 2027-03-19 - gray",
   f"a multi-day all-day block becomes a project band, ending on its LAST day "
   f"({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Club", "DTSTART;TZID=America/New_York:20260907T160000",
                        "DTEND;TZID=America/New_York:20260907T170000",
                        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE")))
ck(it and it["line"] == "@event Mon,Wed - Club - 16:00-17:00 - gray",
   f"a weekly rule becomes weekdays ({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Journal club",
                        "DTSTART;TZID=America/New_York:20260901T190000",
                        "DTEND;TZID=America/New_York:20260901T200000",
                        "RRULE:FREQ=MONTHLY;BYDAY=1TU")))
ck(it and it["line"] == "@event monthly Tue#1 - Journal club - 19:00-20:00 - gray",
   f"first-Tuesday-of-the-month survives ({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Rent", "DTSTART;TZID=America/New_York:20260915T090000",
                        "DTEND;TZID=America/New_York:20260915T091500",
                        "RRULE:FREQ=MONTHLY;BYMONTHDAY=15")))
ck(it and "monthly 15" in it["line"], f"monthly by date ({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Renew", "DTSTART;TZID=America/New_York:20260901T090000",
                        "DTEND;TZID=America/New_York:20260901T093000",
                        "RRULE:FREQ=DAILY;INTERVAL=90")))
ck(it and "every 90 from 2026-09-01" in it["line"],
   f"every-N-days survives ({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Fortnightly", "DTSTART;TZID=America/New_York:20260903T150000",
                        "DTEND;TZID=America/New_York:20260903T160000",
                        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH")))
ck(it and "every 14 from 2026-09-03" in it["line"],
   f"every other Thursday becomes 14 days ({it and it['line']})")

it, _ = one(wrap(vevent("SUMMARY:Season", "DTSTART;TZID=America/New_York:20260901T150000",
                        "DTEND;TZID=America/New_York:20260901T160000",
                        "RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261120T000000Z")))
ck(it and it["line"] == "@event Tue - Season - 15:00-16:00 - 09.01-11.20 - gray",
   f"UNTIL inside a year becomes the date window ({it and it['line']})")

it, sk = one(wrap(vevent("SUMMARY:Birthday", "DTSTART;VALUE=DATE:20261012",
                         "RRULE:FREQ=YEARLY")))
ck(it is None and sk and "yearly" in sk["reason"],
   f"a yearly repeat is REFUSED with a reason, not approximated ({sk})")

it, sk = one(wrap(vevent("SUMMARY:Overnight",
                         "DTSTART;TZID=America/New_York:20260907T220000",
                         "DTEND;TZID=America/New_York:20260908T020000")))
ck(it is None and sk and "midnight" in sk["reason"],
   f"an event running past midnight is refused with a reason ({sk})")

it, _ = one(wrap(vevent("SUMMARY:Meet - Ms. Rivera - about the essay",
                        "DTSTART;VALUE=DATE:20261005")))
ck(it and " - Ms. Rivera" not in it["line"].split(" - ", 2)[2],
   f"a summary containing ' - ' cannot split the line into wrong fields "
   f"({it and it['line']})")
ck(it and ics_import.validated_line(it["line"]), "and it still validates")

# ------------------------------------------------------- hostile input ------
for bad in ["@event 2026-01-01 - x - allday - red\n@pref background = red",
            "@pref background = red",
            "@event 2026-01-01 - x - allday - expression(alert(1))",
            "@event 2026-01-01 - x - 09:00-10:00 - red - and more",
            "@sport Ski - Mon - 15:00-16:00 - cyan",
            "@event 2026-01-01 - a - b - c - allday - red"]:
    ck(ics_import.validated_line(bad) is None,
       f"refused: {bad[:52]!r}")
ck(ics_import.validated_line("@event Mon,Wed - Piano - 16:00-17:00 - 09.01-12.20 - pink"),
   "and a good line is still accepted")

# ------------------------------------------------------------- the routes ---
WORK = tempfile.mkdtemp(prefix="ssa_ics_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5212"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="ics", ADMIN_PASSWORD="apw",
           FLASK_RUN_PORT=PORT)
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    env.pop(k, None)
srv = subprocess.Popen(
    [sys.executable, "-c", f"import app; app.app.run(port={PORT}, debug=False)"],
    cwd=APP_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

BASE = "http://127.0.0.1:" + PORT
MAIL = "27doej@shadysideacademy.org"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(BASE + "/register", urllib.parse.urlencode(
    {"email": MAIL, "name": "Jane Doe", "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/login", urllib.parse.urlencode(
    {"email": MAIL, "password": "pw123456"}).encode()))
op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
    {"schedule_text": "Jane Doe\n27 - Senior\n\n@pref time_format = 12h\n"}).encode()))

def upload(blob, color="pink", filename="app.ics"):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"color\"\r\n\r\n"
            f"{color}\r\n".encode()
            + f"--{b}\r\nContent-Disposition: form-data; name=\"ics\"; "
              f"filename=\"{filename}\"\r\nContent-Type: text/calendar\r\n\r\n".encode()
            + blob + f"\r\n--{b}--\r\n".encode())
    r = op.open(urllib.request.Request(BASE + "/edit/import-ics", body,
                {"Content-Type": f"multipart/form-data; boundary={b}"}))
    return r.read().decode()

def mine():
    return op.open(BASE + "/api/my-schedule").read().decode()

def flash_of(page):
    m = re.search(r'class="flash (ok|error)">\s*(.*?)\s*</div>', page, re.S)
    return (m.group(1), m.group(2)) if m else ("none", page[:120])

page = upload(REAL.encode())
offered = [HTML.unescape(x) for x in re.findall(r'name="line" value="([^"]*)"', page)]
ck(len(offered) == 5, f"the preview offers all five ({len(offered)})")
ck("本科申请" in page, "and names the calendar it came from")
ck("one-time copy" in page,
   "and says in so many words that it is a copy, not a subscription")
ck(mine().count("@event") == 0, "nothing has been written yet")

def confirm(lines):
    data = urllib.parse.urlencode([("line", l) for l in lines]).encode()
    return op.open(urllib.request.Request(
        BASE + "/edit/import-ics/confirm", data)).read().decode()

kind, msg = flash_of(confirm(offered[:3]))
ck(kind == "ok" and "3" in msg, f"importing three says so ({msg[:70]})")
ck(mine().count("@event") == 3, "and three lines are in the schedule")

kind, msg = flash_of(confirm(offered[:3]))
ck(kind == "ok" and "already" in msg,
   f"importing the same three again changes nothing ({msg[:70]})")
ck(mine().count("@event") == 3, "and there are still three, not six")

kind, msg = flash_of(confirm(
    ["@event 2026-01-01 - x - allday - red\n@pref background = red"]))
ck(kind == "error" and mine().count("@pref background") == 0,
   "a second directive smuggled into a line is refused, not stored")

# the cap refuses the whole batch rather than half of it
import app as _app
big = "Jane Doe\n27 - Senior\n\n" + "".join(
    f"@event 2026-03-{d:02d} - filler {d} - allday - gray\n"
    for d in range(1, 29))
op.open(urllib.request.Request(BASE + "/edit", urllib.parse.urlencode(
    {"schedule_text": big + "@pref time_format = 12h\n"}).encode()))
have = mine().count("@event")
many = [f"@event 2027-04-{d:02d} - extra {d} - allday - gray"
        for d in range(1, 29)]
kind, msg = flash_of(confirm(many))
ck(kind == "error" and str(_app.MAX_EVENTS) in msg,
   f"going over the event cap is refused with the real number ({msg[:90]})")
ck(mine().count("@event") == have,
   "and not one of the batch was written -- a half import looks like a "
   "working one until the missing deadline goes past")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()
print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
