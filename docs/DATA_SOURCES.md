# Data sources

This prototype does **not** ship any GIS data. You must download the NY
statewide layers yourself and load them with `backend/scripts/load_layers.py`.
Until a layer is loaded, the matching lookup returns a clean warning and the
backend keeps running.

Always confirm the current authoritative source and license/terms before use.
Public layers change URLs and schemas over time, and field names vary — the
backend reads attributes defensively for this reason.

| Layer (table)                       | What it is                                   | Typical source |
|-------------------------------------|----------------------------------------------|----------------|
| `ny_counties`                       | NY county boundaries                         | NYS GIS Clearinghouse / US Census TIGER |
| `ny_towns`                          | NY town / municipal boundaries               | NYS GIS Clearinghouse (Civil Boundaries) |
| `huc8` / `huc10` / `huc12`          | Watershed Boundary Dataset (HUC 8/10/12)     | USGS WBD ArcGIS REST service; filtered to features whose `states` field includes `NY` |
| `streams_waterbodies`               | Hydrography (streams + waterbodies)          | USGS NHD, or NYS hydrography |
| `wipwl_waterbodies`                 | NY Waterbody Inventory / Priority Waterbodies List | NYSDEC WI/PWL FeatureServer layers for lakes, estuaries, streams, and shorelines |
| `dac_areas`                         | NY Disadvantaged Communities boundaries      | NYSERDA / data.ny.gov Final DAC 2023 Socrata GeoJSON |
| `ssurgo_soils`                      | SSURGO/gSSURGO soil map units (+ drainage/HSG attributes)| USDA NRCS gSSURGO state database or SSURGO/Web Soil Survey county exports |
| DEM / slope rasters (`DEM_TILES_DIR`)| Elevation or pre-computed slope tiles        | USGS 3DEP / NYS digital elevation data |

## Format & CRS

* Vector layers: GeoPackage (`.gpkg`), Shapefile (`.shp`), or GeoJSON.
* Load everything in **EPSG:5070** (the analysis CRS) via `--target-crs 5070`.
* DEM/slope rasters: GeoTIFF, **projected** (meters) so slope-percent is correct.
  If your tiles already store slope percent, set `DEM_IS_SLOPE=true`.

## SSURGO note

The recommended v1 soil path is the **USDA NRCS gSSURGO New York state
database** because it packages statewide polygons and attribute tables in one
File Geodatabase. It is distributed from NRCS/Box/GDG rather than a small stable
direct URL, so it remains a manual download.

The soil polygons (`MUPOLYGON` / `mupolygon`) give you the map unit footprint.
Drainage class and hydrologic soil group live in the **component / muaggatt**
attribute tables. If you only load polygons, the backend reports:

> "Soil polygon found, but drainage-class table not loaded."

Join or pre-flatten the drainage/HSG attributes onto the polygon table (columns
`drainagecl`, `hydgrp`) to populate those facts.

For a first usable soil layer, export or build a flattened `ssurgo_soils`
GeoPackage/GeoJSON with at least:

* `mukey`
* `musym`
* `muname`
* `drainagecl`
* `hydgrp`
* polygon geometry

## Licensing / attribution

Respect each provider's terms. This is an internal screening tool; it must not
be presented as an official determination of grant eligibility or water-quality
status (see `GIS_LOOKUP_PLAN.md`).
