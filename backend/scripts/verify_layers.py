"""Verify that loaded PostGIS layers are usable by the lookup engine.

Checks, per required layer: table exists, row count, invalid-geometry count, and
whether a GIST index exists. Then runs one real test lookup (county + HUC12
intersect) using a sample NY coordinate. Prints overall readiness:

    READY      -- all required layers present, populated, and the test lookup works
    PARTIAL    -- some required layers present/usable, but not all
    NOT READY  -- database unreachable or no required layers usable

Usage:
    python verify_layers.py
    python verify_layers.py --db-url postgresql+psycopg2://... --lat 42.7 --lng -76.65
"""
import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text

from app.settings import get_settings
from download_data import LAYERS

# Sample NY point (Tompkins County, Finger Lakes region).
SAMPLE_LAT, SAMPLE_LNG = 42.7012, -76.6543


def required_tables():
    return [(s.key, s.table) for s in LAYERS.values() if s.required_v1 and s.fmt != "geotiff"]


def optional_tables():
    return [(s.key, s.table) for s in LAYERS.values() if not s.required_v1 and s.fmt != "geotiff"]


def table_exists(conn, table):
    return conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def row_count(conn, table):
    return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def invalid_count(conn, table, geom):
    try:
        return conn.execute(text(
            f'SELECT COUNT(*) FROM "{table}" WHERE NOT ST_IsValid("{geom}")'
        )).scalar()
    except Exception:
        return None


def has_gist_index(conn, table):
    try:
        return conn.execute(text(
            "SELECT COUNT(*) FROM pg_indexes WHERE tablename = :t AND indexdef ILIKE '%USING gist%'"
        ), {"t": table}).scalar() > 0
    except Exception:
        return None


def test_lookup(conn, settings, lat, lng):
    """Return a dict of sample lookup results, or {} on failure."""
    g = (f"ST_Transform(ST_SetSRID(ST_MakePoint(:lng, :lat), {settings.input_crs}), "
         f"{settings.projected_crs})")
    out = {}
    for table, label, name_cols in [
        (settings.counties_table, "county", ["NAME", "COUNTY", "NAMELSAD"]),
        (settings.huc12_table, "huc12", ["HUC12", "huc12", "NAME"]),
    ]:
        if not table_exists(conn, table):
            continue
        try:
            row = conn.execute(text(
                f'SELECT * FROM "{table}" WHERE ST_Intersects("{settings.geom_column}", {g}) LIMIT 1'
            ), {"lat": lat, "lng": lng}).mappings().first()
            if row:
                val = next((row[c] for c in name_cols if c in row and row[c]), "(matched)")
                out[label] = val
            else:
                out[label] = "(no intersect)"
        except Exception as exc:
            out[label] = f"(error: {exc})"
    return out


def main(argv=None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Verify loaded PostGIS layers.")
    p.add_argument("--db-url", default=settings.database_url)
    p.add_argument("--lat", type=float, default=SAMPLE_LAT)
    p.add_argument("--lng", type=float, default=SAMPLE_LNG)
    args = p.parse_args(argv)

    if not args.db_url:
        print("NOT READY: no database URL (pass --db-url or set DATABASE_URL).")
        return 1

    try:
        engine = create_engine(args.db_url, future=True)
        conn = engine.connect()
    except Exception as exc:
        print(f"NOT READY: cannot connect to database: {exc}")
        return 1

    geom = settings.geom_column
    print(f"{'LAYER':<14}{'TABLE':<22}{'ROWS':>8}  {'INVALID':>7}  {'GIST':>5}")
    print("-" * 64)

    required_ok = 0
    required_total = 0
    with conn:
        for key, table in required_tables():
            required_total += 1
            if not table_exists(conn, table):
                print(f"{key:<14}{table:<22}{'MISSING':>8}")
                continue
            n = row_count(conn, table)
            inv = invalid_count(conn, table, geom)
            gist = has_gist_index(conn, table)
            inv_s = "?" if inv is None else str(inv)
            gist_s = "?" if gist is None else ("yes" if gist else "NO")
            print(f"{key:<14}{table:<22}{n:>8}  {inv_s:>7}  {gist_s:>5}")
            if n and n > 0:
                required_ok += 1

        print("\nOptional layers:")
        for key, table in optional_tables():
            present = table_exists(conn, table)
            n = row_count(conn, table) if present else 0
            print(f"  {key:<12}{table:<22}{'present, ' + str(n) + ' rows' if present else 'not loaded'}")

        print("\nTest lookup @ ({:.4f}, {:.4f}):".format(args.lat, args.lng))
        results = test_lookup(conn, settings, args.lat, args.lng)
        if results:
            for k, v in results.items():
                print(f"  {k}: {v}")
        else:
            print("  (no county/HUC12 tables to test)")

    # Readiness verdict.
    print("\n" + "=" * 30)
    lookup_ok = bool(results) and any("error" not in str(v) and "no intersect" not in str(v)
                                      for v in results.values())
    if required_ok == required_total and required_total > 0 and lookup_ok:
        print("READY")
        return 0
    if required_ok > 0:
        print(f"PARTIAL  ({required_ok}/{required_total} required layers usable)")
        return 0
    print("NOT READY  (no required layers usable)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
