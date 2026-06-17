"""Boundary persistence + the /save-boundary workflow.

The browser draw page calls /save-boundary directly; Apps Script later calls
/process-lead. To bridge those two, drawn boundaries are cached in a simple JSON
file keyed by LeadID (no database required). /process-lead falls back to this
cache when Apps Script doesn't already carry the GeoJSON in its payload.

Optionally, if APPSHEET_APP_ID + APPSHEET_API_KEY are set, the boundary status
and acreage are pushed straight into AppSheet so the rep sees it immediately;
otherwise Apps Script reconciles those fields on the next processing cycle.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from . import geometry_utils as geo
from .settings import Settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()


class BoundaryStore:
    """Tiny thread-safe JSON-file store: {lead_id: boundary_record}."""

    def __init__(self, path: str):
        self.path = Path(path)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Boundary store unreadable (%s); starting empty.", exc)
            return {}

    def get(self, lead_id: str) -> Optional[Dict[str, Any]]:
        return self._read().get(lead_id)

    def put(self, lead_id: str, record: Dict[str, Any]) -> None:
        with _lock:
            data = self._read()
            data[lead_id] = record
            try:
                self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not persist boundary store: %s", exc)


def save_boundary(req: Dict[str, Any], settings: Settings, store: BoundaryStore) -> Dict[str, Any]:
    """Validate + persist a drawn boundary; return acreage/centroid/message."""
    warnings: list = []
    lead_id = req.get("LeadID")
    source = req.get("BoundarySource") or "Sales drawn boundary"

    if not lead_id:
        return {"success": False, "message": "LeadID is required."}

    geom, valid, geom_warning = geo.validate_geojson_polygon(req.get("BoundaryGeoJSON"))
    if not valid or geom is None:
        return {
            "success": False,
            "LeadID": lead_id,
            "message": geom_warning or "Invalid boundary polygon.",
        }
    if geom_warning:
        warnings.append(geom_warning)

    acres = geo.calculate_area_acres(geom, settings.area_crs, warnings)
    clat, clng = geo.calculate_centroid(geom)

    record = {
        "LeadID": lead_id,
        "BoundarySource": source,
        "BoundaryGeoJSON": geo.geom_as_geojson(geom),
        "BoundaryWKT": geo.geometry_to_wkt(geom),
        "BoundaryAreaAcres": acres,
        "BoundaryCentroidLat": clat,
        "BoundaryCentroidLng": clng,
        "GeometryValid": True,
        "GeometryWarning": "; ".join(warnings),
    }
    store.put(lead_id, record)

    pushed = _maybe_push_to_appsheet(lead_id, source, acres, settings)

    msg = f"Boundary saved. Approximate area: {acres} acres." if acres is not None \
        else "Boundary saved (acreage unavailable)."
    if not pushed:
        msg += " Return to AppSheet and sync; processing will update the lead."

    return {
        "success": True,
        "LeadID": lead_id,
        "BoundaryAreaAcres": acres,
        "BoundaryCentroidLat": clat,
        "BoundaryCentroidLng": clng,
        "message": msg,
        "warnings": warnings,
    }


def load_stored_geometry(lead_id: str, store: BoundaryStore):
    """Return (geom4326, source, record) for a cached boundary, or (None, None, None)."""
    record = store.get(lead_id)
    if not record:
        return None, None, None
    geom, valid, _ = geo.validate_geojson_polygon(record.get("BoundaryGeoJSON"))
    if not valid:
        return None, None, record
    return geom, record.get("BoundarySource", "Sales drawn boundary"), record


def _maybe_push_to_appsheet(lead_id: str, source: str, acres, settings: Settings) -> bool:
    """Optional immediate update of AppSheet. Returns True if a push was sent."""
    if not (settings.appsheet_app_id and settings.appsheet_api_key):
        return False
    try:
        import urllib.request

        host = "api.eu.appsheet.com" if settings.appsheet_region == "eu" else "api.appsheet.com"
        url = f"https://{host}/api/v2/apps/{settings.appsheet_app_id}/tables/Leads/Action"
        body = json.dumps({
            "Action": "Edit",
            "Properties": {},
            "Rows": [{
                "LeadID": lead_id,
                "BoundaryStatus": "Drawn",
                "BoundarySource": source,
                "BoundaryAreaAcres": acres,
            }],
        }).encode("utf-8")
        request = urllib.request.Request(
            url, data=body,
            headers={"ApplicationAccessKey": settings.appsheet_api_key,
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=10).read()
        return True
    except Exception as exc:
        logger.warning("AppSheet push failed (will reconcile via Apps Script): %s", exc)
        return False
