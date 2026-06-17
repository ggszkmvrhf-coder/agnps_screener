"""Flatten a downloaded NRCS gSSURGO/SSURGO FileGDB for backend lookup.

The backend wants one PostGIS layer named `ssurgo_soils` with geometry plus the
attributes it reads most often:

    mukey, musym, muname, drainagecl, hydgrp, geom

Official gSSURGO stores polygons and soil attributes separately. This script
joins the dominant component by `mukey` onto `MUPOLYGON`, then writes a compact
GeoPackage that can be loaded with `load_layers.py`.

Usage:
    python prepare_ssurgo.py --gdb data/raw/ssurgo/gSSURGO_NY.gdb
    python prepare_ssurgo.py --gdb path/to/gSSURGO_NY.gdb --out data/processed/ssurgo_soils.gpkg
"""
import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "processed" / "ssurgo_soils.gpkg"


def _import_gis():
    try:
        import geopandas as gpd
        import pandas as pd
        import pyogrio
    except Exception as exc:
        print(
            "ERROR: geopandas, pandas, and pyogrio are required. "
            "Install backend/requirements.txt in your backend virtualenv first.\n"
            f"Import error: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return gpd, pd, pyogrio


def _pick_layer(layers, candidates):
    lowered = {name.lower(): name for name in layers}
    for candidate in candidates:
        match = lowered.get(candidate.lower())
        if match:
            return match
    for name in layers:
        lname = name.lower()
        if any(candidate.lower() in lname for candidate in candidates):
            return name
    return None


def _read_table(pyogrio, gdb, layer_name):
    return pyogrio.read_dataframe(gdb, layer=layer_name, read_geometry=False)


def prepare(gdb_path: Path, out_path: Path, target_crs: int) -> None:
    gpd, pd, pyogrio = _import_gis()

    if not gdb_path.exists():
        raise SystemExit(f"ERROR: FileGDB not found: {gdb_path}")

    layer_info = pyogrio.list_layers(gdb_path)
    layers = [row[0] for row in layer_info]
    mupolygon_layer = _pick_layer(layers, ["MUPOLYGON", "mupolygon"])
    component_layer = _pick_layer(layers, ["component"])
    muaggatt_layer = _pick_layer(layers, ["muaggatt"])

    if not mupolygon_layer:
        raise SystemExit("ERROR: could not find MUPOLYGON layer in the FileGDB.")

    print(f"Reading polygon layer: {mupolygon_layer}")
    soils = gpd.read_file(gdb_path, layer=mupolygon_layer)
    soils.columns = [str(c).lower() for c in soils.columns]
    if "mukey" not in soils.columns:
        raise SystemExit("ERROR: MUPOLYGON does not contain mukey.")

    join_cols = None
    if component_layer:
        print(f"Reading component table: {component_layer}")
        comp = _read_table(pyogrio, gdb_path, component_layer)
        comp.columns = [str(c).lower() for c in comp.columns]
        if "mukey" in comp.columns:
            if "comppct_r" in comp.columns:
                comp = comp.sort_values(["mukey", "comppct_r"], ascending=[True, False])
            keep = [c for c in ["mukey", "drainagecl", "hydgrp"] if c in comp.columns]
            if len(keep) > 1:
                join_cols = comp[keep].drop_duplicates("mukey")

    if join_cols is None and muaggatt_layer:
        print(f"Reading aggregate table: {muaggatt_layer}")
        agg = _read_table(pyogrio, gdb_path, muaggatt_layer)
        agg.columns = [str(c).lower() for c in agg.columns]
        keep = [c for c in ["mukey", "hydgrpdcd", "hydgrp"] if c in agg.columns]
        if len(keep) > 1:
            join_cols = agg[keep].drop_duplicates("mukey")
            if "hydgrp" not in join_cols.columns and "hydgrpdcd" in join_cols.columns:
                join_cols = join_cols.rename(columns={"hydgrpdcd": "hydgrp"})

    if join_cols is not None:
        soils = soils.merge(join_cols, on="mukey", how="left")
    else:
        print("WARNING: no component/muaggatt drainage/HSG attributes found; writing polygons only.")
        soils["drainagecl"] = None
        soils["hydgrp"] = None

    keep = [c for c in ["mukey", "musym", "muname", "drainagecl", "hydgrp", soils.geometry.name] if c in soils.columns]
    soils = soils[keep]
    soils = soils.to_crs(epsg=target_crs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    soils.to_file(out_path, layer="ssurgo_soils", driver="GPKG")
    print(f"Wrote {len(soils)} soil polygons -> {out_path}")
    print("Next:")
    print(f"  python scripts/load_layers.py --layer ssurgo --source-file \"{out_path}\" --layer-name ssurgo_soils")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Flatten gSSURGO/SSURGO soils for PostGIS loading.")
    parser.add_argument("--gdb", required=True, help="Path to extracted gSSURGO/SSURGO FileGDB folder.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output GeoPackage path.")
    parser.add_argument("--target-crs", type=int, default=5070, help="Output CRS EPSG code.")
    args = parser.parse_args(argv)

    prepare(Path(args.gdb), Path(args.out), args.target_crs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

