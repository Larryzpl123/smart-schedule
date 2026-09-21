"""
Build the push/ folder — the ONLY thing that gets uploaded to the live server.

Why this exists
---------------
The live site holds things you cannot regenerate:
  - every student's account and schedule (the database)
  - the school-wide schedule.txt that the admin edits IN THE BROWSER
  - your real secrets, which live in the server's WSGI file

So the project is split in two:
  CODE  (safe to overwrite every time)  -> goes into push/
  DATA  (must never be overwritten)     -> never goes into push/

Run this before every deploy:

    python3 build_push.py

Then upload push.zip. Because the data files simply aren't in there, there is
nothing to accidentally overwrite.

The schedule.txt trap
---------------------
Unlike a normal config file, schedule.txt is EDITED ON THE SERVER through
/admin/shared. The server's copy is therefore newer than yours most of the
time, and pushing yours would silently undo the admin's edits. So it is
excluded by default.

If you really did change it locally (new @finals lines, new @semester dates)
and want that to win:

    python3 build_push.py --with-shared

...but download the live one from /admin/shared first and merge, or you will
throw away whatever was changed on the server.

The calendar/ trap (fixed 2026-09-06)
-------------------------------------
calendar/*.txt is shipped as code and overwritten on every push. That used to
destroy anchors: /admin/anchor wrote its @anchor line into the server's copy of
the file, so a deploy replaced it with the laptop's older one. It happened --
the anchor re-syncing the rotation after two rain days vanished mid-week.

Admin-made anchors and community-time overrides are now rows in the database
(table calendar_edits) and are laid over the file when /api/calendar serves it.
The file is safe to push again. If you want an anchor to live in the file as
well, add the @anchor line locally by hand; same date in both, database wins.
"""

import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PUSH = os.path.join(HERE, "push")

WITH_SHARED = "--with-shared" in sys.argv

# --- CODE: replaced on every push -----------------------------------------
FILES = [
    "app.py",
    "pdf_import.py",           # Veracross schedule PDF -> schedule text
    "ics_import.py",           # somebody else's .ics -> @event / @project lines
    "decrypt_backup.py",       # opens an encrypted /admin export, off-server
    "mailer.py",
    "setup_db.py",
    "requirements.txt",
]
FOLDERS = [
    "templates",              # all pages
    "calendar",              # school-year calendars (you edit these locally)
                              # Safe to overwrite: anchors and community-time
                              # overrides added through /admin live in the
                              # database now, not in these files. Before that
                              # they were in the files, and a push wiped them.
]
SINGLE = [
    ("static/app.html", "static/app.html"),   # the front-end, one file
    # PWA bits: without these the "add to home screen" install is silently
    # unavailable on the server even though it works locally.
    ("static/manifest.webmanifest", "static/manifest.webmanifest"),
    ("static/sw.js", "static/sw.js"),
    ("static/icons/icon-192.png", "static/icons/icon-192.png"),
    ("static/icons/icon-512.png", "static/icons/icon-512.png"),
    ("static/icons/icon-maskable-512.png", "static/icons/icon-maskable-512.png"),
    ("static/icons/apple-touch-icon.png", "static/icons/apple-touch-icon.png"),
]

# --- DATA: never copied. Listed so the rule is visible, not hidden. --------
NEVER_PUSH = [
    "schedule.db",             # the database (every account + schedule)
    "pre-import_*.json",       # restore snapshots
    "wsgi_pythonanywhere.py",  # lives on the server, holds your real secrets
    "samples/",                # sample PDFs, development only
    "*.md",                    # docs — you read these on your laptop
]
if not WITH_SHARED:
    NEVER_PUSH.insert(1, "schedule.txt  (admin edits this ON THE SERVER)")
else:
    SINGLE.append(("schedule.txt", "schedule.txt"))


def _templates_app_needs():
    """Every template name app.py passes to render_template(...).

    Catches the classic deploy failure: you add a page, forget to list it,
    and the live site 500s on exactly the route you just built."""
    try:
        with open(os.path.join(HERE, "app.py"), encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return set()
    return set(re.findall(r'render_template\(\s*["\']([^"\']+)["\']', src))


def build():
    if os.path.exists(PUSH):
        try:
            shutil.rmtree(PUSH)
        except OSError:
            print("(note: couldn't fully clear push/ — overwriting in place)")
    os.makedirs(PUSH, exist_ok=True)

    copied = []
    missing = []

    for name in FILES:
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(PUSH, name))
            copied.append(name)
        else:
            missing.append(name)

    for folder in FOLDERS:
        src = os.path.join(HERE, folder)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(PUSH, folder), dirs_exist_ok=True)
            n = sum(len(fs) for _, _, fs in os.walk(src))
            copied.append(f"{folder}/  ({n} files)")
        else:
            missing.append(folder + "/")

    for src_rel, dst_rel in SINGLE:
        src = os.path.join(HERE, src_rel)
        if os.path.exists(src):
            dst = os.path.join(PUSH, dst_rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst_rel)
        else:
            missing.append(src_rel)

    # ---- safety check: refuse to ship data ------------------------------
    problems = []
    for root, dirs, files in os.walk(PUSH):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), PUSH)
            if (f.endswith(".db")
                    or f.startswith("pre-import_")
                    or f == "wsgi_pythonanywhere.py"
                    or (f.endswith("_key.txt") and not f.endswith(".example"))
                    or (f == "schedule.txt" and not WITH_SHARED)):
                problems.append(rel)
    if problems:
        raise SystemExit(
            f"\nREFUSING TO BUILD — data files got into push/: {problems}\n"
            f"Fix build_push.py before uploading anything.")

    # ---- completeness check: will the live site actually run? ------------
    have = {os.path.relpath(os.path.join(r, f), os.path.join(PUSH, "templates"))
            for r, _, fs in os.walk(os.path.join(PUSH, "templates")) for f in fs}
    absent = sorted(t for t in _templates_app_needs() if t not in have)

    # ---- zip -------------------------------------------------------------
    zip_path = os.path.join(HERE, "push.zip")
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
        except OSError:
            pass
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(PUSH):
            for f in sorted(files):
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, HERE))

    size_kb = os.path.getsize(zip_path) / 1024
    print("Built push/ with:")
    for c in copied:
        print("   ", c)
    print("\nNOT included (your live data is safe):")
    for n in NEVER_PUSH:
        print("   ", n)

    if missing:
        print("\n!! expected but not found on your laptop:")
        for m in missing:
            print("   ", m)
    if absent:
        print("\n!! app.py renders these templates but they are NOT in push/:")
        for a in absent:
            print("   ", a)
        print("   The live site will crash on those pages. Fix before uploading.")
    if WITH_SHARED:
        print("\n!! --with-shared: schedule.txt IS included and WILL overwrite")
        print("   the server's copy, including anything the admin changed there.")

    print(f"\nWrote push.zip ({size_kb:.0f} KB) — upload that one file.")
    print("Then in a PythonAnywhere Bash console:")
    print("    cd ~")
    print("    unzip -o push.zip -d push_tmp")
    print("    cp -r push_tmp/push/* schedule-backend/")
    print("    rm -rf push_tmp push.zip")
    print("Then Web tab -> green Reload.")
    print("\nReminder: wsgi_pythonanywhere.py is NOT pushed. If you changed it")
    print("(e.g. added DB_PATH), edit the server's WSGI file by hand on the Web tab.")


if __name__ == "__main__":
    build()
