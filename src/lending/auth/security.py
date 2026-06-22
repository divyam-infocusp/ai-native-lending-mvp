"""
Password hashing + bearer-token signing — stdlib only (no external auth deps).

Passwords: PBKDF2-HMAC-SHA256 with a per-user random salt.
Tokens: a compact `base64(payload).hmac` signed with the server secret, carrying
the subject (user id), role, and an expiry. Stateless — verified by signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, rounds, b64salt, b64dk = stored.split("$")
        salt = _unb64(b64salt)
        expected = _unb64(b64dk)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def create_token(sub: str, role: str, secret: str, *, ttl_seconds: int = 86_400, now: Optional[int] = None) -> str:
    now = now if now is not None else int(time.time())
    body = {"sub": sub, "role": role, "exp": now + ttl_seconds}
    raw = _b64url(json.dumps(body, separators=(",", ":")).encode())
    return f"{raw}.{_sign(raw, secret)}"


def verify_token(token: str, secret: str, *, now: Optional[int] = None) -> Optional[dict]:
    now = now if now is not None else int(time.time())
    try:
        raw, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(raw, secret)):
            return None
        body = json.loads(_unb64url(raw))
        if int(body.get("exp", 0)) < now:
            return None
        return body
    except Exception:
        return None


def _sign(raw: str, secret: str) -> str:
    return _b64url(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
