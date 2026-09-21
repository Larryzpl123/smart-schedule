#!/usr/bin/env python3
"""Open an encrypted backup downloaded from /admin.

    python3 decrypt_backup.py ssa-schedule-backup_2026-09-18_2030.json.enc

The key is BACKUP_KEY from the server's WSGI file. Pass it with --key, or set
BACKUP_KEY in your environment. The plain JSON is written next to the input
with the .enc removed.

Why a separate script rather than a button: the whole point of encrypting the
download is that the key is NOT wherever the file ends up. A "decrypt" button
on the admin page would put them back together.
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--key", default=os.environ.get("BACKUP_KEY", ""))
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    if not a.key:
        sys.exit("No key. Pass --key, or set BACKUP_KEY. It is the same "
                 "string as BACKUP_KEY in the server's WSGI file.")
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        sys.exit("pip install cryptography")

    raw = open(a.path, "rb").read().strip()
    if raw[:1] in (b"{", b"["):
        sys.exit("That file is already plain JSON; nothing to decrypt.")
    try:
        plain = Fernet(a.key.encode()).decrypt(raw)
    except Exception:
        sys.exit("Could not decrypt it. Wrong key, or the file is damaged.")

    out = a.out or (a.path[:-4] if a.path.endswith(".enc") else a.path + ".json")
    with open(out, "wb") as f:
        f.write(plain)
    print(f"Wrote {out} ({len(plain)} bytes)")


if __name__ == "__main__":
    main()
