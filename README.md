# Smart Schedule

A web app for a school that runs an **8 day rotating schedule** rather than a
fixed weekly one. A student enters their courses once, or uploads the schedule
PDF their student information system gives them, and the app lays out their
actual week: rotation letters, late and early lunch tracks, shortened Wednesday
timings, assembly and exam days, and no-school days read from the published
academic calendar.

Built for Shady Side Academy's Senior School. It is a student project and not
an official school tool.

## Two ways to run it, and why

The interesting design decision is that there are two modes and **one app**.

| | Account mode | Local mode |
|---|---|---|
| Where the schedule lives | a row in the server's SQLite database | the browser's `localStorage` |
| What the server receives | the schedule text | nothing |
| Sign-up | school email address plus an emailed code | none |
| Password | hashed with Werkzeug's PBKDF2 | there is no account |
| Works across devices | yes | no, one browser on one device |
| Recoverable if lost | yes, plus per-save version history | no, only from a backup the student exported |

Local mode exists because "trust the operator with the data" is a weak answer
to a privacy question and "the operator never receives the data" is a strong
one. It is at `/local`.

They are not two apps. There is exactly one branch point in the front end:

```js
async function api(path, payload){
  if(!LOCAL){ const r = await fetch(path, {...}); return {status: r.status, body: ...}; }
  return localApi(path, payload);   // same arguments, same shape back
}
```

Everything else, including the schedule format, is shared, so an export from
one mode imports into the other. Building a second app would have let the two
drift apart within a term.

## The schedule format

Plain text, one thing per line, designed to be hand-editable and diffable:

```
Jane Doe
27 - Junior

@term Fall 2026 = 08.25-01.15
A - Physics 2 - PY430 - 1 - Ms. Rivera - MC 210 - green - La - days 2,6
B - French 2 - FR200 - 1 - Mr. Okafor - C 8 - purple - Ea

@sport Cross Country - Mon,Wed - 15:45-17:15 - 08.25-11.05 - cyan
@event Tue - Piano lesson - 17:30-18:30 - pink
@event 2026-11-01 - Application deadline - allday - red
@project History Paper - 2026-09-07 - 2026-11-20 - yellow
@hw E - before - Prospectus - 2026-09-17 - History Paper
@pref time_format = 12h
```

`@hw` hangs on a **rotation letter**, not a time, so a piece of homework
follows its class around the rotation and lands correctly on a shortened
Wednesday or a late-lunch day.

The school-wide half lives in `schedule.txt` (bell schedules, assembly days,
exam timetables, all-school events) and the academic calendar in
`calendar/YYYY-YYYY.txt`. The rotation's ground truth is an anchor line:

```
@anchor 2026-08-26 = 1
```

Cycle days are counted from the nearest anchor, so an unplanned closure is
corrected by adding one line rather than by re-deriving a year.

## Layout

```
app.py                  Flask app: auth, admin, the API, both modes
pdf_import.py           the student information system's PDF -> schedule text
ics_import.py           .ics -> proposed @event / @project lines (RFC 5545)
mailer.py               SMTP, with a dev mode that prints instead of sending
decrypt_backup.py       opens an encrypted admin export, off-server
static/app.html         the whole front end: one file, vanilla JS, no framework
templates/              server-rendered pages (login, editor, admin)
calendar/               published academic calendars
schedule.txt            school-wide bell schedules and events
tests/                  see below
```

## Running it

```
pip3 install -r requirements.txt
python3 app.py            # http://localhost:5001
```

With no SMTP configured it runs in dev mode: verification codes are printed to
the console instead of emailed, so sign-up works offline.

For deployment see `DEPLOY.md` and `wsgi_pythonanywhere.example.py`. Every
secret is read from the environment and set in the WSGI file on the server;
none is in this repository.

## Tests

```
cd tests && ./run_tests.sh          # all of them, 4 at a time
./run_tests.sh -j1 local            # one file, its own output
```

Real browsers via Playwright plus HTTP-level checks. They cover the things
that are easy to get quietly wrong: the rotation across a closure, DST
boundaries, a browser set to another timezone, the exclusive `DTEND` on an
all-day `.ics` event, block overlap and clipping, rate limits surviving a
restart, XSS in every place the page builds HTML, and that local mode reaches
the network exactly zero times.

Three test files are **not** in this repository. They exercise the PDF
importer against real student schedules, which contain home addresses and
parents' phone numbers and are not mine to distribute. Three more skip
themselves unless you point `SSA_PDFS` at a folder of such PDFs.

## A note on what this does not do

It does not connect to, log in to, or scrape any school system. Students type
their own schedule in or upload their own PDF. The only school data in it is
the published academic calendar and the published bell schedule.

## Licence

MIT, see `LICENSE`.
