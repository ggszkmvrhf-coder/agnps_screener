"""Signed, expiring boundary-download links.

A share link looks like:
    {base}/boundary/{LeadID}.kml?exp=<unix_ts>&sig=<hmac_sha256>

The signature is HMAC-SHA256 over "{LeadID}.{exp}" keyed by the backend's
API_KEY. The /boundary endpoint recomputes the signature and rejects the link if
it doesn't match or if `exp` has passed -- so the link genuinely dies after the
TTL (default 24h) and can't be forged by editing the timestamp.
"""
import hashlib
import hmac
import time
from typing import Optional, Tuple
from urllib.parse import quote

from .settings import Settings

def _secret(settings: Settings) -> Optional[str]:
    # AGENT-H1: Prefer share_link_secret for signing; fall back to api_key for backward compatibility.
    return settings.share_link_secret or settings.api_key


def sign(lead_id: str, exp: int, settings: Settings) -> str:
    secret = _secret(settings)
    if not secret:
        raise ValueError("API_KEY is required to sign boundary share links.")
    msg = f"{lead_id}.{exp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def build_share_url(lead_id: str, settings: Settings) -> Optional[str]:
    """Return a full signed KML URL valid for share_link_ttl_hours, or None."""
    base = (settings.public_base_url or "").rstrip("/")
    if not base or not lead_id or not settings.api_key:
        return None
    exp = int(time.time()) + settings.share_link_ttl_hours * 3600
    path_id = quote(str(lead_id), safe="")
    return f"{base}/boundary/{path_id}.kml?exp={exp}&sig={sign(lead_id, exp, settings)}"


def verify(lead_id: str, exp: Optional[str], sig: Optional[str], settings: Settings) -> Tuple[bool, str]:
    """Validate a signed link. Returns (ok, reason)."""
    if not settings.api_key:
        return False, "not-configured"
    if not exp or not sig:
        return False, "missing"
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False, "bad-exp"
    if time.time() > exp_i:
        return False, "expired"
    if not hmac.compare_digest(sign(lead_id, exp_i, settings), sig):
        return False, "bad-sig"
    return True, "ok"
