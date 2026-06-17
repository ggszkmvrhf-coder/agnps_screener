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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from .settings import FT_PER_M, Settings

logger = logging.getLogger(__name__)

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
) -> Dict[str, Any]:
    """Return GIS fact updates from public services."""
    facts: Dict[str, Any] = {}
    facts.update(lookup_county_town(locate_geom, settings, warnings))
    facts.update(lookup_huc12(locate_geom, settings, warnings))
    facts.update(lookup_wipwl_waterbody(locate_geom, settings, warnings))
    facts.update(lookup_dac(locate_geom, analysis_geom, settings, warnings))
    facts.update(lookup_soils(analysis_geom, settings, warnings))
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


def _transformer(settings: Settings):
    try:
        from pyproj import Transformer

        return Transformer.from_crs(4326, settings.projected_crs, always_xy=True).transform
    except Exception:
        return None


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
# Census county/town
# ---------------------------------------------------------------------------
def lookup_county_town(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    out = {"CountyAuto": None, "TownAuto": None}
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
    if counties:
        out["CountyAuto"] = counties[0].get("BASENAME") or counties[0].get("NAME")
    else:
        warnings.append("Live Census lookup did not return a county.")
    if towns:
        out["TownAuto"] = towns[0].get("BASENAME") or towns[0].get("NAME")
    else:
        warnings.append("Live Census lookup did not return a town/county subdivision.")
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
# NYSDEC WI/PWL waterbodies
# ---------------------------------------------------------------------------
def lookup_wipwl_waterbody(
    geom: BaseGeometry, settings: Settings, warnings: List[str]
) -> Dict[str, Any]:
    out = {
        "NearestWaterbodyName": None,
        "NearestWaterbodyType": None,
        "DistanceToWaterbodyFt": None,
        "WIPWLNearby": False,
        "WIPWLSummary": None,
    }
    lon, lat = _lookup_point(geom)
    candidates = []

    for layer_id, layer_type in WIPWL_LAYERS:
        data = _json_get(
            f"{NYSDEC_WIPWL_BASE_URL}/{layer_id}/query",
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
    name = _pick_attr(attrs, ["WATERBODY", "WATER_NAME", "NAME", "PWL_NAME", "WB_NAME"])
    category = _pick_attr(attrs, ["WATERBODY_CATEGORY", "CATEGORY", "ASSESSMENT", "STATUS"])
    water_class = _pick_attr(attrs, ["CLASS", "WQS_CLASS"])
    factsheet = _pick_attr(attrs, ["FACTSHEET", "FACTSHEET_URL"])
    assessed = _pick_attr(attrs, ["CYCLE_LAST_ASSESSED", "LAST_ASSESSED"])
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


# ---------------------------------------------------------------------------
# NY DAC
# ---------------------------------------------------------------------------
def lookup_dac(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    settings: Settings,
    warnings: List[str],
) -> Dict[str, Any]:
    out = {"DACIntersecting": False, "DACNearby": False}
    lon, lat = _lookup_point(locate_geom)
    point_wkt = f"POINT ({lon} {lat})"

    point_rows = _query_dac(
        f"intersects(the_geom, '{point_wkt}')",
        settings,
        warnings,
    )
    if point_rows:
        out["DACIntersecting"] = any(_is_designated_dac(r.get("dac_designation")) for r in point_rows)
        out["DACNearby"] = out["DACIntersecting"]

    if not out["DACNearby"]:
        where = (
            f"intersects(the_geom, '{analysis_geom.wkt}') "
            "AND dac_designation = 'Designated as DAC'"
        )
        rows = _query_dac(where, settings, warnings)
        out["DACNearby"] = bool(rows)
    return out


def _query_dac(where: str, settings: Settings, warnings: List[str]) -> List[Dict[str, Any]]:
    data = _json_get(
        NY_DAC_URL,
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
    try:
        return Polygon(coords).buffer(0)
    except Exception:
        return None


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
