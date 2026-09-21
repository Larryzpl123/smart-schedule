"""
SSA Smart Schedule - backend
============================================================================
Same structure / philosophy as the E-Dining app:

  Students:  register with their @shadysideacademy.org email, verify by a code
             emailed to them, then log in and manage THEIR OWN schedule.
  Shared:    the school-wide rotation / community-time / finals / Wednesday
             definitions live in schedule.txt and are served read-only to
             everyone (only an admin edits that file).

Security notes (identical approach to E-Dining):
  - sessions: Flask remembers who's logged in via a signed cookie.
  - password hashing: we store a scrambled hash, never the real password.
  - email verification: a 6-digit code is sent on sign-up; account stays
    unverified (can't log in) until the code is entered.
  - @login_required: a reusable gate in front of private pages.

The student schedule is stored as TEXT in the exact same format the static
front-end already parses (the `@term / A-H / @immersive / @sport / @pref`
lines), so the front-end's parseStudent() does not change at all.
============================================================================
"""

import hmac
import io
import json
import os
import re
import random
import secrets
import shutil
import sqlite3
import tempfile
import time

from flask import (Flask, request, redirect, url_for, session, flash,
                   render_template, jsonify, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash

import mailer

# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-before-deploy")

# Keep people logged in for a long time (no annoying re-logins).
# The cookie lives ~1 year and is refreshed on every request.
from datetime import datetime, timedelta
app.permanent_session_lifetime = timedelta(days=365)


# Cookie hardening. SameSite=Lax means the browser will not send the session
# cookie on a cross-site POST, which is what stops another page from silently
# submitting /edit or an /admin form on behalf of somebody who is logged in.
# (Modern browsers default to Lax, but relying on a default is not the same as
# saying so.) Secure is opt-in via the environment because it would break
# plain-http local testing; set COOKIE_SECURE=1 in the WSGI file on the server.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "") == "1",
)


@app.before_request
def _make_session_permanent():
    session.permanent = True

ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "shadysideacademy.org")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # empty = admin disabled

# Where the SQLite file lives.
#
# On the server this MUST point OUTSIDE the code directory, so that re-uploading
# code can never overwrite or delete real student data. Set it in the WSGI file:
#     os.environ["DB_PATH"] = "/home/smartschedule/data/schedule.db"
# Locally, with DB_PATH unset, it stays "schedule.db" next to app.py as before.
DB = os.environ.get("DB_PATH", "schedule.db")

# How long an emailed 6-digit code stays usable. Codes used to be valid
# forever, which made them brute-forceable at leisure: 10**6 combinations and
# unlimited time is just a matter of patience.
CODE_TTL_SECONDS = int(os.environ.get("CODE_TTL_SECONDS", "900"))    # 15 min

# Hard ceiling on codes emailed per DAY, across every address.
#
# Google's own limit for a free Gmail account is 500 messages/day, and once you
# hit it sending is blocked for 1-24 hours -- during which nobody can register
# or reset a password. So the app stops itself first, at a number we control
# and can reset from the admin page. Raise this in the WSGI file before a
# school-wide rollout.
MAX_CODES_PER_DAY = int(os.environ.get("MAX_CODES_PER_DAY", "150"))

# Where the code lives (used for the migration guard and for pre-import snapshots).
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DB = os.path.join(APP_DIR, "schedule.db")

# Files that live next to app.py and are served read-only to the front-end.
SHARED_SCHEDULE_FILE = "schedule.txt"
CALENDAR_DIR = "calendar"


# ---------------------------------------------------------------------------
# tiny database helper (same shape as E-Dining's query())
# ---------------------------------------------------------------------------
def query(sql, args=(), fetch=False, one=False):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, args)
    result = None
    if fetch:
        rows = cur.fetchall()
        result = (rows[0] if rows else None) if one else rows
    conn.commit()
    conn.close()
    return result


def ensure_schema():
    """Create tables if missing; safe to run every startup."""
    query("""CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT UNIQUE NOT NULL,
        name          TEXT,
        username      TEXT,                 -- ssa username, e.g. 27doej (for search)
        class_year    TEXT,                 -- e.g. 27
        grade         INTEGER,              -- 9-12
        password_hash TEXT NOT NULL,
        verified      INTEGER DEFAULT 0,
        verify_code   TEXT,
        schedule_text TEXT DEFAULT '',      -- the student's own .txt content
        is_public     INTEGER DEFAULT 0,    -- opt-in: let others look me up
        created_at    TEXT DEFAULT (datetime('now','localtime')))""")

    # Columns added after the first release. ALTER, don't recreate -- the live
    # database has real accounts in it.
    add_column_if_missing("users", "code_expires", "REAL")

    # Somewhere to record one-off migrations, so they run once and not on
    # every startup (which would undo a deliberate choice made afterwards).
    query("""CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT)""")

    # Anchors and community-time overrides added through /admin. They used to
    # be written straight into calendar/<year>.txt -- but build_push.py ships
    # calendar/ as code, so a deploy silently overwrote every one of them (it
    # did, on 2026-09-06, wiping the anchor that re-synced the rotation after
    # two rain days). Keeping them here means the file stays yours to edit
    # locally and the server's additions survive every push.
    query("""CREATE TABLE IF NOT EXISTS calendar_edits (
        year    TEXT NOT NULL,             -- "2026-2027"
        kind    TEXT NOT NULL,             -- "anchor" | "community_on"
        date    TEXT NOT NULL,             -- YYYY-MM-DD
        value   TEXT NOT NULL,             -- cycle day, or the label
        updated TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (year, kind, date))""")

    # What the schedule looked like before each change. See remember_schedule.
    query("""CREATE TABLE IF NOT EXISTS schedule_history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL,
        saved_at TEXT DEFAULT (datetime('now','localtime')),
        text     TEXT NOT NULL)""")
    query("CREATE INDEX IF NOT EXISTS idx_hist_user "
          "ON schedule_history(user_id, id)")

    # Every admin action worth noticing. Kept so that "did somebody get in?"
    # has an answer other than a shrug.
    query("""CREATE TABLE IF NOT EXISTS admin_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      TEXT DEFAULT (datetime('now','localtime')),
        event   TEXT NOT NULL,          -- login_ok | login_fail | export | ...
        ip      TEXT,
        fwd     TEXT,                   -- X-Forwarded-For, verbatim
        agent   TEXT,
        detail  TEXT)""")
    query("CREATE INDEX IF NOT EXISTS idx_admin_log ON admin_log(id)")

    # One row per attempt; old rows are swept. Used for every rate limit.
    query("""CREATE TABLE IF NOT EXISTS rate_limits (
        action TEXT NOT NULL,
        who    TEXT NOT NULL,
        ts     REAL NOT NULL)""")
    query("CREATE INDEX IF NOT EXISTS idx_rate_limits "
          "ON rate_limits(action, who, ts)")


# How many old versions each student keeps. Twelve rows of ~600 bytes is
# ~7 KB a student, so 600 accounts is about 4 MB -- see storage_report().
HISTORY_KEEP = 12
# ...of which this many are the most recent changes, whatever their spacing.
# The rest are spread one per day, because "five minutes ago", five times, is
# no use to somebody who wrecked their schedule on Tuesday.
HISTORY_RECENT = 5


def remember_schedule(user_id, old_text):
    """Keep what the schedule looked like BEFORE a change. Never raises.

    Called with the previous text, not the new one: the point is to be able to
    go back to what was there, and the current version is already in users."""
    try:
        old_text = old_text or ""
        newest = query("SELECT text FROM schedule_history WHERE user_id=? "
                       "ORDER BY id DESC LIMIT 1", (user_id,),
                       fetch=True, one=True)
        if newest and newest["text"] == old_text:
            return                      # nothing changed since the last one
        if not old_text.strip():
            return                      # an empty schedule is not worth a rung
        query("INSERT INTO schedule_history (user_id, text) VALUES (?,?)",
              (user_id, old_text))
        rows = query("SELECT id, saved_at FROM schedule_history WHERE user_id=? "
                     "ORDER BY id DESC", (user_id,), fetch=True) or []
        keep, seen_days = set(), set()
        for i, r in enumerate(rows):
            if i < HISTORY_RECENT:
                keep.add(r["id"])
                continue
            day = (r["saved_at"] or "")[:10]
            if day not in seen_days:
                seen_days.add(day)
                keep.add(r["id"])
            if len(keep) >= HISTORY_KEEP:
                break
        drop = [r["id"] for r in rows if r["id"] not in keep]
        for did in drop:
            query("DELETE FROM schedule_history WHERE id=?", (did,))
    except Exception as e:                       # pragma: no cover
        print(f"[history] could not record: {e}", flush=True)


def set_schedule(user, new_text, grade=None, remember=True):
    """The ONE way the schedule column is written.

    Six routes used to run their own UPDATE, which is six places to forget the
    history row -- and the one that forgets is the one that eats somebody's
    schedule.

    remember=False for the grid's single-line endpoints. A version per
    checkbox tick is noise: it would fill the ladder with states nobody wants
    to go back to, and every one of those changes is already undoable on its
    own terms (delete offers Undo, adding is undone by deleting, a tick
    unticks, an edit shows the old values in the dialog you are typing into).
    The ladder is for the change that loses a lot at once."""
    # Pressing Save without having changed anything is not a version. Left in,
    # it would push the useful rungs off the bottom of the ladder with copies
    # of the state you are already looking at.
    if remember and (user["schedule_text"] or "") != (new_text or ""):
        remember_schedule(user["id"], user["schedule_text"])
    if grade is None:
        query("UPDATE users SET schedule_text=? WHERE id=?",
              (new_text, user["id"]))
    else:
        query("UPDATE users SET schedule_text=?, grade=? WHERE id=?",
              (new_text, grade, user["id"]))


# Pruned inline on every write (see admin_note), so this is a hard ceiling,
# not a cron. A few hundred rows is tens of KB of disk -- the number is about
# how far back you can look, not about space.
ADMIN_LOG_KEEP = int(os.environ.get("ADMIN_LOG_KEEP", "100"))
# Where the "somebody just logged in as admin" mail goes. Falls back to the
# account the mail is sent FROM, which is the one address we know exists.
ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL")
               or os.environ.get("SMTP_FROM")
               or os.environ.get("SMTP_USER") or "")


def client_ip():
    """The visitor's address, as far as it can be known here.

    PythonAnywhere's load balancer sets X-Real-IP to the actual client, and
    their own docs say to read that one: X-Forwarded-For is a list a client
    can inject into, and request.remote_addr is the proxy. Using remote_addr
    is not "stricter", it is one shared bucket for the entire school -- and it
    silently broke the new-place alert, because the proxy address never
    changes, so every sign-in looked like one we had seen before.

    Falls back to remote_addr so that running locally (no proxy, no header)
    still behaves sensibly."""
    return ((request.headers.get("X-Real-IP") or "").strip()
            or request.remote_addr or "?")


def _client_ip():
    """(ip, forwarded-for verbatim, user agent) for the admin log.

    X-Forwarded-For is kept because it is occasionally useful to a human
    reading the log, and it is never used for a decision."""
    return (client_ip(),
            (request.headers.get("X-Forwarded-For") or "")[:120],
            (request.headers.get("User-Agent") or "")[:200])


def admin_note(event, detail=""):
    """Record one admin action. Never raises -- logging must not break the app."""
    try:
        ip, fwd, agent = _client_ip()
        query("INSERT INTO admin_log (event, ip, fwd, agent, detail) "
              "VALUES (?,?,?,?,?)", (event, ip, fwd, agent, detail[:300]))
        row = query("SELECT id FROM admin_log ORDER BY id DESC "
                    "LIMIT 1 OFFSET ?", (ADMIN_LOG_KEEP,), fetch=True, one=True)
        if row:
            query("DELETE FROM admin_log WHERE id <= ?", (row["id"],))
    except Exception as e:                       # pragma: no cover
        print(f"[adminlog] {event}: {e}", flush=True)


def admin_recent(limit=25):
    return query("SELECT * FROM admin_log ORDER BY id DESC LIMIT ?",
                 (limit,), fetch=True) or []


def admin_alert(subject, body):
    """Mail the admin. Silent no-op when SMTP is not configured."""
    admin_alert_to(ADMIN_EMAIL, subject, body)


def admin_alert_to(addr, subject, body):
    """Mail one address, never raising. Empty address = silent no-op."""
    if not addr:
        return
    try:
        mailer.send(addr, subject, body)
    except Exception as e:                       # pragma: no cover
        print(f"[adminalert] {e}", flush=True)


def meta_get(key):
    r = query("SELECT value FROM app_meta WHERE key=?", (key,), fetch=True, one=True)
    return r["value"] if r else None


def meta_set(key, value):
    query("INSERT INTO app_meta (key, value) VALUES (?, ?) "
          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def migrate_privacy_default():
    """Accounts used to be searchable by every other logged-in student unless
    they opted out, and the search box was later hidden, so nobody knew the
    setting existed. Turn sharing off for everyone once; anybody who wants it
    can switch it back on in /edit.

    Runs once, recorded in app_meta -- otherwise it would keep resetting the
    choice of anyone who deliberately opted in."""
    if meta_get("privacy_default_off"):
        return
    changed = query("SELECT COUNT(*) AS n FROM users WHERE is_public=1",
                    fetch=True, one=True)
    query("UPDATE users SET is_public=0 WHERE is_public=1")
    meta_set("privacy_default_off", "done")
    if changed and changed["n"]:
        print(f"[privacy] schedules are now private by default; "
              f"{changed['n']} existing account(s) switched off. They can opt "
              f"back in from /edit.", flush=True)


def add_column_if_missing(table, column, decl):
    """Add a column to an existing table without touching the data in it."""
    cols = {r["name"] for r in query(f"PRAGMA table_info({table})", fetch=True)}
    if column not in cols:
        query(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def db_startup_check():
    """Create the DB folder, and refuse to start quietly if a migration was
    forgotten.

    The dangerous case: DB_PATH is set to a new location that doesn't exist yet
    while the OLD schedule.db is still sitting in the code directory. Without
    this check SQLite would happily create a brand-new EMPTY database and every
    existing account would look deleted."""
    folder = os.path.dirname(os.path.abspath(DB))
    if folder:
        os.makedirs(folder, exist_ok=True)

    if (not os.path.exists(DB)
            and os.path.abspath(DB) != LEGACY_DB
            and os.path.exists(LEGACY_DB)):
        bar = "!" * 72
        print("\n" + bar)
        print("[DB]  DB_PATH points at a database that does not exist yet:")
        print(f"[DB]      {os.path.abspath(DB)}")
        print("[DB]  ...but the OLD database is still in the code directory:")
        print(f"[DB]      {LEGACY_DB}")
        print("[DB]")
        print("[DB]  An EMPTY database is about to be created and every existing")
        print("[DB]  account will look deleted. Copy the old file across FIRST:")
        print(f"[DB]      cp '{LEGACY_DB}' '{os.path.abspath(DB)}'")
        print(bar + "\n", flush=True)


db_startup_check()
ensure_schema()
migrate_privacy_default()


# ---------------------------------------------------------------------------
# RATE LIMITING
#
# Same interface as E-Dining's, but the counters live in SQLite instead of in
# memory. Reason: the daily email cap is a budget for a whole DAY. An in-memory
# counter resets on every Reload and on every worker recycle, so the one limit
# that protects the Gmail quota would be the one that doesn't hold.
#
# What this is actually defending against (registration itself is NOT limited
# -- school emails are one per student and verified, so fake signups aren't a
# realistic threat):
#   1. guessing a classmate's password           -> limit login attempts
#   2. flooding a classmate's inbox with codes   -> limit sends per address
#   3. burning the daily Gmail quota so NOBODY   -> global daily cap
#      can register or reset a password
#   4. brute-forcing a 6-digit code              -> limit guesses + expire codes
# ---------------------------------------------------------------------------
def rate_limited(action, who, limit, per_seconds):
    """Record an attempt. Returns True if this one should be BLOCKED."""
    now = time.time()
    query("DELETE FROM rate_limits WHERE action=? AND who=? AND ts<?",
          (action, who, now - per_seconds))
    n = query("SELECT COUNT(*) c FROM rate_limits WHERE action=? AND who=?",
              (action, who), fetch=True, one=True)["c"]
    if n >= limit:
        return True
    query("INSERT INTO rate_limits (action, who, ts) VALUES (?,?,?)",
          (action, who, now))
    return False


def clear_limit(action, who):
    """Wipe the counter after a success (e.g. a correct password)."""
    query("DELETE FROM rate_limits WHERE action=? AND who=?", (action, who))


def sweep_rate_limits():
    """Drop rows older than the longest window (the daily cap) so the table
    can't grow without bound."""
    query("DELETE FROM rate_limits WHERE ts < ?", (time.time() - 86400 - 3600,))


def code_deadline():
    return time.time() + CODE_TTL_SECONDS


def code_is_valid(user, code):
    """A code must match AND still be within its 15-minute window.

    A row whose code_expires is NULL predates this feature -- treat it as
    expired rather than as valid forever."""
    if not user or not code or not user["verify_code"]:
        return False
    if code != user["verify_code"]:
        return False
    return user["code_expires"] is not None and time.time() <= user["code_expires"]


def codes_sent_today():
    return query("SELECT COUNT(*) c FROM rate_limits WHERE action='global_send' "
                 "AND ts >= ?", (time.time() - 86400,), fetch=True, one=True)["c"]


def send_verification_code(to_email, code):
    """The ONE place codes go out, so the daily cap can't be bypassed by
    adding a new route later. Returns (sent: bool, reason: str)."""
    if not mailer.is_enabled():
        mailer.send_code(to_email, code)      # dev mode: prints to the console
        return True, "dev"
    sweep_rate_limits()
    if rate_limited("global_send", "all", MAX_CODES_PER_DAY, 86400):
        print(f"[rate] DAILY EMAIL CAP HIT ({MAX_CODES_PER_DAY}), refused to "
              f"email {to_email}", flush=True)
        return False, "daily-cap"
    mailer.send_code(to_email, code)
    return True, "sent"


TOO_MANY_SENDS = ("Too many codes have been requested for that address. "
                  "Please wait a while before trying again.")
CAP_HIT = ("This site has hit its daily limit for verification emails. "
           "Please try again tomorrow, or ask the admin.")


def current_user():
    if "user_id" not in session:
        return None
    return query("SELECT * FROM users WHERE id=?", (session["user_id"],),
                 fetch=True, one=True)


# ---------------------------------------------------------------------------
# permission gate
# ---------------------------------------------------------------------------
from functools import wraps


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*a, **kw)
    return wrapped


# How recently the admin password must have been typed for the four routes
# that can leak or destroy data. The session itself lasts a year on purpose
# (one admin, logging in constantly is how people pick a shorter password);
# this is the part that a stolen cookie does not get you.
ADMIN_FRESH_SECONDS = int(os.environ.get("ADMIN_FRESH_SECONDS", "600"))


def admin_is_fresh():
    at = session.get("admin_at")
    return bool(at) and (time.time() - at) < ADMIN_FRESH_SECONDS


def admin_fresh_required(view):
    """For export, restore, delete-account and reset-password.

    Everything else on /admin -- reading the disk panel, adding an anchor,
    editing the shared config -- does NOT ask again, because an admin page
    that demands a password every few minutes is an admin page whose password
    ends up short and written down."""
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        if not admin_is_fresh():
            session["reauth_next"] = request.full_path if request.method == "GET" else url_for("admin_home")
            return redirect(url_for("admin_reauth"))
        return view(*a, **kw)
    return wrapped


def new_code():
    return f"{random.randint(0, 999999):06d}"


def derive_username(email):
    """27doej@shadysideacademy.org -> 27doej"""
    return email.split("@")[0].strip().lower()


def derive_class_year(username):
    """Leading digits of the username, e.g. 27doej -> 27."""
    m = re.match(r"(\d{2})", username or "")
    return m.group(1) if m else ""


# ===========================================================================
# PAGES
# ===========================================================================
@app.route("/")
def home():
    """Two doors, not one door and a footnote.

    This used to redirect straight to /login, which made an account look like
    the only way in and buried the mode that keeps a schedule off this server
    entirely. Those are two real choices with a real trade-off between them,
    so the page states the trade-off and lets people pick. Anyone already
    logged in never sees it."""
    if "user_id" in session:
        return redirect(url_for("app_view"))
    return render_template("home.html")


@app.route("/app")
@login_required
def app_view():
    """Serves the schedule front-end (the existing index.html)."""
    return send_from_directory("static", "app.html")


# ---- register ----
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form["password"]

        if not email.endswith("@" + ALLOWED_DOMAIN):
            flash(f"Please use your @{ALLOWED_DOMAIN} school email.", "error")
            return redirect(url_for("register"))
        # Eight, not six. Everybody's username is derivable from their name
        # (27doej), the login is rate limited but not silent, and six lowercase
        # letters is a few hours of somebody being bored. Existing accounts
        # keep the password they have; this applies to new ones and resets.
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("register"))

        existing = query("SELECT * FROM users WHERE email=?", (email,),
                         fetch=True, one=True)
        if existing and existing["verified"]:
            flash("That email is already registered. Try logging in.", "error")
            return redirect(url_for("login"))

        username = derive_username(email)
        class_year = derive_class_year(username)
        code = new_code()
        pw = generate_password_hash(password)

        # If email is OFF (dev mode), skip verification entirely.
        if not mailer.is_enabled():
            if existing:
                query("UPDATE users SET name=?, username=?, class_year=?, "
                      "password_hash=?, verified=1, verify_code=NULL WHERE email=?",
                      (name, username, class_year, pw, email))
            else:
                query("INSERT INTO users (email, name, username, class_year, "
                      "password_hash, verified) VALUES (?,?,?,?,?,1)",
                      (email, name, username, class_year, pw))
            flash("Account created (dev mode, no email). Please log in.", "ok")
            return redirect(url_for("login"))

        # Checked BEFORE writing anything: a blocked attempt must not be able
        # to overwrite the password hash on someone's unverified account.
        if rate_limited("sendcode", email, limit=5, per_seconds=3600):
            flash(TOO_MANY_SENDS, "error")
            return redirect(url_for("register"))

        if existing:
            query("UPDATE users SET name=?, username=?, class_year=?, "
                  "password_hash=?, verify_code=?, code_expires=? WHERE email=?",
                  (name, username, class_year, pw, code, code_deadline(), email))
        else:
            query("INSERT INTO users (email, name, username, class_year, "
                  "password_hash, verified, verify_code, code_expires) "
                  "VALUES (?,?,?,?,?,0,?,?)",
                  (email, name, username, class_year, pw, code, code_deadline()))

        sent, _ = send_verification_code(email, code)
        if not sent:
            flash(CAP_HIT, "error")
            return redirect(url_for("register"))
        flash("We emailed you a 6-digit code. Enter it below.", "ok")
        return redirect(url_for("verify", email=email))

    return render_template("register.html", domain=ALLOWED_DOMAIN)


# ---- verify ----
@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = request.args.get("email", "") or request.form.get("email", "")
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if rate_limited("verifycode", email, limit=10, per_seconds=900):
            flash("Too many attempts. Please wait a few minutes.", "error")
            return redirect(url_for("verify", email=email))
        user = query("SELECT * FROM users WHERE email=?", (email,),
                     fetch=True, one=True)
        if code_is_valid(user, code):
            query("UPDATE users SET verified=1, verify_code=NULL, "
                  "code_expires=NULL WHERE email=?", (email,))
            clear_limit("verifycode", email)
            flash("Verified! You can log in now.", "ok")
            return redirect(url_for("login"))
        if user and user["verify_code"] and code == user["verify_code"]:
            flash("That code has expired. Send yourself a new one.", "error")
        else:
            flash("That code didn't match. Try again or resend.", "error")
        return redirect(url_for("verify", email=email))
    return render_template("verify.html", email=email)


# POST, not GET. As a GET this was one URL that emailed a code every time it
# was loaded -- a browser refresh, a link preview or a crawler could each fire
# it, with no click required.
@app.route("/verify/resend", methods=["POST"])
def verify_resend():
    email = request.form.get("email", "").strip().lower()
    user = query("SELECT * FROM users WHERE email=?", (email,),
                 fetch=True, one=True)
    if user and not user["verified"]:
        if rate_limited("sendcode", email, limit=5, per_seconds=3600):
            flash(TOO_MANY_SENDS, "error")
            return redirect(url_for("verify", email=email))
        code = new_code()
        query("UPDATE users SET verify_code=?, code_expires=? WHERE email=?",
              (code, code_deadline(), email))
        sent, _ = send_verification_code(email, code)
        flash("New code sent." if sent else CAP_HIT, "ok" if sent else "error")
    return redirect(url_for("verify", email=email))


# ---- login / logout ----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if rate_limited("login", email, limit=8, per_seconds=900):
            flash("Too many login attempts. Please wait a few minutes and "
                  "try again.", "error")
            return redirect(url_for("login"))
        user = query("SELECT * FROM users WHERE email=?", (email,),
                     fetch=True, one=True)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Wrong email or password.", "error")
            return redirect(url_for("login"))
        if not user["verified"]:
            flash("Please verify your email first.", "error")
            return redirect(url_for("verify", email=email))
        clear_limit("login", email)
        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("app_view"))
    return render_template("login.html", domain=ALLOWED_DOMAIN)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "ok")
    return redirect(url_for("login"))


# ---- forgot password (one-time code, NOT a plaintext password) ----
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        # Counts requests, not accounts, so this still doesn't reveal whether
        # the address is registered.
        if rate_limited("sendcode", email, limit=5, per_seconds=3600):
            flash(TOO_MANY_SENDS, "error")
            return redirect(url_for("forgot"))
        user = query("SELECT * FROM users WHERE email=?", (email,),
                     fetch=True, one=True)
        if user:
            code = new_code()
            query("UPDATE users SET verify_code=?, code_expires=? WHERE email=?",
                  (code, code_deadline(), email))
            send_verification_code(email, code)
        # Always say the same thing (don't reveal whether the email exists).
        flash("If that account exists, a reset code was emailed.", "ok")
        return redirect(url_for("reset", email=email))
    return render_template("forgot.html")


@app.route("/reset", methods=["GET", "POST"])
def reset():
    email = request.args.get("email", "") or request.form.get("email", "")
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        new_pw = request.form.get("password", "")
        if rate_limited("resetcode", email, limit=10, per_seconds=900):
            flash("Too many attempts. Please wait a few minutes.", "error")
            return redirect(url_for("reset", email=email))
        user = query("SELECT * FROM users WHERE email=?", (email,),
                     fetch=True, one=True)
        if not code_is_valid(user, code):
            flash("That reset code is wrong or has expired. Request a new one.",
                  "error")
            return redirect(url_for("reset", email=email))
        if len(new_pw) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("reset", email=email))
        query("UPDATE users SET password_hash=?, verify_code=NULL, "
              "code_expires=NULL, verified=1 WHERE email=?",
              (generate_password_hash(new_pw), email))
        clear_limit("resetcode", email)
        clear_limit("login", email)
        flash("Password updated. Please log in.", "ok")
        return redirect(url_for("login"))
    return render_template("reset.html", email=email)


# ---- edit my schedule ----
@app.route("/edit", methods=["GET", "POST"])
@login_required
def edit():
    user = current_user()
    if request.method == "POST":
        text = request.form.get("schedule_text", "")
        problem = schedule_text_problem(text)
        if problem:
            flash(problem, "error")
            return redirect(url_for("edit"))
        # grade from the form line if present (Junior -> 11 etc.)
        grade = _grade_from_text(text)
        # is_public is deliberately NOT written here any more: with the lookup
        # feature removed there is nothing for it to switch on, and a form that
        # silently posts "off" would rewrite a column nothing reads.
        set_schedule(user, text, grade)
        flash("Schedule saved.", "ok")
        return redirect(url_for("edit"))
    return render_template("edit.html", user=user, semesters=load_semesters(),
                           caps={"events": MAX_EVENTS, "hw": MAX_HW,
                                 "sports": MAX_SPORTS, "projects": MAX_PROJECTS,
                                 "kb": MAX_SCHEDULE_BYTES // 1024})


# Uploaded schedules are small; anything bigger is not a Veracross export.
MAX_PDF_BYTES = 4 * 1024 * 1024

# schedule_text has no length limit of its own, so a determined student could
# paste thousands of lines and bloat the database. These caps are enforced HERE,
# on the server: the form counts too, but a form count stops nobody who posts
# straight to the endpoint.
# Raised from 20 once people started actually living in the thing: a student
# with a job, two lessons, a club and a standing meeting runs out at 20 and the
# app starts refusing to hold their week. 50 of them is ~3 KB.
MAX_EVENTS = 50
# A project milestone is an @hw line -- that is the whole point, it has to land
# on a real class period. Six projects with five milestones each is 30, so the
# old limit of 20 would have made the feature unusable on its first day.
MAX_HW = 60
MAX_PROJECTS = 12
MAX_SPORTS = 8          # three seasons, plus room for a second team or two
# These three numbers are coupled to the disk figures on the admin page: a
# bigger per-student ceiling means fewer snapshots fit under BACKUP_MAX_MB.
# 60 milestones at ~80 bytes plus classes, events and sports is about 10 KB, so
# 32 KB leaves room without letting one account become a database.
MAX_SCHEDULE_BYTES = 32 * 1024


def schedule_text_problem(text):
    """None when the text is acceptable, otherwise the sentence to show."""
    if len(text.encode("utf-8")) > MAX_SCHEDULE_BYTES:
        return (f"That schedule is {len(text.encode('utf-8')) // 1024} KB. The "
                f"limit is {MAX_SCHEDULE_BYTES // 1024} KB. Nothing was saved.")
    events = hw = sports = projects = 0
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("@event"):
            events += 1
        elif s.startswith("@hw"):
            hw += 1
        elif s.startswith("@sport"):
            sports += 1
        elif s.startswith("@project"):
            projects += 1
    if events > MAX_EVENTS:
        return (f"You have {events} events; the limit is {MAX_EVENTS}. "
                f"Remove a few and save again. Nothing was saved.")
    if hw > MAX_HW:
        return (f"You have {hw} homework items; the limit is {MAX_HW}. "
                f"Remove a few and save again. Nothing was saved.")
    if sports > MAX_SPORTS:
        return (f"You have {sports} sports; the limit is {MAX_SPORTS}. "
                f"Remove a few and save again. Nothing was saved.")
    if projects > MAX_PROJECTS:
        return (f"You have {projects} projects; the limit is {MAX_PROJECTS}. "
                f"Finish or remove a few and save again. Nothing was saved.")
    return None


def current_school_year():
    """"2026-2027" for any date from August 2026 to July 2027."""
    now = datetime.now()
    start = now.year if now.month >= 8 else now.year - 1
    return f"{start}-{start + 1}"


def school_year_for(class_year, pdf_grade):
    """Which school year a schedule PDF is FROM.

    The PDF carries no year anywhere -- only "SS Semester 1/2" and a grade like
    "11th". But the student's class year is fixed (27doej graduates in 2027), so
    the grade pins the year down: a 27 in 11th grade was in 2025-2026. Without
    this, importing an old schedule files it under the current year, which is
    how last year's Spring turned into this year's Spring.

    Returns "YYYY-YYYY", or None when either input is missing."""
    if not class_year or not pdf_grade:
        return None
    try:
        grad = 2000 + int(class_year)          # class of 27 -> graduates 2027
        grade = int(pdf_grade)
    except (TypeError, ValueError):
        return None
    if not (6 <= grade <= 12):
        return None
    # Graduating year is the END of 12th grade, so 12th grade starts grad-1.
    start = grad - 1 - (12 - grade)
    return f"{start}-{start + 1}"


def pick_semester(pdf_semester, school_year=None):
    """Match "SS Semester 1/2" to one of schedule.txt's @semester ranges.

    The ranges are bare MM.DD, so they cannot tell 2025-2026 apart from
    2026-2027 on their own -- in September BOTH Fall ranges look current. So
    match on the school year first and only fall back to the date ranges.

    school_year, when given, is the year the PDF is actually from (see
    school_year_for); otherwise the current one is assumed."""
    sems = load_semesters()
    if not sems:
        return None
    want = "Fall" if pdf_semester == 1 else "Spring"
    same = [s for s in sems if s["term"] == want]
    if not same:
        return None

    this_year = school_year or current_school_year()
    for s in same:
        if str(s["year"]) == this_year:
            return s

    # No entry for this school year: fall back to a range containing today,
    # then to whichever year is latest.
    today = datetime.now().strftime("%m.%d")
    for s in same:
        wrap = s["from"] > s["to"]
        inside = (today >= s["from"] or today <= s["to"]) if wrap else \
                 (s["from"] <= today <= s["to"])
        if inside:
            return s
    return sorted(same, key=lambda s: str(s["year"]))[-1]


@app.route("/edit/import-pdf", methods=["POST"])
@login_required
def edit_import_pdf():
    """Read a Veracross schedule PDF and hand back schedule text.

    Nothing is saved here. The reply fills the editor so the student can look
    at it and press Save themselves, exactly like the .txt import."""
    u = current_user()
    if rate_limited("pdfimport", u["email"], limit=20, per_seconds=3600):
        return jsonify({"error": "Too many PDF imports in the last hour. "
                                 "Please wait a bit."}), 429

    f = request.files.get("pdf")
    if not f or not f.filename:
        return jsonify({"error": "No file was uploaded."}), 400
    blob = f.read(MAX_PDF_BYTES + 1)
    if len(blob) > MAX_PDF_BYTES:
        return jsonify({"error": "That file is larger than 4 MB. A Veracross "
                                 "schedule is only a few hundred KB."}), 400
    if not blob.startswith(b"%PDF"):
        return jsonify({"error": "That doesn't look like a PDF."}), 400

    try:
        import pdf_import
    except Exception as e:
        print(f"[pdf] cannot import pdf_import: {e}", flush=True)
        return jsonify({"error": f"PDF import isn't set up on the server "
                                 f"({e})."}), 500
    problem = pdf_import.backend_problem()
    if problem:
        print(f"[pdf] backend unavailable: {problem}", flush=True)
        return jsonify({"error": problem}), 503
    try:
        parsed = pdf_import.parse_pdf(io.BytesIO(blob))
    except pdf_import.ScheduleParseError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # Name the failure. "Could not read that PDF" on its own sent a working
        # file back once when the real problem was on the server, not the file.
        print(f"[pdf] parse failed: {type(e).__name__}: {e}", flush=True)
        return jsonify({"error": f"Could not read that PDF "
                                 f"({type(e).__name__}: {e}). Try again, or "
                                 f"ask an admin to take a look."}), 400

    # Work out which YEAR this PDF is from before choosing the semester, or an
    # old schedule lands on top of the current one.
    pdf_year = school_year_for(u["class_year"], parsed.get("grade"))
    sem = pick_semester(parsed.get("semester") or 1, pdf_year)
    if not sem:
        return jsonify({"error": "No semester dates are set up for this year "
                                 "yet. Ask the admin."}), 400
    start_year = int(str(sem["year"]).split("-")[0])
    label = f"{sem['term']} {start_year if sem['term'] == 'Fall' else start_year + 1}"
    text = pdf_import.to_schedule_text(
        parsed, label, sem["from"], sem["to"], start_year,
        time_format=(u["schedule_text"] or "").split("time_format = ")[-1].strip()
        if "time_format = " in (u["schedule_text"] or "") else "12h")

    # Keep the terms the student already had. Importing Spring must not wipe
    # Fall; re-importing the same term replaces just that block.
    existing = u["schedule_text"] or ""
    merged = merge_term(existing, text, label)
    kept = [lbl for lbl, _ in split_terms(existing)[1] if lbl != label]
    text = merged

    # Two lists, deliberately. Everything used to come back as one pile headed
    # "Worth checking", including the things the importer had already handled
    # correctly -- so a student whose science class has a lab double read
    # "period F appears twice" as a bug and spent an evening checking a
    # schedule that was right. Notes say what was done FOR you; warnings are
    # the ones that actually need a human to look.
    warnings, notes = [], []
    if kept:
        notes.append("Kept your existing term"
                     + ("s " if len(kept) > 1 else " ")
                     + ", ".join(kept) + ".")
    for c in parsed["courses"]:
        if c.get("partial"):
            days = ", ".join(str(d) for d in c["days"])
            notes.append(
                f"{c['name']} meets in period {c['period']} on rotation days "
                f"{days} only -- a lab double. Normal, and already written in "
                f"as \"days {','.join(str(d) for d in c['days'])}\".")
    for letter, variants in (parsed.get("conflicts") or {}).items():
        warnings.append(f"Period {letter} appeared with "
                        f"{len(variants)} different courses. Check that row.")
    # Colours come from the department fill on the PDF cell. An unknown fill is
    # deliberately NOT forced onto the nearest known colour, so say which ones
    # got a placeholder instead of quietly showing the wrong department colour.
    unknown = sorted({c["name"] for c in parsed["courses"] if not c.get("subject")})
    if unknown:
        warnings.append(
            "Couldn't match the department colour for "
            + ", ".join(unknown)
            + ". A placeholder colour was used; change it in the form if you like.")
    if not parsed.get("name"):
        warnings.append("Couldn't read your name off the PDF, so fill it in.")
    if not parsed.get("grade"):
        warnings.append("Couldn't read your grade off the PDF, so fill it in.")

    return jsonify({"text": text, "term": label,
                    "count": len(parsed["courses"]),
                    "warnings": warnings, "notes": notes})


# ---- .ics import ----------------------------------------------------------
# One-time copy, not a subscription. The app never reaches out to fetch a
# calendar later: PythonAnywhere's free tier can only talk to whitelisted
# hosts, so a subscription URL would work for some students' calendars and
# silently fail for others -- and a calendar that silently stops updating is
# worse than one that never claimed to.
@app.route("/edit/import-ics", methods=["POST"])
@login_required
def edit_import_ics():
    u = current_user()
    if rate_limited("icsimport", u["email"], limit=20, per_seconds=3600):
        flash("Too many calendar imports in the last hour. Please wait a bit.",
              "error")
        return redirect(url_for("edit"))
    try:
        import ics_import
    except Exception as e:                       # pragma: no cover
        flash(f"Calendar import isn't set up on the server ({e}).", "error")
        return redirect(url_for("edit"))

    f = request.files.get("ics")
    if not f or not f.filename:
        flash("No file was chosen.", "error")
        return redirect(url_for("edit"))
    blob = f.read(ics_import.MAX_ICS_BYTES + 1)
    if len(blob) > ics_import.MAX_ICS_BYTES:
        flash("That file is larger than 1 MB, which is far bigger than any "
              "calendar. Nothing was read.", "error")
        return redirect(url_for("edit"))
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older exporters still emit Latin-1. Decoding it wrongly is better
        # than refusing the file: the student sees the mojibake in the preview
        # and knows to re-export.
        text = blob.decode("latin-1", "replace")
    if "BEGIN:VCALENDAR" not in text.upper():
        flash("That doesn't look like a calendar file. A .ics starts with "
              "BEGIN:VCALENDAR.", "error")
        return redirect(url_for("edit"))

    color = (request.form.get("color") or "gray").strip()
    items, skipped, calname = ics_import.convert(text, color)
    if not items and not skipped:
        flash("That file has no events in it.", "error")
        return redirect(url_for("edit"))

    existing = set(l.strip() for l in (u["schedule_text"] or "").split("\n"))
    today = datetime.now().strftime("%Y-%m-%d")
    horizon = (datetime.now() + timedelta(days=400)).strftime("%Y-%m-%d")
    back = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    for it in items:
        it["already"] = it["line"].strip() in existing
        # Ticked by default only if it is near enough to matter. A calendar
        # exported from a phone can carry ten years of history, and ticking
        # all of it by default is how somebody blows their event cap in one
        # click on things that happened in 2019.
        it["default"] = (not it["already"]) and back <= it["date"] <= horizon
    n_ev = len(_count_lines(u["schedule_text"] or "", "@event"))
    n_pr = len(_count_lines(u["schedule_text"] or "", "@project"))
    return render_template(
        "edit_import_ics.html", items=items, skipped=skipped,
        calname=calname, filename=f.filename, today=today,
        have={"events": n_ev, "projects": n_pr},
        caps={"events": MAX_EVENTS, "projects": MAX_PROJECTS})


@app.route("/edit/import-ics/confirm", methods=["POST"])
@login_required
def edit_import_ics_confirm():
    """Write the ticked lines.

    Every line is re-checked from scratch against the grammar. The preview put
    them in hidden fields, so what comes back is whatever the browser felt
    like sending -- it is not evidence of anything."""
    u = current_user()
    try:
        import ics_import
    except Exception as e:                       # pragma: no cover
        flash(f"Calendar import isn't set up on the server ({e}).", "error")
        return redirect(url_for("edit"))

    wanted, bad = [], 0
    for raw in request.form.getlist("line"):
        ok = ics_import.validated_line(raw)
        if ok:
            wanted.append(ok)
        else:
            bad += 1
    if not wanted:
        flash("Nothing was ticked, so nothing was imported." if not bad
              else f"None of those {bad} lines were in a form this app can "
                   f"store, so nothing was imported.", "error")
        return redirect(url_for("edit"))

    text = u["schedule_text"] or ""
    have = set(l.strip() for l in text.split("\n"))
    fresh, dupes = [], 0
    for line in wanted:
        if line in have:
            dupes += 1
            continue
        have.add(line)
        fresh.append(line)
    if not fresh:
        flash(f"All {dupes} of those are already in your schedule. Nothing "
              f"changed.", "ok")
        return redirect(url_for("edit"))

    # Caps first, and refuse the whole batch rather than importing part of it.
    # A half-import looks exactly like a working import until the missing
    # deadline goes past.
    add_ev = sum(1 for l in fresh if l.startswith("@event"))
    add_pr = sum(1 for l in fresh if l.startswith("@project"))
    n_ev = len(_count_lines(text, "@event")) + add_ev
    n_pr = len(_count_lines(text, "@project")) + add_pr
    if n_ev > MAX_EVENTS:
        flash(f"That would give you {n_ev} events and the limit is "
              f"{MAX_EVENTS}. Tick {n_ev - MAX_EVENTS} fewer, or delete some "
              f"old ones first. Nothing was imported.", "error")
        return redirect(url_for("edit"))
    if n_pr > MAX_PROJECTS:
        flash(f"That would give you {n_pr} projects and the limit is "
              f"{MAX_PROJECTS}. Nothing was imported.", "error")
        return redirect(url_for("edit"))

    lines = text.replace("\r\n", "\n").split("\n")
    # sit them with their own kind, or before the @pref trailer
    for line in fresh:
        prefix = line.split(" ", 1)[0]
        last = max((i for i, l in enumerate(lines)
                    if l.strip().startswith(prefix)), default=None)
        if last is None:
            last = max((i for i, l in enumerate(lines)
                        if l.strip().startswith("@pref")), default=None)
            if last is None:
                lines.append(line)
            else:
                lines[last:last] = [line, ""]
        else:
            lines.insert(last + 1, line)
    merged = "\n".join(lines)
    problem = schedule_text_problem(merged)
    if problem:
        flash(problem, "error")
        return redirect(url_for("edit"))
    set_schedule(u, merged)
    msg = f"Imported {len(fresh)} into your schedule."
    if dupes:
        msg += f" {dupes} were already there."
    if bad:
        msg += f" {bad} could not be stored and were left out."
    msg += " They are a copy -- the original calendar will not update them."
    flash(msg, "ok")
    return redirect(url_for("edit"))


def _count_lines(text, prefix):
    return [l for l in (text or "").replace("\r\n", "\n").split("\n")
            if l.strip().startswith(prefix)]


@app.route("/api/quick-add", methods=["POST"])
@login_required
def api_quick_add():
    """Add ONE @event or @hw line from the schedule view.

    Double-clicking the grid is how people expect to add something to a
    calendar, so this exists to avoid a round trip through the whole edit form
    for a single line. The 20-item cap is enforced here as well as in /edit --
    this is a second door into the same field. When the cap is already reached
    the request comes back with the oldest line so the page can offer to drop
    it, rather than sending the student off to hunt for it."""
    u = current_user()
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    line = (data.get("line") or "").strip()
    if kind not in ("event", "hw") or not line:
        return jsonify({"error": "Nothing to add."}), 400
    if "\n" in line or "\r" in line:
        return jsonify({"error": "One line at a time."}), 400

    prefix, cap = ("@event", MAX_EVENTS) if kind == "event" else ("@hw", MAX_HW)
    text = u["schedule_text"] or ""
    existing = _count_lines(text, prefix)

    if len(existing) >= cap and not data.get("replace_oldest"):
        return jsonify({"full": True, "cap": cap,
                        "oldest": existing[0].strip()[len(prefix):].strip(),
                        "kind": kind}), 409

    lines = text.replace("\r\n", "\n").split("\n")
    if len(existing) >= cap:                    # confirmed: drop the first one
        oldest = existing[0]
        lines.remove(oldest)

    new_line = f"{prefix} {line}"
    # Put it with its own kind if any are there, else before the @pref trailer.
    last = max((i for i, l in enumerate(lines)
                if l.strip().startswith(prefix)), default=None)
    if last is None:
        last = max((i for i, l in enumerate(lines)
                    if l.strip().startswith("@pref")), default=None)
        if last is None:
            lines.append(new_line)
        else:
            lines[last:last] = [new_line, ""]
    else:
        lines.insert(last + 1, new_line)

    merged = "\n".join(lines)
    problem = schedule_text_problem(merged)
    if problem:
        return jsonify({"error": problem}), 400
    set_schedule(u, merged, remember=False)
    return jsonify({"ok": True, "added": new_line,
                    "count": len(_count_lines(merged, prefix)), "cap": cap})


@app.route("/api/quick-delete", methods=["POST"])
@login_required
def api_quick_delete():
    """Remove one @event or @hw line, given exactly the line to remove.

    The other half of the double-click shortcut: adding something by clicking
    the grid but then having to go into the edit form to take it away again is
    the sort of asymmetry that makes people stop using the shortcut."""
    u = current_user()
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    line = (data.get("line") or "").strip()
    if kind not in ("event", "hw") or not line:
        return jsonify({"error": "Nothing to remove."}), 400
    prefix = "@event" if kind == "event" else "@hw"
    want = line if line.startswith(prefix) else f"{prefix} {line}"

    lines = (u["schedule_text"] or "").replace("\r\n", "\n").split("\n")
    for i, l in enumerate(lines):
        if l.strip() == want.strip():
            lines.pop(i)
            break
    else:
        return jsonify({"error": "Couldn't find that one; it may already be gone."}), 404

    merged = "\n".join(lines)
    set_schedule(u, merged, remember=False)
    return jsonify({"ok": True, "removed": want})


@app.route("/api/quick-done", methods=["POST"])
@login_required
def api_quick_done():
    """Tick one @hw line off, or untick it.

    The whole point of a milestone is that you mark it done the moment you hand
    it in, and making somebody open the edit form to do that means it never
    gets marked at all -- which is worse than no progress bar, because a stale
    one lies. Done-ness lives in the line itself (a trailing "done" field), so
    there is no second place for it to disagree with."""
    u = current_user()
    data = request.get_json(silent=True) or {}
    line = (data.get("line") or "").strip()
    want_done = bool(data.get("done", True))
    if not line:
        return jsonify({"error": "Nothing to mark."}), 400
    want = line if line.startswith("@hw") else f"@hw {line}"

    lines = (u["schedule_text"] or "").replace("\r\n", "\n").split("\n")
    for i, l in enumerate(lines):
        if l.strip() == want.strip():
            parts = [x.strip() for x in l.strip().split(" - ")]
            parts = [x for x in parts if x.lower() != "done"]
            if want_done:
                parts.append("done")
            lines[i] = " - ".join(parts)
            break
    else:
        return jsonify({"error": "Couldn't find that one; reload and try again."}), 404

    merged = "\n".join(lines)
    problem = schedule_text_problem(merged)
    if problem:
        return jsonify({"error": problem}), 400
    set_schedule(u, merged, remember=False)
    return jsonify({"ok": True, "line": lines[i]})


@app.route("/api/quick-edit", methods=["POST"])
@login_required
def api_quick_edit():
    """Replace one @event or @hw line with an edited version.

    Double-clicking used to delete outright, behind a confirm(). Creating with
    a double-click and destroying with the same gesture is a trap: the two
    things you most want to do to something you just made are adjust it and
    remove it, and only one of them was reachable without opening the form.
    So the gesture now opens an editor, and deleting is a button inside it."""
    u = current_user()
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    old_line = (data.get("line") or "").strip()
    new_line = (data.get("newline") or "").strip()
    if kind not in ("event", "hw") or not old_line or not new_line:
        return jsonify({"error": "Nothing to change."}), 400
    prefix = "@event" if kind == "event" else "@hw"
    want = old_line if old_line.startswith(prefix) else f"{prefix} {old_line}"
    repl = new_line if new_line.startswith(prefix) else f"{prefix} {new_line}"

    lines = (u["schedule_text"] or "").replace("\r\n", "\n").split("\n")
    for i, l in enumerate(lines):
        if l.strip() == want.strip():
            lines[i] = repl
            break
    else:
        return jsonify({"error": "Couldn't find that one; reload and try again."}), 404

    merged = "\n".join(lines)
    problem = schedule_text_problem(merged)
    if problem:
        return jsonify({"error": problem}), 400
    set_schedule(u, merged, remember=False)
    return jsonify({"ok": True, "line": repl})


@app.route("/api/my-history")
@login_required
def api_my_history():
    """The student's own earlier versions. Their own, and nobody else's.

    Restoring is deliberately NOT a route: the reply carries the text, the
    edit page loads it into the form, and nothing is written until the student
    presses Save. That is the same rule the PDF and .txt imports follow, and
    it means a mis-click while browsing history cannot cost anything."""
    u = current_user()
    rows = query("SELECT id, saved_at, text FROM schedule_history "
                 "WHERE user_id=? ORDER BY id DESC", (u["id"],), fetch=True) or []
    now_text = u["schedule_text"] or ""
    out = []
    for r in rows:
        t = r["text"] or ""
        out.append({
            "id": r["id"],
            "saved_at": r["saved_at"],
            "bytes": len(t.encode("utf-8")),
            "events": len(_count_lines(t, "@event")),
            "hw": len(_count_lines(t, "@hw")),
            "sports": len(_count_lines(t, "@sport")),
            "projects": len(_count_lines(t, "@project")),
            # how far this version is from what is there now, in whole lines
            "changed": len(set(t.split("\n")) ^ set(now_text.split("\n"))),
            "text": t,
        })
    return jsonify({"versions": out, "keep": HISTORY_KEEP})


@app.route("/edit/export")
@login_required
def edit_export():
    """Download my own schedule as a plain .txt (same format the app parses)."""
    u = current_user()
    stem = re.sub(r"[^0-9A-Za-z_-]", "", (u["username"] or "")) or "schedule"
    stamp = datetime.now().strftime("%Y-%m-%d")
    body = u["schedule_text"] or ""
    return body, 200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition":
            f'attachment; filename="{stem}_schedule_{stamp}.txt"',
    }


def load_semesters():
    """Read @semester lines from schedule.txt -> list of {year,term,from,to}."""
    out = []
    try:
        with open(SHARED_SCHEDULE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("@semester"):
                    continue
                body = line[len("@semester"):].strip()
                parts = [p.strip() for p in body.split("|")]
                if len(parts) == 3:
                    rng = parts[2].split("-")
                    if len(rng) == 2:
                        out.append({"year": parts[0], "term": parts[1],
                                    "from": rng[0], "to": rng[1]})
    except FileNotFoundError:
        pass
    return out


DEFAULT_TEMPLATE = """Your Name
27 - Junior

@term Fall 2025 = 08.26-01.16
A - Class - NUM - 1 - Teacher - Room - blue
B - Class - NUM - 1 - Teacher - Room - green

@pref time_format = 12h
"""


def _grade_from_text(text):
    head = "\n".join(text.splitlines()[:3]).lower()
    if "fresh" in head: return 9
    if "soph" in head: return 10
    if "jun" in head: return 11
    if "sen" in head: return 12
    m = re.search(r"grade\s*(\d+)", head)
    return int(m.group(1)) if m else None


# ===========================================================================
# API used by the front-end
# ===========================================================================
# ---- local mode -----------------------------------------------------------
# Nothing here reads or writes the database. These routes exist only to serve
# the same front end with a flag telling it to keep everything in the browser.
@app.route("/local")
def local_intro():
    """What it is and what it costs, before anybody relies on it."""
    return render_template("local_intro.html", local=True)


@app.route("/local/app")
def local_app():
    return send_from_directory("static", "app.html")


@app.route("/local/edit")
def local_edit():
    """The same editor, with no account behind it.

    user is a blank stand-in: the template reads schedule_text out of it, and
    in local mode the page replaces that with whatever is in localStorage
    before anybody sees it."""
    return render_template("edit.html", user={"schedule_text": ""},
                           local=True, semesters=load_semesters(),
                           caps={"events": MAX_EVENTS, "hw": MAX_HW,
                                 "sports": MAX_SPORTS, "projects": MAX_PROJECTS,
                                 "kb": MAX_SCHEDULE_BYTES // 1024})


@app.route("/api/caps")
def api_caps():
    """One source of truth for the limits, so local mode cannot drift from the
    server's idea of them."""
    return jsonify({"events": MAX_EVENTS, "hw": MAX_HW, "sports": MAX_SPORTS,
                    "projects": MAX_PROJECTS, "bytes": MAX_SCHEDULE_BYTES})


# A ceiling on the whole server, not per visitor. The per-visitor limit below
# is the fair-use rule; this is what stops the day's CPU seconds going to one
# determined person with a lot of addresses.
PUBLIC_PARSE_PER_DAY = int(os.environ.get("PUBLIC_PARSE_PER_DAY", "400"))


def public_parse_allowed():
    """(ok, message). Applies to the no-account parse endpoints only."""
    if rate_limited("publicparse", client_ip(), limit=12, per_seconds=3600):
        return False, ("That is a lot of imports from one place in an hour. "
                       "Try again later, or type the schedule in by hand.")
    if rate_limited("publicparse_all", "all", PUBLIC_PARSE_PER_DAY, 86400):
        print(f"[rate] public parse daily cap hit ({PUBLIC_PARSE_PER_DAY})",
              flush=True)
        return False, ("The site has done all the imports it can for today. "
                       "Nothing is wrong with your file -- try tomorrow, or "
                       "type it in by hand.")
    return True, ""


@app.route("/api/parse-pdf", methods=["POST"])
def api_parse_pdf():
    """Veracross PDF in, schedule text out. Nothing is stored.

    Local mode has no account, so this cannot be behind @login_required. It is
    the same parser /edit/import-pdf uses; the difference is only that the
    result goes back to the browser instead of into a database row."""
    ok, msg = public_parse_allowed()
    if not ok:
        return jsonify({"error": msg}), 429
    f = request.files.get("pdf")
    if not f or not f.filename:
        return jsonify({"error": "No file was uploaded."}), 400
    blob = f.read(MAX_PDF_BYTES + 1)
    if len(blob) > MAX_PDF_BYTES:
        return jsonify({"error": "That file is larger than 4 MB."}), 400
    if not blob.startswith(b"%PDF"):
        return jsonify({"error": "That doesn't look like a PDF."}), 400
    try:
        import pdf_import
    except Exception as e:                       # pragma: no cover
        return jsonify({"error": f"PDF import isn't set up ({e})."}), 500
    problem = pdf_import.backend_problem()
    if problem:
        return jsonify({"error": problem}), 503
    try:
        parsed = pdf_import.parse_pdf(io.BytesIO(blob))
    except pdf_import.ScheduleParseError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[pdf] public parse failed: {type(e).__name__}: {e}", flush=True)
        return jsonify({"error": f"Could not read that PDF "
                                 f"({type(e).__name__})."}), 400
    sem = pick_semester(parsed.get("semester") or 1)
    if not sem:
        return jsonify({"error": "No semester dates are set up for this year "
                                 "yet."}), 400
    start_year = int(str(sem["year"]).split("-")[0])
    label = f"{sem['term']} {start_year if sem['term'] == 'Fall' else start_year + 1}"
    text = pdf_import.to_schedule_text(parsed, label, sem["from"], sem["to"],
                                       start_year)
    return jsonify({"text": text, "term": label,
                    "count": len(parsed["courses"]),
                    "name": parsed.get("name") or "",
                    "grade": parsed.get("grade") or ""})


@app.route("/api/parse-ics", methods=["POST"])
def api_parse_ics():
    """.ics in, proposed @event / @project lines out. Nothing is stored.

    The logged-in flow parks the decrypted upload in a temp file so the
    preview page can diff it; this one hands the proposals straight back and
    the browser holds them, so there is nothing on disk to leak or sweep."""
    ok, msg = public_parse_allowed()
    if not ok:
        return jsonify({"error": msg}), 429
    try:
        import ics_import
    except Exception as e:                       # pragma: no cover
        return jsonify({"error": f"Calendar import isn't set up ({e})."}), 500
    f = request.files.get("ics")
    if not f or not f.filename:
        return jsonify({"error": "No file was chosen."}), 400
    blob = f.read(ics_import.MAX_ICS_BYTES + 1)
    if len(blob) > ics_import.MAX_ICS_BYTES:
        return jsonify({"error": "That file is larger than 1 MB."}), 400
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("latin-1", "replace")
    if "BEGIN:VCALENDAR" not in text.upper():
        return jsonify({"error": "That doesn't look like a calendar file."}), 400
    color = (request.form.get("color") or "gray").strip()
    items, skipped, calname = ics_import.convert(text, color)
    return jsonify({"items": items, "skipped": skipped, "calname": calname})


@app.route("/sample")
def sample():
    """A read-only demo, no account needed.

    Somebody who lands on a login wall with nothing to look at just leaves. The
    front-end already has a "static" mode for being opened as a plain file --
    any path that isn't /app makes it fall back to the schedule baked into
    app.html -- so serving the same file here gives a working week grid with a
    made-up student in it, and no way to touch real data."""
    return send_from_directory("static", "app.html")


@app.route("/manifest.webmanifest")
def manifest():
    """Served from the site root so its scope covers the whole app."""
    return send_from_directory("static", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.route("/manifest-local.webmanifest")
def manifest_local():
    """The same app, installed to open at /local/app instead of /app.

    This matters more than it looks. Installing from the shared manifest would
    give a home screen icon whose start_url is /app, which asks for a login
    that a local-mode user does not have -- so the one action that keeps their
    data alive would appear to break the app. Built from the same file so the
    icons and colours cannot drift apart."""
    import json as _json
    path = os.path.join(app.static_folder, "manifest.webmanifest")
    with open(path, encoding="utf-8") as fh:
        man = _json.load(fh)
    man["start_url"] = "/local/app"
    man["scope"] = "/local/"
    man["id"] = "/local/app"          # or it installs over the cloud app's entry
    man["name"] = "SSA Smart Schedule (local)"
    return app.response_class(_json.dumps(man, indent=2),
                              mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    """Also root-scoped: a worker served from /static could only control
    /static, which would not make the site installable."""
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/api/me")
@login_required
def api_me():
    u = current_user()
    return jsonify({"email": u["email"], "name": u["name"],
                    "username": u["username"], "class_year": u["class_year"],
                    "is_admin": bool(session.get("is_admin"))})


@app.route("/api/my-schedule")
@login_required
def api_my_schedule():
    """Returns the logged-in user's schedule text (the .txt format)."""
    u = current_user()
    return (u["schedule_text"] or ""), 200, {"Content-Type": "text/plain; charset=utf-8"}


# Looking up another student's schedule used to live here: /api/student-index
# listed everyone with is_public=1, and /api/student/<id> served their file.
# The feature is gone -- the search box on /app was removed, and leaving the
# endpoints up would have meant the data was still one URL away. is_public
# stays in the table so nobody's old choice is silently reinterpreted, but
# nothing reads it now.


@app.route("/api/schedule-shared")
def api_schedule_shared():
    """School-wide schedule.txt (rotation, community, finals, wednesday)."""
    try:
        with open(SHARED_SCHEDULE_FILE, encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except FileNotFoundError:
        return "", 404


@app.route("/api/calendar/<name>")
def api_calendar(name):
    """Serve a calendar file by name, e.g. 2025-2026."""
    safe = re.sub(r"[^0-9\-]", "", name)
    path = os.path.join(CALENDAR_DIR, safe + ".txt")
    if not os.path.exists(path):
        return "", 404
    with open(path, encoding="utf-8") as f:
        body = apply_calendar_edits(safe, f.read())
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


# ===========================================================================
# ADMIN (optional): edit the shared schedule.txt
# ===========================================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        flash("Admin is disabled (no ADMIN_PASSWORD set).", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        # Per visitor, not per proxy -- see client_ip(). Keyed on remote_addr
        # this was one bucket for the whole school, so anybody could lock the
        # real admin out by guessing wrong eight times.
        who = client_ip()
        if rate_limited("adminlogin", who, limit=8, per_seconds=900):
            flash("Too many attempts. Please wait a few minutes.", "error")
            return redirect(url_for("admin_login"))
        # compare_digest rather than ==: constant time, one line, and it costs
        # nothing to not have to think about it again.
        given = request.form.get("password") or ""
        if hmac.compare_digest(given, ADMIN_PASSWORD):
            clear_limit("adminlogin", who)
            session["is_admin"] = True
            session["admin_at"] = time.time()
            ip, fwd, agent = _client_ip()
            seen = query("SELECT 1 FROM admin_log WHERE event='login_ok' "
                         "AND ip=? AND agent=? LIMIT 1", (ip, agent),
                         fetch=True, one=True)
            admin_note("login_ok")
            if not seen:
                # First time from this address and browser. Erring toward too
                # many alerts: a mail you ignore is cheap, a login you never
                # heard about is not.
                admin_alert(
                    "SSA Smart Schedule: admin sign-in from a new place",
                    f"Somebody signed in to /admin.\n\n"
                    f"When: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                    f"IP (as the app sees it): {ip}\n"
                    f"X-Forwarded-For: {fwd or '(none)'}\n"
                    f"Browser: {agent}\n\n"
                    f"If this was you, nothing to do. If it was not, change "
                    f"ADMIN_PASSWORD in the WSGI file and reload the web app.")
            return redirect(url_for("admin_home"))
        admin_note("login_fail")
        flash("Wrong admin password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/reauth", methods=["GET", "POST"])
@admin_required
def admin_reauth():
    """Type the password again before something dangerous."""
    nxt = session.get("reauth_next") or url_for("admin_home")
    if request.method == "POST":
        who = client_ip()
        if rate_limited("adminlogin", who, limit=8, per_seconds=900):
            flash("Too many attempts. Please wait a few minutes.", "error")
            return redirect(url_for("admin_home"))
        if hmac.compare_digest(request.form.get("password") or "", ADMIN_PASSWORD):
            session["admin_at"] = time.time()
            admin_note("reauth_ok")
            session.pop("reauth_next", None)
            return redirect(nxt)
        admin_note("reauth_fail")
        flash("Wrong admin password.", "error")
    return render_template("admin_reauth.html", nxt=nxt)


@app.route("/admin/log")
@admin_required
def admin_log_page():
    """The whole log, not just the last few on the dashboard."""
    return render_template("admin_log.html", rows=admin_recent(ADMIN_LOG_KEEP),
                           keep=ADMIN_LOG_KEEP)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Admin logged out.", "ok")
    return redirect(url_for("login"))


@app.route("/admin")
@admin_required
def admin_home():
    """Dashboard: list every account, optionally filtered by ?q= search."""
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        users = query(
            "SELECT * FROM users WHERE name LIKE ? OR email LIKE ? "
            "OR username LIKE ? OR class_year LIKE ? "
            "ORDER BY class_year, username",
            (like, like, like, like), fetch=True) or []
    else:
        users = query("SELECT * FROM users ORDER BY class_year, username",
                      fetch=True) or []
    all_users = query("SELECT verified, schedule_text FROM users", fetch=True) or []
    total = len(all_users)
    verified = sum(1 for u in all_users if u["verified"])
    with_sched = sum(1 for u in all_users if (u["schedule_text"] or "").strip())
    backup_if_stale()
    last_backup, backup_age = newest_backup()
    return render_template("admin_home.html", users=users, total=total,
                           last_backup=os.path.basename(last_backup) if last_backup else None,
                           backup_age=round(backup_age) if backup_age is not None else None,
                           admin_log=admin_recent(12),
                           admin_fresh=admin_is_fresh(),
                           backup_key_set=bool(BACKUP_KEY),
                           backup_dir=BACKUP_DIR,
                           verified=verified, with_sched=with_sched, q=q,
                           sends_today=codes_sent_today(),
                           max_codes=MAX_CODES_PER_DAY,
                           email_on=mailer.is_enabled(),
                           mail_problem=mailer.alignment_problem(),
                           unverified=[u for u in users if not u["verified"]],
                           pdf_problem=_pdf_backend_problem(),
                           today=datetime.now().strftime("%Y-%m-%d"),
                           anchors=merged_anchors(
                               datetime.now().strftime("%Y-%m-%d")),
                           storage=storage_report(),
                           publics=read_publics(),
                           comm_ons=merged_comm_ons(
                               datetime.now().strftime("%Y-%m-%d")))


# ---------------------------------------------------------------------------
# CYCLE-DAY ANCHORS
#
# The 8-day rotation cannot be computed from the calendar alone: a snow day or
# any unplanned closure shifts it, and nothing tells the app that happened. So
# the calendar files carry anchors -- "this date was cycle day N" -- and every
# other school day is counted from the nearest one.
#
# Rather than make the admin hand-edit a calendar file after a snow day, this
# writes the anchor. Adding one dated after the disruption re-syncs everything
# downstream automatically.
# ---------------------------------------------------------------------------
ANCHOR_RE = re.compile(r"^@anchor\s+(\d{4}-\d{2}-\d{2})\s*=\s*([1-8])\s*$")


def calendar_year_for(date_str):
    """Which school year owns this date, as "2026-2027". August starts one."""
    y, m, _d = (int(x) for x in date_str.split("-"))
    start = y if m >= 8 else y - 1
    return f"{start}-{start + 1}"


def calendar_path_for(date_str):
    """Which school-year file owns this date."""
    return os.path.join(CALENDAR_DIR, calendar_year_for(date_str) + ".txt")


def cal_edit_line(kind, date_str, value):
    """One stored edit, back in calendar-file syntax."""
    if kind == "anchor":
        return f"@anchor {date_str} = {value}"
    return f"@community_on {date_str} = {value}".rstrip()


def cal_edits(year, kind=None):
    if kind:
        return query("SELECT * FROM calendar_edits WHERE year=? AND kind=? "
                     "ORDER BY date", (year, kind), fetch=True) or []
    return query("SELECT * FROM calendar_edits WHERE year=? ORDER BY kind, date",
                 (year,), fetch=True) or []


def set_cal_edit(year, kind, date_str, value):
    query("INSERT INTO calendar_edits (year, kind, date, value, updated) "
          "VALUES (?, ?, ?, ?, datetime('now','localtime')) "
          "ON CONFLICT(year, kind, date) DO UPDATE SET "
          "value=excluded.value, updated=excluded.updated",
          (year, kind, date_str, str(value)))


def apply_calendar_edits(year, text):
    """Lay the stored edits over a calendar file's text.

    Same date, same kind -> the stored one replaces the file's line, so the
    admin always wins over whatever was pushed. Anything new is appended; the
    parsers are line-based and sort anchors themselves, so position is free."""
    rows = cal_edits(year)
    if not rows:
        return text
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for r in rows:
        rx = ANCHOR_RE if r["kind"] == "anchor" else COMM_ON_RE
        line = cal_edit_line(r["kind"], r["date"], r["value"])
        for i, l in enumerate(lines):
            m = rx.match(l.strip())
            if m and m.group(1) == r["date"]:
                lines[i] = line
                break
        else:
            lines.append(line)
    return "\n".join(lines)


def read_anchors(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = ANCHOR_RE.match(line.strip())
                if m:
                    out.append((m.group(1), int(m.group(2))))
    except FileNotFoundError:
        pass
    return sorted(out)


def write_anchor(date_str, day):
    """Add or replace the anchor for one date. Returns (ok, note).

    Stored in the database, not in the calendar file -- see calendar_edits in
    ensure_schema() for why."""
    path = calendar_path_for(date_str)
    if not os.path.exists(path):
        return False, f"No calendar file for that date ({os.path.basename(path)})."
    set_cal_edit(calendar_year_for(date_str), "anchor", date_str, int(day))
    return True, cal_edit_line("anchor", date_str, day)


def merged_anchors(date_str):
    """What the app will actually see for that date's school year."""
    d = dict(read_anchors(calendar_path_for(date_str)))
    for r in cal_edits(calendar_year_for(date_str), "anchor"):
        d[r["date"]] = int(r["value"])
    return sorted(d.items())


# ---------------------------------------------------------------------------
# MERGING AN IMPORTED TERM INTO AN EXISTING SCHEDULE
#
# The format has always allowed several @term blocks (that is how last term's
# classes stay visible), but importing used to hand back a document containing
# only the term just parsed, so every import quietly dropped the others. This
# splits the existing text and puts the new term in its place instead.
# ---------------------------------------------------------------------------
def split_terms(text):
    """(header_lines, [(label, block_lines)], trailer_lines).

    Header is everything before the first @term -- name, grade, and any shared
    lines. Trailer is the @pref block at the end, which belongs to the document
    rather than to the last term."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    head, terms, cur = [], [], None
    for line in lines:
        if line.strip().startswith("@term"):
            m = re.match(r"^@term\s+(.*?)\s*=", line.strip())
            cur = [(m.group(1) if m else line.strip()), [line]]
            terms.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur[1].append(line)

    # @pref lines at the very end are document-wide, not part of the last term.
    trailer = []
    if terms:
        body = terms[-1][1]
        while body and (not body[-1].strip()
                        or body[-1].strip().startswith("@pref")):
            trailer.insert(0, body.pop())
    else:
        while head and (not head[-1].strip()
                        or head[-1].strip().startswith("@pref")):
            trailer.insert(0, head.pop())
    return head, [(lbl, b) for lbl, b in terms], trailer


def merge_term(existing_text, new_text, label):
    """Put the freshly imported term into the student's existing schedule.

    Same label -> replace that block. New label -> append. Everything else
    (other terms with their @sport/@immersive lines) is kept byte for byte.
    The name/grade header and the @pref trailer come from what the student
    already had -- they may have corrected them by hand -- and only fall back
    to the imported document when there is nothing there yet."""
    old_head, old_terms, old_trailer = split_terms(existing_text)
    new_head, new_terms, new_trailer = split_terms(new_text)
    if not new_terms:
        return new_text

    head = old_head if any(l.strip() for l in old_head) else new_head
    trailer = old_trailer if any(l.strip() for l in old_trailer) else new_trailer
    incoming = new_terms[0][1]

    out_terms, replaced = [], False
    for lbl, block in old_terms:
        if lbl == label:
            out_terms.append(incoming)
            replaced = True
        else:
            out_terms.append(block)
    if not replaced:
        out_terms.append(incoming)

    parts = ["\n".join(head).rstrip("\n")]
    for block in out_terms:
        parts.append("\n".join(block).strip("\n"))
    tail = "\n".join(trailer).strip("\n")
    if tail:
        parts.append(tail)
    return "\n\n".join(p for p in parts if p) + "\n"


# ---------------------------------------------------------------------------
# VALIDATING THE SHARED CONFIG
#
# /admin/shared is a plain textarea over the one file that decides the
# timetable for every student. A single typo there is the most damaging thing
# anybody can do to this app, and until now it saved whatever you typed.
#
# Errors refuse the save. Warnings are things that look wrong but might be
# deliberate, so they save and say so -- a checker that blocks a legitimate
# edit is its own kind of failure.
# ---------------------------------------------------------------------------
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _mins(t):
    m = TIME_RE.match(t.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def _check_range(rng):
    """'08:15-09:05' -> None if fine, else why not."""
    parts = rng.split("-")
    if len(parts) != 2:
        return f"{rng!r} is not a HH:MM-HH:MM range"
    a, b = _mins(parts[0]), _mins(parts[1])
    if a is None or b is None:
        return f"{rng!r} has a time that isn't HH:MM"
    if b <= a:
        return f"{rng!r} ends before it starts"
    return None


def check_shared(text):
    """(errors, warnings) for the shared schedule.txt."""
    errors, warnings = [], []
    days = {}
    letters = {}

    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("@day"):
            m = re.match(r"^@day\s+(\d+)\s*=\s*(.*)$", line)
            if not m:
                errors.append(f"Line {n}: @day line is malformed.")
                continue
            day = int(m.group(1))
            if not 1 <= day <= 8:
                errors.append(f"Line {n}: @day {day} is outside 1-8.")
                continue
            if day in days:
                errors.append(f"Line {n}: @day {day} is defined twice.")
            slots = m.group(2).split()
            days[day] = len(slots)
            for tok in slots:
                if ":" not in tok:
                    errors.append(f"Line {n}: {tok!r} is not LETTER:HH:MM-HH:MM.")
                    continue
                letter, rest = tok.split(":", 1)
                if not re.match(r"^[A-H]$", letter):
                    errors.append(f"Line {n}: {letter!r} is not a period letter A-H.")
                    continue
                letters[letter] = letters.get(letter, 0) + 1
                # "11:50-12:40|12:25-13:15" = early|late lunch tracks
                for part in rest.split("|"):
                    bad = _check_range(part)
                    if bad:
                        errors.append(f"Line {n}: {bad}.")

        elif line.startswith("@semester"):
            if not re.match(r"^@semester\s+\d{4}-\d{4}\s*\|\s*(Fall|Spring)\s*\|\s*"
                            r"\d{2}\.\d{2}-\d{2}\.\d{2}\s*$", line):
                errors.append(f"Line {n}: @semester should look like "
                              f"'@semester 2026-2027 | Fall | 08.25-01.15'.")

        elif line.startswith("@community time") or line.startswith("@imm_community time"):
            m = re.match(r"^@\w+\s+time\s*=\s*(\S+)\s*$", line)
            bad = _check_range(m.group(1)) if m else "the time range is missing"
            if bad:
                errors.append(f"Line {n}: {bad}.")

        elif line.startswith("@public"):
            if not PUBLIC_RE.match(line):
                errors.append(f"Line {n}: @public should look like "
                              f"'@public 2026-10-17 - Homecoming - 12:00-16:00 - ALL - pink'.")

        elif line.startswith("@special"):
            parts = [x.strip() for x in line.replace("@special", "", 1).split("|")]
            if len(parts) < 3:
                errors.append(f"Line {n}: @special needs NAME | HH:MM-HH:MM | what [| grades].")
            else:
                bad = _check_range(parts[1])
                if bad:
                    errors.append(f"Line {n}: {bad}.")

        elif line.startswith("@imm_block"):
            m = re.match(r"^@imm_block\s+(.*?)\s+-\s+(\S+)\s*$", line)
            bad = _check_range(m.group(2)) if m else "the time range is missing"
            if bad:
                errors.append(f"Line {n}: {bad}.")

        elif line.startswith("@finals"):
            if not re.match(r"^@finals\s+\d{2}\.\d{2}\s*\|[^|]+\|.*-\s*\d{1,2}:\d{2}-\d{1,2}:\d{2}\s*$", line):
                errors.append(f"Line {n}: @finals should look like "
                              f"'@finals 12.17 | 11,12 | History Assessment - 08:30-10:00'.")

        elif line.startswith("@"):
            # Every parser in this app ignores what it does not recognise, so a
            # typo like "@publik" or "@dayy" does nothing at all and says
            # nothing. Warn rather than refuse: new directives get added.
            known = ("@day", "@semester", "@community", "@imm_community",
                     "@imm_block", "@immersive", "@public", "@special",
                     "@finals", "@wed", "@dismiss", "@latearly", "@sport",
                     "@pref", "@term", "@event", "@hw", "@anchor")
            word = re.split(r"[\s=|]", line, 1)[0]
            if word not in known:
                warnings.append(f"Line {n}: {word!r} isn't a directive this app "
                                f"knows, so it will be ignored.")

    missing = [d for d in range(1, 9) if d not in days]
    if missing:
        errors.append("The rotation is incomplete: no @day line for "
                      + ", ".join(str(d) for d in missing) + ".")

    if days and not missing:
        # Every letter gets the same number of slots across a full rotation.
        # This is the check that would have caught the real bug where H was
        # missing from days 2 and 4 and nothing complained.
        counts = {c for c in letters.values()}
        if len(counts) > 1:
            detail = ", ".join(f"{k}x{v}" for k, v in sorted(letters.items()))
            errors.append("Periods are not evenly spread across the rotation "
                          f"({detail}). Each letter should appear the same "
                          "number of times.")
        absent = [c for c in "ABCDEFGH" if c not in letters]
        if absent:
            warnings.append("No slot anywhere for period "
                            + ", ".join(absent) + ".")
        sizes = set(days.values())
        if len(sizes) > 1:
            warnings.append("The days do not all have the same number of "
                            "periods (" + ", ".join(
                                f"day {d}: {c}" for d, c in sorted(days.items())) + ").")

    # School-wide events never expire on their own. They are a few bytes each,
    # so this is about the file staying readable, not about disk: after two or
    # three years the admin page is a list of homecomings nobody remembers.
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    stale = sorted(m.group(1) for m in
                   (PUBLIC_RE.match(l.strip()) for l in
                    text.replace("\r\n", "\n").split("\n")) if m
                   and m.group(1) < cutoff)
    if stale:
        warnings.append(
            f"{len(stale)} school-wide event(s) are more than a year old "
            f"(oldest {stale[0]}). They still work; remove the ones you are "
            f"done with under 'School-wide events' on the admin page.")

    return errors, warnings


# ---------------------------------------------------------------------------
# SCHOOL-WIDE EVENTS
#
# @public lines live in schedule.txt (everyone reads that file), so adding one
# used to mean hand-editing the shared config through /admin/shared. That is a
# lot of ceremony, and one typo in that box breaks the timetable for the whole
# school, so this writes just the one line.
# ---------------------------------------------------------------------------
PUBLIC_RE = re.compile(
    r"^@public\s+(\d{4}-\d{2}-\d{2})\s*-\s*(.*?)\s*-\s*"
    r"(\d{1,2}:\d{2}-\d{1,2}:\d{2})\s*-\s*([^-]+?)\s*-\s*(\S+)\s*$")


def read_publics():
    out = []
    try:
        with open(SHARED_SCHEDULE_FILE, encoding="utf-8") as f:
            for line in f:
                m = PUBLIC_RE.match(line.strip())
                if m:
                    out.append({"date": m.group(1), "label": m.group(2),
                                "time": m.group(3), "grades": m.group(4),
                                "color": m.group(5)})
    except FileNotFoundError:
        pass
    return sorted(out, key=lambda p: (p["date"], p["time"]))


def write_public(line, remove=None):
    """Append one @public line, or remove the one matching `remove`."""
    try:
        with open(SHARED_SCHEDULE_FILE, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        return False, "schedule.txt is missing on the server."

    if remove is not None:
        # Only ever remove an @public line. Without this check a crafted or
        # stale hidden field could delete a @day rotation line instead, and
        # that would break the timetable for the whole school.
        if not remove.strip().startswith("@public "):
            return False, "That isn't a school-wide event."
        for i, l in enumerate(lines):
            if l.strip() == remove.strip():
                lines.pop(i)
                break
        else:
            return False, "That event is no longer there."
    else:
        if any(l.strip() == line.strip() for l in lines):
            return False, "That exact event is already listed."
        last = max((i for i, l in enumerate(lines)
                    if l.strip().startswith("@public")), default=None)
        if last is None:
            lines.append(line)
        else:
            lines.insert(last + 1, line)

    with open(SHARED_SCHEDULE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True, line


@app.route("/admin/public", methods=["POST"])
@admin_required
def admin_public():
    if request.form.get("remove"):
        ok, note = write_public(None, remove=request.form["remove"])
        flash("Event removed." if ok else note, "ok" if ok else "error")
        return redirect(url_for("admin_home"))

    date_str = (request.form.get("date") or "").strip()
    label = (request.form.get("label") or "").strip()
    start = (request.form.get("start") or "").strip()
    end = (request.form.get("end") or "").strip()
    grades = (request.form.get("grades") or "ALL").strip() or "ALL"
    color = (request.form.get("color") or "pink").strip()

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        flash("Give the date as YYYY-MM-DD.", "error")
        return redirect(url_for("admin_home"))
    if not label:
        flash("Give the event a name.", "error")
        return redirect(url_for("admin_home"))
    if "-" in label:
        flash("The name can't contain a hyphen; the format uses it as a "
              "separator. Try an en dash or a colon.", "error")
        return redirect(url_for("admin_home"))
    for t in (start, end):
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            flash("Times look like 17:00.", "error")
            return redirect(url_for("admin_home"))
    if grades.upper() != "ALL" and not re.match(r"^\d{1,2}(,\d{1,2})*$", grades):
        flash("Grades must be ALL or a list like 11,12.", "error")
        return redirect(url_for("admin_home"))

    line = f"@public {date_str} - {label} - {start}-{end} - {grades} - {color}"
    ok, note = write_public(line)
    flash(f"Added: {note}" if ok else note, "ok" if ok else "error")
    return redirect(url_for("admin_home"))


# ---------------------------------------------------------------------------
# COMMUNITY-TIME OVERRIDES
#
# The community slot is a static day-of-week map in schedule.txt ("Thu = Club
# Time"). Once a month that Thursday is a class/form meeting instead, on a date
# nobody can compute -- it's "first or second Thursday" but not reliably. So the
# calendar file carries per-date overrides, same shape as the anchors above.
# An empty label means "no community block that day".
# ---------------------------------------------------------------------------
COMM_ON_RE = re.compile(
    r"^@community_on\s+(\d{4}-\d{2}-\d{2})\s*=\s*(.*?)\s*$")


def read_comm_ons(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = COMM_ON_RE.match(line.strip())
                if m:
                    out.append((m.group(1), m.group(2)))
    except FileNotFoundError:
        pass
    return sorted(out)


def write_comm_on(date_str, label):
    """Add or replace the community override for one date. (ok, note).

    Database, not file -- same reason as write_anchor()."""
    path = calendar_path_for(date_str)
    if not os.path.exists(path):
        return False, f"No calendar file for that date ({os.path.basename(path)})."
    set_cal_edit(calendar_year_for(date_str), "community_on", date_str, label)
    return True, cal_edit_line("community_on", date_str, label)


def merged_comm_ons(date_str):
    d = dict(read_comm_ons(calendar_path_for(date_str)))
    for r in cal_edits(calendar_year_for(date_str), "community_on"):
        d[r["date"]] = r["value"]
    return sorted(d.items())


@app.route("/admin/community-on", methods=["POST"])
@admin_required
def admin_community_on():
    date_str = (request.form.get("date") or "").strip()
    label = (request.form.get("label") or "").strip()
    times = (request.form.get("times") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        flash("Give the date as YYYY-MM-DD.", "error")
        return redirect(url_for("admin_home"))
    if times:
        if not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", times):
            flash("Times look like 10:05-10:45, or leave blank for the usual "
                  "community slot.", "error")
            return redirect(url_for("admin_home"))
        label = f"{label} - {times}"
    ok, note = write_comm_on(date_str, label)
    if ok:
        flash(f"Saved: {note}." if label else
              f"Saved: {date_str} now has no community block.", "ok")
    else:
        flash(note, "error")
    return redirect(url_for("admin_home"))


# ---------------------------------------------------------------------------
# AUTOMATIC BACKUPS
#
# The admin page has said "there is no other copy of this database, so export
# it regularly" since day one, which is a polite way of saying the only backup
# is one somebody remembers to take. This writes the same JSON export to disk
# on its own.
#
# It runs opportunistically: whenever an admin page is opened and the newest
# snapshot is more than a day old, one is written. That needs no scheduler and
# works on the free PythonAnywhere plan. If you would rather have it on a
# timer, point a scheduled task at:
#     cd ~/schedule-backend && python3 -c "import app; print(app.write_backup('cron'))"
# ---------------------------------------------------------------------------
BACKUP_DIR = os.environ.get(
    "BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(DB)), "backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "30"))
# ...and never more than this much disk in total, whatever the count says.
BACKUP_MAX_MB = float(os.environ.get("BACKUP_MAX_MB", "40"))
BACKUP_EVERY_HOURS = int(os.environ.get("BACKUP_EVERY_HOURS", "24"))


# PythonAnywhere free accounts get 512 MB of disk in total, and this app is not
# the only thing on it, so "how much are we actually using" has to be a number
# on the admin page rather than an estimate in a document. Every snapshot holds
# every account, so the thing that grows is snapshots x students.
DISK_QUOTA_MB = float(os.environ.get("DISK_QUOTA_MB", "512"))


def storage_report():
    """Real measured numbers for the admin page. Never raises."""
    def size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    import glob
    r = {"quota_mb": DISK_QUOTA_MB, "keep": BACKUP_KEEP, "cap_mb": BACKUP_MAX_MB,
         "history_keep": HISTORY_KEEP}
    try:
        r["db_bytes"] = sum(size(DB + suf) for suf in ("", "-wal", "-shm"))
        files = glob.glob(os.path.join(BACKUP_DIR, "auto_*.json"))
        r["backup_count"] = len(files)
        r["backup_bytes"] = sum(size(f) for f in files)
        row = query("SELECT COUNT(*) AS n, "
                    "SUM(CASE WHEN schedule_text != '' THEN 1 ELSE 0 END) AS filled, "
                    # NULLIF, so the empty accounts do not drag it down: the
                    # projection below asks what 600 accounts WITH schedules
                    # would cost, and an average that counts the blank ones as
                    # zero answers a different question.
                    "AVG(NULLIF(LENGTH(schedule_text), 0)) AS avg_text, "
                    "MAX(LENGTH(schedule_text)) AS max_text FROM users",
                    fetch=True, one=True) or {}
        r["users"] = row["n"] or 0
        r["filled"] = row["filled"] or 0
        # Measured, not assumed. The 2.9 KB figure this was first sized against
        # came from the built-in sample; a real schedule with events and
        # homework on it can be several times that.
        r["avg_text"] = int(row["avg_text"] or 0)
        r["max_text"] = int(row["max_text"] or 0)
        hrow = query("SELECT COUNT(*) AS n, SUM(LENGTH(text)) AS b "
                     "FROM schedule_history", fetch=True, one=True) or {}
        r["hist_rows"] = hrow["n"] or 0
        r["hist_bytes"] = int(hrow["b"] or 0)
        # A student's own version ladder lives in the database, so it counts
        # against the same quota. Worst case is HISTORY_KEEP rungs each about
        # the size of the schedule itself.
        # A student's own version ladder lives in the database, so it counts
        # against the same quota -- but it is deliberately NOT in the backup
        # snapshots (build_backup takes BACKUP_COLUMNS only), because a
        # convenience ladder is not primary data and multiplying every
        # snapshot by thirteen to carry it would be the wrong trade.
        live_row = r["avg_text"] + 400          # email, name, hash, timestamps
        per_user = live_row + r["avg_text"] * HISTORY_KEEP
        r["per_user"] = per_user
        r["hist_per_user"] = r["avg_text"] * HISTORY_KEEP
        snap = int(live_row * 1.35)             # JSON field names + escaping
        r["projected"] = [
            {"users": n,
             "db_mb": round(n * per_user / 1e6, 2),
             "snap_mb": round(n * snap / 1e6, 2),
             "kept_mb": round(min(BACKUP_KEEP * n * snap / 1e6, BACKUP_MAX_MB), 1)}
            for n in (100, 300, 600)]
        r["used_mb"] = round((r["db_bytes"] + r["backup_bytes"]) / 1e6, 2)
        r["worst_mb"] = round(r["db_bytes"] / 1e6 + BACKUP_MAX_MB, 1)
        r["ok"] = r["worst_mb"] < DISK_QUOTA_MB * 0.5
    except Exception as e:
        r["error"] = str(e)
    return r


def newest_backup():
    """(path, age_in_hours) of the most recent snapshot, or (None, None)."""
    import glob
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "auto_*.json")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return None, None
    return files[0], (time.time() - os.path.getmtime(files[0])) / 3600.0


def write_backup(reason="auto"):
    """Write one snapshot. Returns the path, or an error string."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = os.path.join(BACKUP_DIR, f"auto_{stamp}_{reason}.json")
        body = json.dumps(build_backup(), ensure_ascii=False, indent=2)
        # Write beside the target and rename, so a crash mid-write cannot
        # leave a truncated file that looks like a good backup.
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        _prune(os.path.join(BACKUP_DIR, "auto_*.json"), keep=BACKUP_KEEP,
               max_bytes=int(BACKUP_MAX_MB * 1024 * 1024))
        print(f"[backup] wrote {path}", flush=True)
        return path
    except Exception as e:
        print(f"[backup] FAILED: {e}", flush=True)
        return f"Backup failed: {e}"


def backup_if_stale():
    """Called when an admin page is opened. Never raises, never blocks."""
    try:
        _, age = newest_backup()
        if age is None or age >= BACKUP_EVERY_HOURS:
            write_backup("auto")
    except Exception as e:
        print(f"[backup] stale check failed: {e}", flush=True)


@app.route("/admin/backup-now", methods=["POST"])
@admin_required
def admin_backup_now():
    got = write_backup("manual")
    flash(f"Snapshot written: {os.path.basename(got)}" if got.endswith(".json")
          else got, "ok" if got.endswith(".json") else "error")
    return redirect(url_for("admin_home"))


def _prune(pattern, keep, max_bytes=None):
    """Keep the newest `keep` files matching a glob; delete the rest.

    With max_bytes, also stop once the kept files add up to that much. A count
    alone is not a size limit: every snapshot holds every account, so 30 of
    them grows with the school. At ~2.9 KB of schedule text per student that is
    roughly 1.3 MB a snapshot for 300 students and 2.6 MB for 600, so 30 kept
    copies would be 39 MB and 77 MB. Newest first, so the cap drops the oldest.
    """
    import glob
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    kept, total = [], 0
    for f in files[:keep]:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        if max_bytes is not None and kept and total + size > max_bytes:
            break
        kept.append(f)
        total += size
    for old_file in [f for f in files if f not in kept]:
        try:
            os.remove(old_file)
        except OSError:
            pass


def _pdf_backend_problem():
    """None when the server can read schedule PDFs; a sentence when it can't."""
    try:
        import pdf_import
        return pdf_import.backend_problem()
    except Exception as e:
        return f"pdf_import.py failed to load ({e})."


@app.route("/admin/anchor", methods=["POST"])
@admin_required
def admin_anchor():
    date_str = (request.form.get("date") or "").strip()
    day = (request.form.get("day") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        flash("Give the date as YYYY-MM-DD.", "error")
        return redirect(url_for("admin_home"))
    if day not in list("12345678"):
        flash("Cycle day must be 1-8.", "error")
        return redirect(url_for("admin_home"))
    ok, note = write_anchor(date_str, int(day))
    flash(f"Anchor saved: {note}. Everything after it re-counts from here."
          if ok else note, "ok" if ok else "error")
    return redirect(url_for("admin_home"))


@app.route("/admin/reset-send-cap", methods=["POST"])
@admin_required
def admin_reset_send_cap():
    """Zero the daily email counter. For when the cap tripped on legitimate
    traffic (a rollout day) rather than on abuse -- Google's own 500/day
    ceiling is NOT reset by this, only ours."""
    clear_limit("global_send", "all")
    flash("Daily email counter reset to 0.", "ok")
    return redirect(url_for("admin_home"))


@app.route("/admin/user/<int:uid>")
@admin_required
def admin_user(uid):
    """One account, WITHOUT their schedule.

    This page used to print the whole schedule in a textarea. Nothing an
    admin actually does here needs to read it: verifying an account, resetting
    a password and deleting need the row, not its contents. So the page counts
    the lines and shows nothing else -- and a leaked admin password stops
    being a way to read twenty people's week."""
    u = query("SELECT * FROM users WHERE id=?", (uid,), fetch=True, one=True)
    if not u:
        flash("No such user.", "error")
        return redirect(url_for("admin_home"))
    # Whether a schedule EXISTS -- nothing more. Delete needs to know it is
    # about to destroy data; no admin action needs to know how much, or of
    # what kind, and a leaked password should not turn this page into a way
    # to learn even the shape of somebody's week.
    has = bool((u["schedule_text"] or "").strip())
    return render_template("admin_user.html", u=u, has_schedule=has)


@app.route("/admin/user/<int:uid>/verify", methods=["POST"])
@admin_required
def admin_user_verify(uid):
    """Mark an account verified WITHOUT the emailed code.

    Half the school never receives the code -- it is filtered somewhere between
    a consumer mail account and the school's gateway, which is not something
    this app can fix from the sending side alone. Rather than have the admin
    forward codes by hand out of Gmail, let him confirm the person directly.
    The point of the code is proving the address belongs to a real SSA student;
    an admin who knows the student satisfies that just as well."""
    u = query("SELECT * FROM users WHERE id=?", (uid,), fetch=True, one=True)
    if not u:
        flash("No such user.", "error")
        return redirect(url_for("admin_home"))
    query("UPDATE users SET verified=1, verify_code=NULL, code_expires=NULL "
          "WHERE id=?", (uid,))
    clear_limit("verify_attempt", u["email"])
    clear_limit("send_code", u["email"])
    flash(f"{u['email']} is verified and can log in now.", "ok")
    return redirect(request.referrer or url_for("admin_home"))


@app.route("/admin/verify-bulk", methods=["POST"])
@admin_required
def admin_verify_bulk():
    """Same thing for a whole list of addresses pasted in one go."""
    raw = request.form.get("emails", "")
    wanted = {e.strip().lower() for e in re.split(r"[\s,;]+", raw) if e.strip()}
    if not wanted:
        flash("Paste some email addresses first.", "error")
        return redirect(url_for("admin_home"))
    done, missing = [], []
    for e in sorted(wanted):
        u = query("SELECT id FROM users WHERE lower(email)=?", (e,),
                  fetch=True, one=True)
        if not u:
            missing.append(e)
            continue
        query("UPDATE users SET verified=1, verify_code=NULL, code_expires=NULL "
              "WHERE id=?", (u["id"],))
        clear_limit("verify_attempt", e)
        clear_limit("send_code", e)
        done.append(e)
    if done:
        flash(f"Verified {len(done)}: " + ", ".join(done), "ok")
    if missing:
        flash(f"No account yet for: {', '.join(missing)} They still have to "
              f"register first (the password is set at registration).", "error")
    return redirect(url_for("admin_home"))


RESET_CODE_TTL = 10 * 60


def _do_reset(u):
    """The actual reset. Only reached after the emailed code is right."""
    alphabet = "abcdefghijkmnopqrstuvwxyz23456789"   # readable out loud
    temp = "-".join("".join(secrets.choice(alphabet) for _ in range(4))
                    for _ in range(3))
    query("UPDATE users SET password_hash=?, verified=1, verify_code=NULL, "
          "code_expires=NULL WHERE id=?", (generate_password_hash(temp), u["id"]))
    clear_limit("login", u["email"])
    admin_note("reset_password", u["email"])
    # A second channel to the student, in case their mail does get through.
    admin_alert_to(u["email"],
        "SSA Smart Schedule: your password was reset",
        f"An admin reset your Smart Schedule password on "
        f"{datetime.now():%Y-%m-%d %H:%M}. If you asked for this, use the "
        f"temporary password they give you and change it after logging in. "
        f"If you did NOT ask for this, say so to whoever runs this site.")
    return temp


@app.route("/admin/user/<int:uid>/reset-password", methods=["POST"])
@admin_fresh_required
def admin_user_reset_password(uid):
    """Step 1: email a code to the admin mailbox before anything changes.

    Forgot-password mail goes through the same filters that swallow the
    verification codes, so a student who forgets their password and never
    receives the reset email has no way back in. This is the way back -- but
    it is also the one control that turns the admin password into a way to
    READ a schedule, so it is gated behind a code sent to ADMIN_EMAIL."""
    u = query("SELECT * FROM users WHERE id=?", (uid,), fetch=True, one=True)
    if not u:
        flash("No such user.", "error")
        return redirect(url_for("admin_home"))
    if not ADMIN_EMAIL:
        flash("Reset needs ADMIN_EMAIL set (that is where the code goes). "
              "Add it in the WSGI file and reload.", "error")
        return redirect(url_for("admin_user", uid=uid))
    who = client_ip()
    if rate_limited("resetcode", who, limit=6, per_seconds=900):
        flash("Too many reset attempts. Wait a few minutes.", "error")
        return redirect(url_for("admin_user", uid=uid))
    code = new_code()
    session["reset_gate"] = {"uid": uid, "code": code,
                             "exp": time.time() + RESET_CODE_TTL}
    admin_note("reset_code_sent", u["email"])
    ok, note = mailer.send(ADMIN_EMAIL,
        "SSA Smart Schedule: code to reset a password",
        f"Someone asked to reset the password for {u['email']} on Smart "
        f"Schedule.\n\nCode: {code}\n\nEnter it within ten minutes to "
        f"finish the reset.\n\nIf this was NOT you, do not enter the code. "
        f"Somebody may have your admin password -- change ADMIN_PASSWORD in "
        f"the WSGI file and reload the web app.")
    if not ok:
        flash(f"Could not email the code ({note}). Nothing was changed.",
              "error")
        return redirect(url_for("admin_user", uid=uid))
    return render_template("admin_reset_code.html", u=u)


@app.route("/admin/user/<int:uid>/reset-password/confirm", methods=["POST"])
@admin_fresh_required
def admin_user_reset_password_confirm(uid):
    """Step 2: the code from the email, then the reset happens."""
    u = query("SELECT * FROM users WHERE id=?", (uid,), fetch=True, one=True)
    if not u:
        flash("No such user.", "error")
        return redirect(url_for("admin_home"))
    g = session.get("reset_gate") or {}
    given = (request.form.get("code") or "").strip()
    if (g.get("uid") != uid or time.time() > g.get("exp", 0)
            or not hmac.compare_digest(given, g.get("code", "\x00"))):
        admin_note("reset_code_bad", u["email"])
        flash("Wrong or expired code. Start the reset again.", "error")
        return redirect(url_for("admin_user", uid=uid))
    session.pop("reset_gate", None)
    temp = _do_reset(u)
    flash(f"Temporary password for {u['email']}: {temp}  "
          f"Give it to them in person and tell them to change it after logging in.",
          "ok")
    return redirect(url_for("admin_user", uid=uid))


@app.route("/admin/user/<int:uid>/delete", methods=["POST"])
@admin_fresh_required
def admin_user_delete(uid):
    u = query("SELECT email FROM users WHERE id=?", (uid,), fetch=True, one=True)
    query("DELETE FROM users WHERE id=?", (uid,))
    query("DELETE FROM schedule_history WHERE user_id=?", (uid,))
    admin_note("delete_user", (u["email"] if u else str(uid)))
    flash("Account deleted.", "ok")
    return redirect(url_for("admin_home"))


@app.route("/admin/shared", methods=["GET", "POST"])
@admin_required
def admin_shared():
    if request.method == "POST":
        text = request.form.get("content", "")
        errors, warnings = check_shared(text)
        if errors:
            flash("Not saved. " + " ".join(errors), "error")
            return render_template("admin_shared.html", content=text,
                                   errors=errors, warnings=warnings)
        # Keep the version being replaced. This one file decides the timetable
        # for everybody, and "undo" otherwise means retyping it from memory.
        if os.path.exists(SHARED_SCHEDULE_FILE):
            try:
                stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                shutil.copy2(SHARED_SCHEDULE_FILE,
                             f"{SHARED_SCHEDULE_FILE}.{stamp}.bak")
                _prune(f"{SHARED_SCHEDULE_FILE}.*.bak", keep=10)
            except Exception as e:
                print(f"[shared] could not keep a .bak: {e}", flush=True)
        with open(SHARED_SCHEDULE_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        flash("Shared schedule saved." + (" " + " ".join(warnings) if warnings else ""),
              "ok")
        return redirect(url_for("admin_shared"))
    content = ""
    if os.path.exists(SHARED_SCHEDULE_FILE):
        with open(SHARED_SCHEDULE_FILE, encoding="utf-8") as f:
            content = f.read()
    return render_template("admin_shared.html", content=content)


# ===========================================================================
# BACKUP / RESTORE  (admin)
#
# The whole point: schedule.db holds real student data and there is no other
# copy of it. "Export" writes every account to one JSON file you can keep;
# "Import" reads that file back, but never blindly -- it shows you a diff and
# you tick exactly which accounts to write.
# ===========================================================================
BACKUP_FORMAT = "ssa-smart-schedule-backup"
BACKUP_VERSION = 1

# Columns that go into a backup.
# verify_code is deliberately EXCLUDED: it is a live one-time code, it is
# worthless in a backup, and putting it in a downloadable file is a needless
# leak. Everything else (including the password HASH, never a password) is
# kept so a restore gives students their accounts back exactly as they were.
BACKUP_COLUMNS = ["id", "email", "name", "username", "class_year", "grade",
                  "password_hash", "verified", "schedule_text", "is_public",
                  "created_at"]

IMPORT_TMP_DIR = os.path.join(tempfile.gettempdir(), "ssa_schedule_imports")


def _norm(v):
    """None and missing both read as empty, so they don't show up as a change."""
    return "" if v is None else v


# A Fernet key, generated once and pasted into the WSGI file next to
# SECRET_KEY. Empty = exporting is refused rather than quietly handing over
# every schedule in the clear.
BACKUP_KEY = os.environ.get("BACKUP_KEY", "")


def _fernet():
    """(fernet, problem). Never raises."""
    if not BACKUP_KEY:
        return None, ("No BACKUP_KEY is set, so the export would be plain "
                      "text. Generate one and put it in the WSGI file "
                      "(see DEPLOY.md), then reload the web app.")
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None, ("The cryptography package is not installed on the "
                      "server, so the export cannot be encrypted. Install it "
                      "(pip3.10 install --user cryptography) and reload.")
    try:
        return Fernet(BACKUP_KEY.encode()), None
    except Exception as e:
        return None, f"BACKUP_KEY is not a valid Fernet key ({e})."


def decrypt_backup_bytes(raw):
    """Accept an encrypted export, or an old plain-text one. -> (text, error).

    Old plain backups still restore: refusing them would mean the safety net
    stopped working on the day the encryption was added, which is the wrong
    trade for a feature whose entire job is to be there when something has
    already gone wrong."""
    head = raw.lstrip()[:1]
    if head in (b"{", b"["):
        return raw.decode("utf-8", "replace"), None
    f, problem = _fernet()
    if not f:
        return None, ("That file looks encrypted, and it cannot be opened "
                      "here. " + (problem or ""))
    try:
        return f.decrypt(raw.strip()).decode("utf-8"), None
    except Exception:
        return None, ("That file could not be decrypted. It was probably "
                      "made with a different BACKUP_KEY.")


def build_backup():
    rows = query(f"SELECT {', '.join(BACKUP_COLUMNS)} FROM users ORDER BY id",
                 fetch=True) or []
    users = [{c: r[c] for c in BACKUP_COLUMNS} for r in rows]
    edits = query("SELECT year, kind, date, value FROM calendar_edits "
                  "ORDER BY year, kind, date", fetch=True) or []
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "user_count": len(users),
        "users": users,
        # Anchors and community overrides made through /admin. They are not in
        # any file, so without this a snapshot would not actually hold them.
        "calendar_edits": [dict(r) for r in edits],
    }


def read_backup(raw):
    """Parse + validate an uploaded backup. Returns (data, error_or_None)."""
    try:
        data = json.loads(raw)
    except Exception as e:
        return None, f"That file isn't valid JSON ({e})."
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        return None, "That doesn't look like a Smart Schedule backup file."
    if data.get("version") != BACKUP_VERSION:
        return None, (f"Backup version {data.get('version')!r} isn't supported "
                      f"by this build (expected {BACKUP_VERSION}).")
    users = data.get("users")
    if not isinstance(users, list):
        return None, "That backup has no 'users' list."
    for u in users:
        if not isinstance(u, dict) or not (u.get("email") or "").strip():
            return None, "That backup contains a record with no email."
        if not u.get("password_hash"):
            return None, (f"The record for {u.get('email')} has no password "
                          f"hash, so restoring it would lock that account out.")
    return data, None


def diff_backup(users):
    """Compare each backup record against the live DB.

    status is one of:
      new       -- this email isn't in the database at all
      conflict  -- the email exists and at least one field differs
      same      -- the email exists and nothing differs (nothing to do)
    """
    live = {r["email"]: r
            for r in (query("SELECT * FROM users", fetch=True) or [])}
    out = []
    for u in users:
        email = (u.get("email") or "").strip().lower()
        cur = live.get(email)
        if cur is None:
            out.append({"email": email, "status": "new", "backup": u,
                        "live": None, "changes": []})
            continue
        changes = [c for c in BACKUP_COLUMNS
                   if c not in ("id", "created_at")
                   and _norm(u.get(c)) != _norm(cur[c])]
        out.append({"email": email,
                    "status": "conflict" if changes else "same",
                    "backup": u, "live": cur, "changes": changes})
    order = {"conflict": 0, "new": 1, "same": 2}
    out.sort(key=lambda r: (order[r["status"]], r["email"]))
    return out


# An abandoned preview must not leave a plaintext dump behind. Anything older
# than this is swept the next time somebody uploads one.
IMPORT_TMP_MAX_AGE = 30 * 60


def _sweep_import_tmp():
    """Delete stale half-finished restores. Never raises."""
    try:
        import glob as _glob
        cutoff = time.time() - IMPORT_TMP_MAX_AGE
        for f in _glob.glob(os.path.join(IMPORT_TMP_DIR, "*.json")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except OSError:
                pass
    except Exception as e:                       # pragma: no cover
        print(f"[import] sweep failed: {e}", flush=True)


def _import_path(token):
    safe = re.sub(r"[^0-9a-f]", "", token or "")
    if len(safe) != 32:
        return None
    return os.path.join(IMPORT_TMP_DIR, safe + ".json")


@app.route("/admin/export")
@admin_fresh_required
def admin_export():
    """Download every account as one ENCRYPTED file.

    This is the route that matters. Hiding schedule text from /admin/user
    while this one still handed over every schedule in one click would have
    been security theatre."""
    f, problem = _fernet()
    if not f:
        admin_note("export_refused", problem or "")
        flash(problem, "error")
        return redirect(url_for("admin_home"))
    body = json.dumps(build_backup(), ensure_ascii=False, indent=2)
    blob = f.encrypt(body.encode("utf-8"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    admin_note("export", f"{len(blob)} bytes")
    admin_alert("SSA Smart Schedule: a full backup was downloaded",
                f"Every account was exported from /admin at "
                f"{datetime.now():%Y-%m-%d %H:%M:%S}.\n\n"
                f"The file is encrypted with BACKUP_KEY, so it is unreadable "
                f"without that key.\n\n"
                f"If this was not you, change ADMIN_PASSWORD in the WSGI file "
                f"and reload the web app.")
    return blob, 200, {
        "Content-Type": "application/octet-stream",
        "Content-Disposition":
            f'attachment; filename="ssa-schedule-backup_{stamp}.json.enc"',
    }


@app.route("/admin/import", methods=["GET", "POST"])
@admin_fresh_required
def admin_import():
    """Step 1: upload a backup and see exactly what it would change."""
    if request.method == "POST":
        f = request.files.get("backup")
        if not f or not f.filename:
            flash("Choose a backup .json file first.", "error")
            return redirect(url_for("admin_import"))
        blob = f.read()
        raw, err = decrypt_backup_bytes(blob)
        if err:
            admin_note("import_unreadable", err[:200])
            flash(err, "error")
            return redirect(url_for("admin_import"))
        data, err = read_backup(raw)
        if err:
            flash(err, "error")
            return redirect(url_for("admin_import"))

        os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
        _sweep_import_tmp()
        token = secrets.token_hex(16)
        with io.open(_import_path(token), "w", encoding="utf-8") as fh:
            fh.write(raw)
        session["import_token"] = token

        rows = diff_backup(data["users"])
        return render_template(
            "admin_import_preview.html", rows=rows,
            exported_at=data.get("exported_at", "unknown"),
            filename=f.filename,
            n_new=sum(1 for r in rows if r["status"] == "new"),
            n_conflict=sum(1 for r in rows if r["status"] == "conflict"),
            n_same=sum(1 for r in rows if r["status"] == "same"))
    return render_template("admin_import.html")


@app.route("/admin/import/apply", methods=["POST"])
@admin_fresh_required
def admin_import_apply():
    """Step 2: write only the accounts that were ticked on the preview page."""
    admin_note("restore", f"{len(request.form.getlist('apply'))} ticked")
    path = _import_path(session.get("import_token", ""))
    if not path or not os.path.exists(path):
        flash("That import expired. Upload the backup file again.", "error")
        return redirect(url_for("admin_import"))
    with io.open(path, encoding="utf-8") as fh:
        data, err = read_backup(fh.read())
    if err:
        flash(err, "error")
        return redirect(url_for("admin_import"))

    chosen = set(request.form.getlist("apply"))
    rows = [r for r in diff_backup(data["users"])
            if r["email"] in chosen and r["status"] != "same"]
    if not rows:
        flash("Nothing was ticked, so nothing changed.", "error")
        return redirect(url_for("admin_import"))

    # Safety net: snapshot the CURRENT data next to the database before
    # touching anything, so a wrong import is always undoable.
    snap = os.path.join(os.path.dirname(os.path.abspath(DB)),
                        f"pre-import_{datetime.now():%Y-%m-%d_%H%M%S}.json")
    try:
        with io.open(snap, "w", encoding="utf-8") as fh:
            json.dump(build_backup(), fh, ensure_ascii=False, indent=2)
    except Exception as e:
        flash(f"Could not write the safety snapshot ({e}), so the import stopped.",
              "error")
        return redirect(url_for("admin_import"))

    created = updated = 0
    for r in rows:
        u = r["backup"]
        if r["status"] == "new":
            # Keep the original id when it's free, so /api/student/<id> links
            # that people already have keep working after a restore.
            uid = u.get("id")
            id_free = uid is not None and not query(
                "SELECT 1 FROM users WHERE id=?", (uid,), fetch=True, one=True)
            cols = [c for c in BACKUP_COLUMNS if c != "id" or id_free]
            query(f"INSERT INTO users ({', '.join(cols)}) "
                  f"VALUES ({', '.join('?' * len(cols))})",
                  tuple(u.get(c) for c in cols))
            created += 1
        else:
            cols = [c for c in BACKUP_COLUMNS if c not in ("id", "email")]
            query(f"UPDATE users SET {', '.join(c + '=?' for c in cols)} "
                  f"WHERE email=?",
                  tuple(u.get(c) for c in cols) + (r["email"],))
            updated += 1

    session.pop("import_token", None)
    try:
        os.remove(path)
    except OSError:
        pass
    flash(f"Imported: {created} account(s) created, {updated} overwritten. "
          f"Snapshot of the previous data: {snap}", "ok")
    return redirect(url_for("admin_home"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
