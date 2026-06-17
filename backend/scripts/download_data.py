"""Download (or print manual instructions for) the NY public GIS layers.

Two modes, decided per layer:
  * AUTOMATIC  -- a stable direct URL is configured -> the file is downloaded.
  * MANUAL     -- no stable URL (large/unstable/requires selection) -> printed
                  step-by-step instructions instead.

Nothing here crashes the pipeline: each layer is attempted independently and the
result (ok / present / manual / failed) is logged and summarized.

This module also defines LAYERS, the single registry of layer keys, table names,
raw subfolders, and sources -- load_layers.py and verify_layers.py import it.

Usage:
    python download_data.py --layers all
    python download_data.py --layers huc12 wipwl dac counties towns hydrography
    python download_data.py --layers counties towns --output-dir data/raw --force
"""
import argparse
import json
import logging
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_RAW = PROJECT_ROOT / "data" / "raw"
LOG_DIR = PROJECT_ROOT / "data" / "logs"


@dataclass(frozen=True)
class ArcGISLayerSpec:
    name: str
    service_url: str
    where: str = "1=1"
    page_size: int = 500


@dataclass(frozen=True)
class LayerSpec:
    key: str
    table: str
    subdir: str
    required_v1: bool
    fmt: str                 # shapefile_zip | geojson | geopackage | filegdb_zip
    source_name: str
    url: Optional[str]       # None => manual download
    manual: str = ""
    arcgis_layers: Tuple[ArcGISLayerSpec, ...] = ()


# --- The single source of truth for every layer ----------------------------
# TIGER URLs are national/statewide and stable; bump the year if a file 404s.
# HUC and WI/PWL come from official ArcGIS REST services and are downloaded as
# paged GeoJSON. NRCS gSSURGO is still manual because the state databases are
# distributed through Box/GDG rather than a stable, small direct URL.
LAYERS: Dict[str, LayerSpec] = {
    "counties": LayerSpec(
        key="counties", table="ny_counties", subdir="counties", required_v1=True,
        fmt="shapefile_zip", source_name="US Census TIGER/Line Counties 2025 (national)",
        url="https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip",
        manual="National file. On load, filter to NY with: --where \"STATEFP='36'\".",
    ),
    "towns": LayerSpec(
        key="towns", table="ny_towns", subdir="towns", required_v1=True,
        fmt="shapefile_zip", source_name="US Census TIGER/Line County Subdivisions 2025, NY (state 36)",
        url="https://www2.census.gov/geo/tiger/TIGER2025/COUSUB/tl_2025_36_cousub.zip",
        manual="NY-only file; no filter needed.",
    ),
    "huc8": LayerSpec(
        key="huc8", table="huc8", subdir="huc8", required_v1=True,
        fmt="geojson", source_name="USGS Watershed Boundary Dataset (WBD) HUC8",
        url=None,
        manual="Automatic download from USGS WBD ArcGIS REST service. Filter: states LIKE '%NY%'.",
        arcgis_layers=(
            ArcGISLayerSpec(
                name="huc8",
                service_url="https://hydrowfs.nationalmap.gov/arcgis/rest/services/wbd/MapServer/4",
                where="states LIKE '%NY%'",
                page_size=25,
            ),
        ),
    ),
    "huc10": LayerSpec(
        key="huc10", table="huc10", subdir="huc10", required_v1=True,
        fmt="geojson", source_name="USGS Watershed Boundary Dataset (WBD) HUC10",
        url=None,
        manual="Automatic download from USGS WBD ArcGIS REST service. Filter: states LIKE '%NY%'.",
        arcgis_layers=(
            ArcGISLayerSpec(
                name="huc10",
                service_url="https://hydrowfs.nationalmap.gov/arcgis/rest/services/wbd/MapServer/5",
                where="states LIKE '%NY%'",
                page_size=25,
            ),
        ),
    ),
    "huc12": LayerSpec(
        key="huc12", table="huc12", subdir="huc12", required_v1=True,
        fmt="geojson", source_name="USGS Watershed Boundary Dataset (WBD) HUC12",
        url=None,
        manual="Automatic download from USGS WBD ArcGIS REST service. Filter: states LIKE '%NY%'.",
        arcgis_layers=(
            ArcGISLayerSpec(
                name="huc12",
                service_url="https://hydrowfs.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6",
                where="states LIKE '%NY%'",
                page_size=25,
            ),
        ),
    ),
    "wipwl": LayerSpec(
        key="wipwl", table="wipwl_waterbodies", subdir="wipwl", required_v1=True,
        fmt="geojson", source_name="NYS DEC Waterbody Inventory / Priority Waterbodies List (WI/PWL)",
        url=None,
        manual="Automatic download from NYSDEC WI/PWL FeatureServer layers 2, 3, 4, and 5.",
        arcgis_layers=(
            ArcGISLayerSpec(
                name="Priority Waterbody List - Lakes",
                service_url="https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/Waterbody_Inventory_List/FeatureServer/2",
                page_size=250,
            ),
            ArcGISLayerSpec(
                name="Priority Waterbody List - Estuaries",
                service_url="https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/Waterbody_Inventory_List/FeatureServer/3",
                page_size=250,
            ),
            ArcGISLayerSpec(
                name="Priority Waterbody List - Streams",
                service_url="https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/Waterbody_Inventory_List/FeatureServer/4",
                page_size=250,
            ),
            ArcGISLayerSpec(
                name="Priority Waterbody List - Shorelines",
                service_url="https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/Waterbody_Inventory_List/FeatureServer/5",
                page_size=250,
            ),
        ),
    ),
    "dac": LayerSpec(
        key="dac", table="dac_areas", subdir="dac", required_v1=True,
        fmt="geojson", source_name="NYSERDA Final Disadvantaged Communities (DAC) 2023",
        url="https://data.ny.gov/resource/2e6c-s6fp.geojson?$limit=50000",
        manual="Downloaded from the data.ny.gov Socrata GeoJSON endpoint.",
    ),
    "hydrography": LayerSpec(
        key="hydrography", table="streams_waterbodies", subdir="hydrography", required_v1=True,
        fmt="filegdb_zip", source_name="USGS National Hydrography Dataset (NHD) / NYS hydrography",
        url=None,
        manual=(
            "NHD is large; download the New York subset.\n"
            "1. The National Map downloader -> 'National Hydrography Dataset (NHD)', area = New York.\n"
            "2. Download NHD (GDB). Use the NHDFlowline + NHDWaterbody layers.\n"
            "3. Place the file in data/raw/hydrography/ (load with --layer-name NHDFlowline)."
        ),
    ),
    # --- Optional for v1 -----------------------------------------------------
    "ssurgo": LayerSpec(
        key="ssurgo", table="ssurgo_soils", subdir="ssurgo", required_v1=False,
        fmt="filegdb_zip", source_name="USDA NRCS gSSURGO / SSURGO soils",
        url=None,
        manual=(
            "OPTIONAL for v1, but recommended for soil/drainage facts.\n"
            "1. Go to the NRCS gSSURGO page and open State Databases.\n"
            "2. Download the New York gSSURGO state zip.\n"
            "3. Extract the .gdb folder into data/raw/ssurgo/.\n"
            "4. Run: python scripts/prepare_ssurgo.py --gdb ..\\data\\raw\\ssurgo\\gSSURGO_NY.gdb"
        ),
    ),
    "dem": LayerSpec(
        key="dem", table="(raster tiles)", subdir="dem", required_v1=False,
        fmt="geotiff", source_name="USGS 3DEP / NYS LiDAR-derived DEM",
        url=None,
        manual=(
            "OPTIONAL for v1. NOT loaded into PostGIS -- used as clipped raster tiles.\n"
            "1. Download projected DEM (meters) GeoTIFF tiles covering your areas.\n"
            "2. Statewide DEM/LiDAR is very large -- download TILED, not statewide.\n"
            "3. Place tiles in data/raw/dem/ and set DEM_TILES_DIR to that folder."
        ),
    ),
}


def _build_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"download_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = logging.getLogger("download_data")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.info("Logging to %s", log_path)
    return logger


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "agnps-data-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)


def _arcgis_query_geojson(layer: ArcGISLayerSpec, object_ids: List[int]) -> Dict:
    params = {
        "f": "geojson",
        "objectIds": ",".join(str(oid) for oid in object_ids),
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
    }
    url = f"{layer.service_url}/query?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "agnps-data-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = resp.read().decode("utf-8")
    data = json.loads(payload)
    if "error" in data:
        raise RuntimeError(f"{layer.name} query failed: {data['error']}")
    return data


def _arcgis_object_ids(layer: ArcGISLayerSpec) -> List[int]:
    params = {"f": "json", "where": layer.where, "returnIdsOnly": "true"}
    url = f"{layer.service_url}/query?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "agnps-data-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"{layer.name} object ID query failed: {data['error']}")
    return [int(oid) for oid in data.get("objectIds", [])]


def _download_arcgis_layers(spec: LayerSpec, dest: Path, log: logging.Logger) -> None:
    all_features = []
    metadata = {
        "source_name": spec.source_name,
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "layers": [],
    }

    for layer in spec.arcgis_layers:
        object_ids = _arcgis_object_ids(layer)
        expected = len(object_ids)
        log.info("[%s] %s: %s feature(s) expected.", spec.key, layer.name, expected)
        fetched = 0
        skipped = 0
        for start in range(0, expected, layer.page_size):
            chunk = object_ids[start:start + layer.page_size]
            features, skipped_chunk = _fetch_arcgis_features(spec.key, layer, chunk, log)
            skipped += skipped_chunk
            if not features:
                log.info("[%s] %s: object ID chunk returned no features at offset %s.", spec.key, layer.name, start)
                continue
            for feature in features:
                props = feature.setdefault("properties", {})
                props["_source_layer"] = layer.name
                props["_source_url"] = layer.service_url
            all_features.extend(features)
            fetched += len(features)
            log.info("[%s] %s: fetched %s/%s.", spec.key, layer.name, fetched, expected)
            time.sleep(0.1)

        metadata["layers"].append(
            {
                "name": layer.name,
                "service_url": layer.service_url,
                "where": layer.where,
                "expected_features": expected,
                "fetched_features": fetched,
                "skipped_features": skipped,
            }
        )

        if skipped:
            raise RuntimeError(f"{layer.name}: skipped {skipped} feature(s) after recursive retries")

    collection = {"type": "FeatureCollection", "features": all_features}
    dest.write_text(json.dumps(collection), encoding="utf-8")
    dest.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _fetch_arcgis_features(
    spec_key: str,
    layer: ArcGISLayerSpec,
    object_ids: List[int],
    log: logging.Logger,
) -> Tuple[List[Dict], int]:
    if not object_ids:
        return [], 0

    try:
        data = _arcgis_query_geojson(layer, object_ids)
        return data.get("features") or [], 0
    except Exception as exc:
        if len(object_ids) == 1:
            log.error(
                "[%s] %s: object ID %s failed after recursive retry: %s",
                spec_key,
                layer.name,
                object_ids[0],
                exc,
            )
            return [], 1

        mid = len(object_ids) // 2
        log.info(
            "[%s] %s: chunk of %s failed; splitting into %s + %s.",
            spec_key,
            layer.name,
            len(object_ids),
            mid,
            len(object_ids) - mid,
        )
        left, skipped_left = _fetch_arcgis_features(spec_key, layer, object_ids[:mid], log)
        right, skipped_right = _fetch_arcgis_features(spec_key, layer, object_ids[mid:], log)
        return left + right, skipped_left + skipped_right


def download_layer(spec: LayerSpec, raw_root: Path, force: bool, log: logging.Logger) -> str:
    dest_dir = raw_root / spec.subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    if spec.arcgis_layers:
        dest = dest_dir / f"{spec.key}.geojson"
        if dest.exists() and not force:
            log.info("[%s] Already present: %s (use --force to re-download).", spec.key, dest.name)
            return "present"
        try:
            log.info("[%s] Downloading from ArcGIS REST service(s): %s", spec.key, spec.source_name)
            _download_arcgis_layers(spec, dest, log)
            size_mb = dest.stat().st_size / 1e6
            log.info("[%s] Saved -> %s (%.1f MB)", spec.key, dest, size_mb)
            return "ok"
        except Exception as exc:
            log.error("[%s] ArcGIS download FAILED: %s", spec.key, exc)
            if spec.manual:
                log.error("        Fallback: %s", spec.manual)
            return "failed"

    if not spec.url:
        log.info("[%s] MANUAL download required (%s).", spec.key, spec.source_name)
        for line in spec.manual.splitlines():
            log.info("        %s", line)
        return "manual"

    fname = spec.url.split("/")[-1].split("?")[0] or f"{spec.key}.dat"
    dest = dest_dir / fname
    if dest.exists() and not force:
        log.info("[%s] Already present: %s (use --force to re-download).", spec.key, dest.name)
        return "present"

    try:
        log.info("[%s] Downloading from %s", spec.key, spec.url)
        _download(spec.url, dest)
        size_mb = dest.stat().st_size / 1e6
        log.info("[%s] Saved -> %s (%.1f MB) [%s]", spec.key, dest, size_mb, spec.source_name)
        return "ok"
    except Exception as exc:  # network error, 404, timeout, ...
        log.error("[%s] Download FAILED: %s", spec.key, exc)
        if spec.manual:
            log.error("        Fallback (manual): see DATA_DOWNLOAD_SETUP.md / %s", spec.source_name)
        return "failed"


def resolve_layers(requested: List[str]) -> List[LayerSpec]:
    if not requested or "all" in requested:
        return list(LAYERS.values())
    specs = []
    for key in requested:
        if key in LAYERS:
            specs.append(LAYERS[key])
        else:
            print(f"WARNING: unknown layer '{key}' (known: {', '.join(LAYERS)})", file=sys.stderr)
    return specs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download NY public GIS layers.")
    p.add_argument("--layers", nargs="+", default=["all"],
                   help="Layer keys or 'all'. Keys: " + ", ".join(LAYERS))
    p.add_argument("--output-dir", default=str(DEFAULT_RAW), help="Raw output root (default data/raw)")
    p.add_argument("--force", action="store_true", help="Re-download even if a file exists")
    args = p.parse_args(argv)

    log = _build_logger(LOG_DIR)
    raw_root = Path(args.output_dir)
    specs = resolve_layers(args.layers)

    results = {}
    for spec in specs:
        results[spec.key] = download_layer(spec, raw_root, args.force, log)

    log.info("")
    log.info("==================== SUMMARY ====================")
    for key, status in results.items():
        req = "required" if LAYERS[key].required_v1 else "optional"
        log.info("  %-12s %-8s (%s)", key, status.upper(), req)
    manual = [k for k, s in results.items() if s == "manual"]
    failed = [k for k, s in results.items() if s == "failed"]
    if manual:
        log.info("Manual download needed: %s  (see docs/DATA_DOWNLOAD_SETUP.md)", ", ".join(manual))
    if failed:
        log.info("Failed (retry or download manually): %s", ", ".join(failed))
    log.info("Next: python load_layers.py --all   then   python verify_layers.py")
    return 0  # never fail the whole run because one layer failed


if __name__ == "__main__":
    raise SystemExit(main())
