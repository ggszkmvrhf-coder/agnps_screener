# AGENT-L3: lead_id threaded through run_live_public_lookups for log correlation.
"""Live public GIS lookups used when PostGIS is not configured.

These calls keep the Render deployment cheap for v1. They query authoritative
public services at processing time and fail soft: a service outage becomes a
human-review warning, never a failed lead.
"""
import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from . import state_registry
from .settings import FT_PER_M, Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level NY constants — kept verbatim so PostGIS branch and any other
# code that imports them directly continues to work unchanged.
# ---------------------------------------------------------------------------
USGS_WBD_HUC12_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6/query"
NYSDEC_WIPWL_BASE_URL = (
    "https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/"
    "Waterbody_Inventory_List/FeatureServer"
)
CENSUS_COORDINATES_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
NY_DAC_URL = "https://data.ny.gov/resource/2e6c-s6fp.json"
USDA_SDA_TABULAR_URL = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"
USDA_SDA_WFS_URL = "https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDMWGS84Geographic.wfs"

WIPWL_LAYERS = [
    (2, "Lake"),
    (3, "Estuary"),
    (4, "Stream"),
    (5, "Shoreline"),
]


def run_live_public_lookups(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    lead_id: str = "-",
) -> Dict[str, Any]:
    """Return GIS fact updates from public services.

    Phase 1: run lookup_county_town synchronously so state detection is
    available before the remaining lookups run.

    Phase 2: remaining lookups (huc12, soils, waterbody, dac) run concurrently
    in a ThreadPoolExecutor(max_workers=4), with state passed to waterbody+dac.

    Any lookup that raises an exception is caught, a warning is appended, and
    an empty dict is used as its result so a single service failure cannot block
    the others.

    The multi_state_routing_enabled kill-switch on settings forces state="NY"
    when False, preserving legacy behaviour without a code deploy.
    """
    # ------------------------------------------------------------------
    # Phase 1: county/town lookup — must complete first for state detection.
    # ------------------------------------------------------------------
    try:
        county_town_facts = lookup_county_town(locate_geom, settings, warnings)
    except Exception as exc:
        logger.warning("lead_id=%s Public GIS lookup 'county_town' raised an exception: %s", lead_id, exc)
        warnings.append(f"Public GIS lookup 'county_town' failed with error: {exc}")
        county_town_facts = {}

    # Derive routing state from detected StateAuto.
    detected_state = county_town_facts.get("StateAuto")
    if getattr(settings, "multi_state_routing_enabled", True):
        routing_state = state_registry.normalize_state(detected_state)
    else:
        routing_state = "NY"
        if detected_state and detected_state != "NY":
            logger.info(
                "lead_id=%s multi_state_routing_enabled=False; forcing state=NY "
                "(detected=%s)",
                lead_id,
                detected_state,
            )

    # ------------------------------------------------------------------
    # Phase 2: remaining lookups in parallel.
    # ------------------------------------------------------------------
    lookup_tasks: Dict[str, Any] = {
        "huc12": lambda: lookup_huc12(locate_geom, settings, warnings),
        "soils": lambda: lookup_soils(analysis_geom, settings, warnings),
        "waterbody": lambda s=routing_state: lookup_waterbody(locate_geom, settings, warnings, s),
        "dac": lambda s=routing_state: lookup_dac(locate_geom, analysis_geom, settings, warnings, s),
    }

    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_name = {executor.submit(fn): name for name, fn in lookup_tasks.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.warning(
                    "lead_id=%s Public GIS lookup '%s' raised an exception: %s",
                    lead_id, name, exc,
                )
                warnings.append(f"Public GIS lookup '{name}' failed with error: {exc}")
                results[name] = {}

    facts: Dict[str, Any] = {}
    facts.update(county_town_facts)
    for partial in results.values():
        facts.update(partial)
    return facts


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _timeout(settings: Settings) -> float:
    return float(getattr(settings, "public_api_timeout_sec", 12.0))


def _read_url(url: str, settings: Settings, label: str, warnings: List[str]) -> Optional[bytes]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AgNPS-Candidate-Screener/0.2"},
        )
        with urllib.request.urlopen(req, timeout=_timeout(settings)) as resp:
            return resp.read()
    except Exception as exc:
        warnings.append(f"{label} public API lookup failed: {exc}")
        logger.warning("%s public API lookup failed: %s", label, exc)
        return None


def _json_get(
    url: str,
    params: Dict[str, Any],
    settings: Settings,
    label: str,
    warnings: List[str],
) -> Optional[Any]:
    full_url = url + "?" + urllib.parse.urlencode(params)
    raw = _read_url(full_url, settings, label, warnings)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        warnings.append(f"{label} public API returned unreadable JSON: {exc}")
        return None


def _form_post(
    url: str,
    fields: Dict[str, str],
    settings: Settings,
    label: str,
    warnings: List[str],
) -> Optional[Any]:
    try:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": "AgNPS-Candidate-Screener/0.2",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=_timeout(settings)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        warnings.append(f"{label} public API lookup failed: {exc}")
        logger.warning("%s public API lookup failed: %s", label, exc)
        return None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _lookup_point(geom: BaseGeometry) -> Tuple[float, float]:
    """Return lon/lat for point-in-polygon style service queries."""
    if isinstance(geom, Point):
        p = geom
    else:
        try:
            p = geom.representative_point()
        except Exception:
            p = geom.centroid
    return float(p.x), float(p.y)


def _lookup_points(geom: BaseGeometry) -> List[Tuple[float, float]]:
    """Return a few lon/lat samples so edge-near features are less likely missed."""
    if isinstance(geom, Point):
        return [(float(geom.x), float(geom.y))]

    points: List[Tuple[float, float]] = []

    def add_point(candidate: BaseGeometry) -> None:
        if candidate is None or candidate.is_empty:
            return
        key = (round(float(candidate.x), 6), round(float(candidate.y), 6))
        if key not in points:
            points.append(key)

    try:
        add_point(geom.representative_point())
    except Exception:
        pass
    try:
        add_point(geom.centroid)
    except Exception:
        pass

    minx, miny, maxx, maxy = geom.bounds
    midx = (minx + maxx) / 2
    midy = (miny + maxy) / 2
    for x, y in ((minx, midy), (maxx, midy), (midx, miny), (midx, maxy)):
        add_point(Point(x, y))
    return points[:6]


def _transformer(settings: Settings):
    try:
        from pyproj import Transformer

        return Transformer.from_crs(4326, settings.projected_crs, always_xy=True).transform
    except Exception:
        return None


def _buffer_geom_feet(geom: BaseGeometry, feet: float, settings: Settings) -> BaseGeometry:
    try:
        from pyproj import Transformer

        fwd = Transformer.from_crs(4326, settings.projected_crs, always_xy=True).transform
        rev = Transformer.from_crs(settings.projected_crs, 4326, always_xy=True).transform
        return shp_transform(rev, shp_transform(fwd, geom).buffer(feet / FT_PER_M))
    except Exception:
        return geom


def _distance_ft(a: BaseGeometry, b: BaseGeometry, settings: Settings) -> Optional[float]:
    tx = _transformer(settings)
    if tx is None:
        return None
    try:
        return round(shp_transform(tx, a).distance(shp_transform(tx, b)) * FT_PER_M, 1)
    except Exception:
        return None


def _overlap_area_m2(a: BaseGeometry, b: BaseGeometry, settings: Settings) -> float:
    tx = _transformer(settings)
    if tx is None:
        return 0.0
    try:
        inter = a.intersection(b)
        if inter.is_empty:
            return 0.0
        return float(shp_transform(tx, inter).area)
    except Exception:
        return 0.0


def _arcgis_geom_to_shapely(value: Dict[str, Any]) -> Optional[BaseGeometry]:
    if not value:
        return None
    if "x" in value and "y" in value:
        return Point(float(value["x"]), float(value["y"]))
    if "paths" in value:
        lines = []
        for path in value.get("paths") or []:
            if len(path) >= 2:
                lines.append(LineString([(float(x), float(y)) for x, y in path]))
        if not lines:
            return None
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    if "rings" in value:
        polys = []
        for ring in value.get("rings") or []:
            if len(ring) >= 4:
                try:
                    polys.append(Polygon([(float(x), float(y)) for x, y in ring]).buffer(0))
                except Exception:
                    continue
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return None


def _pick_attr(row: Dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = lowered.get(name.lower())
        if val not in (None, "", " "):
            return val
    return default


def _is_designated_dac(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and not text.startswith("not ") and "designated" in text


# ---------------------------------------------------------------------------
# Census county/town  (now also detects StateAuto)
# ---------------------------------------------------------------------------
def lookup_county_town(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"CountyAuto": None, "TownAuto": None, "StateAuto": None}
    lon, lat = _lookup_point(geom)
    data = _json_get(
        CENSUS_COORDINATES_URL,
        {
            "x": lon,
            "y": lat,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "format": "json",
        },
        settings,
        "Census county/town",
        warnings,
    )
    geos = (((data or {}).get("result") or {}).get("geographies") or {})
    counties = geos.get("Counties") or []
    towns = geos.get("County Subdivisions") or []
    states = geos.get("States") or []

    if counties:
        out["CountyAuto"] = counties[0].get("BASENAME") or counties[0].get("NAME")
    else:
        warnings.append("Live Census lookup did not return a county.")

    if towns:
        out["TownAuto"] = towns[0].get("BASENAME") or towns[0].get("NAME")
    else:
        warnings.append("Live Census lookup did not return a town/county subdivision.")

    # --- State detection (soft fail: never raises, sets StateAuto=None on miss) ---
    state_abbr: Optional[str] = None

    # Try States geography first.
    if states:
        raw = states[0].get("STUSAB")
        if raw:
            state_abbr = str(raw).strip().upper()

    # Fallback: STUSAB from Counties geography.
    if not state_abbr and counties:
        raw = counties[0].get("STUSAB")
        if raw:
            state_abbr = str(raw).strip().upper()

    # Fallback: numeric STATE FIPS -> abbreviation lookup.
    if not state_abbr and counties:
        fips = str(counties[0].get("STATE") or "").strip().zfill(2)
        state_abbr = state_registry.STATE_FIPS_TO_ABBR.get(fips)

    if state_abbr:
        out["StateAuto"] = state_abbr
    else:
        warnings.append(
            "Live Census lookup could not determine the US state from the coordinates."
        )

    return out


# ---------------------------------------------------------------------------
# USGS HUC12
# ---------------------------------------------------------------------------
def lookup_huc12(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    out = {"HUC8": None, "HUC10": None, "HUC12": None, "HUC12Name": None}
    lon, lat = _lookup_point(geom)
    data = _json_get(
        USGS_WBD_HUC12_URL,
        {
            "f": "json",
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
        },
        settings,
        "USGS HUC12",
        warnings,
    )
    features = (data or {}).get("features") or []
    if not features:
        warnings.append("Live USGS WBD lookup did not return a HUC12.")
        return out
    attrs = features[0].get("attributes") or {}
    huc12 = str(_pick_attr(attrs, ["huc12", "HUC12"]) or "")
    out["HUC12"] = huc12 or None
    out["HUC10"] = huc12[:10] if len(huc12) >= 10 else None
    out["HUC8"] = huc12[:8] if len(huc12) >= 8 else None
    out["HUC12Name"] = _pick_attr(attrs, ["name", "NAME", "hu_12_name"])
    return out


# ---------------------------------------------------------------------------
# Waterbody lookup — router + per-provider implementations
# ---------------------------------------------------------------------------

def lookup_waterbody(
    geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    state: str = "NY",
) -> Dict[str, Any]:
    """Route waterbody lookup to the correct provider for the given state."""
    cfg = state_registry.get_state_config(state)
    src = cfg.waterbody
    if src.provider == "arcgis_featureserver":
        return _waterbody_featureserver(geom, settings, warnings, src)
    if src.provider == "arcgis_mapserver":
        return _waterbody_mapserver(geom, settings, warnings, src)
    # Unknown provider: warn and return empty output.
    warnings.append(
        f"Waterbody provider '{src.provider}' for state '{state}' is not implemented."
    )
    return {
        "NearestWaterbodyName": None,
        "NearestWaterbodyType": None,
        "DistanceToWaterbodyFt": None,
        "WIPWLNearby": False,
        "WIPWLSummary": None,
    }


def _waterbody_featureserver(
    geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    src: "state_registry.WaterbodySource",
) -> Dict[str, Any]:
    """NY-style ArcGIS FeatureServer waterbody lookup.

    This is the CURRENT lookup_wipwl_waterbody body moved verbatim and
    parameterized by WaterbodySource so NY behaviour is byte-for-byte
    identical to pre-routing.
    """
    out = {
        "NearestWaterbodyName": None,
        "NearestWaterbodyType": None,
        "DistanceToWaterbodyFt": None,
        "WIPWLNearby": False,
        "WIPWLSummary": None,
    }
    candidates = []

    # AGENT-H4: Sample points capped to limit max HTTP calls.
    sample_points = list(_lookup_points(geom))[:src.max_sample_points]
    for lon, lat in sample_points:
        for layer_id, layer_type in src.layers:
            data = _json_get(
                f"{src.base_url}/{layer_id}/query",
                {
                    "f": "json",
                    "geometry": f"{lon},{lat}",
                    "geometryType": "esriGeometryPoint",
                    "inSR": 4326,
                    "outSR": 4326,
                    "distance": settings.wipwl_search_radius_ft,
                    "units": "esriSRUnit_Foot",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "returnGeometry": "true",
                },
                settings,
                f"NYSDEC WI/PWL {layer_type}",
                warnings,
            )
            for feature in (data or {}).get("features") or []:
                attrs = feature.get("attributes") or {}
                fgeom = _arcgis_geom_to_shapely(feature.get("geometry") or {})
                dist = _distance_ft(geom, fgeom, settings) if fgeom is not None else None
                candidates.append((dist if dist is not None else 1e12, layer_type, attrs))

    if not candidates:
        warnings.append(
            f"No live NYSDEC WI/PWL waterbody found within "
            f"{settings.wipwl_search_radius_ft:.0f} ft."
        )
        return out

    dist, layer_type, attrs = sorted(candidates, key=lambda x: x[0])[0]
    name = _pick_attr(attrs, src.name_fields)
    category = _pick_attr(attrs, src.category_fields)
    water_class = _pick_attr(attrs, src.class_fields)
    factsheet = _pick_attr(attrs, src.factsheet_fields)
    assessed = _pick_attr(attrs, src.date_fields)
    descr = _pick_attr(attrs, ["DESCRIPT", "DESCRIPTION"])

    parts = []
    if name:
        parts.append(f"Waterbody: {name}")
    if category:
        parts.append(f"Assessment/category: {category}")
    if water_class:
        parts.append(f"Class: {water_class}")
    if assessed:
        parts.append(f"Assessed: {assessed}")
    if descr:
        parts.append(f"Description: {descr}")
    if dist < 1e12:
        parts.append(f"~{round(dist):.0f} ft away")
    if factsheet:
        parts.append(f"Factsheet: {factsheet}")

    out["NearestWaterbodyName"] = name
    out["NearestWaterbodyType"] = layer_type if not category else f"{layer_type}: {category}"
    out["DistanceToWaterbodyFt"] = None if dist >= 1e12 else dist
    out["WIPWLNearby"] = True
    out["WIPWLSummary"] = "; ".join(parts) or f"WI/PWL {layer_type} feature nearby."
    return out


def _waterbody_mapserver(
    geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    src: "state_registry.WaterbodySource",
) -> Dict[str, Any]:
    """EPA ATTAINS-style ArcGIS MapServer waterbody lookup (DE and similar).

    Uses the same _json_get + ArcGIS point-query pattern as _waterbody_featureserver.
    The base path differs: {base}/{layer}/query (MapServer convention).
    When src.where is set it is sent as a server-side filter.
    When src.dedupe_field is set, candidates are deduplicated on that key.
    When src.class_fields / src.date_fields are empty, those summary fragments
    are skipped.

    WIPWLSummary parity: DE output always contains the substring "Waterbody:"
    (when a name is found) and either "status" or "assessment" (from the
    status/category fragment) so scoring._wipwl greps work identically for NY
    and DE leads.
    """
    out = {
        "NearestWaterbodyName": None,
        "NearestWaterbodyType": None,
        "DistanceToWaterbodyFt": None,
        "WIPWLNearby": False,
        "WIPWLSummary": None,
    }
    candidates = []
    seen_dedupe: set = set()

    sample_points = list(_lookup_points(geom))[:src.max_sample_points]
    for lon, lat in sample_points:
        for layer_id, layer_type in src.layers:
            params: Dict[str, Any] = {
                "f": "json",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "outSR": 4326,
                "distance": settings.wipwl_search_radius_ft,
                "units": "esriSRUnit_Foot",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
            }
            if src.where:
                params["where"] = src.where
            data = _json_get(
                f"{src.base_url}/{layer_id}/query",
                params,
                settings,
                f"ATTAINS {layer_type}",
                warnings,
            )
            for feature in (data or {}).get("features") or []:
                attrs = feature.get("attributes") or {}

                # Deduplication by assessmentunitidentifier (or any dedupe_field).
                if src.dedupe_field:
                    dedup_val = _pick_attr(attrs, [src.dedupe_field])
                    if dedup_val is not None:
                        if dedup_val in seen_dedupe:
                            continue
                        seen_dedupe.add(dedup_val)

                fgeom = _arcgis_geom_to_shapely(feature.get("geometry") or {})
                dist = _distance_ft(geom, fgeom, settings) if fgeom is not None else None
                candidates.append((dist if dist is not None else 1e12, layer_type, attrs))

    if not candidates:
        warnings.append(
            f"No ATTAINS waterbody found within "
            f"{settings.wipwl_search_radius_ft:.0f} ft."
        )
        return out

    dist, layer_type, attrs = sorted(candidates, key=lambda x: x[0])[0]
    name = _pick_attr(attrs, src.name_fields)
    category = _pick_attr(attrs, src.category_fields)
    status = _pick_attr(attrs, src.status_fields)
    cycle = _pick_attr(attrs, src.cycle_fields)
    # class_fields and date_fields are empty for DE — skip those fragments.
    water_class = _pick_attr(attrs, src.class_fields) if src.class_fields else None
    assessed = _pick_attr(attrs, src.date_fields) if src.date_fields else None
    factsheet = _pick_attr(attrs, src.factsheet_fields) if src.factsheet_fields else None

    parts = []
    if name:
        parts.append(f"Waterbody: {name}")
    # Emit a fragment containing "status" so scoring._wipwl matches correctly.
    if status:
        parts.append(f"Overall status: {status}")
    elif category:
        parts.append(f"Assessment/category: {category}")
    # Additional detail.
    if category and status:
        # Both present: add category as a second fragment.
        parts.append(f"Category: {category}")
    if water_class:
        parts.append(f"Class: {water_class}")
    if cycle:
        parts.append(f"Reporting cycle: {cycle}")
    if assessed:
        parts.append(f"Assessed: {assessed}")
    if dist < 1e12:
        parts.append(f"~{round(dist):.0f} ft away")
    if factsheet:
        parts.append(f"Factsheet: {factsheet}")

    out["NearestWaterbodyName"] = name
    out["NearestWaterbodyType"] = layer_type if not category else f"{layer_type}: {category}"
    out["DistanceToWaterbodyFt"] = None if dist >= 1e12 else dist
    out["WIPWLNearby"] = True
    out["WIPWLSummary"] = "; ".join(parts) or f"ATTAINS {layer_type} assessment nearby."
    return out


# ---------------------------------------------------------------------------
# Backward-compatible alias — existing callers (PostGIS branch, tests) that
# call lookup_wipwl_waterbody directly continue to work unchanged.
# ---------------------------------------------------------------------------
def lookup_wipwl_waterbody(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    """NY-only waterbody lookup (backward-compatible alias).

    This preserves the original public API so any code that calls
    lookup_wipwl_waterbody directly is unaffected.  New code should call
    lookup_waterbody(geom, settings, warnings, state) instead.
    """
    return lookup_waterbody(geom, settings, warnings, state="NY")


# ---------------------------------------------------------------------------
# DAC lookup — router + per-provider implementations
# ---------------------------------------------------------------------------

def lookup_dac(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    state: str = "NY",
) -> Dict[str, Any]:
    """Route DAC lookup to the correct provider for the given state."""
    cfg = state_registry.get_state_config(state)
    src = cfg.dac
    if src.provider == "socrata":
        return _dac_socrata(locate_geom, analysis_geom, settings, warnings, src)
    if src.provider == "arcgis_featureserver":
        return _dac_arcgis_ej(locate_geom, analysis_geom, settings, warnings, src)
    warnings.append(
        f"DAC provider '{src.provider}' for state '{state}' is not implemented."
    )
    return {"DACIntersecting": False, "DACNearby": False, "DACSource": None}


def _dac_socrata(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    src: "state_registry.DacSource",
) -> Dict[str, Any]:
    """NY DAC lookup via Socrata (data.ny.gov).

    This is the original lookup_dac body moved verbatim. Both injection guards
    (coordinate-range check and single-quote WKT rejection) are preserved
    exactly as they were.
    """
    out: Dict[str, Any] = {
        "DACIntersecting": False,
        "DACNearby": False,
        "DACSource": "NY DAC (data.ny.gov)",
    }
    lon, lat = _lookup_point(locate_geom)

    # SECURITY-FIX-3: Validate coordinates and reject single-quoted WKT before SoQL interpolation.
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        logger.warning(
            "DAC lookup: coordinate out of range lon=%s lat=%s — skipping", lon, lat
        )
        return {}

    point_wkt = f"POINT ({lon} {lat})"

    if "'" in point_wkt:
        logger.warning("DAC lookup: WKT contains single quote — skipping to prevent injection")
        return {}

    point_rows = _query_dac(
        f"intersects(the_geom, '{point_wkt}')",
        src.base_url,
        settings,
        warnings,
    )
    if point_rows:
        out["DACIntersecting"] = any(
            _is_designated_dac(r.get(src.designation_field or "dac_designation"))
            for r in point_rows
        )
        out["DACNearby"] = out["DACIntersecting"]

    if not out["DACNearby"]:
        nearby_geom = _buffer_geom_feet(
            analysis_geom,
            settings.dac_nearby_distance_ft,
            settings,
        )
        nearby_wkt = nearby_geom.wkt

        if "'" in nearby_wkt:
            logger.warning("DAC lookup: WKT contains single quote — skipping to prevent injection")
            return out

        where = (
            f"intersects(the_geom, '{nearby_wkt}') "
            "AND dac_designation = 'Designated as DAC'"
        )
        rows = _query_dac(where, src.base_url, settings, warnings)
        out["DACNearby"] = bool(rows)
    return out


def _dac_arcgis_ej(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
    src: "state_registry.DacSource",
) -> Dict[str, Any]:
    """Delaware EJScreen DAC lookup via ArcGIS FeatureServer.

    Queries the DE_EJScreen layer twice:
      1. Point query at the locate_geom centroid → DACIntersecting.
      2. Envelope query using the analysis_geom bounding box → DACNearby.

    A census tract is considered a DAC when EXCEED_COUNT_80 > 0.
    DACNearby is also True whenever DACIntersecting is True.
    """
    dac_source = "DE EJScreen EXCEED_COUNT_80>0"
    out: Dict[str, Any] = {
        "DACIntersecting": False,
        "DACNearby": False,
        "DACSource": dac_source,
    }
    lon, lat = _lookup_point(locate_geom)

    # Coordinate-range guard: mirror _dac_socrata to prevent out-of-range queries.
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        logger.warning(
            "DE EJScreen DAC lookup: coordinate out of range lon=%s lat=%s — skipping", lon, lat
        )
        warnings.append(
            f"DE EJScreen DAC lookup: coordinate out of range (lon={lon}, lat={lat}) — skipped."
        )
        return {"DACIntersecting": False, "DACNearby": False, "DACSource": dac_source}

    ej_field = src.designation_field or "EXCEED_COUNT_80"

    # ------------------------------------------------------------------
    # Query 1: point intersect at locate_geom → DACIntersecting
    # ------------------------------------------------------------------
    data = _json_get(
        src.base_url,
        {
            "f": "json",
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": ej_field,
            "returnGeometry": "false",
        },
        settings,
        "DE EJScreen",
        warnings,
    )
    features = (data or {}).get("features") or []
    intersecting = False
    for feat in features:
        attrs = feat.get("attributes") or {}
        field_val = attrs.get(ej_field)
        try:
            if int(field_val or 0) > 0:
                intersecting = True
                break
        except (TypeError, ValueError):
            pass
    out["DACIntersecting"] = intersecting
    # DACNearby is at minimum True whenever DACIntersecting is True.
    out["DACNearby"] = intersecting

    # ------------------------------------------------------------------
    # Query 2: envelope (bounding box) of analysis_geom → DACNearby
    # Only run when DACIntersecting is False; fail-soft on any error.
    # ------------------------------------------------------------------
    if not intersecting:
        try:
            minx, miny, maxx, maxy = analysis_geom.bounds
            nearby_data = _json_get(
                src.base_url,
                {
                    "f": "json",
                    "geometry": f"{minx},{miny},{maxx},{maxy}",
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": 4326,
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": ej_field,
                    "returnGeometry": "false",
                },
                settings,
                "DE EJScreen nearby",
                warnings,
            )
            nearby_features = (nearby_data or {}).get("features") or []
            for feat in nearby_features:
                attrs = feat.get("attributes") or {}
                field_val = attrs.get(ej_field)
                try:
                    if int(field_val or 0) > 0:
                        out["DACNearby"] = True
                        break
                except (TypeError, ValueError):
                    pass
        except Exception as exc:
            warnings.append(f"DE EJScreen nearby DAC lookup failed: {exc}")
            logger.warning("DE EJScreen nearby DAC lookup failed: %s", exc)
            # Leave DACNearby = DACIntersecting (already set above).

    return out


def _query_dac(
    where: str,
    url: str,
    settings: Settings,
    warnings: List[str],
) -> List[Dict[str, Any]]:
    data = _json_get(
        url,
        {
            "$limit": 3,
            "$select": "geoid,dac_designation,county,city_town",
            "$where": where,
        },
        settings,
        "NY DAC",
        warnings,
    )
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# USDA SDA soils
# ---------------------------------------------------------------------------
def lookup_soils(
    analysis_geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    out = {"DominantSoilDrainageClass": None, "DominantHydrologicSoilGroup": None}
    features = _sda_mapunit_features(analysis_geom, settings, warnings)
    if not features:
        warnings.append("Live USDA soil lookup did not return a soil map unit.")
        return out

    ranked = sorted(
        features,
        key=lambda f: _overlap_area_m2(f["geometry"], analysis_geom, settings),
        reverse=True,
    )
    mukeys = []
    for item in ranked[:10]:
        mukey = str(item.get("mukey") or "").strip()
        if mukey and mukey.isdigit() and mukey not in mukeys:
            mukeys.append(mukey)
    if not mukeys:
        warnings.append("Live USDA soil lookup returned map units without mukeys.")
        return out

    rows = _sda_component_rows(mukeys, settings, warnings)
    if not rows:
        warnings.append("Live USDA soil attributes were unavailable for returned map units.")
        return out

    dominant_mukey = mukeys[0]
    dominant_rows = [r for r in rows if str(r.get("mukey")) == dominant_mukey] or rows
    dominant_rows.sort(key=lambda r: _safe_float(r.get("comppct_r")), reverse=True)
    row = dominant_rows[0]
    out["DominantSoilDrainageClass"] = row.get("drainagecl")
    out["DominantHydrologicSoilGroup"] = row.get("hydgrp")
    return out


def _sda_mapunit_features(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> List[Dict[str, Any]]:
    minx, miny, maxx, maxy = geom.bounds
    if (maxx - minx) > 0.2 or (maxy - miny) > 0.2:
        lon, lat = _lookup_point(geom)
        minx, maxx = lon - 0.02, lon + 0.02
        miny, maxy = lat - 0.02, lat + 0.02
        warnings.append("Soil live lookup used a smaller centroid window because the geometry is large.")

    raw = _read_url(
        USDA_SDA_WFS_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "SERVICE": "WFS",
                "VERSION": "1.1.0",
                "REQUEST": "GetFeature",
                "TYPENAME": "mapunitpoly",
                "BBOX": f"{minx},{miny},{maxx},{maxy}",
            }
        ),
        settings,
        "USDA SDA spatial",
        warnings,
    )
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        warnings.append(f"Live USDA soil geometry response could not be parsed: {exc}")
        return []

    ns = {"ms": "http://mapserver.gis.umn.edu/mapserver", "gml": "http://www.opengis.net/gml"}
    out: List[Dict[str, Any]] = []
    for node in root.findall(".//ms:mapunitpoly", ns):
        mukey = _xml_text(node.find("ms:mukey", ns))
        polygons = []
        for coords in node.findall(".//gml:coordinates", ns):
            poly = _polygon_from_sda_coordinates(coords.text or "")
            if poly is not None:
                polygons.append(poly)
        if not mukey or not polygons:
            continue
        geom_value: BaseGeometry = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
        if geom_value.intersects(geom):
            out.append({"mukey": mukey, "geometry": geom_value})
    return out


def _polygon_from_sda_coordinates(value: str) -> Optional[Polygon]:
    # AGENT-H3: Coordinate bounds check and acreage sanity guard added.
    coords = []
    for pair in value.split():
        parts = pair.split(",")
        if len(parts) < 2:
            continue
        try:
            # SDA WFS advertises EPSG:4326 but emits coordinates as lat,lon.
            lat, lon = float(parts[0]), float(parts[1])
            coords.append((lon, lat))
        except ValueError:
            continue
    if len(coords) < 4:
        return None
    # Validate coordinate bounds before building the polygon.
    for lon, lat in coords:
        if not (-180.0 <= lon <= 180.0):
            logger.warning(
                "SDA coordinate out of longitude bounds: lon=%s (expected -180 to 180); "
                "discarding polygon.",
                lon,
            )
            return None
        if not (-90.0 <= lat <= 90.0):
            logger.warning(
                "SDA coordinate out of latitude bounds: lat=%s (expected -90 to 90); "
                "discarding polygon.",
                lat,
            )
            return None
    try:
        polygon = Polygon(coords).buffer(0)
    except Exception:
        return None
    # Sanity-check polygon area in degree-squared units.
    # >0.1 deg² is roughly >10,000 km² — catches continent-scale inverted polygons.
    if polygon.area > 0.1:
        logger.warning(
            "SDA polygon area %.6f deg² exceeds sanity threshold (0.1 deg²); "
            "likely inverted or erroneous coordinates — discarding polygon.",
            polygon.area,
        )
        return None
    return polygon


def _sda_component_rows(
    mukeys: List[str], settings: Settings, warnings: List[str]
) -> List[Dict[str, Any]]:
    quoted = ",".join(f"'{m}'" for m in mukeys if re.fullmatch(r"\d+", m))
    if not quoted:
        return []
    query = f"""
    SELECT TOP 30
      mu.mukey,
      mu.muname,
      c.compname,
      c.comppct_r,
      c.drainagecl,
      c.hydgrp
    FROM mapunit mu
    LEFT JOIN component c ON mu.mukey = c.mukey
    WHERE mu.mukey IN ({quoted})
    ORDER BY c.comppct_r DESC
    """
    data = _form_post(
        USDA_SDA_TABULAR_URL,
        {"QUERY": query, "FORMAT": "JSON+COLUMNNAME"},
        settings,
        "USDA SDA tabular",
        warnings,
    )
    table = (data or {}).get("Table") or []
    if len(table) < 2:
        return []
    headers = [str(h) for h in table[0]]
    rows = []
    for values in table[1:]:
        rows.append({headers[i]: values[i] if i < len(values) else None for i in range(len(headers))})
    return rows


def _xml_text(node) -> Optional[str]:
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
