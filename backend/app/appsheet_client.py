# AGENT-L2: AppSheet HTTP client extracted from boundary.py.
"""Low-level AppSheet REST API helpers.

Provides the three HTTP primitives that boundary.py (and any future module)
needs to talk to the AppSheet API.  Business logic that decides *when* to call
these functions stays in boundary.py (_maybe_push_to_appsheet, etc.).
"""
import json
import logging
from typing import Any

from .settings import Settings

logger = logging.getLogger(__name__)


def _appsheet_find(settings: Settings, table: str, selector: str) -> list:
    """Run an AppSheet API 'Find' action and return the matching rows."""
    import urllib.error
    import urllib.request

    host = "api.eu.appsheet.com" if settings.appsheet_region == "eu" else "api.appsheet.com"
    url = f"https://{host}/api/v2/apps/{settings.appsheet_app_id}/tables/{table}/Action"
    body = json.dumps({
        "Action": "Find",
        "Properties": {"Selector": selector},
        "Rows": [],
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"ApplicationAccessKey": settings.appsheet_api_key, "Content-Type": "application/json"},
    )
    try:
        raw = urllib.request.urlopen(request, timeout=12).read()
        data = json.loads(raw or b"[]")
        return data.get("Rows", []) if isinstance(data, dict) else (data or [])
    except Exception as exc:
        logger.warning("AppSheet Find failed: %s", exc)
        return []


def _appsheet_action(
    settings: Settings,
    table: str,
    action: str,
    rows: list,
    raise_errors: bool = True,
) -> bool:
    """Send an AppSheet API table action."""
    import urllib.error
    import urllib.request

    host = "api.eu.appsheet.com" if settings.appsheet_region == "eu" else "api.appsheet.com"
    url = f"https://{host}/api/v2/apps/{settings.appsheet_app_id}/tables/{table}/Action"
    body = json.dumps({
        "Action": action,
        "Properties": {},
        "Rows": rows,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "ApplicationAccessKey": settings.appsheet_api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        raw = urllib.request.urlopen(request, timeout=10).read()
        text_body = raw.decode("utf-8", errors="replace").strip()
        if text_body:
            try:
                data = json.loads(text_body)
            except json.JSONDecodeError:
                data = None
            if _appsheet_response_has_error(data):
                message = f"AppSheet {action} on {table} returned an error: {text_body[:500]}"
                if raise_errors:
                    raise RuntimeError(message)
                logger.warning(message)
                return False
        return True
    except urllib.error.HTTPError as exc:
        if raise_errors:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"AppSheet {action} on {table} failed with HTTP {exc.code}: {body[:500]}"
            ) from exc
        return False


def _appsheet_response_has_error(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    status = str(data.get("Status") or data.get("status") or "").strip().lower()
    if status in ("error", "failed", "failure"):
        return True
    for key in ("Errors", "Error", "error", "ErrorMessage", "errorMessage"):
        value = data.get(key)
        if value not in (None, "", [], {}):
            return True
    return False
