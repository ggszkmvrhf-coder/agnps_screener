"""
draw_tokens.py — Short-lived, per-lead signed tokens for the Draw Boundary page.

Token format: HMAC-SHA256 of "draw.{lead_id}.{exp}" using SHARE_LINK_SECRET (or
api_key as fallback), base64url-encoded without padding.

Namespaced with "draw." prefix to prevent token reuse across different signing contexts
(e.g. KML share links use a different prefix).
"""
import base64
import hashlib
import hmac
import time

from .settings import get_settings

_DEFAULT_TTL_DAYS = 7


def _secret() -> bytes:
    settings = get_settings()
    s = settings.share_link_secret or settings.api_key or ""
    return s.encode()


def _sign(lead_id: str, exp: int) -> str:
    msg = f"draw.{lead_id}.{exp}".encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def generate(lead_id: str, ttl_days: int = _DEFAULT_TTL_DAYS) -> tuple:
    """Return (token, exp) for the given lead_id."""
    exp = int(time.time()) + ttl_days * 86400
    return _sign(lead_id, exp), exp


def verify(lead_id: str, token: str, exp: int) -> bool:
    """Return True if the token is valid and not expired."""
    if not token or not lead_id:
        return False
    if time.time() > exp:
        return False
    expected = _sign(lead_id, exp)
    return hmac.compare_digest(token, expected)
