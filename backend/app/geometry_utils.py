"""Geometry helpers: parsing, buffering, validation, acreage, centroid.

All geometry is WGS84 (EPSG:4326). Area/buffer math is done in a projected /
equal-area CRS (configurable) then converted back. Everything fails soft: bad
input yields (None / warning), never an exception out of the API.
"""
import json
import logging
import re
from typing import Any, Optional, Tuple
from xml.sax.saxutils import escape

from shapely import wkt as shapely_wkt
from shapely.geometry import Point, mapping, shape
from shapely.geometry.polygon import orient
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

    warnings_list: list = []
    # AGENT-H5: orient() enforces CCW exterior ring on every saved polygon. C1 agent depends on this being here.
    geom = orient(geom, sign=1.0)
    if geom.area > 1.0:
        warnings_list.append("Boundary area is very large — please verify the polygon is correct.")

    if warnings_list:
        combined = "; ".join(warnings_list)
        warning = f"{warning}; {combined}".lstrip("; ") if warning else combined
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


_ANNOTATION_GEOM_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString"}
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _count_positions(coords: Any) -> int:
    if not isinstance(coords, list):
        return 0
    if coords and all(isinstance(v, (int, float)) for v in coords[:2]):
        return 1
    return sum(_count_positions(child) for child in coords)


def normalize_annotation_geojson(gj: Any, max_features: int = 75, max_positions: int = 5000) -> Tuple[Optional[dict], str]:
    """Sanitize optional map notes.

    Notes are intentionally limited to Point/MultiPoint/LineString/MultiLineString
    and kept separate from the boundary polygon so they cannot affect scoring.
    """
    if gj in (None, ""):
        return None, ""
    try:
        if isinstance(gj, str):
            gj = json.loads(gj)
        if not isinstance(gj, dict):
            return None, "Boundary notes were ignored because they were not GeoJSON."

        gtype = gj.get("type")
        if gtype == "FeatureCollection":
            raw_features = gj.get("features") or []
        elif gtype == "Feature":
            raw_features = [gj]
        else:
            raw_features = [{"type": "Feature", "properties": {}, "geometry": gj}]

        features = []
        positions = 0
        for feature in raw_features[:max_features]:
            if not isinstance(feature, dict):
                continue
            geom = feature.get("geometry")
            if not isinstance(geom, dict) or geom.get("type") not in _ANNOTATION_GEOM_TYPES:
                continue
            count = _count_positions(geom.get("coordinates"))
            if count <= 0:
                continue
            positions += count
            if positions > max_positions:
                return None, "Boundary notes were ignored because the drawing was too large."

            props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            color = props.get("color")
            clean_props = {"color": color} if isinstance(color, str) and _HEX_COLOR.fullmatch(color) else {}
            features.append({"type": "Feature", "properties": clean_props, "geometry": geom})

        if not features:
            return None, ""
        return {"type": "FeatureCollection", "features": features}, ""
    except Exception as exc:
        return None, f"Boundary notes were ignored because they could not be parsed: {exc}"


def _kml_ring(ring) -> str:
    return " ".join(f"{x},{y},0" for x, y in ring.coords)


def _kml_polygon(poly) -> str:
    outer = _kml_ring(poly.exterior)
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{_kml_ring(r)}"
        f"</coordinates></LinearRing></innerBoundaryIs>"
        for r in poly.interiors
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}"
        f"</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def _kml_color(hex_color: str) -> str:
    value = hex_color if isinstance(hex_color, str) and _HEX_COLOR.fullmatch(hex_color) else "#e6194b"
    return "ff" + value[5:7] + value[3:5] + value[1:3]


def _kml_coord(position: Any) -> Optional[str]:
    if not isinstance(position, list) or len(position) < 2:
        return None
    try:
        return f"{float(position[0])},{float(position[1])},0"
    except (TypeError, ValueError):
        return None


def _kml_coord_list(positions: Any) -> str:
    if not isinstance(positions, list):
        return ""
    return " ".join(coord for coord in (_kml_coord(pos) for pos in positions) if coord)


def _annotation_geometry_to_kml(geom: dict) -> str:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Point":
        coord = _kml_coord(coords)
        return f"<Point><coordinates>{coord}</coordinates></Point>" if coord else ""
    if gtype == "MultiPoint":
        parts = []
        for point in coords or []:
            coord = _kml_coord(point)
            if coord:
                parts.append(f"<Point><coordinates>{coord}</coordinates></Point>")
        return "<MultiGeometry>" + "".join(parts) + "</MultiGeometry>" if parts else ""
    if gtype == "LineString":
        coord_text = _kml_coord_list(coords)
        return f"<LineString><coordinates>{coord_text}</coordinates></LineString>" if coord_text else ""
    if gtype == "MultiLineString":
        parts = []
        for line in coords or []:
            coord_text = _kml_coord_list(line)
            if coord_text:
                parts.append(f"<LineString><coordinates>{coord_text}</coordinates></LineString>")
        return "<MultiGeometry>" + "".join(parts) + "</MultiGeometry>" if parts else ""
    return ""


def _annotations_to_kml(annotations: Any) -> Tuple[str, str]:
    normalized, _ = normalize_annotation_geojson(annotations)
    if not normalized:
        return "", ""
    styles = []
    placemarks = []
    for idx, feature in enumerate(normalized.get("features", []), start=1):
        props = feature.get("properties") or {}
        style_id = f"note{idx}"
        styles.append(
            f'<Style id="{style_id}"><LineStyle><color>{_kml_color(props.get("color"))}</color>'
            "<width>4</width></LineStyle><IconStyle><scale>0.9</scale></IconStyle></Style>"
        )
        body = _annotation_geometry_to_kml(feature.get("geometry") or {})
        if body:
            placemarks.append(
                f'<Placemark><name>Note {idx}</name><styleUrl>#{style_id}</styleUrl>{body}</Placemark>'
            )
    return "".join(styles), "".join(placemarks)


def geometry_to_kml(geom4326: BaseGeometry, name: str = "boundary", annotations: Any = None) -> str:
    """Serialize a WGS84 Polygon/MultiPolygon and optional notes to KML."""
    if geom4326.geom_type == "MultiPolygon":
        body = "<MultiGeometry>" + "".join(_kml_polygon(p) for p in geom4326.geoms) + "</MultiGeometry>"
    else:
        body = _kml_polygon(geom4326)
    safe_name = escape(str(name or "boundary"))
    styles, annotation_marks = _annotations_to_kml(annotations)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{safe_name}</name>{styles}<Placemark><name>Field boundary</name>{body}</Placemark>"
        f"{annotation_marks}</Document></kml>"
    )
