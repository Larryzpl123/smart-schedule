"""
Turn a Veracross "SS Schedule" PDF into the schedule text this app already
parses.

What we actually need from the PDF
----------------------------------
Only the mapping **period letter -> course**. The PDF lays the same course out
eight times (once per SS Day) at eight different times, but the period letter
never changes, and the times come from the school-wide @day rotation in
schedule.txt. So the times in the PDF are noise; the letters are the signal.

Why this reads geometry instead of text
---------------------------------------
`page.extract_text()` on these files is unusable: the eight day-columns
interleave line by line, bold text is drawn twice so "Unscheduled" comes out as
"UUnnsscchheedduulleedd", and a cell that wraps splits a course's period letter
onto the line above its "- Ea)". `page.find_tables()` is better but splits a
course block in half whenever it straddles a table row.

So: take the column boundaries from the table's own rectangles, bucket every
word into its column, sort by vertical position, and cut into blocks on the
line gap. Inside a column the reading order is unambiguous.

Everything here is plain pdfplumber + re; no network, no API key.
"""

import re
import unicodedata

# A course block looks like this once its lines are joined:
#
#     Revolution in Ancient Rome:      <- name, may wrap over 2 lines
#     HI445 - 1 - S1                   <- code - section - which semester
#     8:15 - 9:05 (Period H - Ea)      <- may wrap after "(Period H"
#     Mr. Lindqvist - R 205              <- teacher - room, may wrap
#
CODE_LINE = re.compile(
    r"^([A-Z]{2,5}\d{2,5}[A-Z]?)\s*-\s*(\S+?)\s*-\s*(All|ALL|S1|S2)$")
PERIOD = re.compile(r"\(Period\s+([A-H])\s*-\s*(La|Ea)\)")
HEAD_NAME = re.compile(r"^(.+?)\s*[—–-]\s*SS\s+Semester\s+([12])\s*$")
HEAD_GRADE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)\b")

GRADE_WORD = {9: "Freshman", 10: "Sophomore", 11: "Junior", 12: "Senior"}
COLORS = ["red", "blue", "green", "purple", "orange", "teal",
          "indigo", "pink", "cyan", "yellow", "gray"]

# Shady Side colours each course cell by DEPARTMENT, and the PDF keeps those as
# the cell's background fill. Reading them beats guessing from the course code:
# it needs no table of prefixes and it stays right when a new subject appears.
#
# Measured off the real exports (pastel fills, 86x53pt cells):
SSA_SUBJECT_FILLS = [
    ((0.9765, 0.8549, 0.8549), "red",    "Math"),
    ((0.9765, 0.9216, 0.8902), "orange", "Computer Science"),
    ((0.9725, 0.9765, 0.7843), "yellow", "History"),
    ((0.8314, 0.9686, 0.8549), "green",  "Science"),
    ((0.8157, 0.9647, 0.9333), "cyan",   "Art"),
    ((0.8118, 0.9490, 0.9843), "blue",   "English"),
    ((0.9333, 0.8824, 0.9843), "purple", "World Language"),
    ((0.7373, 0.9373, 0.8510), "teal",   "Music"),
]
# How far a fill may sit from a known one and still count as that subject.
# Every fill in every real export matches one of the above EXACTLY (distance
# 0.0), so this only has to absorb rounding -- and it must stay well under half
# the closest pair (Art/English, 0.0535 apart) so that a genuinely new 9th
# department colour is rejected rather than quietly forced into a neighbour.
# A rejected fill falls back to the cycling palette and the import warns.
FILL_TOLERANCE = 0.025


def _as_rgb(value):
    """pdfplumber gives a scalar for greyscale and a tuple for RGB/CMYK."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value),) * 3
    v = tuple(float(x) for x in value)
    if len(v) == 3:
        return v
    if len(v) == 1:
        return (v[0],) * 3
    if len(v) == 4:                     # CMYK
        c, m, y, k = v
        return (1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k))
    return None


def subject_from_fill(rgb):
    """Cell background -> (colour name, subject), or (None, None)."""
    if not rgb:
        return None, None
    best, best_d = None, 1e9
    for known, name, subject in SSA_SUBJECT_FILLS:
        d = sum((a - b) ** 2 for a, b in zip(rgb, known)) ** 0.5
        if d < best_d:
            best, best_d = (name, subject), d
    return best if best_d <= FILL_TOLERANCE else (None, None)


class ScheduleParseError(Exception):
    pass


def backend_problem():
    """Why PDF reading can't work here, or None if it can.

    pdfplumber is imported lazily so the whole app still starts without it --
    but that means a missing install used to surface as "could not read that
    PDF", which blames the file for a server problem."""
    try:
        import pdfplumber           # noqa: F401
    except Exception as e:
        return (f"The PDF reader isn't installed on the server "
                f"(pdfplumber: {e}). On PythonAnywhere, open a Bash console "
                f"and run:  pip3 install --user pdfplumber  "
                f"then hit Reload on the Web tab.")
    return None


# ---------------------------------------------------------------- geometry
def _dedupe(words):
    """Bold text in these PDFs is the same string painted twice, a hair apart.
    Left alone it turns "Period" into "PPeerriioodd"."""
    out, seen = [], set()
    for w in words:
        key = (w["text"], round(w["x0"], 0), round(w["top"], 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _column_edges(page):
    """Day-column boundaries, taken from the table's own rectangles.

    Returns [] for a page with no table (the header sometimes gets a page to
    itself when a student's address is long enough to push the grid over)."""
    if len(page.rects) < 10:
        return []
    xs = sorted({round(r["x0"], 1) for r in page.rects} |
                {round(r["x1"], 1) for r in page.rects})
    clusters, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] > 3:
            clusters.append(sum(cur) / len(cur))
            cur = [x]
        else:
            cur.append(x)
    clusters.append(sum(cur) / len(cur))
    if len(clusters) < 3:
        return []

    # Keep only the equal-width day columns; this drops the narrow time gutter
    # on the left ("8 AM", "9 AM", ...).
    spans = list(zip(clusters, clusters[1:]))
    widths = sorted(b - a for a, b in spans)
    typical = widths[len(widths) // 2]
    return [(a, b) for a, b in spans if abs((b - a) - typical) <= typical * 0.2]


def _lines(words, tol=3.0, keep=False):
    """Words -> text lines, top to bottom. keep=True also returns the words,
    which is how the cell's background colour gets looked up later."""
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]) <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return [(top, " ".join(x["text"] for x in sorted(ws, key=lambda z: z["x0"])),
             ws) for top, ws in rows]


def _blocks(lines, gap=11.0, keep=False):
    """Split a column's lines into course blocks on the vertical gap.
    Lines inside a block sit ~8.3pt apart; blocks are >=10.6pt apart."""
    out = []
    for row in lines:
        top = row[0]
        if out and top - out[-1][-1][0] <= gap:
            out[-1].append(row)
        else:
            out.append([row])
    if keep:
        return [([r[1] for r in b], [w for r in b for w in r[2]]) for b in out]
    return [[r[1] for r in b] for b in out]


# ---------------------------------------------------------------- parsing
def _cell_fill(page_rects, words):
    """The background fill behind a block of words, if there is one."""
    if not words:
        return None
    cx = sum((w["x0"] + w["x1"]) / 2 for w in words) / len(words)
    cy = sum((w["top"] + w["bottom"]) / 2 for w in words) / len(words)
    best, best_area = None, 1e18
    for r in page_rects:
        if r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]:
            area = (r["x1"] - r["x0"]) * (r["bottom"] - r["top"])
            if area < best_area:            # the tightest box wins
                best, best_area = r, area
    return _as_rgb(best.get("non_stroking_color")) if best else None


def _parse_block(lines):
    """One block -> dict, or None if it isn't a course (free period, header)."""
    code_at = next((i for i, ln in enumerate(lines) if CODE_LINE.match(ln)), None)
    if code_at is None or code_at == 0:
        return None
    num, section, term = CODE_LINE.match(lines[code_at]).groups()

    name = " ".join(lines[:code_at]).strip()
    if not name.endswith(":"):
        return None                     # not a course block after all
    name = name[:-1].strip()

    rest = " ".join(lines[code_at + 1:])
    m = PERIOD.search(rest)
    if not m:
        return None                     # e.g. an "Unscheduled" free period
    teacher, room = "", ""
    tail = rest[m.end():].strip()
    if tail:
        teacher, _, room = tail.partition(" - ")
        teacher, room = teacher.strip(), room.strip()
    return {"period": m.group(1), "latearly": m.group(2), "name": name,
            "num": num, "section": section, "term": term,
            "teacher": teacher, "room": room}


def _header(page_texts):
    """Student name, grade and which semester, from the top of the document."""
    name = semester = None
    grade = None
    for text in page_texts:
        for raw in text.split("\n")[:8]:
            line = unicodedata.normalize("NFKC", raw).strip()
            if name is None:
                m = HEAD_NAME.match(line)
                if m:
                    name, semester = m.group(1).strip(), int(m.group(2))
                    continue
            if grade is None:
                m = HEAD_GRADE.match(line)
                if m and 9 <= int(m.group(1)) <= 12:
                    grade = int(m.group(1))
        if name and grade:
            break
    return name, grade, semester


def undouble(text):
    """Bold is painted twice at char level, so "PPeerriioodd" -> "Period".
    Only used to read the free-period cells, which are always bold."""
    out, i = [], 0
    while i < len(text):
        if i + 1 < len(text) and text[i] == text[i + 1]:
            out.append(text[i])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


FREE_PERIOD = re.compile(r"\(Period\s+([A-H])")


def parse_pdf(fileobj):
    """Veracross schedule PDF -> {name, grade, semester, courses[], conflicts{}}.

    One entry per period letter, A-H order, each carrying:
      period, latearly, name, num, section, term, teacher, room, color,
      days      -- the rotation days it meets  (1-8)
      all_days  -- every rotation day that letter has a slot at all
      partial   -- True when days != all_days

    `partial` is the lab case: a science course holds its own letter for all six
    of that letter's slots and then takes two more in a letter that is otherwise
    a free period. Every one of the five sample schedules has exactly one.
    Without `days` the app would draw that lab on all six slots -- four of them
    wrong -- so the caller must carry it through.

    `conflicts` records a period whose blocks disagreed. The same course repeats
    across all eight day-columns, so a conflict means the layout was misread and
    the caller should say so rather than quietly pick one."""
    problem = backend_problem()
    if problem:
        raise ScheduleParseError(problem)
    import pdfplumber

    seen, letter_days, page_texts = {}, {}, []
    with pdfplumber.open(fileobj) as pdf:
        if not pdf.pages:
            raise ScheduleParseError("That PDF has no pages.")
        for page in pdf.pages:
            page_texts.append(page.extract_text() or "")
            edges = _column_edges(page)
            if not edges:
                continue                        # header-only page
            words = _dedupe(page.extract_words())
            for day, (left, right) in enumerate(edges, start=1):
                col = [w for w in words
                       if left <= (w["x0"] + w["x1"]) / 2 < right]
                for block, block_words in _blocks(_lines(col), keep=True):
                    got = _parse_block(block)
                    if got:
                        got["day"] = day
                        got["fill"] = _cell_fill(page.rects, block_words)
                        seen.setdefault(got["period"], []).append(got)
                        letter_days.setdefault(got["period"], set()).add(day)
                        continue
                    # A free period still tells us this letter has a slot that
                    # day, which is how we know a lab is only using some of them.
                    flat = undouble(" ".join(block))
                    if "Unscheduled" in flat:
                        m = FREE_PERIOD.search(flat)
                        if m:
                            letter_days.setdefault(m.group(1), set()).add(day)

    if not seen:
        raise ScheduleParseError(
            "No classes found. Is this the Veracross 'SS Schedule' PDF?")

    key = ("name", "num", "section", "teacher", "room")
    courses, conflicts = [], {}
    for letter in sorted(seen):
        blocks = seen[letter]
        variants = {tuple(c[k] for k in key) for c in blocks}
        if len(variants) > 1:
            conflicts[letter] = [dict(zip(key, v)) for v in sorted(variants)]
        best = max(blocks, key=lambda c: sum(1 for x in blocks
                                             if all(x[k] == c[k] for k in key)))
        best = dict(best)
        best["days"] = sorted({c["day"] for c in blocks
                               if all(c[k] == best[k] for k in key)})
        best["all_days"] = sorted(letter_days.get(letter, set()))
        best["partial"] = best["days"] != best["all_days"]
        courses.append(best)

    # Colour: Shady Side's own department colour, read off the cell background.
    # Only if a cell has no recognisable fill do we fall back to handing out
    # palette colours in order, which is what this used to do for everything.
    palette = {}
    for c in courses:
        name, subject = subject_from_fill(c.get("fill"))
        if name:
            c["color"], c["subject"] = name, subject
        else:
            c["color"] = palette.setdefault(
                c["num"], COLORS[len(palette) % len(COLORS)])
            c["subject"] = None
        c.pop("fill", None)

    name, grade, semester = _header(page_texts)
    return {"name": name, "grade": grade, "semester": semester,
            "courses": courses, "conflicts": conflicts}


# ---------------------------------------------------------------- output
def class_year(grade, school_year_start):
    """Grade 12 in the 2026-2027 school year graduates in 2027 -> "27"."""
    if not grade:
        return None
    return f"{(school_year_start + 1 + (12 - grade)) % 100:02d}"


def to_schedule_text(parsed, term_label, term_from, term_to,
                     school_year_start, time_format="12h"):
    """Build the exact text format /edit and the front-end already parse."""
    year = class_year(parsed.get("grade"), school_year_start)
    lines = [parsed.get("name") or "Your Name",
             f"{year} - {GRADE_WORD.get(parsed.get('grade'), '')}".strip(" -"),
             "",
             f"@term {term_label} = {term_from}-{term_to}"]
    for c in parsed["courses"]:
        row = [c["period"], c["name"], c["num"], c["section"],
               c["teacher"], c["room"], c["color"]]
        # Optional trailing fields. Old seven-field lines keep working; these
        # are only added when the PDF actually says something extra.
        if c.get("latearly"):
            row.append(c["latearly"])
        if c.get("partial") and c.get("days"):
            row.append("days " + ",".join(str(d) for d in c["days"]))
        lines.append(" - ".join(row))
    lines += ["", f"@pref time_format = {time_format}"]
    return "\n".join(lines) + "\n"
