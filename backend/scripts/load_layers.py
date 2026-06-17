"""Load NY vector layers into PostGIS.

Layer-aware: knows the table name + raw subfolder for each layer key
(huc12, wipwl, dac, counties, towns, hydrography, ssurgo), so you can load by key
and let it find the downloaded file -- or pass an explicit file/table.

Accepts GeoJSON, Shapefile (.shp or zipped .zip), GeoPackage (.gpkg), and FileGDB
(.gdb folder or zip, with --layer-name). Reprojects to the configured analysis
CRS (settings.projected_crs, default EPSG:5070), writes the table, creates a GIST
index on geom, and prints the record count.

Usage:
    python load_layers.py --all
    python load_layers.py --layer counties --where "STATEFP='36'"
    python load_layers.py --layer huc12 --source-file data/raw/huc12/WBDHU12.gpkg --layer-name WBDHU12
    python load_layers.py --table-name ny_counties --source-file path.shp   # explicit mode
"""
import argparse
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))   # for download_data
sys.path.insert(0, str(BACKEND_DIR))   # for app.settings

import geopandas as gpd
from geoalchemy2 import Geometry
from sqlalchemy import create_engine, text

from app.settings import get_settings
from download_data import LAYERS, DEFAULT_RAW

# Search order when auto-finding a file in a layer's raw subfolder.
_EXT_PRIORITY = ["*.gpkg", "*.geojson", "*.json", "*.shp", "*.zip", "*.gdb"]


def find_source_file(subdir: str, raw_root: Path):
    folder = raw_root / subdir
    if not folder.exists():
        return None
    for pattern in _EXT_PRIORITY:
        matches = sorted(folder.glob(pattern))
        if matches:
            return matches[0]
    return None


def read_vector(source: Path, layer_name=None, where=None):
    """Read a vector file into a GeoDataFrame, handling zipped shapefiles."""
    path = str(source)
    read_kwargs = {}
    if layer_name:
        read_kwargs["layer"] = layer_name
    if where:
        # pyogrio supports attribute filtering at read time.
        read_kwargs["where"] = where
    if path.lower().endswith(".zip"):
        path = "zip://" + path
    try:
        return gpd.read_file(path, **read_kwargs)
    except Exception:
        if not where:
            raise
        # Driver didn't accept `where`; read all and apply a simple COL='VAL' filter.
        gdf = gpd.read_file(path, **{k: v for k, v in read_kwargs.items() if k != "where"})
        m = re.match(r"\s*(\w+)\s*=\s*'?([^']*)'?\s*$", where)
        if m and m.group(1) in gdf.columns:
            col, val = m.group(1), m.group(2)
            return gdf[gdf[col].astype(str) == val]
        print(f"  WARNING: could not apply --where '{where}'; loaded all features.")
        return gdf


def load_one(engine, gdf, table_name, geom_column, target_crs, if_exists):
    if gdf.crs is None:
        print("  WARNING: source has no CRS; assuming EPSG:4326.")
        gdf = gdf.set_crs(epsg=4326)
    print(f"  {len(gdf)} features in {gdf.crs}; reprojecting to EPSG:{target_crs}")
    gdf = gdf.to_crs(epsg=target_crs)
    if gdf.geometry.name != geom_column:
        gdf = gdf.rename_geometry(geom_column)

    # Use generic GEOMETRY so combined service downloads can contain both
    # polygon and line features, e.g. WI/PWL lakes + streams + shorelines.
    gdf.to_postgis(
        table_name,
        engine,
        if_exists=if_exists,
        index=False,
        dtype={geom_column: Geometry("GEOMETRY", srid=target_crs)},
    )

    index_name = f"idx_{table_name}_{geom_column}"
    with engine.begin() as conn:
        conn.execute(text(
            f'CREATE INDEX IF NOT EXISTS {index_name} '
            f'ON "{table_name}" USING GIST ("{geom_column}")'
        ))
        conn.execute(text(f'ANALYZE "{table_name}"'))
        count = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar()
        _record_metadata(conn, table_name, str(target_crs), count)
    print(f"  Done: '{table_name}' has {count} rows; GIST index '{index_name}'.")
    return count


def _record_metadata(conn, table_name, crs, count):
    """Best-effort upsert into gis_layers_metadata (ignored if table absent)."""
    try:
        conn.execute(text(
            "INSERT INTO gis_layers_metadata (table_name, crs_epsg, feature_count, loaded_at) "
            "VALUES (:t, :c, :n, now()) "
            "ON CONFLICT (table_name) DO UPDATE SET crs_epsg = :c, feature_count = :n, loaded_at = now()"
        ), {"t": table_name, "c": int(crs), "n": int(count)})
    except Exception:
        pass


def main(argv=None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Load vector layers into PostGIS.")
    p.add_argument("--db-url", default=settings.database_url, help="SQLAlchemy DB URL (default: settings)")
    p.add_argument("--all", action="store_true", help="Load every layer that has a downloaded file")
    p.add_argument("--layer", help="Layer key: " + ", ".join(LAYERS))
    p.add_argument("--source-file", help="Explicit path (overrides auto-find)")
    p.add_argument("--table-name", help="Explicit table name (explicit mode)")
    p.add_argument("--layer-name", help="Sub-layer name for GeoPackage/FileGDB")
    p.add_argument("--where", help="Attribute filter, e.g. \"STATEFP='36'\"")
    p.add_argument("--target-crs", type=int, default=settings.projected_crs)
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    p.add_argument("--if-exists", default="replace", choices=["replace", "append", "fail"])
    args = p.parse_args(argv)

    if not args.db_url:
        print("ERROR: no database URL (pass --db-url or set DATABASE_URL).", file=sys.stderr)
        return 2

    engine = create_engine(args.db_url, future=True)
    raw_root = Path(args.raw_dir)
    geom_col = settings.geom_column

    # Build the list of (table, source, layer_name) jobs.
    jobs = []
    if args.all:
        for spec in LAYERS.values():
            if spec.fmt == "geotiff":      # DEM is raster, not loaded here
                continue
            src = find_source_file(spec.subdir, raw_root)
            if src:
                jobs.append((spec.table, src, args.layer_name))
            else:
                print(f"[{spec.key}] no file found in {raw_root / spec.subdir} -- skipping.")
    elif args.layer:
        spec = LAYERS.get(args.layer)
        if not spec:
            print(f"ERROR: unknown layer '{args.layer}'.", file=sys.stderr)
            return 2
        src = Path(args.source_file) if args.source_file else find_source_file(spec.subdir, raw_root)
        if not src:
            print(f"ERROR: no file for '{args.layer}' in {raw_root / spec.subdir}.", file=sys.stderr)
            return 1
        jobs.append((args.table_name or spec.table, Path(src), args.layer_name))
    elif args.source_file and args.table_name:
        jobs.append((args.table_name, Path(args.source_file), args.layer_name))
    else:
        print("ERROR: specify --all, --layer KEY, or --source-file + --table-name.", file=sys.stderr)
        return 2

    loaded = 0
    for table, source, layer_name in jobs:
        print(f"\n== {table} <- {source} ==")
        try:
            gdf = read_vector(source, layer_name=layer_name, where=args.where)
            load_one(engine, gdf, table, geom_col, args.target_crs, args.if_exists)
            loaded += 1
        except Exception as exc:
            print(f"  FAILED: {exc}")
    print(f"\nLoaded {loaded}/{len(jobs)} layer(s). Next: python verify_layers.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
