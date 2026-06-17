"""GIS lookups against PostGIS vector layers + local DEM/slope rasters.

Design rules (do not violate):
  * Input API coordinates are EPSG:4326 (lat/lon).
  * Distance / area / slope are computed in a PROJECTED CRS (settings.projected_crs,
    default EPSG:5070, meters). Layers are loaded already-projected by
    scripts/load_layers.py so spatial queries hit the GIST index.
  * Every lookup is independent and FAILS SOFT: on any problem it appends a
    human-readable string to `warnings` and returns Nones/False -- it never
    raises out of the orchestrator.
  * Attribute column names vary wildly between public datasets, so attributes
    are read defensively via pick_attr().

No AI is used to invent any water-quality fact. Everything returned is read
directly from the loaded layers or computed from rasters.
"""
import glob
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from .database import fetch_all, table_exists
from .settings import FT_PER_M, Settings

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.warp import transform_geom

    RASTERIO_OK = True
except Exception:  # pragma: no cover
    RASTERIO_OK = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def default_facts() -> Dict[str, Any]:
    """All auto-fact keys, defaulted so output shape is always complete."""
    return {
        "AnalysisGeometrySource": None,
        "CountyAuto": None,
        "TownAuto": None,
        "HUC8": None,
        "HUC10": None,
        "HUC12": None,
        "HUC12Name": None,
        "NearestWaterbodyName": None,
        "NearestWaterbodyType": None,
        "DistanceToWaterbodyFt": None,
        "WIPWLNearby": False,
        "WIPWLSummary": None,
        "DACIntersecting": False,
        "DACNearby": False,
        "DominantSoilDrainageClass": None,
        "DominantHydrologicSoilGroup": None,
        "MeanSlopePercent": None,
        "MaxSlopePercent": None,
        "ProcessingError": None,
    }


def pick_attr(
    row: Dict[str, Any], candidates: List[str], default: Any = None
) -> Any:
    """Case-insensitively return the first present, non-empty attribute."""
    lowered = {k.lower(): v for k, v in row.items()}
    for name in candidates:
        val = lowered.get(name.lower())
        if val not in (None, "", " "):
            return val
    return default


# Inline SQL fragment: input WKT (4326) reprojected into the analysis CRS.
def _g(settings: Settings) -> str:
    return f"ST_Transform(ST_GeomFromText(:wkt, :icrs), {settings.projected_crs})"


# ---------------------------------------------------------------------------
# Vector lookups
# ---------------------------------------------------------------------------
def lookup_county_town(engine, wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"CountyAuto": None, "TownAuto": None}
    gcol = settings.geom_column
    params = {"wkt": wkt, "icrs": settings.input_crs}

    if table_exists(engine, settings.counties_table):
        rows = fetch_all(
            engine,
            f"SELECT * FROM {settings.counties_table} "
            f"WHERE ST_Intersects({gcol}, {_g(settings)}) LIMIT 1",
            params,
        )
        if rows:
            out["CountyAuto"] = pick_attr(
                rows[0], ["NAME", "COUNTY", "COUNTYNAME", "NAMELSAD", "county", "county_name"]
            )
        else:
            warnings.append("Location did not fall inside any NY county polygon "
                            "(verify the point is within New York State).")
    else:
        warnings.append("County layer not loaded.")

    if table_exists(engine, settings.towns_table):
        rows = fetch_all(
            engine,
            f"SELECT * FROM {settings.towns_table} "
            f"WHERE ST_Intersects({gcol}, {_g(settings)}) LIMIT 1",
            params,
        )
        if rows:
            out["TownAuto"] = pick_attr(
                rows[0], ["NAME", "TOWN", "TOWNNAME", "MUNI", "NAMELSAD", "town", "town_name"]
            )
    else:
        warnings.append("Town/municipal layer not loaded.")

    return out


def _lookup_one_huc(engine, table, code_keys, name_keys, wkt, settings, warnings):
    if not table_exists(engine, table):
        warnings.append(f"{table} layer not loaded.")
        return None, None
    rows = fetch_all(
        engine,
        f"SELECT * FROM {table} "
        f"WHERE ST_Intersects({settings.geom_column}, {_g(settings)}) LIMIT 1",
        {"wkt": wkt, "icrs": settings.input_crs},
    )
    if not rows:
        return None, None
    return pick_attr(rows[0], code_keys), pick_attr(rows[0], name_keys)


def lookup_huc(engine, wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"HUC8": None, "HUC10": None, "HUC12": None, "HUC12Name": None}
    out["HUC8"], _ = _lookup_one_huc(
        engine, settings.huc8_table, ["HUC8", "huc8", "HUC_8"], ["NAME", "name"], wkt, settings, warnings
    )
    out["HUC10"], _ = _lookup_one_huc(
        engine, settings.huc10_table, ["HUC10", "huc10", "HUC_10"], ["NAME", "name"], wkt, settings, warnings
    )
    out["HUC12"], out["HUC12Name"] = _lookup_one_huc(
        engine, settings.huc12_table, ["HUC12", "huc12", "HUC_12"], ["NAME", "name", "HU_12_NAME"], wkt, settings, warnings
    )
    return out


def lookup_nearest_waterbody(engine, wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"NearestWaterbodyName": None, "NearestWaterbodyType": None, "DistanceToWaterbodyFt": None}
    if not table_exists(engine, settings.streams_table):
        warnings.append("Streams/waterbodies layer not loaded.")
        return out

    gcol = settings.geom_column
    rows = fetch_all(
        engine,
        f"""
        SELECT *, ST_Distance({gcol}, {_g(settings)}) AS dist_m
        FROM {settings.streams_table}
        WHERE ST_DWithin({gcol}, {_g(settings)}, :radius_m)
        ORDER BY dist_m ASC
        LIMIT 1
        """,
        {"wkt": wkt, "icrs": settings.input_crs, "radius_m": settings.waterbody_search_radius_m},
    )
    if not rows:
        warnings.append(
            f"No mapped waterbody found within {settings.waterbody_search_radius_ft:.0f} ft."
        )
        return out

    r = rows[0]
    out["NearestWaterbodyName"] = pick_attr(r, ["GNIS_NAME", "NAME", "gnis_name", "name", "WB_NAME"])
    out["NearestWaterbodyType"] = pick_attr(r, ["FTYPE", "WBTYPE", "TYPE", "ftype", "fcode", "FCODE"])
    dist_m = r.get("dist_m")
    if dist_m is not None:
        out["DistanceToWaterbodyFt"] = round(float(dist_m) * FT_PER_M, 1)
    return out


def lookup_wipwl(engine, wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"WIPWLNearby": False, "WIPWLSummary": None}
    if not table_exists(engine, settings.wipwl_table):
        warnings.append("WI/PWL waterbody layer not loaded.")
        return out

    gcol = settings.geom_column
    rows = fetch_all(
        engine,
        f"""
        SELECT *, ST_Distance({gcol}, {_g(settings)}) AS dist_m
        FROM {settings.wipwl_table}
        WHERE ST_DWithin({gcol}, {_g(settings)}, :radius_m)
        ORDER BY dist_m ASC
        LIMIT 1
        """,
        {"wkt": wkt, "icrs": settings.input_crs, "radius_m": settings.wipwl_search_radius_m},
    )
    if not rows:
        return out

    out["WIPWLNearby"] = True
    r = rows[0]
    # Build a summary from whatever attributes happen to exist. WI/PWL schemas
    # vary by vintage, so each field is optional.
    parts = []
    name = pick_attr(r, ["WATER_NAME", "NAME", "PWL_NAME", "WB_NAME", "name"])
    if name:
        parts.append(f"Waterbody: {name}")
    status = pick_attr(r, ["WQ_STATUS", "ASSESSMENT", "STATUS", "WQS_CLASS", "CLASS", "WI_PWL_CLASS"])
    if status:
        parts.append(f"Status/assessment: {status}")
    pollutant = pick_attr(r, ["POLLUTANT", "POLLUTANTS", "PARAMETER", "CAUSE"])
    if pollutant:
        parts.append(f"Pollutant(s): {pollutant}")
    source = pick_attr(r, ["SOURCE", "SOURCES", "POLL_SOURCE"])
    if source:
        parts.append(f"Source(s): {source}")
    dist_m = r.get("dist_m")
    if dist_m is not None:
        parts.append(f"~{round(float(dist_m) * FT_PER_M):.0f} ft away")

    out["WIPWLSummary"] = (
        "; ".join(parts)
        if parts
        else "WI/PWL feature nearby (attribute fields not recognized in this dataset)."
    )
    return out


def lookup_dac(engine, point_wkt: str, buffer_wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"DACIntersecting": False, "DACNearby": False}
    if not table_exists(engine, settings.dac_table):
        warnings.append("Disadvantaged Community (DAC) layer not loaded.")
        return out

    gcol = settings.geom_column
    inter = fetch_all(
        engine,
        f"SELECT 1 FROM {settings.dac_table} "
        f"WHERE ST_Intersects({gcol}, {_g(settings)}) LIMIT 1",
        {"wkt": point_wkt, "icrs": settings.input_crs},
    )
    if inter:
        out["DACIntersecting"] = True
        out["DACNearby"] = True
        return out

    near = fetch_all(
        engine,
        f"SELECT 1 FROM {settings.dac_table} "
        f"WHERE ST_DWithin({gcol}, {_g(settings)}, :radius_m) LIMIT 1",
        {"wkt": buffer_wkt, "icrs": settings.input_crs, "radius_m": settings.dac_nearby_distance_m},
    )
    out["DACNearby"] = bool(near)
    return out


def lookup_soil(engine, buffer_wkt: str, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"DominantSoilDrainageClass": None, "DominantHydrologicSoilGroup": None}
    if not table_exists(engine, settings.ssurgo_table):
        warnings.append("SSURGO soils layer not loaded.")
        return out

    gcol = settings.geom_column
    # Dominant map unit = largest intersection area with the project footprint.
    rows = fetch_all(
        engine,
        f"""
        SELECT *,
               ST_Area(ST_Intersection({gcol}, {_g(settings)})) AS overlap_m2
        FROM {settings.ssurgo_table}
        WHERE ST_Intersects({gcol}, {_g(settings)})
        ORDER BY overlap_m2 DESC NULLS LAST
        LIMIT 1
        """,
        {"wkt": buffer_wkt, "icrs": settings.input_crs},
    )
    if not rows:
        warnings.append("No SSURGO soil polygon found at the project location.")
        return out

    r = rows[0]
    drainage = pick_attr(r, ["drainagecl", "DRAINAGECL", "drainage_class", "DRAINAGE"])
    hsg = pick_attr(r, ["hydgrp", "HYDGRP", "hydrologic_group", "HSG"])
    out["DominantSoilDrainageClass"] = drainage
    out["DominantHydrologicSoilGroup"] = hsg

    if drainage is None and hsg is None:
        warnings.append(
            "Soil polygon found, but drainage-class table not loaded "
            "(load the SSURGO component/muaggatt tables to populate this)."
        )
    return out


# ---------------------------------------------------------------------------
# Raster (DEM / slope) lookup
# ---------------------------------------------------------------------------
def lookup_slope(geom4326, settings: Settings, warnings: List[str]) -> Dict[str, Any]:
    out = {"MeanSlopePercent": None, "MaxSlopePercent": None}

    if not settings.dem_tiles_dir:
        warnings.append("DEM/slope data not configured.")
        return out
    if not RASTERIO_OK:
        warnings.append("rasterio/numpy not available; slope skipped.")
        return out

    tiles = sorted(glob.glob(os.path.join(settings.dem_tiles_dir, "*.tif"))) + sorted(
        glob.glob(os.path.join(settings.dem_tiles_dir, "*.tiff"))
    )
    if not tiles:
        warnings.append("DEM/slope data not configured (no rasters found in dem_tiles_dir).")
        return out

    collected = []
    for path in tiles:
        try:
            with rasterio.open(path) as src:
                geom_in_raster = transform_geom("EPSG:4326", src.crs.to_string(), mapping(geom4326))
                out_image, _ = rio_mask(src, [geom_in_raster], crop=True, filled=True)
                arr = out_image[0].astype("float64")
                if src.nodata is not None:
                    arr = np.where(arr == src.nodata, np.nan, arr)
                if settings.dem_is_slope:
                    vals = arr
                else:
                    # gradient (units/units) -> percent. Assumes a projected DEM
                    # whose horizontal + vertical units match (e.g. meters).
                    px, py = abs(src.res[0]), abs(src.res[1])
                    gy, gx = np.gradient(arr, py, px)
                    vals = np.sqrt(gx ** 2 + gy ** 2) * 100.0
                vals = vals[~np.isnan(vals)]
                if vals.size:
                    collected.append(vals)
        except ValueError:
            # rasterio.mask raises ValueError when the geom doesn't overlap the
            # tile -- that's expected when iterating a tile set.
            continue
        except Exception as exc:
            warnings.append(f"Slope tile {os.path.basename(path)} skipped: {exc}")
            continue

    if not collected:
        warnings.append("No DEM/slope tiles intersected the project area.")
        return out

    allv = np.concatenate(collected)
    out["MeanSlopePercent"] = round(float(np.nanmean(allv)), 1)
    out["MaxSlopePercent"] = round(float(np.nanmax(allv)), 1)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_lookups(
    locate_geom: BaseGeometry,
    analysis_geom: BaseGeometry,
    analysis_source: str,
    engine,
    settings: Settings,
) -> Tuple[Dict[str, Any], List[str]]:
    """Run every lookup against prepared geometries. Returns (facts, warnings).

    * locate_geom  -- a boundary polygon if drawn, else the raw GPS point. Used
      for intersect/nearest lookups (county, town, HUC, waterbody, WI/PWL, DAC).
    * analysis_geom -- boundary polygon if drawn, else the buffered point. Used
      for area-based lookups (soil, DAC-nearby) and slope rasters.
    """
    warnings: List[str] = []
    facts = default_facts()
    facts["AnalysisGeometrySource"] = analysis_source

    locate_wkt = locate_geom.wkt
    analysis_wkt = analysis_geom.wkt

    if engine is None:
        warnings.append("Database not configured/unreachable; vector GIS lookups skipped.")
    else:
        facts.update(lookup_county_town(engine, locate_wkt, settings, warnings))
        facts.update(lookup_huc(engine, locate_wkt, settings, warnings))
        facts.update(lookup_nearest_waterbody(engine, locate_wkt, settings, warnings))
        facts.update(lookup_wipwl(engine, locate_wkt, settings, warnings))
        facts.update(lookup_dac(engine, locate_wkt, analysis_wkt, settings, warnings))
        facts.update(lookup_soil(engine, analysis_wkt, settings, warnings))

    # Raster slope is independent of the database.
    facts.update(lookup_slope(analysis_geom, settings, warnings))

    return facts, warnings
