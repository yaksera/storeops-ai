"""Compact HMAC-signed tokens for links we hand out (unsubscribe, billing return URLs)."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.config import get_settings


class BadSignatureError(Exception):
    pass


def _key(purpose: str) -> bytes:
    secret = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(secret, purpose.encode(), hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(purpose: str, payload: dict[str, Any], max_age_seconds: int | None = None) -> str:
    body = dict(payload)
    if max_age_seconds is not None:
        body["exp"] = int(time.time()) + max_age_seconds
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    mac = _b64(hmac.new(_key(purpose), raw.encode(), hashlib.sha256).digest()[:18])
    return f"{raw}.{mac}"


def unsign(purpose: str, token: str) -> dict[str, Any]:
    try:
        raw, mac = token.split(".", 1)
        expected = _b64(hmac.new(_key(purpose), raw.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(mac, expected):
            raise BadSignatureError("signature mismatch")
        payload: dict[str, Any] = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadSignatureError("malformed token") from exc
    if "exp" in payload and payload["exp"] < time.time():
        raise BadSignatureError("token expired")
    return payload
