"""⑦ Importing a PDF must MERGE into the existing schedule, not replace it.
Driven through the real /edit/import-pdf route with a real PDF."""
import os, json, subprocess, sys, tempfile, time

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import urllib.request, urllib.parse, http.cookiejar

# The Veracross PDFs these check against are real student schedules and
# are not in this repository. Without them there is nothing to parse, so
# skip rather than report a failure that is not one.
import glob as _g, os as _o, sys as _s
_dir = _o.environ.get("SSA_PDFS", "/tmp/pdfs")
if not _g.glob(_o.path.join(_dir, "*.pdf")):
    print("  SKIP  no sample PDFs (set SSA_PDFS to a folder of them)")
    _s.exit(0)



WORK = tempfile.mkdtemp(prefix="ssa_mg_")
DB = os.path.join(WORK, "d", "schedule.db")
PORT = "5135"
env = dict(os.environ, DB_PATH=DB, SECRET_KEY="mgtest", ADMIN_PASSWORD="apw",
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
PDF = os.path.join(os.environ.get("SSA_PDFS", "/tmp/pdfs"), "sample-schedule.pdf")

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(path, data):
    return op.open(urllib.request.Request(
        BASE + path, urllib.parse.urlencode(data).encode()))

post("/register", {"email": "27doej@shadysideacademy.org",
                   "name": "Jane Doe", "password": "pw123456"})
post("/login", {"email": "27doej@shadysideacademy.org", "password": "pw123456"})

# An existing schedule: a Spring term the student already had, plus their own
# event and homework, plus a hand-corrected name.
EXISTING = """Jane Q. Doe
27 - Junior

@term Spring 2026 = 01.20-06.30
B - French 2 - FR200 - 1 - Mr. Okafor - C 8 - purple
@sport Swimming - Mon,Thu - 15:45-17:15 - 09.01-11.30 - cyan

@event Mon - Piano - 16:00-17:00 - 09.01-12.20 - pink
@hw B - before - Vocab quiz - 2026-09-10

@pref time_format = 24h
"""
post("/edit", {"schedule_text": EXISTING})

def import_pdf():
    body, bnd = [], "----x"
    with open(PDF, "rb") as f:
        blob = f.read()
    pre = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"pdf\"; "
           f"filename=\"s.pdf\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    payload = pre + blob + f"\r\n--{bnd}--\r\n".encode()
    req = urllib.request.Request(BASE + "/edit/import-pdf", payload)
    req.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    return json.loads(op.open(req).read().decode())

r = import_pdf()
text = r.get("text", "")
print("--- merged ---"); print(text); print("---")

ck("@term Spring 2026" in text, "the term the student already had is still there")
ck("@term Fall 2026" in text or "@term Fall 2027" in text,
   f"the imported term was added (term={r.get('term')})")
ck("French 2 - FR200" in text, "the old term's classes survive")
ck("@sport Swimming" in text, "the old term's @sport survives")
ck("@event Mon - Piano" in text, "the student's own @event survives an import")
ck("@hw B - before - Vocab quiz" in text, "the student's homework survives an import")
ck("Jane Q. Doe" in text and text.count("Jane") == 1,
   "the student's hand-corrected name is kept, not overwritten by the PDF's")
ck(text.count("@pref time_format") == 1, "exactly one @pref line")
ck("24h" in text, "the student's 24h preference is kept")
# Told, but as a note rather than a warning: it is something the import did
# correctly, not something to go and check.
ck(any("Kept your existing term" in w for w in r.get("notes", [])),
   f"the student is told what was kept ({r.get('notes')})")

# import the SAME term again -> replace that block, don't duplicate it
before_terms = text.count("@term")
r2 = import_pdf()
t2 = r2["text"]
ck(t2.count("@term") == before_terms,
   f"re-importing the same term replaces it ({t2.count('@term')} terms, was {before_terms})")
ck(t2.count("Game Design") <= 1, "no duplicated course lines after re-import")

srv.terminate()
try:
    srv.wait(timeout=5)
except Exception:
    srv.kill()

print(f"\n{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
