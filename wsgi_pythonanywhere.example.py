# ===========================================================================
#  PythonAnywhere WSGI configuration  —  COPY THIS INTO YOUR WEB-APP WSGI FILE
# ===========================================================================
# On PythonAnywhere, the "Web" tab points to a file like:
#     /var/www/YOURNAME_pythonanywhere_com_wsgi.py
# Open that file (edit link on the Web tab), DELETE everything in it, and paste
# this. Then change the CAPS values below and click the green "Reload" button.
# ===========================================================================

import os
import sys

# 1) Where your code lives. Replace YOURNAME with your PythonAnywhere username.
PROJECT_DIR = "/home/YOURNAME/schedule-backend"

# 2) WHERE THE DATABASE LIVES  <-- the important one.
#
#    This path MUST be OUTSIDE PROJECT_DIR. The database holds every student's
#    account and schedule, and there is no other copy of it. If it sits inside
#    the code folder, re-uploading code can wipe it.
#
#    FIRST TIME ONLY, before you reload: create the folder and move the
#    existing database across (Bash console on PythonAnywhere):
#
#        mkdir -p /home/YOURNAME/data
#        cp /home/YOURNAME/schedule-backend/schedule.db /home/YOURNAME/data/schedule.db
#
#    If you forget, the app still starts but logs a loud warning in the error
#    log saying an EMPTY database is about to be created. Check that log.
os.environ["DB_PATH"] = "/home/YOURNAME/data/schedule.db"

# 3) Your real secrets (they live here on the server, NOT in your code).
os.environ["SECRET_KEY"] = "PUT-A-LONG-RANDOM-STRING-HERE"
os.environ["ALLOWED_DOMAIN"] = "shadysideacademy.org"
os.environ["ADMIN_PASSWORD"] = "PUT-AN-ADMIN-PASSWORD-OR-LEAVE-BLANK"

# 3b) The key that encrypts the backup you DOWNLOAD from /admin. Without it
#     the export is refused rather than quietly handing over every student's
#     schedule in plain text.
#
#     Generate one ONCE and paste it here. Lose it and old downloaded backups
#     can never be opened again, so keep a copy somewhere that is not this
#     server (a password manager):
#
#         python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
#     If the import fails, install it first:  pip3 install --user cryptography
#
#     Note what this does and does not do. It protects a file that LEAVES the
#     server -- a download sitting in a Downloads folder, reachable by anybody
#     who gets the admin password. It does nothing about the automatic
#     snapshots on this machine, because the key is on this machine too;
#     encrypting those would buy nothing and would add a way to lose every
#     backup at once.
os.environ["BACKUP_KEY"] = "PUT-A-GENERATED-FERNET-KEY-HERE"

# Where the "somebody signed in to /admin from a new place" email goes.
# Defaults to the address the app sends FROM, so you only need this if you
# want the alerts somewhere else.
# os.environ["ADMIN_EMAIL"] = "you@example.com"

# Send the login cookie over HTTPS only. PythonAnywhere serves your site over
# HTTPS, so leave this on. (It is off by default so that testing on plain http
# on your laptop still works; with it on there, logging in would silently fail.)
os.environ["COOKIE_SECURE"] = "1"

# Where the automatic snapshots go. Defaults to a "backups" folder next to the
# database, which is what you want; uncomment only to move them.
# os.environ["BACKUP_DIR"] = "/home/YOURNAME/data/backups"

# 4) Abuse limits (optional — sensible defaults are built in).
#
#    MAX_CODES_PER_DAY is a GLOBAL ceiling on verification emails per 24h,
#    across every address. Google cuts a free Gmail account off at 500/day and
#    the block lasts 1-24 hours, during which nobody can register or reset a
#    password — so the app stops itself first, at a number you can reset from
#    the admin page. Raise it before a school-wide rollout.
# os.environ["MAX_CODES_PER_DAY"] = "150"     # default 150
# os.environ["CODE_TTL_SECONDS"]  = "900"     # code lifetime, default 15 min

# 5) Email (optional). If you leave these unset, the app runs in DEV MODE:
#    no verification is required and codes are printed to the server log.
#
#    OPTION A — Outlook/Hotmail (often easiest):
# os.environ["SMTP_HOST"] = "smtp-mail.outlook.com"
# os.environ["SMTP_PORT"] = "587"
# os.environ["SMTP_USER"] = "youraddress@outlook.com"
# os.environ["SMTP_PASS"] = "your-password-or-app-password"
#
#    OPTION B — Gmail (needs a Google App Password):
# os.environ["SMTP_HOST"] = "smtp.gmail.com"
# os.environ["SMTP_PORT"] = "587"
# os.environ["SMTP_USER"] = "youraddress@gmail.com"
# os.environ["SMTP_PASS"] = "your-16-char-app-password"
#
# os.environ["SMTP_FROM_NAME"] = "SSA Smart Schedule"   # optional sender name

# --- you normally don't need to touch anything below this line ---
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

from app import app as application   # PythonAnywhere looks for "application"
