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

from .settings import Settings

# Fallback only if API_KEY is unset (links would still verify, just not secret).
_FALLBACK_SECRET = "agnps-unsigned-fallback"


def _secret(settings: Settings) -> str:
    return settings.api_key or _FALLBACK_SECRET


def sign(lead_id: str, exp: int, settings: Settings) -> str:
    msg = f"{lead_id}.{exp}".encode("utf-8")
    return hmac.new(_secret(settings).encode("utf-8"), msg, hashlib.sha256).hexdigest()


def build_share_url(lead_id: str, settings: Settings) -> Optional[str]:
    """Return a full signed KML URL valid for share_link_ttl_hours, or None."""
    base = (settings.public_base_url or "").rstrip("/")
    if not base or not lead_id:
        return None
    exp = int(time.time()) + settings.share_link_ttl_hours * 3600
    return f"{base}/boundary/{lead_id}.kml?exp={exp}&sig={sign(lead_id, exp, settings)}"


def verify(lead_id: str, exp: Optional[str], sig: Optional[str], settings: Settings) -> Tuple[bool, str]:
    """Validate a signed link. Returns (ok, reason)."""
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
