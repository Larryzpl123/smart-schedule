"""Fall 2026 final assessments: Dec 17 and 18 are finals days.

Upper formers (11, 12): History on the morning of the 17th, English on the
morning of the 18th; 9 and 10 the other way round -- the same @finals lines
as last year (they are written MM.DD, so they carry over). Finals days are
not cycle days, the student's classes are not drawn on them, and the days
around them are untouched."""
import os, subprocess, sys, tempfile, time

APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

WORK = tempfile.mkdtemp(prefix="ssa_fin_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5233"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="fintest", ADMIN_PASSWORD="apw",
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
def student(form):
    return f"""Jane Doe
{form}

@term Fall 2026 = 08.25-01.15
A - Game Design - CS300 - 4 - Ms. Rivera - GH 043 - orange - Ea
B - Art Core - AR100 - 2 - Ms. Delgado - BC Studio - cyan - Ea
C - French 3 - FR300 - 1 - Mr. Okafor - R 310 - purple - Ea
D - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
E - Playwriting - EN424A - 1 - Ms. Ferraro - R 110 - blue - La
F - Linear Algebra - MA514 - 2 - Mr. Halvorsen - R 202 - red - La
G - Adv. Biology - BI400 - 1 - Mr. Bennett - MC 222 - green - La
H - Revolution in Ancient Rome - HI445 - 1 - Mr. Lindqvist - R 205 - yellow - Ea

@pref time_format = 24h
"""

DAY = """k => { const c = document.querySelector(`#gbody .col[data-k='${k}']`);
  const h = Array.from(document.querySelectorAll('#ghead .dcell')).find(x => x.textContent.includes(k.slice(5).replace('-', ' ')) );
  return { blocks: c ? Array.from(c.querySelectorAll('.block:not(.dayfull)')).map(b =>
             b.querySelector('.bname').textContent + ' ' + (b.querySelector('.btime') || {}).textContent) : null,
           info: (typeof DAYMAP !== 'undefined' && DAYMAP[k]) ? DAYMAP[k] : null }; }"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        errs = []
        res = {}
        for form in ("27 - Junior", "29 - Freshman"):
            pg = b.new_page(viewport={"width": 1280, "height": 1000})
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.clock.install(time="2026-12-16T14:00:00Z")
            pg.goto(BASE + "/local/app")
            pg.evaluate("s=>{localStorage.setItem('ssa-local-schedule',s);"
                        "localStorage.setItem('ssa-local-install','off');"
                        "localStorage.setItem('ssa-local-remind','off');}", student(form))
            pg.reload()
            pg.wait_for_selector("#gbody .col", timeout=8000)
            pg.wait_for_timeout(400)
            res[form] = {k: pg.evaluate(DAY, k) for k in
                         ("2026-12-14", "2026-12-16", "2026-12-17", "2026-12-18")}
            if form == "27 - Junior":
                res["colors"] = pg.evaluate("""() => { const out = {};
                  document.querySelectorAll('#gbody .block:not(.dayfull)').forEach(b => {
                    const n = b.querySelector('.bname').textContent.split(' ')[0];
                    out[n] = getComputedStyle(b).backgroundColor; });
                  return out; }""")
            pg.close()

        up, low = res["27 - Junior"], res["29 - Freshman"]
        d17, d18 = up["2026-12-17"], up["2026-12-18"]
        ck(d17["info"] and d17["info"]["type"] == "finals" and d18["info"]["type"] == "finals",
           f"Dec 17 and 18 are finals days ({d17['info']}, {d18['info']})")
        ck(d17["info"]["cycle"] is None and d18["info"]["cycle"] is None, "and not cycle days")
        ck(any(x.startswith("History Assessment") and "08:30" in x for x in d17["blocks"]),
           f"upper former, Dec 17 morning: History ({d17['blocks']})")
        ck(any(x.startswith("English Assessment") and "08:00" in x for x in d18["blocks"]),
           f"upper former, Dec 18 morning: English ({d18['blocks']})")
        ck(not any(x.startswith(("English Assessment")) for x in d17["blocks"])
           and not any(x.startswith("History Assessment") for x in d18["blocks"]),
           "and not the lower formers' papers")
        ck(not [x for x in d17["blocks"] + d18["blocks"]
                if x.split(" ")[0] in ("Game", "Art", "French", "Adv.", "Playwriting", "Linear", "Revolution")],
           "no ordinary classes drawn on finals days")
        l17, l18 = low["2026-12-17"], low["2026-12-18"]
        ck(any(x.startswith("English Assessment") for x in l17["blocks"])
           and any(x.startswith("History Assessment") for x in l18["blocks"]),
           f"9th grade gets it the other way round ({l17['blocks'][:2]}, {l18['blocks'][:2]})")
        # (the live site also has admin anchors in its database, so the exact
        # day numbers differ here; what matters is that the days before stay
        # ordinary, consecutive cycle days)
        before, mon = up["2026-12-16"], up["2026-12-14"]
        ck(before["info"] and before["info"]["cycle"] and len(before["blocks"]) >= 5,
           f"Wed Dec 16 is still an ordinary school day ({before['info'] and before['info']['cycle']}, {len(before['blocks'])} blocks)")
        ck(mon["info"]["cycle"] and (mon["info"]["cycle"] % 8) + 2 == before["info"]["cycle"]
           or ((mon["info"]["cycle"] + 1) % 8) + 1 == before["info"]["cycle"],
           f"Mon-Wed run on as consecutive cycle days ({mon['info']['cycle']} .. {before['info']['cycle']})")

        # each final in its subject's colour: the student's own class colour
        # for that department (History yellow, Math red...), not all pink
        cols = res.get("colors", {})
        ck(cols.get("History") == cols.get("Revolution") and cols.get("Math") == cols.get("Linear")
           and cols.get("English") == cols.get("Playwriting") and len({cols.get("History"), cols.get("Math"), cols.get("English")}) == 3,
           f"finals take their subject's class colour ({cols})")
        ck(not errs, f"no page errors: {errs}")
        b.close()
except ImportError:
    print("  SKIP  playwright not installed")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
