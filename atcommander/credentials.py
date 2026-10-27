from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import keyring

from .config import APP_DIR

SERVICE = "ATCommander"
USER_ID_KEY = "animation_throwdown_user_id"
PASSWORD_KEY = "animation_throwdown_password_hash"
REFERENCE_FILE = APP_DIR / "login_credentials.txt"


@dataclass
class Credentials:
    user_id: str
    password_hash: str

    @property
    def ready(self) -> bool:
        return bool(self.user_id and self.password_hash)


def _clean(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"<redacted>", "redacted", "none", "null"}:
        return ""
    return text


def _load_reference_file() -> Credentials:
    if not REFERENCE_FILE.exists():
        return Credentials("", "")
    uid = ""
    ph = ""
    try:
        for raw in REFERENCE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = _clean(value)
            if key in {"user_id", "userid", "at_user_id"}:
                uid = value
            elif key in {"login_hash", "password_hash", "password", "hash"}:
                ph = value
    except Exception:
        pass
    return Credentials(uid, ph)


def _keyring_value(key: str) -> str:
    try:
        return _clean(keyring.get_password(SERVICE, key))
    except Exception:
        return ""


def load_credentials() -> Credentials:
    """Load AT credentials from Credential Manager with TXT fallback.

    The TXT fallback exists because WebView2 does not always expose POST form
    data through the same CDP event on every Windows installation.  The user
    explicitly requested a local reference copy so the smart pilot is not
    dependent on one capture mechanism.
    """
    file_creds = _load_reference_file()
    return Credentials(
        _keyring_value(USER_ID_KEY) or file_creds.user_id,
        _keyring_value(PASSWORD_KEY) or file_creds.password_hash,
    )


def _write_reference(user_id: str, password_hash: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    text = (
        "# AT Commander local login reference\n"
        "# Treat login_hash like a password. Do not upload or share this file.\n"
        f"user_id={user_id}\n"
        f"login_hash={password_hash}\n"
    )
    try:
        REFERENCE_FILE.write_text(text, encoding="utf-8")
        try:
            os.chmod(REFERENCE_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def save_credentials(user_id: str, password_hash: str) -> None:
    user_id = _clean(user_id)
    password_hash = _clean(password_hash)
    existing = load_credentials()
    final_uid = user_id or existing.user_id
    final_hash = password_hash or existing.password_hash

    if user_id:
        try:
            keyring.set_password(SERVICE, USER_ID_KEY, user_id)
        except Exception:
            pass
    if password_hash:
        try:
            keyring.set_password(SERVICE, PASSWORD_KEY, password_hash)
        except Exception:
            pass

    # The user requested a plaintext local reference/fallback.  We keep it in
    # %USERPROFILE%\\.atcommander rather than scattering the secret through logs.
    if final_uid or final_hash:
        _write_reference(final_uid, final_hash)


def save_user_id(user_id: str) -> None:
    save_credentials(user_id, "")


def clear_credentials() -> None:
    for key in (USER_ID_KEY, PASSWORD_KEY):
        try:
            keyring.delete_password(SERVICE, key)
        except Exception:
            pass
    try:
        REFERENCE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
