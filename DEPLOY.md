# SSA Smart Schedule — Backend

A login + email-verification backend so students register with their school
email, verify with a code, and manage their own schedule. Same structure as
your E-Dining app (Flask + SQLite + the same `mailer.py`).

## What's here

```
app.py                     Flask app: register / verify / login / edit / API / admin
build_push.py              Builds push.zip — code only, never data. Run before deploy.
mailer.py                  Sends the 6-digit code (real email OR dev-console mode)
wsgi_pythonanywhere.py     Paste into your PythonAnywhere WSGI file
requirements.txt           Flask
schedule.txt               SHARED rotation / community / finals / Wednesday (admin edits)
calendar/2025-2026.txt     Calendars (served read-only)
calendar/2026-2027.txt
static/app.html            The schedule front-end (your index.html, API-aware)
templates/                 register, verify, login, forgot, reset, edit, admin pages
schedule.db                The database. Lives OUTSIDE this folder on the server
                           (see DB_PATH below) so deploys can't destroy it.
```

## How the front-end stays unchanged

`static/app.html` is your existing `index.html`. It auto-detects when it's
served by the backend (URL path `/app`) and, in that mode, fetches:

| Front-end need        | Static mode (file/local) | Backend mode (logged in)   |
|-----------------------|--------------------------|----------------------------|
| own schedule          | embedded / student .txt  | `/api/my-schedule`         |
| other students        | `students/index.txt`     | `/api/student-index`       |
| a student's schedule  | `students/<c>/<n>.txt`   | `/api/student/<id>`        |
| shared rotation etc.  | `schedule.txt`           | `/api/schedule-shared`     |
| calendar              | `calendar/<year>.txt`    | `/api/calendar/<year>`     |

`parseStudent()` and all rendering logic are untouched. So the same file works
both ways.

## Run locally (dev mode — no email needed)

```
pip install -r requirements.txt
python3 app.py        # http://localhost:5001
```

With no SMTP set, the app is in **dev mode**: sign-up skips the email step and
any codes are printed to the console. Good for testing the whole flow.

Flow: open `/register` → `/login` → `/edit` (paste your schedule) → `/app`.

## Deploy to PythonAnywhere

1. Upload this folder to `/home/YOURNAME/schedule-backend` (zip + Upload, or
   `git clone`).
2. **Web tab → Add a new web app → Manual configuration → Python 3.x.**
3. Edit the WSGI file (link on the Web tab). Delete everything, paste
   `wsgi_pythonanywhere.py`, and set:
   - `PROJECT_DIR` to your real path
   - `DB_PATH` to a folder **outside** `PROJECT_DIR` (see below)
   - `SECRET_KEY` to a long random string
   - `ADMIN_PASSWORD` (or leave blank to disable the admin page)
4. **Static files** (Web tab): URL `/static/` → Directory
   `/home/YOURNAME/schedule-backend/static/`.
5. Click **Reload**. Visit `https://YOURNAME.pythonanywhere.com/`.

### Moving the database out of the code folder (do this once)

`schedule.db` holds every account and schedule, and there is no other copy.
If it sits inside the code folder, re-uploading code can overwrite or delete
it. So the app reads its location from `DB_PATH`:

```python
DB = os.environ.get("DB_PATH", "schedule.db")
```

**Migration — in a PythonAnywhere Bash console, BEFORE you reload:**

```bash
mkdir -p /home/YOURNAME/data
cp /home/YOURNAME/schedule-backend/schedule.db /home/YOURNAME/data/schedule.db
ls -l /home/YOURNAME/data/schedule.db          # confirm it's there and non-zero
```

Then set in the WSGI file and reload:

```python
os.environ["DB_PATH"] = "/home/YOURNAME/data/schedule.db"
```

Copy, don't move — leave the old file where it is until you've logged in and
confirmed the accounts are all still there. Then delete it.

If you set `DB_PATH` but forget the copy, the app still starts, but writes a
loud block into the **error log** saying an empty database is about to be
created, with the exact `cp` command. Check the error log after a reload.

With `DB_PATH` unset (i.e. on your laptop) nothing changes — it still uses
`schedule.db` next to `app.py`.

### Importing a Veracross schedule PDF

`/edit` has an **Import my Veracross PDF** button. The server parses the PDF and
sends back schedule text that fills the editor; nothing is saved until the
student presses Save.

Needs `pdfplumber` on the server: `pip3 install --user pdfplumber`, **then hit
Reload on the Web tab** — the web app is a long-running process and won't see a
newly installed package until it restarts. Without it the import returns 503
naming pdfplumber and the exact command, and the admin dashboard shows a banner;
the rest of the app is unaffected. A PDF that genuinely fails to parse returns
400 with the underlying exception in the message, so a server problem is never
reported as a bad file.

`pdf_import.py` reads the PDF **geometrically**, not as text. `extract_text()`
interleaves the eight day-columns line by line, paints bold twice
("UUnnsscchheedduulleedd"), and splits a wrapped cell's period letter onto the
line above its "- Ea)". `find_tables()` is better but cuts a course block in
half when it straddles a table row. So the parser takes column boundaries from
the table's rectangles, buckets words by x-centre, and cuts blocks on the
vertical line gap (8.3pt inside a block, >=10.6pt between). One sample also puts
the header on page 1 and the grid on page 2, so every page is scanned.

### Two optional fields on a class line

```
A - Physics 2 (Calc.) - PY430 - 1 - Dr. Peterson - MC 111 - blue - La - days 2,6
```

The first seven fields are unchanged and old schedules still parse. After the
colour, in any order:

| Field | Meaning |
|---|---|
| `La` / `Ea` | which lunch track — only changes the one midday period each day |
| `days 2,6` | meets only on these rotation days |

`days` exists because every one of the five real schedules has a science course
that holds one letter for all six of its slots **and takes two more in a letter
that is otherwise free** — a lab. Without it the app draws that lab on all six,
four of them wrong.

Per-course `La`/`Ea` exists because the school-wide `@latearly` line cannot be
right for everyone: period D is `La` for one student and `Ea` for another. That
line is kept only as a fallback for schedules saved before this change.

### The rotation and its anchors

`@day` lines now carry both lunch tracks for the midday period:

```
@day 1 = A:08:15-09:05 ... D:11:50-12:40|12:25-13:15 ... F:14:15-15:00
```

These were rebuilt from four 2026-2027 PDFs; the previous config was **missing
H on days 2 and 4**, so period H appeared four times per rotation instead of six.

`calendar/2026-2027.txt` is anchored from the SSA Daily WALK:

```
@anchor 2026-08-26 = 1
@anchor 2026-09-04 = 8
08.25-08.25 "Convocation" noclass
```

**Aug 25 is Convocation — the first day for students but NOT cycle day 1.**
Counted as a normal school day it phase-shifts the whole year by one. Two other
corrections came from the official Academic Calendar: Dec 11 and May 17 are the
*Lower School's* evaluation days and the Senior School still has class, and
June 3 is an early dismissal rather than a closure.

**A snow day still can't be predicted**, and nothing tells the app one happened.
So the admin page has **Fix the cycle day**: pick the date, pick what cycle day
it really is, Save. That writes an `@anchor` into the right school-year file;
every school day after it re-counts, earlier dates are untouched, and re-saving
the same date replaces the line rather than duplicating it. No hand-editing.

### Updating the live site (push.zip)

Don't upload files one at a time. Build one zip that physically cannot contain
data:

```bash
cd "~/Desktop/Smart Schedule"
python3 build_push.py          # prints what's in and what's out, writes push.zip
```

On PythonAnywhere: **Files** → upload `push.zip` to `/home/YOURNAME/`, then in a
Bash console:

```bash
cd ~
unzip -o push.zip -d push_tmp
cp -r push_tmp/push/* schedule-backend/
rm -rf push_tmp push.zip
```

Then **Web** tab → green **Reload**.

| Never pushed | Why |
|---|---|
| `schedule.db` | the database (and it lives outside the code folder anyway) |
| `schedule.txt` | **the admin edits the server's copy** via `/admin/shared` |
| `wsgi_pythonanywhere.py` | the server's copy holds the real secrets |
| `pre-import_*.json` | restore snapshots |
| `samples/`, `*.md` | development only |

The script aborts the build if a `.db` or a key file ever ends up in `push/`,
and warns if `app.py` renders a template that isn't in the zip.

`schedule.txt` is the one that catches people: it is *server-mutable*, so
pushing your local copy would silently undo whatever the admin changed in the
browser. If you really did edit it locally, merge with the live copy from
`/admin/shared` first, then `python3 build_push.py --with-shared`.

`wsgi_pythonanywhere.py` is never pushed, so WSGI changes (like adding
`DB_PATH`) must be made by hand on the Web tab.

### Abuse limits

Registration itself is not limited — school emails are one per student and
verified, so fake signups aren't a realistic threat. These are:

| Limit | Value | Stops |
|---|---|---|
| `login` | 8 per address / 15 min | guessing a classmate's password |
| `sendcode` | 5 per address / hour | flooding someone's inbox with codes |
| `global_send` | **150 per day, all addresses** | burning the Gmail quota |
| `verifycode` | 10 per address / 15 min | brute-forcing a 6-digit code |
| `resetcode` | 10 per address / 15 min | same, on the password-reset path |
| `adminlogin` | 8 per IP / 15 min | guessing the admin password |

Codes also **expire after 15 minutes** (`CODE_TTL_SECONDS`). They used to be
valid forever, which made 10^6 combinations a matter of patience rather than a
real obstacle. With a 10-guess window the same attack needs
10^6 / (10 x 4/hr x 24) ≈ 1,042 days.

The global daily cap is the one that isn't obvious. A per-address limit does
nothing against someone rotating through a list of school addresses:
300 addresses x 5/hour = 1,500 emails/hour, and Gmail's ceiling is 500/day.

Counters live in a `rate_limits` table, **not in memory**, because the daily
cap is a budget for a whole day — an in-memory counter resets on every Reload
and every worker recycle, so the one limit that protects the quota would be
the one that doesn't hold.

The admin dashboard shows the 24h email count and a **Reset counter** button
for when the cap trips on legitimate traffic. That resets *ours*, not Google's.

`/verify/resend` is POST-only. As a GET it was a single URL that emailed a code
on every load — a refresh, a link preview or a crawler could each fire it.

Both schema changes (`users.code_expires`, the `rate_limits` table) are applied
by `ensure_schema()` on startup with `ALTER TABLE`, so an existing database is
upgraded in place with nothing lost.

### Backing up and restoring

There is no automatic backup. Download one regularly.

| Who | Where | What it does |
|-----|-------|--------------|
| Admin | `/admin` → **Export all data** | Downloads every account as one JSON file |
| Admin | `/admin` → **Restore from a backup** | Upload that JSON, review a diff, tick what to write |
| Student | `/edit` → **Download my schedule** | Their own schedule as a `.txt` |
| Student | `/edit` → **Load from a .txt file** | Fills the editor from a file (nothing saves until Save) |

The backup contains password **hashes**, not passwords, so a restore gives
students their accounts back without anyone learning a password. Live
verification codes are deliberately left out of the file.

Restore never overwrites blindly:

- Accounts in the backup that don't exist yet are listed as **new** and are
  pre-ticked.
- Accounts that exist but differ are listed as **conflict**, showing which
  fields would change, and are **not** pre-ticked.
- Identical accounts are greyed out.
- Before anything is written, the current data is snapshotted next to the
  database as `pre-import_<timestamp>.json`, so a wrong restore is undoable.

### Turning on real verification emails

PythonAnywhere free accounts can only reach whitelisted hosts; Gmail SMTP is
allowed. In a Google account, create an **App Password**, then in the WSGI file
uncomment and fill:

```python
os.environ["SMTP_HOST"] = "smtp.gmail.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = "youraddress@gmail.com"
os.environ["SMTP_PASS"] = "your-app-password"
```

Reload. Now sign-up requires the emailed code.

## Security choices (same as E-Dining)

- Passwords are stored **hashed** (Werkzeug). The app never stores or emails a
  plaintext password.
- Sign-up requires a code sent to the `@shadysideacademy.org` address, so only
  someone with that mailbox can register it.
- "Forgot password" emails a **one-time code** to set a new password — it never
  sends the old one.
- Each logged-in user can only read/write **their own** schedule. Other users
  appear in search only if they tick "let others find my schedule."

## Admin

If `ADMIN_PASSWORD` is set, visit `/admin/login` to edit the shared
`schedule.txt` (rotation, community time, finals, Wednesday remap) in the
browser. A syntax error there affects everyone, so edit carefully.
