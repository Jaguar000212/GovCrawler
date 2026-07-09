"""SMTP credential encryption at rest (Fernet/cryptography).

Same env-first-else-persisted-file pattern as server.py's _ensure_jwt_secret:
containers supply CREDENTIAL_ENC_KEY via env; local/dev installs get one
generated and persisted to config.yaml on first run. Losing every key in the
chain makes every stored credential permanently undecryptable — there is no
recovery path, by design (plan.md §13).

CREDENTIAL_ENC_KEY_PREV (optional) supports rotation: encrypt_password always
uses the *first* key (the current primary); decrypt_password tries every key
in the chain via MultiFernet, so credentials written under an old primary
stay readable until scripts/rotate_credential_encryption_key.py re-encrypts
them and the old key is dropped. See deploy/SECURITY.md.
"""

import logging
import os

import yaml
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pathlib import Path

log = logging.getLogger(__name__)


def ensure_credential_enc_key(config_dict: dict, config_path: Path) -> list[str]:
    """Returns the key chain (current primary first, then any previous key)."""
    keys = []
    if os.environ.get("CREDENTIAL_ENC_KEY"):
        keys.append(os.environ["CREDENTIAL_ENC_KEY"])
    else:
        existing = config_dict.get("auth", {}).get("credential_enc_key")
        if existing:
            keys.append(existing)
        else:
            key = Fernet.generate_key().decode()
            keys.append(key)
            config_dict.setdefault("auth", {})["credential_enc_key"] = key
            with open(config_path, "w") as f:
                yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            log.info("Generated and persisted a new auth.credential_enc_key")
    config_dict.setdefault("auth", {})["credential_enc_key"] = keys[0]

    prev = os.environ.get("CREDENTIAL_ENC_KEY_PREV") or config_dict.get("auth", {}).get("credential_enc_key_prev")
    if prev:
        keys.append(prev)
        config_dict.setdefault("auth", {})["credential_enc_key_prev"] = prev
    return keys


def _multi_fernet(keys: list[str] | str) -> MultiFernet:
    if isinstance(keys, str):
        keys = [keys]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt_password(plain: str, keys: list[str] | str) -> bytes:
    """Always encrypts with the first (current primary) key."""
    primary = keys[0] if isinstance(keys, list) else keys
    return Fernet(primary.encode()).encrypt(plain.encode())


def decrypt_password(blob, keys: list[str] | str) -> str:
    if isinstance(blob, str):
        blob = blob.encode()
    try:
        return _multi_fernet(keys).decrypt(bytes(blob)).decode()
    except InvalidToken:
        raise ValueError("Could not decrypt credential — CREDENTIAL_ENC_KEY may have changed")
