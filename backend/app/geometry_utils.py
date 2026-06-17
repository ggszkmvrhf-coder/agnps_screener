"""Geometry helpers: parsing, buffering, validation, acreage, centroid.

All geometry is WGS84 (EPSG:4326). Area/buffer math is done in a projected /
equal-area CRS (configurable) then converted back. Everything fails soft: bad
input yields (None / warning), never an exception out of the API.
"""
import json
import logging
from typing import Any, Optional, Tuple

from shapely import wkt as shapely_wkt
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from .settings import FT_PER_M, SQM_PER_ACRE, Settings

logger = logging.getLogger(__name__)

try:
    from pyproj import Transformer

    PYPROJ_OK = True
except Exception:  # pragma: no cover
    PYPROJ_OK = False


def parse_latlong(value: Any) -> Optional[Tuple[float, float]]:
    """Parse a lat/lng from AppSheet 'lat , lng' text, a dict, or a tuple.

    Returns (lat, lng) or None.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, dict):
            lat = value.get("lat") if value.get("lat") is not None else value.get("latitude")
            lng = value.get("lng") if value.get("lng") is not None else value.get("longitude")
            return float(lat), float(lng)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]), float(value[1])
        text = str(value).replace("(", "").replace(")", "").strip()
        sep = "," if "," in text else None
        parts = text.split(sep)
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    return None


def point_to_geometry(lat: float, lng: float) -> Point:
    """Shapely Point in (lon, lat) order, EPSG:4326."""
    return Point(float(lng), float(lat))


def buffer_point_feet(
    point4326: BaseGeometry, feet: float, projected_crs: int, warnings: list
) -> BaseGeometry:
    """Buffer a point by N feet, performing the buffer in a projected CRS."""
    if not PYPROJ_OK:
        warnings.append("pyproj unavailable; point used without a buffer.")
        return point4326
    try:
        fwd = Transformer.from_crs(4326, projected_crs, always_xy=True).transform
        rev = Transformer.from_crs(projected_crs, 4326, always_xy=True).transform
        projected = shp_transform(fwd, point4326)
        return shp_transform(rev, projected.buffer(feet / FT_PER_M))
    except Exception as exc:
        warnings.append(f"CRS transform failed while buffering point: {exc}")
        return point4326


def _geom_from_geojson(gj: Any) -> Optional[BaseGeometry]:
    if isinstance(gj, str):
        gj = json.loads(gj)
    if not isinstance(gj, dict):
        return None
    gtype = gj.get("type")
    if gtype == "FeatureCollection":
        feats = gj.get("features") or []
        return shape(feats[0]["geometry"]) if feats else None
    if gtype == "Feature":
        return shape(gj["geometry"])
    return shape(gj)


def validate_geojson_polygon(gj: Any) -> Tuple[Optional[BaseGeometry], bool, str]:
    """Parse + validate a GeoJSON polygon.

    Returns (geometry, is_valid, warning). A repairable self-intersection is
    fixed with buffer(0) and flagged in the warning rather than rejected.
    """
    try:
        geom = _geom_from_geojson(gj)
    except Exception as exc:
        return None, False, f"GeoJSON could not be parsed: {exc}"

    if geom is None or geom.is_empty:
        return None, False, "GeoJSON contained no geometry."
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None, False, f"Expected a Polygon, got {geom.geom_type}."

    warning = ""
    if not geom.is_valid:
        repaired = geom.buffer(0)
        if repaired.is_valid and not repaired.is_empty:
            geom = repaired
            warning = "Polygon had a minor topology issue and was auto-repaired."
        else:
            return None, False, "Polygon geometry is invalid and could not be repaired."
    return geom, True, warning


def calculate_area_acres(geom4326: BaseGeometry, area_crs: int, warnings: list) -> Optional[float]:
    if not PYPROJ_OK:
        warnings.append("pyproj unavailable; acreage not calculated.")
        return None
    try:
        fwd = Transformer.from_crs(4326, area_crs, always_xy=True).transform
        projected = shp_transform(fwd, geom4326)
        return round(projected.area / SQM_PER_ACRE, 2)
    except Exception as exc:
        warnings.append(f"CRS transform failed while computing acreage: {exc}")
        return None


def calculate_centroid(geom4326: BaseGeometry) -> Tuple[Optional[float], Optional[float]]:
    """Returns (lat, lng) of the centroid, or (None, None)."""
    try:
        c = geom4326.centroid
        return round(c.y, 6), round(c.x, 6)
    except Exception:
        return None, None


def geometry_to_wkt(geom: BaseGeometry) -> Optional[str]:
    try:
        return geom.wkt
    except Exception:
        return None


def choose_analysis_geometry(
    boundary_geom: Optional[BaseGeometry],
    boundary_source: Optional[str],
    point4326: Optional[BaseGeometry],
    settings: Settings,
    warnings: list,
) -> Tuple[Optional[BaseGeometry], str]:
    """Pick the geometry to run GIS lookups against.

    Preference: a valid drawn/uploaded boundary; else the GPS point buffered by
    the default radius. Returns (geometry, AnalysisGeometrySource label).
    """
    if boundary_geom is not None and not boundary_geom.is_empty:
        label = boundary_source or "Sales drawn boundary"
        return boundary_geom, label
    if point4326 is not None:
        buffered = buffer_point_feet(point4326, settings.point_buffer_ft, settings.projected_crs, warnings)
        return buffered, f"GPS point + {settings.point_buffer_ft:.0f} ft buffer"
    return None, "none"


def parse_boundary_wkt(value: str) -> Tuple[Optional[BaseGeometry], bool, str]:
    """Parse a WKT polygon (used when a boundary arrives as WKT not GeoJSON)."""
    try:
        geom = shapely_wkt.loads(value)
    except Exception as exc:
        return None, False, f"WKT could not be parsed: {exc}"
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None, False, f"Expected a Polygon, got {geom.geom_type}."
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom, True, ""


def geom_as_geojson(geom: BaseGeometry) -> dict:
    return mapping(geom)
