"""
Sends the verification code to a student's email.

Two modes, chosen automatically:
  - REAL email: if SMTP settings are provided as environment variables, we send
    an actual email.
  - DEV mode: if they're not set, we just PRINT the code to the server console.
    That lets you test the whole sign-up flow on your laptop without email set up.
    (You'll see the code in the terminal running the app.)

To turn on real email, set these environment variables (e.g. a Gmail account
with an "app password", or your school's SMTP):
  SMTP_HOST   e.g. smtp.gmail.com
  SMTP_PORT   e.g. 587
  SMTP_USER   the login / from-address
  SMTP_PASS   the app password
"""

import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def _smtp_configured():
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def is_enabled():
    """True only when real email is configured. When False, the app skips
    email verification entirely so you can test sign-up without email set up."""
    return _smtp_configured()


def alignment_problem():
    """None when the From address matches the account we authenticate as.

    This is the single most common reason a code lands in Junk or vanishes:
    SPF and DKIM authorise the SENDING account's domain, so a From: header on a
    different domain fails alignment and the receiving school filters it. Set
    SMTP_FROM to the SMTP_USER address, or leave SMTP_FROM unset."""
    if not _smtp_configured():
        return None
    user = (os.environ.get("SMTP_USER") or "").strip().lower()
    frm = (os.environ.get("SMTP_FROM") or user).strip().lower()
    if frm and user and frm != user:
        return (f"Mail is sent as {frm} but authenticated as {user}. Those "
                f"domains don't match, so SPF/DKIM won't line up and the school "
                f"will very likely junk or quarantine the code. Either unset "
                f"SMTP_FROM, or set it to {user}.")
    return None


def send_code(to_email, code):
    """Returns (ok: bool, note: str). Never raises."""
    subject = "Your SSA Smart Schedule verification code"
    body = (f"Welcome to SSA Smart Schedule!\n\n"
            f"Your verification code is: {code}\n\n"
            f"Enter it on the verification page to activate your account.\n"
            f"If you didn't sign up, you can ignore this email.")
    return send(to_email, subject, body, dev_label=f"Verification code for {to_email}: {code}")


def send(to_email, subject, body, dev_label=None):
    """The one place that actually talks to SMTP. Returns (ok, note).

    Pulled out of send_code when admin alerts arrived: an alert that only
    worked if somebody remembered to duplicate the SMTP handling would be an
    alert that silently did not go out on the night it mattered."""
    if not _smtp_configured():
        # DEV MODE: print to the console so you can test locally.
        print("\n" + "=" * 50)
        print(f"[DEV] {dev_label or (subject + ' -> ' + to_email)}")
        print(f"[DEV] to: {to_email}")
        print("[DEV] --- body ---")
        print(body)
        print("[DEV] (Set SMTP_* env vars to send real emails.)")
        print("=" * 50 + "\n", flush=True)
        return True, "dev"

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        from_name = os.environ.get("SMTP_FROM_NAME", "SSA Smart Schedule")
        from_addr = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER"))
        msg["From"] = f"{from_name} <{from_addr}>"
        msg["To"] = to_email
        # Date and Message-ID are expected on real mail; filters treat their
        # absence as a bot signal. smtplib does not add them, and not every
        # provider fills them in on the way out, so set them here.
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=(from_addr.split("@")[-1] or None))
        # Somewhere to reply if a student's mail client asks, and a hint to the
        # receiving filter that this is a one-to-one transactional message.
        msg["Reply-To"] = from_addr
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body)
        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ["SMTP_USER"]
        pw = os.environ["SMTP_PASS"]
        if port == 465:
            # SSL from the start (some providers prefer this)
            import ssl
            with smtplib.SMTP_SSL(host, port, timeout=20,
                                  context=ssl.create_default_context()) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            # STARTTLS (Gmail/Outlook default, port 587)
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
        return True, "sent"
    except Exception as e:
        print(f"[mailer] failed to send: {e}", flush=True)
        return False, str(e)
