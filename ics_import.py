# -*- coding: utf-8 -*-
"""Read a .ics file and turn what fits into Smart Schedule lines.

Why this exists: a student's college-application deadlines, their family's
shared calendar, a club's schedule -- all of it already exists as an .ics that
some other app exported. Retyping fifteen deadlines by hand is exactly the
sort of thing that makes people not bother.

Two rules run through the whole file:

  * Nothing is imported silently. Every VEVENT either becomes a line the user
    sees before it is written, or appears in a "couldn't import" list WITH the
    reason. A dropped event is worse than a refused one, because nobody finds
    out until the deadline passes.

  * This is a COPY, not a subscription. Nothing here reaches back out to
    fetch anything later, and the caller says so on the page.

RFC 5545 is bigger than this. What is handled: line unfolding, the four
escapes, VALUE=DATE all-day events, TZID and UTC timestamps, and the RRULE
shapes this app happens to be able to express. Everything else is listed as
unimportable rather than approximated -- an "approximately monthly" deadline
is not a deadline.
"""
import re
from datetime import datetime, timedelta, date

try:                                    # 3.9+, and PythonAnywhere has tzdata
    from zoneinfo import ZoneInfo
    _HAVE_TZ = True
except Exception:                       # pragma: no cover - very old runtimes
    ZoneInfo = None
    _HAVE_TZ = False

SCHOOL_TZ = "America/New_York"

# The palette the app draws with; anything else is refused rather than passed
# through into a CSS variable.
COLORS = ["gray", "pink", "cyan", "teal", "indigo", "orange",
          "red", "green", "blue", "yellow", "purple", "lime", "grey"]

DOW3 = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
DOW_APP = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu",
           "FR": "Fri", "SA": "Sat", "SU": "Sun"}

MAX_ICS_BYTES = 1024 * 1024      # a year of anybody's calendar is far under this
MAX_EVENTS_SHOWN = 300


def unfold(text):
    """RFC 5545 3.1: a line beginning with a space or tab continues the one above.

    Apple wraps at 75 octets mid-character, so this joins the raw text and
    lets the caller's already-decoded string carry on; decoding happens before
    we get here, which is fine because the continuation marker is ASCII."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_prop(line):
    """"DTSTART;TZID=Asia/Shanghai:20270203T090000" -> (name, {params}, value).

    The value may contain colons (a URL, a description), so the split is at the
    first colon that is not inside a quoted parameter value."""
    depth = False                       # inside a "quoted" param value
    for i, ch in enumerate(line):
        if ch == '"':
            depth = not depth
        elif ch == ":" and not depth:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None, {}, ""
    bits = []
    cur, q = "", False
    for ch in head:
        if ch == '"':
            q = not q
            continue
        if ch == ";" and not q:
            bits.append(cur)
            cur = ""
        else:
            cur += ch
    bits.append(cur)
    name = bits[0].upper()
    params = {}
    for b in bits[1:]:
        if "=" in b:
            k, v = b.split("=", 1)
            params[k.upper()] = v
    return name, params, value


def unescape(v):
    """The four escapes of RFC 5545 5.1, and nothing else."""
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append({"n": "\n", "N": "\n", ";": ";", ",": ",",
                        "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _parse_dt(value, params):
    """-> (datetime|date, is_all_day, tzid_or_None, problem_or_None).

    A VALUE=DATE property is a plain calendar date with no time and no zone;
    treating it as midnight is how an all-day deadline ends up drawn as a
    00:00 appointment in the wrong timezone."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
        try:
            return date(int(v[0:4]), int(v[4:6]), int(v[6:8])), True, None, None
        except ValueError:
            return None, False, None, "unreadable date " + v
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", v)
    if not m:
        return None, False, None, "unreadable date/time " + v
    d, t, z = m.group(1), m.group(2), m.group(3)
    try:
        dt = datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]),
                      int(t[0:2]), int(t[2:4]), int(t[4:6]))
    except ValueError:
        return None, False, None, "unreadable date/time " + v
    if z == "Z":
        return dt, False, "UTC", None
    return dt, False, params.get("TZID"), None


def to_school_time(dt, tzid):
    """A wall-clock time in some zone -> the wall-clock time at school.

    Larry's own file has events in Asia/Shanghai; importing 09:00 Shanghai as
    09:00 Pittsburgh would put a Beijing video call in the middle of second
    period. Returns (datetime, note_or_None) -- the note is shown to the user
    when a conversion actually moved something."""
    if not tzid or tzid == SCHOOL_TZ:
        return dt, None
    if not _HAVE_TZ:
        return dt, "kept the clock time as written (%s): no timezone database here" % tzid
    try:
        src = ZoneInfo("UTC") if tzid == "UTC" else ZoneInfo(tzid)
        out = dt.replace(tzinfo=src).astimezone(ZoneInfo(SCHOOL_TZ))
    except Exception:
        return dt, "unknown timezone %s, kept the clock time as written" % tzid
    naive = out.replace(tzinfo=None)
    if naive == dt:
        return naive, None
    return naive, "%s %s in %s is %s here" % (
        dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), tzid,
        naive.strftime("%Y-%m-%d %H:%M"))


def parse_ics(text):
    """-> list of raw event dicts. VTIMEZONE and VALARM blocks are skipped.

    VALARM matters: it contains its own DESCRIPTION and UID, and a parser that
    does not track nesting will overwrite the event's summary with the alarm's
    reminder text."""
    events = []
    cur = None
    skip_depth = 0
    for line in unfold(text):
        if not line.strip():
            continue
        name, params, value = _split_prop(line)
        if name is None:
            continue
        if name == "BEGIN":
            v = value.strip().upper()
            if skip_depth or v in ("VTIMEZONE", "VALARM"):
                skip_depth += 1
            elif v == "VEVENT":
                cur = {"props": {}, "raw": []}
            continue
        if name == "END":
            v = value.strip().upper()
            if skip_depth:
                skip_depth -= 1
            elif v == "VEVENT" and cur is not None:
                events.append(cur)
                cur = None
            continue
        if skip_depth or cur is None:
            continue
        # first one wins; a duplicated property is malformed anyway
        cur["props"].setdefault(name, (params, value))
    return events


def _clean_name(s, limit=70):
    """The app's line format uses " - " between fields, so a summary that
    contains one would silently split into the wrong columns. Swap it for an
    en dash rather than refusing the event."""
    s = unescape(s or "").replace("\n", " ").replace("\r", " ")
    s = s.replace(" - ", " – ").replace("|", "｜")
    # The page escapes on the way out, which is where it has to be fixed. This
    # is the second layer: a SUMMARY is somebody else's text, and angle
    # brackets in one are far more likely to be an attempt than a title.
    s = s.replace("<", "‹").replace(">", "›")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s or "Imported event"


def _rrule(value):
    out = {}
    for part in (value or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def _mmdd(d):
    return "%02d.%02d" % (d.month, d.day)


def _when_from_rrule(rr, start_date, warn):
    """The repeating shapes this app can actually express, or None.

    Everything it cannot say -- FREQ=YEARLY, a BYDAY list on a monthly rule,
    BYSETPOS -- comes back None and the event is listed as unimportable. An
    approximation would be worse: "roughly the first Tuesday" is not a date."""
    freq = rr.get("FREQ", "").upper()
    interval = int(rr.get("INTERVAL", "1") or 1)
    if freq == "DAILY":
        return "every %d from %s" % (interval, start_date.isoformat())
    if freq == "WEEKLY":
        days = [d.strip()[-2:].upper() for d in rr.get("BYDAY", "").split(",") if d.strip()]
        days = [d for d in days if d in DOW3]
        if not days:
            days = [DOW3[start_date.weekday()]]
        if interval == 1:
            return ",".join(DOW_APP[d] for d in days)
        if len(days) == 1:
            # every other Tuesday is 14 days, which the app CAN say
            return "every %d from %s" % (interval * 7, start_date.isoformat())
        warn.append("repeats every %d weeks on several days, which this app "
                    "cannot say" % interval)
        return None
    if freq == "MONTHLY":
        if interval != 1:
            warn.append("repeats every %d months; this app only does every "
                        "month" % interval)
            return None
        byday = [d.strip().upper() for d in rr.get("BYDAY", "").split(",") if d.strip()]
        if len(byday) > 1:
            warn.append("repeats on several weekdays of the month")
            return None
        if byday:
            m = re.fullmatch(r"(-?\d)([A-Z]{2})", byday[0])
            if not m or m.group(2) not in DOW3:
                warn.append("monthly rule %s is not one this app can say" % byday[0])
                return None
            return "monthly %s#%s" % (DOW_APP[m.group(2)], m.group(1))
        md = rr.get("BYMONTHDAY", "").split(",")[0].strip()
        if md and re.fullmatch(r"\d{1,2}", md):
            return "monthly %s" % md
        return "monthly %d" % start_date.day
    warn.append("repeats %s, which this app has no way to say"
                % (freq.lower() or "in an unknown way"))
    return None


def convert(text, color="gray", school_year=None):
    """The whole job: .ics text in, proposals out.

    Returns (items, skipped, calendar_name).
      items    [{line, kind, when_human, name, note, suggest}] -- ready to show
      skipped  [{name, reason}]
    Nothing is written anywhere; the caller shows this and asks."""
    if color not in COLORS:
        color = "gray"
    raws = parse_ics(text)
    calname = ""
    m = re.search(r"^X-WR-CALNAME:(.*)$", text.replace("\r\n", "\n"), re.M)
    if m:
        calname = _clean_name(m.group(1), 60)

    items, skipped = [], []
    for ev in raws:
        props = ev["props"]
        summary = props.get("SUMMARY", ({}, ""))[1]
        name = _clean_name(summary)
        if "DTSTART" not in props:
            skipped.append({"name": name, "reason": "no start date in the file"})
            continue
        sp, sv = props["DTSTART"]
        start, allday, tzid, problem = _parse_dt(sv, sp)
        if problem:
            skipped.append({"name": name, "reason": problem})
            continue
        note = None
        end = None
        if "DTEND" in props:
            ep, evv = props["DTEND"]
            end, end_allday, end_tz, end_problem = _parse_dt(evv, ep)
            if end_problem:
                end = None

        warn = []
        rrule = _rrule(props["RRULE"][1]) if "RRULE" in props else None

        if allday:
            # DTEND on an all-day event is EXCLUSIVE: a one-day event ends the
            # next morning. Reading it as inclusive turns every deadline into a
            # two-day block, which is the classic .ics off-by-one.
            last = start
            if isinstance(end, date) and not isinstance(end, datetime) and end > start:
                last = end - timedelta(days=1)
            if rrule:
                when = _when_from_rrule(rrule, start, warn)
                if not when:
                    skipped.append({"name": name, "reason": warn[-1] if warn
                                    else "a repeat rule this app cannot say"})
                    continue
                line = "@event %s - %s - allday - %s" % (when, name, color)
                items.append({"line": line, "kind": "event", "name": name,
                              "when_human": "repeats: %s, all day" % when,
                              "date": start.isoformat(), "note": None})
                continue
            if last > start:
                # several days with no time of day: that is what a project band is
                line = "@project %s - %s - %s - %s" % (
                    name, start.isoformat(), last.isoformat(), color)
                items.append({"line": line, "kind": "project", "name": name,
                              "when_human": "%s to %s, all day"
                                            % (start.isoformat(), last.isoformat()),
                              "date": start.isoformat(),
                              "note": "several days long, so it comes in as a "
                                      "project band rather than an event"})
                continue
            line = "@event %s - %s - allday - %s" % (start.isoformat(), name, color)
            items.append({"line": line, "kind": "event", "name": name,
                          "when_human": "%s, all day" % start.isoformat(),
                          "date": start.isoformat(), "note": None})
            continue

        # ---- timed ---------------------------------------------------------
        start_local, note = to_school_time(start, tzid)
        if isinstance(end, datetime):
            end_local, _ = to_school_time(end, tzid)
        else:
            end_local = start_local + timedelta(minutes=30)
        if end_local <= start_local:
            end_local = start_local + timedelta(minutes=30)
        if end_local.date() != start_local.date():
            skipped.append({"name": name,
                            "reason": "runs past midnight; this app's events "
                                      "live inside one day"})
            continue
        span = "%02d:%02d-%02d:%02d" % (start_local.hour, start_local.minute,
                                        end_local.hour, end_local.minute)
        if rrule:
            when = _when_from_rrule(rrule, start_local.date(), warn)
            if not when:
                skipped.append({"name": name, "reason": warn[-1] if warn
                                else "a repeat rule this app cannot say"})
                continue
            window = ""
            until = rrule.get("UNTIL", "")
            if until:
                u, _ad, _tz, _p = _parse_dt(until.replace("Z", "") + ("Z" if until.endswith("Z") else ""), {})
                if u is not None:
                    ud = u.date() if isinstance(u, datetime) else u
                    if (ud - start_local.date()).days < 365:
                        window = " - %s-%s" % (_mmdd(start_local.date()), _mmdd(ud))
                    else:
                        warn.append("it ends more than a year out, and this "
                                    "app's date window is month-and-day only, "
                                    "so no end date was set")
            elif rrule.get("COUNT"):
                warn.append("the file says it happens %s times; this app has no "
                            "way to count repeats, so it was imported as "
                            "open-ended" % rrule.get("COUNT"))
            line = "@event %s - %s - %s%s - %s" % (when, name, span, window, color)
            items.append({"line": line, "kind": "event", "name": name,
                          "when_human": "repeats: %s, %s" % (when, span),
                          "date": start_local.date().isoformat(),
                          "note": "; ".join(warn + ([note] if note else [])) or None})
            continue
        line = "@event %s - %s - %s - %s" % (
            start_local.date().isoformat(), name, span, color)
        items.append({"line": line, "kind": "event", "name": name,
                      "when_human": "%s %s" % (start_local.date().isoformat(), span),
                      "date": start_local.date().isoformat(), "note": note})

    items.sort(key=lambda x: (x["date"], x["name"]))
    return items, skipped, calname


# ---- the other direction: never trust the browser's copy of a line ---------
_RE_EVENT = re.compile(
    r"^@event (?P<when>\d{4}-\d{2}-\d{2}"
    r"|every \d{1,3} from \d{4}-\d{2}-\d{2}"
    r"|monthly (?:\d{1,2}|[A-Z][a-z]{2}#-?\d)"
    r"|[A-Z][a-z]{2}(?:,[A-Z][a-z]{2})*)"
    r" - (?P<name>[^\n]{1,90}?)"
    r" - (?P<time>allday|\d{2}:\d{2}-\d{2}:\d{2})"
    r"(?: - (?P<range>\d{2}\.\d{2}-\d{2}\.\d{2}))?"
    r" - (?P<color>[a-z]{3,7})$")
_RE_PROJECT = re.compile(
    r"^@project (?P<name>[^\n]{1,90}?) - (?P<from>\d{4}-\d{2}-\d{2})"
    r" - (?P<to>\d{4}-\d{2}-\d{2}) - (?P<color>[a-z]{3,7})$")


def validated_line(line):
    """A line the preview page sent back, re-checked from scratch.

    The preview puts the proposed lines in hidden fields, which means the
    browser could send anything at all -- a newline, a second @-directive, a
    colour that is really a CSS expression. Returns the line, or None."""
    line = (line or "").strip()
    if "\n" in line or "\r" in line or len(line) > 200:
        return None
    m = _RE_EVENT.match(line) or _RE_PROJECT.match(line)
    if not m:
        return None
    if m.group("color") not in COLORS:
        return None
    if " - " in m.group("name"):
        return None
    return line
