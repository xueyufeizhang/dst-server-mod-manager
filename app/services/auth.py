"""Session tokens for the login page.

Stateless HMAC-signed expiry stamps: the signing key is derived from the
configured credentials, so sessions survive panel restarts but are all
invalidated the moment the password (or username) changes in config.yaml.
Logout simply clears the cookie — fine for the single-operator,
localhost-only threat model of this panel.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SESSION_TTL_SECONDS = 7 * 24 * 3600  # stay logged in for a week
COOKIE_NAME = "dst_mod_manager_session"


def derive_key(username: str, password: str) -> bytes:
    return hashlib.sha256(
        f"dst-mod-manager-session-v1|{username}|{password}".encode("utf-8")
    ).digest()


def make_token(key: bytes) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    signature = hmac.new(key, str(expires).encode("ascii"), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_token(key: bytes, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires_str, signature = token.split(".", 1)
    if not expires_str.isdigit() or int(expires_str) < time.time():
        return False
    expected = hmac.new(key, expires_str.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
