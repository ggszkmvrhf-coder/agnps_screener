"""Boundary persistence + the /save-boundary workflow.

The browser draw page calls /save-boundary directly; Apps Script later calls
/process-lead. To bridge those two, drawn boundaries are cached in a simple JSON
file keyed by LeadID (no database required). /process-lead falls back to this
cache when Apps Script doesn't already carry the GeoJSON in its payload.

Optionally, if APPSHEET_APP_ID + APPSHEET_API_KEY are set, the boundary status
and the Field_Boundaries row are pushed straight into AppSheet so Sheets remains
the durable system of record; otherwise Apps Script can only use the local cache
while this backend instance is alive.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

from .database import database_reachable, get_engine, table_exists
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

    db_saved = _save_boundary_to_postgis(record, settings)
    pushed = _maybe_push_to_appsheet(record, settings)

    msg = f"Boundary saved. Approximate area: {acres} acres." if acres is not None \
        else "Boundary saved (acreage unavailable)."
    if db_saved:
        msg += " It is saved in PostGIS for processing."
    elif pushed:
        msg += " It is saved to AppSheet for processing."
    elif not pushed:
        msg += " Return to AppSheet and sync; processing will use the backend cache."

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


def load_database_geometry(lead_id: str, settings: Settings):
    """Return (geom4326, source, record) from PostGIS, or (None, None, None)."""
    if not lead_id:
        return None, None, None

    engine = get_engine(settings.database_url)
    if not database_reachable(engine) or not table_exists(engine, "field_boundaries"):
        return None, None, None

    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                """
                SELECT
                    lead_id,
                    boundary_source,
                    boundary_geojson,
                    boundary_wkt,
                    boundary_area_acres,
                    boundary_centroid_lat,
                    boundary_centroid_lng,
                    geometry_valid,
                    geometry_warning
                FROM field_boundaries
                WHERE lead_id = :lead_id
                  AND COALESCE(geometry_valid, true) = true
                ORDER BY created_at DESC
                LIMIT 1
                """
            ), {"lead_id": lead_id}).mappings().first()
    except Exception as exc:
        logger.warning("Could not load boundary from PostGIS: %s", exc)
        return None, None, None

    if not row:
        return None, None, None

    record = dict(row)
    geom = None
    if record.get("boundary_geojson"):
        geom, valid, _ = geo.validate_geojson_polygon(record["boundary_geojson"])
        if not valid:
            geom = None
    if geom is None and record.get("boundary_wkt"):
        geom, valid, _ = geo.parse_boundary_wkt(record["boundary_wkt"])
        if not valid:
            geom = None
    if geom is None:
        return None, None, record

    return geom, record.get("boundary_source", "Sales drawn boundary"), {
        "LeadID": record.get("lead_id"),
        "BoundarySource": record.get("boundary_source"),
        "BoundaryGeoJSON": record.get("boundary_geojson"),
        "BoundaryWKT": record.get("boundary_wkt"),
        "BoundaryAreaAcres": record.get("boundary_area_acres"),
        "BoundaryCentroidLat": record.get("boundary_centroid_lat"),
        "BoundaryCentroidLng": record.get("boundary_centroid_lng"),
        "GeometryValid": record.get("geometry_valid"),
        "GeometryWarning": record.get("geometry_warning"),
    }


def _save_boundary_to_postgis(record: Dict[str, Any], settings: Settings) -> bool:
    """Best-effort durable boundary save. JSON cache remains the fallback."""
    engine = get_engine(settings.database_url)
    if not database_reachable(engine):
        return False
    if not table_exists(engine, "leads") or not table_exists(engine, "field_boundaries"):
        return False

    lead_id = record["LeadID"]
    boundary_id = f"{lead_id}-sales-drawn"
    try:
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO leads (
                    lead_id, boundary_status, boundary_source,
                    boundary_area_acres, updated_at, geom
                )
                VALUES (
                    :lead_id, 'Drawn', :source,
                    :acres, now(),
                    ST_Transform(ST_GeomFromText(:wkt, :input_crs), :projected_crs)
                )
                ON CONFLICT (lead_id) DO UPDATE SET
                    boundary_status = 'Drawn',
                    boundary_source = EXCLUDED.boundary_source,
                    boundary_area_acres = EXCLUDED.boundary_area_acres,
                    updated_at = now(),
                    geom = EXCLUDED.geom
                """
            ), {
                "lead_id": lead_id,
                "source": record.get("BoundarySource"),
                "acres": record.get("BoundaryAreaAcres"),
                "wkt": record.get("BoundaryWKT"),
                "input_crs": settings.input_crs,
                "projected_crs": settings.projected_crs,
            })
            conn.execute(text(
                """
                INSERT INTO field_boundaries (
                    boundary_id, lead_id, boundary_source,
                    boundary_geojson, boundary_wkt, boundary_area_acres,
                    boundary_centroid_lat, boundary_centroid_lng,
                    boundary_confidence, geometry_valid, geometry_warning,
                    geom
                )
                VALUES (
                    :boundary_id, :lead_id, :source,
                    :geojson, :wkt, :acres,
                    :centroid_lat, :centroid_lng,
                    'Rough sales boundary', :valid, :warning,
                    ST_Transform(ST_GeomFromText(:wkt, :input_crs), :projected_crs)
                )
                ON CONFLICT (boundary_id) DO UPDATE SET
                    created_at = now(),
                    boundary_source = EXCLUDED.boundary_source,
                    boundary_geojson = EXCLUDED.boundary_geojson,
                    boundary_wkt = EXCLUDED.boundary_wkt,
                    boundary_area_acres = EXCLUDED.boundary_area_acres,
                    boundary_centroid_lat = EXCLUDED.boundary_centroid_lat,
                    boundary_centroid_lng = EXCLUDED.boundary_centroid_lng,
                    boundary_confidence = EXCLUDED.boundary_confidence,
                    geometry_valid = EXCLUDED.geometry_valid,
                    geometry_warning = EXCLUDED.geometry_warning,
                    geom = EXCLUDED.geom
                """
            ), {
                "boundary_id": boundary_id,
                "lead_id": lead_id,
                "source": record.get("BoundarySource"),
                "geojson": json.dumps(record.get("BoundaryGeoJSON")),
                "wkt": record.get("BoundaryWKT"),
                "acres": record.get("BoundaryAreaAcres"),
                "centroid_lat": record.get("BoundaryCentroidLat"),
                "centroid_lng": record.get("BoundaryCentroidLng"),
                "valid": record.get("GeometryValid"),
                "warning": record.get("GeometryWarning"),
                "input_crs": settings.input_crs,
                "projected_crs": settings.projected_crs,
            })
        return True
    except Exception as exc:
        logger.warning("Could not persist boundary to PostGIS: %s", exc)
        return False


def find_boundary_via_appsheet(lead_id: str, settings: Settings):
    """Read a boundary back from the Field_Boundaries table via the AppSheet API.

    This is the durable source when there's no PostGIS and the backend's local
    JSON cache has been wiped (e.g. Render free-tier restart). Returns
    (geom4326, source, record) or (None, None, None).
    """
    if not lead_id or not (settings.appsheet_app_id and settings.appsheet_api_key):
        return None, None, None
    rows = _appsheet_find(
        settings, "Field_Boundaries",
        f'Filter(Field_Boundaries, [LeadID] = "{lead_id}")',
    )
    for row in rows:
        gj = row.get("BoundaryGeoJSON")
        if not gj:
            continue
        geom, valid, _ = geo.validate_geojson_polygon(gj)
        if valid:
            return geom, row.get("BoundarySource", "Sales drawn boundary"), row
    return None, None, None


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


def _maybe_push_to_appsheet(record: Dict[str, Any], settings: Settings) -> bool:
    """Optional immediate update of AppSheet. Returns True if all pushes worked."""
    if not (settings.appsheet_app_id and settings.appsheet_api_key):
        return False
    try:
        _appsheet_action(settings, "Leads", "Edit", [{
            "LeadID": record["LeadID"],
            "BoundaryStatus": "Drawn",
            "BoundarySource": record.get("BoundarySource"),
            "BoundaryAreaAcres": record.get("BoundaryAreaAcres"),
        }])

        boundary_id = f"{record['LeadID']}-sales-drawn"
        boundary_row = {
            "BoundaryID": boundary_id,
            "LeadID": record["LeadID"],
            "CreatedAt": datetime.now(timezone.utc).isoformat(),
            "BoundarySource": record.get("BoundarySource"),
            "BoundaryGeoJSON": json.dumps(record.get("BoundaryGeoJSON")),
            "BoundaryWKT": record.get("BoundaryWKT"),
            "BoundaryAreaAcres": record.get("BoundaryAreaAcres"),
            "BoundaryCentroidLat": record.get("BoundaryCentroidLat"),
            "BoundaryCentroidLng": record.get("BoundaryCentroidLng"),
            "BoundaryConfidence": "Rough sales boundary",
            "GeometryValid": record.get("GeometryValid"),
            "GeometryWarning": record.get("GeometryWarning"),
        }
        if not _appsheet_action(settings, "Field_Boundaries", "Add", [boundary_row], raise_errors=False):
            _appsheet_action(settings, "Field_Boundaries", "Edit", [boundary_row])
        return True
    except Exception as exc:
        logger.warning("AppSheet push failed (will reconcile via Apps Script): %s", exc)
        return False


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
        urllib.request.urlopen(request, timeout=10).read()
        return True
    except urllib.error.HTTPError:
        if raise_errors:
            raise
        return False
