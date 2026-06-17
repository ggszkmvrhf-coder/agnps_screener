# Data Layer Checklist

Track each layer from download to load to verification. The backend runs without any single layer, but missing layers produce warnings and blank facts.

## Current Status

| Layer | Table | Required v1 | Format | Downloaded | Loaded | Indexed | Verified |
| --- | --- | :---: | --- | :---: | :---: | :---: | :---: |
| HUC8 watersheds | `huc8` | yes | GeoJSON | done | todo | todo | todo |
| HUC10 watersheds | `huc10` | yes | GeoJSON | done | todo | todo | todo |
| HUC12 watersheds | `huc12` | yes | GeoJSON | done | todo | todo | todo |
| DAC boundaries | `dac_areas` | yes | GeoJSON | done | todo | todo | todo |
| County boundaries | `ny_counties` | yes | TIGER zip | done | todo | todo | todo |
| Town boundaries | `ny_towns` | yes | TIGER zip | done | todo | todo | todo |
| WI/PWL waterbodies | `wipwl_waterbodies` | yes | GeoJSON | done | todo | todo | todo |
| Streams/waterbodies | `streams_waterbodies` | yes | GDB/GeoJSON | todo | todo | todo | todo |
| SSURGO soils | `ssurgo_soils` | optional | GDB to GeoPackage | manual | todo | todo | todo |
| DEM/slope tiles | `DEM_TILES_DIR` | optional | GeoTIFF | manual | n/a | n/a | todo |

## What Done Means

- Downloaded: a file exists in `data/raw/<layer>/`.
- Loaded: `load_layers.py` wrote the table and printed a non-zero row count.
- Indexed: a GIST index exists on `geom`.
- Verified: `verify_layers.py` shows rows, valid geometry, GIST = yes, and a sample lookup match.

## Load Hints

- Counties: load with `--where "STATEFP='36'"` because the TIGER county file is national.
- Towns: already NY-only.
- HUC8/HUC10/HUC12: downloaded from the USGS WBD service as GeoJSON filtered to features whose `states` field includes `NY`.
- DAC: downloaded from data.ny.gov as GeoJSON.
- WI/PWL: download with `python scripts/download_data.py --layers wipwl`; the resulting file combines lakes, estuaries, streams, and shorelines.
- Hydrography: choose either NYS Hydrography or current USGS/3DHP/NHD products; load flowlines and waterbodies into `streams_waterbodies`.
- SSURGO: download the NY gSSURGO state FileGDB, run `prepare_ssurgo.py`, then load the processed GeoPackage.

## Sanity Expectations

- `ny_counties` filtered to NY: about 62 rows.
- `ny_towns`: about 900+ county subdivisions.
- Current HUC downloads: HUC8 = 59, HUC10 = 348, HUC12 = 1,686 features.
- DAC current download: 4,918 features.
- WI/PWL current combined download: 4,726 features.
- Invalid geometry count should be 0 or small; large counts suggest a bad source or import issue.
