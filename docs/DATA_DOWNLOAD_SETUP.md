# Data Download And Load Pipeline

Three scripts take you from no GIS data to working PostGIS lookups:

```powershell
cd backend
python scripts/download_data.py --layers all
python scripts/load_layers.py --all
python scripts/verify_layers.py
```

All paths live under `data/`:

```text
data/raw/{huc8,huc10,huc12,wipwl,dac,counties,towns,hydrography,ssurgo,dem}/
data/processed/
data/logs/
```

## V1 Priority Layers

The first useful backend should load:

- HUC8, HUC10, and HUC12 watersheds
- County and town boundaries
- DAC boundaries
- WI/PWL waterbodies
- Streams/waterbodies

SSURGO/gSSURGO and DEM/slope are optional for v1. If missing, the backend returns clear warnings and keeps processing.

## Automatic Vs Manual

| Layer | Table | Mode | Official source |
| --- | --- | --- | --- |
| counties | `ny_counties` | Auto, national TIGER 2025 zip, filter `STATEFP='36'` on load | US Census TIGER/Line Counties |
| towns | `ny_towns` | Auto, NY TIGER 2025 county subdivisions zip | US Census TIGER/Line County Subdivisions |
| huc8 | `huc8` | Auto, ArcGIS REST GeoJSON by object-ID chunks | USGS Watershed Boundary Dataset |
| huc10 | `huc10` | Auto, ArcGIS REST GeoJSON by object-ID chunks | USGS Watershed Boundary Dataset |
| huc12 | `huc12` | Auto, ArcGIS REST GeoJSON by object-ID chunks | USGS Watershed Boundary Dataset |
| wipwl | `wipwl_waterbodies` | Auto, ArcGIS REST GeoJSON by object-ID chunks | NYSDEC WI/PWL FeatureServer |
| dac | `dac_areas` | Auto, Socrata GeoJSON | NYSERDA / data.ny.gov Final DAC 2023 |
| hydrography | `streams_waterbodies` | Manual | USGS NHD/3DHP or NYS hydrography |
| ssurgo | `ssurgo_soils` | Manual download plus `prepare_ssurgo.py` | USDA NRCS gSSURGO / SSURGO |
| dem | raster tiles | Manual | USGS 3DEP / NYS DEM |

Auto layers download from official service endpoints or stable public file URLs. If a configured URL or service fails, the script logs the failure, keeps going, and shows the manual fallback. TIGER year and service URLs live in `backend/scripts/download_data.py`.

## Download

Examples:

```powershell
cd backend
python scripts/download_data.py --layers huc8 huc10 huc12 counties towns dac
python scripts/download_data.py --layers wipwl
python scripts/download_data.py --layers all --force
```

The script logs each run to `data/logs/` and prints `OK`, `PRESENT`, `MANUAL`, or `FAILED` per layer.

## Load

Examples:

```powershell
cd backend
python scripts/load_layers.py --layer counties --where "STATEFP='36'"
python scripts/load_layers.py --layer towns
python scripts/load_layers.py --layer huc8
python scripts/load_layers.py --layer huc10
python scripts/load_layers.py --layer huc12
python scripts/load_layers.py --layer dac
python scripts/load_layers.py --layer wipwl
```

`load_layers.py` accepts GeoJSON, Shapefile, zipped Shapefile, GeoPackage, and FileGDB sources. It reprojects to EPSG:5070, writes to PostGIS, creates a GIST index on `geom`, and records best-effort metadata in `gis_layers_metadata`.

## Verify

```powershell
cd backend
python scripts/verify_layers.py
```

The verifier checks table existence, row counts, invalid geometry counts, GIST indexes, and one sample NY lookup.

## Soil / gSSURGO

For soil drainage and hydrologic soil group facts:

1. Open the NRCS gSSURGO page.
2. Use State Databases.
3. Download the New York gSSURGO zip.
4. Extract the `.gdb` folder into `data/raw/ssurgo/`.
5. Prepare and load it:

```powershell
cd backend
python scripts/prepare_ssurgo.py --gdb ..\data\raw\ssurgo\gSSURGO_NY.gdb
python scripts/load_layers.py --layer ssurgo --source-file ..\data\processed\ssurgo_soils.gpkg --layer-name ssurgo_soils
```

## DEM / Slope

DEM is not loaded into PostGIS. Put projected GeoTIFF tiles in a folder and set `DEM_TILES_DIR` in `backend/.env`. Avoid statewide DEM downloads for v1; use tiles around working areas.

