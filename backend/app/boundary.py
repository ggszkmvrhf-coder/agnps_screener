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
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

from .database import database_reachable, get_engine, table_exists
from . import geometry_utils as geo
from .settings import Settings
from .appsheet_client import _appsheet_action, _appsheet_find, _appsheet_response_has_error  # AGENT-L2

# AGENT-L3: Log messages include lead_id= prefix for traceability.
logger = logging.getLogger(__name__)
_lock = threading.Lock()
_SAFE_LEAD_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_lead_id(value: Any) -> Optional[str]:
    lead_id = str(value or "").strip()
    return lead_id if _SAFE_LEAD_ID.fullmatch(lead_id) else None


def _notes_for_annotations(annotations: Any) -> Optional[str]:
    if not annotations:
        return None
    return json.dumps({"BoundaryAnnotationsGeoJSON": annotations}, separators=(",", ":"))


def _annotations_from_notes(notes: Any) -> Optional[dict]:
    if not notes:
        return None
    try:
        value = json.loads(notes) if isinstance(notes, str) else notes
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    annotations, _ = geo.normalize_annotation_geojson(value.get("BoundaryAnnotationsGeoJSON"))
    return annotations


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
    lead_id = _safe_lead_id(lead_id)
    if not lead_id:
        return {
            "success": False,
            "message": "LeadID may only contain letters, numbers, underscores, and hyphens.",
        }

    geom, valid, geom_warning = geo.validate_geojson_polygon(req.get("BoundaryGeoJSON"))
    if not valid or geom is None:
        return {
            "success": False,
            "LeadID": lead_id,
            "message": geom_warning or "Invalid boundary polygon.",
        }
    if geom_warning:
        warnings.append(geom_warning)

    annotations, annotation_warning = geo.normalize_annotation_geojson(
        req.get("BoundaryAnnotationsGeoJSON")
    )
    if annotation_warning:
        warnings.append(annotation_warning)

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
    if annotations:
        record["BoundaryAnnotationsGeoJSON"] = annotations
    store.put(lead_id, record)

    db_saved = _save_boundary_to_postgis(record, settings)
    pushed = _maybe_push_to_appsheet(record, settings)
    durable_configured = bool(
        settings.database_url or (settings.appsheet_app_id and settings.appsheet_api_key)
    )

    # AGENT-M5: CachedKML stored after successful durable write. /boundary endpoint uses this to skip AppSheet read.
    if db_saved or pushed:
        try:
            record["CachedKML"] = geo.geometry_to_kml(
                geom,
                lead_id,
                annotations=record.get("BoundaryAnnotationsGeoJSON"),
            )
            store.put(lead_id, record)
        except Exception as _kml_exc:
            logger.warning("lead_id=%s Could not cache KML: %s", lead_id, _kml_exc)

    msg = f"Boundary saved. Approximate area: {acres} acres." if acres is not None \
        else "Boundary saved (acreage unavailable)."
    if db_saved:
        msg += " It is saved in PostGIS for processing."
    elif pushed:
        msg += " It is saved to AppSheet for processing."
    elif durable_configured:
        msg = (
            "Boundary was valid, but it could not be saved to the configured durable "
            "system. Check the AppSheet/PostGIS settings and try again."
        )
        warnings.append("Boundary is only present in the backend cache; durable save failed.")
        return {
            "success": False,
            "LeadID": lead_id,
            "BoundaryAreaAcres": acres,
            "BoundaryCentroidLat": clat,
            "BoundaryCentroidLng": clng,
            "message": msg,
            "warnings": warnings,
        }
    else:
        # AGENT-C1: No durable storage = success: False. Cache write still happens for within-session use, but caller must not treat this as a confirmed save.
        return {
            "success": False,
            "LeadID": lead_id,
            "BoundaryAreaAcres": 0.0,
            "BoundaryCentroidLat": None,
            "BoundaryCentroidLng": None,
            "message": "No durable storage is configured. Set DATABASE_URL or both APPSHEET_APP_ID and APPSHEET_API_KEY in the Render environment. The boundary was NOT saved durably.",
            "warnings": ["Boundary not saved: no durable storage backend is configured."],
        }

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
                    geometry_warning,
                    notes
                FROM field_boundaries
                WHERE lead_id = :lead_id
                  AND COALESCE(geometry_valid, true) = true
                ORDER BY created_at DESC
                LIMIT 1
                """
            ), {"lead_id": lead_id}).mappings().first()
    except Exception as exc:
        logger.warning("lead_id=%s Could not load boundary from PostGIS: %s", lead_id, exc)
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
        "BoundaryAnnotationsGeoJSON": _annotations_from_notes(record.get("notes")),
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
                    notes, geom
                )
                VALUES (
                    :boundary_id, :lead_id, :source,
                    :geojson, :wkt, :acres,
                    :centroid_lat, :centroid_lng,
                    'Rough sales boundary', :valid, :warning,
                    :notes, ST_Transform(ST_GeomFromText(:wkt, :input_crs), :projected_crs)
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
                    notes = EXCLUDED.notes,
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
                "notes": _notes_for_annotations(record.get("BoundaryAnnotationsGeoJSON")),
                "input_crs": settings.input_crs,
                "projected_crs": settings.projected_crs,
            })
        return True
    except Exception as exc:
        logger.warning("lead_id=%s Could not persist boundary to PostGIS: %s", lead_id, exc)
        return False


def find_boundary_via_appsheet(lead_id: str, settings: Settings):
    """Read a boundary back from the Field_Boundaries table via the AppSheet API.

    This is the durable source when there's no PostGIS and the backend's local
    JSON cache has been wiped (e.g. Render free-tier restart). Returns
    (geom4326, source, record) or (None, None, None).
    """
    lead_id = _safe_lead_id(lead_id)
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
            record = dict(row)
            annotations = record.get("BoundaryAnnotationsGeoJSON")
            if not annotations:
                annotations = _annotations_from_notes(record.get("Notes"))
            annotations, _ = geo.normalize_annotation_geojson(annotations)
            if annotations:
                record["BoundaryAnnotationsGeoJSON"] = annotations
            return geom, row.get("BoundarySource", "Sales drawn boundary"), record
    return None, None, None


# AGENT-H2: Field_Boundaries written first (durable GeoJSON). Leads written second (status). Failure in either step returns False without partial state.
def _maybe_push_to_appsheet(record: Dict[str, Any], settings: Settings) -> bool:
    """Optional immediate update of AppSheet. Returns True if all pushes worked.

    Write order: Field_Boundaries first (durable GeoJSON record), Leads second
    (status update). If Field_Boundaries fails we return False immediately and
    never touch Leads, preventing the corrupted state where Leads shows
    BoundaryStatus=Drawn but no GeoJSON row exists in Field_Boundaries.
    If Leads fails after a successful Field_Boundaries write we still return
    False — the boundary data is safe and a re-save will retry both writes.
    """
    if not (settings.appsheet_app_id and settings.appsheet_api_key):
        return False

    # --- Step 1: Write Field_Boundaries (durable GeoJSON record) ---
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
        "Notes": _notes_for_annotations(record.get("BoundaryAnnotationsGeoJSON")) or "",
    }
    try:
        if not _appsheet_action(settings, "Field_Boundaries", "Add", [boundary_row], raise_errors=False):
            _appsheet_action(settings, "Field_Boundaries", "Edit", [boundary_row])
    except Exception as exc:
        logger.warning(
            "lead_id=%s AppSheet push failed at Field_Boundaries step — Leads NOT updated to avoid partial state: %s",
            record["LeadID"],
            exc,
        )
        return False

    # --- Step 2: Write Leads (status update — only reached if Field_Boundaries succeeded) ---
    try:
        _appsheet_action(settings, "Leads", "Edit", [{
            "LeadID": record["LeadID"],
            "BoundaryStatus": "Drawn",
            "BoundarySource": record.get("BoundarySource"),
            "BoundaryAreaAcres": record.get("BoundaryAreaAcres"),
        }])
    except Exception as exc:
        logger.warning(
            "lead_id=%s AppSheet push failed at Leads step (Field_Boundaries already written — boundary data is safe, status is stale): %s",
            record["LeadID"],
            exc,
        )
        return False

    return True


