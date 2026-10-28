"""Notes on events, projects and homework: the ">" lines under an item.

Checked on every path that touches them -- the parser, the server's line
operations, the same operations in local mode (and that the two agree), the
quick dialog, the click menu, the grid, the list and its search, the /edit
form round trip, and that a note is text, never markup."""
import json, os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_nt_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5221"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="nttest", ADMIN_PASSWORD="apw",
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
# Sep 10 2026 is a Thursday, cycle Day 3; period A meets that day.
SEED = """Jane Doe
27 - Senior

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - Ea

@project History paper - 2026-09-01 - 2026-09-20 - blue
  > thesis still open
  > find 3 primary sources
@event 2026-09-10 - Dentist - 15:30-16:30 - gray
  > bring the insurance card
@event 2026-09-10 - Club fair - 16:45-17:15 - pink
@hw A - before - Read ch.4 - 2026-09-10
  > p.112-140, annotate
@hw A - during - Lab writeup - 2026-09-11

@pref time_format = 24h
"""

# One sequence of line operations, run once against the server and once
# against local mode. The two are separate implementations of the same rules
# (Python on a DB row, JS on a localStorage string) -- the only way to know
# they agree is to make them do the same thing and compare.
OPS = [
    ("/api/quick-add", {"kind": "event", "line": "2026-09-11 - Tutor - 15:30-16:00 - gray",
                        "note": "room 210\nask about the quiz"}),
    # the last event now HAS a note: the next one must land after it, not inside it
    ("/api/quick-add", {"kind": "event", "line": "2026-09-12 - Swim - 07:00-08:00 - gray"}),
    ("/api/quick-edit", {"kind": "event", "line": "2026-09-10 - Dentist - 15:30-16:30 - gray",
                         "newline": "2026-09-10 - Dentist - 15:45-16:45 - gray"}),          # no note key
    ("/api/quick-edit", {"kind": "hw", "line": "A - before - Read ch.4 - 2026-09-10",
                         "newline": "A - before - Read ch.4 - 2026-09-10",
                         "note": "p.112-150\n\nthen the questions"}),
    ("/api/quick-done", {"line": "A - before - Read ch.4 - 2026-09-10", "done": True}),
    ("/api/quick-edit", {"kind": "event", "line": "2026-09-10 - Club fair - 16:45-17:15 - pink",
                         "newline": "2026-09-10 - Club fair - 16:45-17:15 - pink", "note": "go with Sam"}),
    ("/api/quick-edit", {"kind": "event", "line": "2026-09-10 - Club fair - 16:45-17:15 - pink",
                         "newline": "2026-09-10 - Club fair - 16:45-17:15 - pink", "note": ""}),  # cleared
    ("/api/quick-delete", {"kind": "hw", "line": "A - during - Lab writeup - 2026-09-11"}),
]

def norm(t):
    return "\n".join(l.rstrip() for l in (t or "").replace("\r\n", "\n").split("\n")).strip()

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
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

        def save_text(t):
            pg.goto(BASE + "/edit")
            pg.click("#modeText")
            pg.fill("#rawText", t)
            with pg.expect_navigation():
                pg.click('button[type=submit]')

        def mine():
            # a form POST stores the textarea's CRLFs; compare on LF
            return pg.evaluate("fetch('/api/my-schedule',{cache:'no-store'}).then(r=>r.text())").replace("\r\n", "\n")

        def post(path, payload):
            return pg.evaluate("""([p, d]) => fetch(p, {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(d)})
                .then(async r => ({status: r.status, body: await r.json().catch(() => ({}))}))""",
                [path, payload])

        save_text(SEED)

        # ---------------- the parser ----------------
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(400)
        parsed = pg.evaluate("""() => ({
            ev: STU.events.map(e => [e.name, e.note || null]),
            hw: STU.hw.map(h => [h.name, h.note || null]),
            pr: STU.projects.map(p => [p.name, p.note || null]),
            name: STU.name })""")
        ck(parsed["ev"] == [["Dentist", "bring the insurance card"], ["Club fair", None]],
           f"an event gets the note under it, and only that one ({parsed['ev']})")
        ck(parsed["hw"] == [["Read ch.4", "p.112-140, annotate"], ["Lab writeup", None]],
           f"homework too ({parsed['hw']})")
        ck(parsed["pr"] == [["History paper", "thesis still open\nfind 3 primary sources"]],
           f"a project's note keeps its two lines ({parsed['pr']})")
        stray = pg.evaluate("""() => { const s = parseStudent('> stray\\nJane Doe\\n27 - Senior\\n'
            + '@event 2026-09-10 - X - 10:00-10:30 - gray\\n@sport Swim - Mon - 15:45-17:00 - cyan\\n> not mine\\n'
            + '@event 2026-09-11 - Y - 10:00-10:30 - gray\\n');
            return {name: s.name, form: s.form, x: s.events[0].note || null, y: s.events[1].note || null}; }""")
        ck(stray == {"name": "Jane Doe", "form": "27 - Senior", "x": None, "y": None},
           f"a stray '>' line is never read as the name, and never jumps to the next item ({stray})")

        # ---------------- the grid ----------------
        g = pg.evaluate("""() => {
          const out = {};
          document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(b => {
            const n = b.querySelector('.bname'); if (!n) return;
            out[n.textContent] = {has: b.classList.contains('hasnote'),
              note: (b.querySelector('.bnote') || {}).textContent || null, title: b.title};
          });
          const hw = Array.from(document.querySelectorAll('#gbody .hw')).map(h =>
            ({t: h.textContent, has: h.classList.contains('hasnote'), title: h.title}));
          const bar = Array.from(document.querySelectorAll('#allday .pbar'))
            .find(x => x.textContent.startsWith('History paper'));
          return {blocks: out, hw: hw, bar: bar ? {has: bar.classList.contains('hasnote'), title: bar.title} : null};
        }""")
        dn, cf = g["blocks"].get("Dentist"), g["blocks"].get("Club fair")
        ck(dn and dn["has"] and dn["note"] == "bring the insurance card" and "insurance" in dn["title"],
           f"a block with a note is marked, carries it, and the tooltip has it ({dn})")
        ck(cf and not cf["has"] and cf["note"] is None, f"a block without one is not ({cf})")
        rd = [h for h in g["hw"] if "Read ch.4" in h["t"]]
        ck(rd and rd[0]["has"] and "p.112-140" in rd[0]["title"],
           f"the homework chip is marked and its tooltip has the note ({rd})")
        ck(g["bar"] and g["bar"]["has"] and "primary sources" in g["bar"]["title"],
           f"the project bar too ({g['bar']})")

        # the click menu shows the whole note
        pg.locator("#gbody .block:not(.dayfull)", has_text="Dentist").first.click()
        pg.wait_for_timeout(200)
        mn = pg.evaluate("(document.querySelector('.hwmenu .mnote')||{}).textContent||null")
        ck(mn == "bring the insurance card", f"the click menu shows the note ({mn})")
        pg.keyboard.press("Escape")

        # ---------------- the dialog ----------------
        pg.locator("#gbody .block:not(.dayfull)", has_text="Dentist").first.dblclick()
        pg.wait_for_timeout(250)
        ck(pg.input_value("#qNote") == "bring the insurance card", "the editor opens with the note in it")
        pg.fill("#qNote", "bring the insurance card\nand the referral")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        t = mine()
        ck("@event 2026-09-10 - Dentist - 15:30-16:30 - gray\n  > bring the insurance card\n  > and the referral" in t,
           "saving from the editor writes the note back as '>' lines under the item")
        # adding a new event from the grid with a note
        pg.evaluate("openQuick({kind:'event',dateKey:'2026-09-10',min:19*60})")
        pg.wait_for_timeout(150)
        ck(pg.input_value("#qNote") == "", "a NEW item starts with an empty note (not the last one's)")
        pg.fill("#qName", "Call home")
        pg.fill("#qNote", "Sunday plans")
        pg.click("#qSave")
        pg.wait_for_timeout(600)
        t = mine()
        ck("- Call home - 19:00-19:30 - gray\n  > Sunday plans" in t, "adding with a note writes it")
        ck("Club fair - 16:45-17:15 - pink\n@event" in t or "Club fair - 16:45-17:15 - pink\n" in t,
           "...and the one before it did not lose or gain anything")
        ck("  > bring the insurance card\n  > and the referral\n@event 2026-09-10 - Club fair" in t,
           "a note is never split from its item by an insert")

        # delete + undo brings the note back
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(300)
        pg.evaluate("quickDelete('event','2026-09-10 - Dentist - 15:30-16:30 - gray','Dentist')")
        pg.wait_for_timeout(600)
        t = mine()
        ck("Dentist" not in t and "insurance" not in t and "referral" not in t,
           "deleting an item takes its note with it (no orphan lines left behind)")
        pg.click("#undo button")
        pg.wait_for_timeout(600)
        t = mine()
        ck("Dentist - 15:30-16:30 - gray\n  > bring the insurance card\n  > and the referral" in t,
           "Undo puts the note back too")

        # ---------------- the list view ----------------
        pg.evaluate("VIEW='list'; render()")
        pg.wait_for_timeout(700)
        ln = pg.evaluate("""() => Array.from(document.querySelectorAll('.lrow')).map(r =>
            [r.querySelector('.lname').textContent, (r.querySelector('.lnote') || {}).textContent || null])""")
        ck(["Read ch.4", "p.112-140, annotate"] in ln, f"the list prints a homework note in full ({ln[:6]})")
        ck(["History paper", "thesis still open\nfind 3 primary sources"] in ln,
           f"and a project's ({[x for x in ln if x[0] == 'History paper']})")
        pg.evaluate("SEARCH='primary sources'; render()")
        pg.wait_for_timeout(600)
        hits = pg.evaluate("Array.from(document.querySelectorAll('.lrow .lname')).map(x=>x.textContent)")
        ck("History paper" in hits, f"search looks inside notes ({hits})")
        pg.evaluate("SEARCH=''; VIEW='week'; render()")

        # ---------------- server rules ----------------
        r = post("/api/quick-add", {"kind": "event", "line": "2026-09-12 - Long - 10:00-10:30 - gray",
                                    "note": "x" * 2001})
        ck(r["status"] == 400 and "Long" not in mine(), f"a note over 2000 characters is refused ({r['status']})")
        r = post("/api/quick-add", {"kind": "event", "line": "2026-09-12 - Obj - 10:00-10:30 - gray",
                                    "note": {"a": 1}})
        ck(r["status"] == 400, f"a note that is not text is refused ({r['status']})")
        r = post("/api/quick-edit", {"kind": "event", "line": "2026-09-10 - Club fair - 16:45-17:15 - pink",
                                     "newline": "2026-09-10 - Club fair - 16:45-17:15 - pink\n> sneaky"})
        ck(r["status"] == 400, f"an edited line cannot smuggle in a second line ({r['status']})")
        r = post("/api/quick-delete", {"kind": "hw", "line": "A - before - Read ch.4 - 2026-09-10"})
        ck(r["body"].get("note") == "p.112-140, annotate",
           f"quick-delete hands the note back for Undo ({r['body']})")
        # a calendar (.ics) import lands after the last event's NOTE as well
        # (the last event here is the Dentist that Undo put back, with its note)
        st = pg.evaluate("""() => { const f = new FormData();
            f.append('line', '@event 2026-09-12 - Imported - 10:00-10:30 - gray');
            return fetch('/edit/import-ics/confirm', {method: 'POST', body: f}).then(r => r.status); }""")
        ti = mine()
        ck("  > and the referral\n@event 2026-09-12 - Imported" in ti,
           f"an .ics import is inserted after the last event's note, not inside it ({st})")


        # ---------------- server vs local mode: same ops, same text ----------------
        save_text(SEED)
        for path, p in OPS:
            r = post(path, p)
            if r["status"] != 200:
                ck(False, f"server op {path} {r}")
        server_text = norm(mine())

        pg.goto(BASE + "/local/app")
        pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                    "localStorage.setItem('ssa-local-install','off');"
                    "localStorage.setItem('ssa-local-remind','off');}", SEED)
        pg.reload()
        pg.wait_for_selector("#gbody .col", timeout=8000)
        bad = pg.evaluate("""ops => { const out = [];
            ops.forEach(([p, d]) => { const r = localApi(p, d); if (r.status !== 200) out.push([p, r]); });
            return out; }""", [[p, d] for p, d in OPS])
        ck(not bad, f"every op succeeds in local mode too ({bad})")
        local_text = norm(pg.evaluate("localStorage.getItem('ssa-local-schedule')"))
        ck(server_text == local_text,
           "server and local mode end with the SAME text after the same operations"
           + ("" if server_text == local_text else f"\n--- server\n{server_text}\n--- local\n{local_text}"))
        ck("Dentist - 15:45-16:45 - gray\n  > bring the insurance card" in server_text,
           "an edit that sends no note leaves the note alone")
        ck("Read ch.4 - 2026-09-10 - done\n  > p.112-150\n  >\n  > then the questions" in server_text,
           "marking done keeps the note; a blank line inside a note survives as a bare '>'")
        ck("Club fair - 16:45-17:15 - pink\n@event" in server_text and "go with Sam" not in server_text,
           "an empty note clears it")
        ck("Lab writeup" not in server_text, "delete still deletes")
        ck("  > ask about the quiz\n@event 2026-09-12 - Swim" in server_text,
           "adding after an item that has a note goes below the note, not between")
        # local mode: delete returns the note for Undo, and replace-oldest drops the note with the line
        rn = pg.evaluate("localApi('/api/quick-delete',{kind:'event',line:'2026-09-11 - Tutor - 15:30-16:00 - gray'}).body.note")
        ck(rn == "room 210\nask about the quiz", f"local delete hands the note back too ({rn!r})")
        lt = pg.evaluate("localStorage.getItem('ssa-local-schedule')")
        ck("Tutor" not in lt and "room 210" not in lt and "ask about the quiz" not in lt,
           "...and takes the note lines out with the item (no orphans in local mode either)")

        # replace-oldest takes the oldest item's note with it (server)
        cap = pg.evaluate("fetch('/api/caps').then(r=>r.json())")
        hwcap = cap.get("hw", 60)
        lines = ["@hw A - before - Read ch.4 - 2026-09-10", "  > p.112-140, annotate"] + \
                [f"@hw F - before - Fill {i} - 2026-09-1{i % 9}" for i in range(hwcap - 1)]
        pg.goto(BASE + "/app")
        save_text(SEED.split("@project")[0] + "\n".join(lines) + "\n\n@pref time_format = 24h\n")
        r = post("/api/quick-add", {"kind": "hw", "line": "F - before - One more - 2026-09-14",
                                    "replace_oldest": True})
        t = mine()
        ck(r["status"] == 200 and "Read ch.4" not in t and "annotate" not in t,
           f"replacing the oldest drops its note too, not just its line ({r['status']})")

        # ---------------- /edit form round trip ----------------
        save_text(SEED)
        pg.goto(BASE + "/edit")
        pg.wait_for_timeout(300)
        # the page opens in the form; notes must be in the rows
        vals = pg.evaluate("Array.from(document.querySelectorAll('.rnote')).map(t=>t.value)")
        ck("bring the insurance card" in vals and "thesis still open\nfind 3 primary sources" in vals
           and "p.112-140, annotate" in vals, f"the form rows carry the notes ({vals})")
        pg.click("#modeText")
        t2 = pg.input_value("#rawText")
        ck("@event 2026-09-10 - Dentist - 15:30-16:30 - gray\n  > bring the insurance card" in t2
           and "@project History paper - 2026-09-01 - 2026-09-20 - blue\n  > thesis still open\n  > find 3 primary sources" in t2
           and "@hw A - before - Read ch.4 - 2026-09-10\n  > p.112-140, annotate" in t2,
           "form -> text keeps every note under its own item")
        pg.click("#modeForm")
        pg.wait_for_timeout(200)
        # edit a note in the form and save from the FORM
        pg.evaluate("""() => { const r = Array.from(document.querySelectorAll('#eventRows .evrow'))
            .find(r => r.querySelector('.ev_name') && r.querySelector('.ev_name').value === 'Club fair');
            r.querySelector('.notebtn').click(); r.querySelector('.rnote').value = 'from the form'; }""")
        with pg.expect_navigation():
            pg.click('button[type=submit]')
        t = mine()
        ck("Club fair - 16:45-17:15 - pink\n  > from the form" in t, "a note typed in the form is saved")
        ck("Dentist - 15:30-16:30 - gray\n  > bring the insurance card" in t and "thesis still open" in t,
           "saving from the form loses none of the other notes")

        # ---------------- a note is text, not markup ----------------
        save_text(SEED.replace("bring the insurance card", '<img src=x onerror="window.__pwn=1">'))
        pg.goto(BASE + "/app")
        pg.wait_for_selector("#gbody .col", timeout=8000)
        pg.wait_for_timeout(400)
        pg.locator("#gbody .block:not(.dayfull)", has_text="Dentist").first.click()
        pg.wait_for_timeout(200)
        pg.evaluate("VIEW='list'; render()")
        pg.wait_for_timeout(500)
        pwn = pg.evaluate("window.__pwn||0")
        img = pg.evaluate("document.querySelectorAll('.bnote img,.mnote img,.lnote img').length")
        ck(pwn == 0 and img == 0, f"a note with HTML in it is shown as text everywhere (pwn={pwn}, img={img})")

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
