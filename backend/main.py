"""FastAPI entry point for the AgNPS Candidate Lead Screener backend.

Run from the `backend/` directory:
    uvicorn main:app --reload --port 8000

Endpoints:
    GET  /health                  -- liveness + DB/layer status
    POST /save-boundary           -- validate + persist a drawn boundary, return acres
    POST /process-lead            -- full pipeline: GIS + score + calculators
    POST /debug/process-sample    -- process the bundled sample_payload.json
    GET  /draw_boundary.html      -- Leaflet boundary-drawing page (static)

If API_KEY is set in the environment, /save-boundary and /process-lead require it
(header `X-API-Key`, or `?key=` for the browser draw page).
"""
import hmac
import json
import logging
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from app import boundary as boundary_mod
from app import bmp_rules, calculators, gis_lookup, report_data, scoring, share_links
from app import geometry_utils as geo
from app.boundary import BoundaryStore
from app.database import database_reachable, get_engine, table_exists
from app.schemas import (
    BoundarySaveRequest, BoundarySaveResponse, LeadProcessRequest, LeadProcessResponse,
)
from app import draw_tokens
from app.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agnps")

# AGENT-L3: Per-request UUID for log correlation.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

HERE = Path(__file__).parent
SAMPLE_PAYLOAD = HERE / "sample_payload.json"

_settings = get_settings()
# SECURITY-FIX-1: Docs/schema disabled in production unless DEBUG_ENDPOINTS_ENABLED=true.
app = FastAPI(
    title="AgNPS Candidate Lead Screener",
    version="0.2.0",
    docs_url="/docs" if _settings.debug_endpoints_enabled else None,
    redoc_url="/redoc" if _settings.debug_endpoints_enabled else None,
    openapi_url="/openapi.json" if _settings.debug_endpoints_enabled else None,
)
# AGENT-M3: Restricted to GET/POST and Content-Type/X-API-Key only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key", "X-Draw-Token", "X-Draw-Exp", "X-Lead-ID"],
)


# AGENT-L3: Per-request UUID for log correlation.
@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    _request_id_var.set(request_id)
    lead_id = request.path_params.get("lead_id", "-")
    logger.info(
        "request started lead_id=%s request_id=%s path=%s",
        lead_id,
        request_id,
        request.url.path,
    )
    return await call_next(request)


MAX_BODY_BYTES = 512 * 1024  # 512 KB


# SECURITY-FIX-4: Reject oversized request bodies before parsing.
@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(
            {"success": False, "message": "Request body too large. Maximum size is 512 KB."},
            status_code=413,
        )
    return await call_next(request)


# SECURITY-FIX-5: Security response headers on all responses.
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https://unpkg.com https://*.arcgisonline.com https://tile.openstreetmap.org https://*.openstreetmap.org; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return response


_store = BoundaryStore(_settings.boundary_store_path)

_LAYER_ATTRS = [
    "counties_table", "towns_table", "huc8_table", "huc10_table", "huc12_table",
    "streams_table", "wipwl_table", "dac_table", "ssurgo_table",
]


_LEAD_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_lead_id(value: str) -> str:
    """Return the lead_id if it matches the safe pattern, else empty string."""
    v = (value or "").strip()
    return v if _LEAD_ID_RE.match(v) else ""


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    key: Optional[str] = Query(default=None),
) -> None:
    settings = get_settings()
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="Server auth is not configured")
    submitted_key = x_api_key or key or ""
    # SECURITY-FIX-2: Constant-time comparison prevents timing side-channel.
    if not hmac.compare_digest(submitted_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# SECURITY-FIX-10: /save-boundary accepts master API key OR per-lead draw token.
async def require_save_auth(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    key: Optional[str] = Query(default=None),
    x_draw_token: Optional[str] = Header(default=None, alias="X-Draw-Token"),
    x_draw_exp: Optional[str] = Header(default=None, alias="X-Draw-Exp"),
    x_lead_id: Optional[str] = Header(default=None, alias="X-Lead-ID"),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Accept either:
    - Master API key (X-API-Key header or ?key= query param) — existing method
    - Draw token (X-Draw-Token + X-Draw-Exp + X-Lead-ID headers) — new per-lead method
    """
    # Try master API key first (backward compat)
    if settings.api_key:
        submitted_key = x_api_key or key or ""
        if hmac.compare_digest(submitted_key, settings.api_key):
            request.state.auth_lead_id = None
            return  # Authenticated via master key

    # Try draw token
    if x_draw_token and x_draw_exp and x_lead_id:
        try:
            exp_int = int(x_draw_exp)
            safe_lid = _safe_lead_id(x_lead_id)
            if safe_lid and draw_tokens.verify(safe_lid, x_draw_token, exp_int):
                request.state.auth_lead_id = safe_lid
                return  # Authenticated via draw token
        except (ValueError, TypeError):
            pass

    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --------------------------------------------------------------- pipeline ---
def _boundary_status(source: Optional[str]) -> str:
    s = (source or "").lower()
    if "upload" in s:
        return "Uploaded"
    if "office" in s:
        return "Office Digitized"
    return "Drawn"


def _location_point(lead: Dict[str, Any]):
    latlng = geo.parse_latlong(lead.get("ProblemLocation"))
    if latlng is None and lead.get("GPSLatitude") is not None and lead.get("GPSLongitude") is not None:
        try:
            latlng = (float(lead["GPSLatitude"]), float(lead["GPSLongitude"]))
        except (TypeError, ValueError):
            latlng = None
    return geo.point_to_geometry(*latlng) if latlng else None


def _resolve_boundary(lead: Dict[str, Any], warnings):
    """Return (geom, source, acres) from payload, else the backend store."""
    if lead.get("BoundaryGeoJSON"):
        g, valid, w = geo.validate_geojson_polygon(lead["BoundaryGeoJSON"])
        if valid:
            return g, lead.get("BoundarySource") or "Sales drawn boundary", None
        if w:
            warnings.append(w)
    if lead.get("BoundaryWKT"):
        g, valid, w = geo.parse_boundary_wkt(lead["BoundaryWKT"])
        if valid:
            return g, lead.get("BoundarySource") or "Uploaded boundary", None
        if w:
            warnings.append(w)
    g, source, record = boundary_mod.load_database_geometry(lead.get("LeadID", ""), get_settings())
    if g is not None:
        return g, source, (record or {}).get("BoundaryAreaAcres")
    g, source, record = boundary_mod.load_stored_geometry(lead.get("LeadID", ""), _store)
    if g is not None:
        return g, source, (record or {}).get("BoundaryAreaAcres")
    return None, None, None


def _process(lead: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    engine = get_engine(settings.database_url)
    engine_present = database_reachable(engine)
    if not engine_present:
        engine = None

    try:
        warnings: list = []
        point_geom = _location_point(lead)
        boundary_geom, boundary_source, boundary_acres = _resolve_boundary(lead, warnings)

        if boundary_geom is None and point_geom is None:
            facts = gis_lookup.default_facts()
            facts["ProcessingError"] = (
                "No usable location: a valid boundary or GPS latitude/longitude is required."
            )
            scoring_result = scoring.evaluate(lead, facts, "none", "none", warnings, engine_present, settings)
            return report_data.build_response(lead, facts, [], scoring_result, {}, {})

        if boundary_geom is not None and boundary_acres is None:
            boundary_acres = geo.calculate_area_acres(boundary_geom, settings.area_crs, warnings)

        analysis_geom, analysis_source = geo.choose_analysis_geometry(
            boundary_geom, boundary_source, point_geom, settings, warnings
        )
        locate_geom = boundary_geom if boundary_geom is not None else point_geom
        geom_kind = "boundary" if boundary_geom is not None else "point"

        facts, gis_warnings = gis_lookup.run_lookups(
            locate_geom, analysis_geom, analysis_source, engine, settings,
            lead_id=lead.get("LeadID", "-"),
        )
        warnings.extend(gis_warnings)

        bmps, match_strength = bmp_rules.suggest_bmps(lead, facts, settings)
        scoring_result = scoring.evaluate(lead, facts, match_strength, geom_kind, warnings, engine_present, settings)
        calc = calculators.build_calculation(lead, boundary_acres, settings)

        boundary_info = {
            "BoundaryStatus": _boundary_status(boundary_source) if geom_kind == "boundary" else "Not Started",
            "BoundarySource": boundary_source or "GPS point only",
            "BoundaryAreaAcres": boundary_acres,
        }
        response = report_data.build_response(lead, facts, bmps, scoring_result, calc, boundary_info)
        # A signed, 24h boundary download link -- only meaningful when a boundary exists.
        if geom_kind == "boundary":
            response["BoundaryShareURL"] = share_links.build_share_url(lead.get("LeadID", ""), settings)
        return response
    except Exception as exc:  # never leak a 500 to AppSheet's automation
        logger.exception("Unhandled error processing lead %s", lead.get("LeadID"))
        return report_data.error_response(lead, str(exc))


# --------------------------------------------------------------- endpoints ---
@app.get("/health")
def health() -> Dict[str, Any]:
    settings = get_settings()
    engine = get_engine(settings.database_url)
    reachable = database_reachable(engine)
    layers = {}
    if reachable:
        for attr in _LAYER_ATTRS:
            name = getattr(settings, attr)
            layers[name] = table_exists(engine, name)
    return {
        "status": "ok",
        "version": app.version,
        "database_configured": settings.database_url is not None,
        "database_reachable": reachable,
        "public_gis_lookups_enabled": settings.public_gis_lookups_enabled,
        "api_key_required": settings.api_key is not None,
        "projected_crs": settings.projected_crs,
        "dem_configured": settings.dem_tiles_dir is not None,
        "layers_loaded": layers,
    }


@app.post("/generate-draw-token")
async def generate_draw_token(
    body: dict,
    request: Request,
    _api_key: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """
    Generate a short-lived signed draw URL for a specific lead.
    Called by Apps Script when a lead is created/updated.
    The returned URL is stored in BoundaryDrawURL in the Sheet.
    """
    lead_id = _safe_lead_id(str(body.get("lead_id", "")))
    if not lead_id:
        raise HTTPException(status_code=400, detail="Missing or invalid lead_id")

    def _coord(v, lo, hi):
        try:
            f = float(v)
            return f if lo <= f <= hi else None
        except (TypeError, ValueError):
            return None

    lat = _coord(body.get("lat"), -90, 90)
    lng = _coord(body.get("lng"), -180, 180)

    token, exp = draw_tokens.generate(lead_id, ttl_days=settings.draw_token_ttl_days)

    # Build the full draw URL with the token (no master API key in URL)
    base = str(request.base_url).rstrip("/")
    url = f"{base}/draw_boundary.html?lead_id={lead_id}&token={token}&exp={exp}"
    if lat is not None:
        url += f"&lat={lat}"
    if lng is not None:
        url += f"&lng={lng}"

    return {
        "draw_url": url,
        "lead_id": lead_id,
        "exp": exp,
        "ttl_days": settings.draw_token_ttl_days,
    }


@app.post("/save-boundary", response_model=BoundarySaveResponse, dependencies=[Depends(require_save_auth)])
def save_boundary(req: BoundarySaveRequest, request: Request) -> Dict[str, Any]:
    auth_lead = getattr(request.state, "auth_lead_id", None)
    if auth_lead is not None and _safe_lead_id(str(req.LeadID)) != auth_lead:
        raise HTTPException(status_code=403, detail="Token is not valid for this LeadID")
    return boundary_mod.save_boundary(req.model_dump(), get_settings(), _store)


@app.post("/process-lead", response_model=LeadProcessResponse, dependencies=[Depends(require_api_key)])
def process_lead(lead: LeadProcessRequest) -> Dict[str, Any]:
    return _process(lead.model_dump())


# SECURITY-FIX-6: Debug route only registered when DEBUG_ENDPOINTS_ENABLED=true.
if _settings.debug_endpoints_enabled:
    @app.post("/debug/process-sample", response_model=LeadProcessResponse, dependencies=[Depends(require_api_key)])
    def process_sample() -> Dict[str, Any]:
        payload = json.loads(SAMPLE_PAYLOAD.read_text(encoding="utf-8"))
        return _process(payload)


def _kml_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value or "boundary"))
    safe = safe.strip("._")[:80] or "boundary"
    return f"{safe}.kml"


@app.get("/boundary/{lead_id}.kml")
def boundary_kml(
    lead_id: str,
    exp: Optional[str] = Query(default=None),
    sig: Optional[str] = Query(default=None),
) -> Response:
    """Export a saved boundary as KML (XML). Requires a valid, unexpired signed
    link (?exp=&sig=) so the link dies after the TTL. Source order: local cache
    -> PostGIS -> Field_Boundaries sheet (via AppSheet API)."""
    settings = get_settings()
    ok, reason = share_links.verify(lead_id, exp, sig, settings)
    if not ok:
        msg = "This download link has expired." if reason == "expired" else "Invalid or missing download link."
        if reason == "not-configured":
            msg = "Boundary downloads are not configured."
        return Response(
            content=f'<?xml version="1.0" encoding="UTF-8"?>\n<error>{escape(msg)}</error>',
            media_type="application/xml", status_code=403,
        )
    geom, _, record = boundary_mod.load_stored_geometry(lead_id, _store)
    # AGENT-M5: Return cached KML if available, skipping AppSheet read on warm cache.
    if record and record.get("CachedKML"):
        return Response(
            content=record["CachedKML"],
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="{_kml_filename(lead_id)}"'},
        )
    if geom is None:
        geom, _, record = boundary_mod.load_database_geometry(lead_id, settings)
    if geom is None:
        geom, _, record = boundary_mod.find_boundary_via_appsheet(lead_id, settings)
    if geom is None:
        return Response(
            content=(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<error>No boundary found for {escape(lead_id)}</error>"
            ),
            media_type="application/xml", status_code=404,
        )
    return Response(
        content=geo.geometry_to_kml(
            geom,
            lead_id,
            annotations=(record or {}).get("BoundaryAnnotationsGeoJSON"),
        ),
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{_kml_filename(lead_id)}"'},
    )


# Static web (Leaflet draw page). Mounted last so API routes take precedence.
app.mount("/", StaticFiles(directory=get_settings().web_dir, html=True), name="web")
