# Layer download & load notes

You must obtain the NY statewide layers yourself (see `docs/DATA_SOURCES.md` for
sources and licensing). This file is the quick "what command do I run" cheat
sheet once you have the files.

## Load order doesn't matter — load whatever you have

Every layer is optional. The backend reports a clean warning for any layer that
is missing, so load the high-value ones first (counties, HUC, hydrography) and
add the rest later.

## CRS

Load everything in **EPSG:5070** (the analysis CRS, `PROJECTED_CRS` in `.env`).
`load_layers.py` reprojects for you with `--target-crs 5070`.

## Example commands

```bash
DB="postgresql+psycopg2://agnps:agnps@localhost:5432/agnps"

python load_layers.py --db-url "$DB" --source-file data/ny_counties.gpkg        --table-name ny_counties          --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/ny_towns.gpkg           --table-name ny_towns             --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/wbdhu8.shp              --table-name huc8                 --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/wbdhu10.shp             --table-name huc10                --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/wbdhu12.shp             --table-name huc12                --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/nhd_flowline.gpkg       --table-name streams_waterbodies  --layer-name flowline --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/ny_wipwl.gpkg           --table-name wipwl_waterbodies    --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/ny_dac.gpkg             --table-name dac_areas            --target-crs 5070
python load_layers.py --db-url "$DB" --source-file data/ssurgo_ny.gpkg          --table-name ssurgo_soils         --layer-name mupolygon --target-crs 5070
```

After a bulk load, refresh indexes/stats:

```bash
psql "postgresql://agnps:agnps@localhost:5432/agnps" -f create_indexes.sql
```

## DEM / slope rasters

Rasters are NOT loaded into PostGIS in v1. Put GeoTIFF tiles in a folder and
point `DEM_TILES_DIR` at it. Use **projected** DEMs (meters) so the slope-percent
gradient calculation is correct. Set `DEM_IS_SLOPE=true` if the tiles already
store slope percent rather than elevation.
## Soil data / gSSURGO

Use the official NRCS gSSURGO state database for New York when possible.

1. Open the NRCS gSSURGO page:
   https://www.nrcs.usda.gov/resources/data-and-reports/gridded-soil-survey-geographic-gssurgo-database
2. Use **State Databases**.
3. Download the New York state gSSURGO zip.
4. Extract the `.gdb` folder into `data/raw/ssurgo/`.
5. Flatten it for this backend:

```powershell
cd backend
python scripts/prepare_ssurgo.py --gdb ..\data\raw\ssurgo\gSSURGO_NY.gdb
python scripts/load_layers.py --layer ssurgo --source-file ..\data\processed\ssurgo_soils.gpkg --layer-name ssurgo_soils
```

The flatten step joins dominant-component `drainagecl` and `hydgrp` values onto
the polygon layer so `lookup_soil()` can populate drainage and hydrologic soil
group facts.
