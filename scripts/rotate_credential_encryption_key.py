"""
Rotate CREDENTIAL_ENC_KEY: re-encrypts every stored SMTP credential password
under a new primary key, so the old key can be safely dropped afterward.

RUNBOOK (see deploy/SECURITY.md for the full checklist):
    1. Generate a new key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    2. Set CREDENTIAL_ENC_KEY_PREV=<old key>, CREDENTIAL_ENC_KEY=<new key> and
       redeploy (api + dispatcher both need both keys set to decrypt existing
       rows with the old key while this script runs).
    3. Run this script against the same DATABASE_URL.
    4. Drop CREDENTIAL_ENC_KEY_PREV and redeploy again.

Usage:
    python scripts/rotate_credential_encryption_key.py <postgres-or-sqlite-url>
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud.db import Database  # noqa: E402
from cloud.db.tables.outreach import SMTPCredential  # noqa: E402
from cloud.security.crypto import decrypt_password, encrypt_password  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/rotate_credential_encryption_key.py <database-url>")
        sys.exit(1)

    if not os.environ.get("CREDENTIAL_ENC_KEY_PREV"):
        log.error(
            "CREDENTIAL_ENC_KEY_PREV is not set — nothing to rotate FROM. See the runbook in this file's docstring.")
        sys.exit(1)

    db = Database({"database": {"uri": sys.argv[1]}, "auth": {}})
    new_key = db._cred_enc_key[0]

    with db._Session() as s:
        creds = s.query(SMTPCredential).all()
        rotated = 0
        for c in creds:
            plain = decrypt_password(c.password_encrypted, db._cred_enc_key)
            c.password_encrypted = encrypt_password(plain, [new_key])
            rotated += 1
        s.commit()

    log.info(f"Rotated {rotated} SMTP credential(s) to the new primary key.")
    log.info("Now drop CREDENTIAL_ENC_KEY_PREV and redeploy.")
    db.close()


if __name__ == "__main__":
    main()
