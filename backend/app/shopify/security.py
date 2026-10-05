"""Shopify request verification. All comparisons are constant-time."""

import base64
import hashlib
import hmac
import re
from collections.abc import Mapping

SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")


def is_valid_shop_domain(shop: str) -> bool:
    return bool(SHOP_DOMAIN.fullmatch(shop.lower()))


def webhook_hmac(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_webhook(secret: str, body: bytes, header: str | None) -> bool:
    if not header:
        return False
    return hmac.compare_digest(webhook_hmac(secret, body), header)


def query_hmac(secret: str, params: Mapping[str, str]) -> str:
    """HMAC Shopify adds to OAuth redirects: sorted `key=value` pairs joined by `&`, minus hmac."""
    message = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
        if key not in {"hmac", "signature"}
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_query(secret: str, params: Mapping[str, str]) -> bool:
    provided = params.get("hmac")
    if not provided:
        return False
    return hmac.compare_digest(query_hmac(secret, params), provided)
