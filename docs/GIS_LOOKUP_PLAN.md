# GIS lookup plan

How each fact is derived, what's cheap vs. expensive, and why the architecture
splits the way it does.

## Instant vector lookups (PostGIS, index-backed)

These are single spatial queries against indexed geometry. They return in
milliseconds once layers are loaded:

| Fact(s)                                  | Operation |
|------------------------------------------|-----------|
| `CountyAuto`, `TownAuto`                 | point/boundary **intersects** county & town polygons |
| `HUC8`, `HUC10`, `HUC12`, `HUC12Name`    | intersects HUC polygons |
| `NearestWaterbodyName/Type`, `DistanceToWaterbodyFt` | `ST_DWithin` + `ST_Distance` nearest within radius |
| `WIPWLNearby`, `WIPWLSummary`            | `ST_DWithin` against WI/PWL features |
| `DACIntersecting`, `DACNearby`           | intersect, then buffered nearby check |
| `DominantSoilDrainageClass`, `DominantHydrologicSoilGroup` | intersect SSURGO, pick largest-overlap map unit |

All distances/areas use the projected CRS (EPSG:5070, meters) so results are in
real feet, not degrees.

## Raster processing (slope) — heavier, done locally + clipped

`MeanSlopePercent` / `MaxSlopePercent` require reading elevation rasters:

1. Buffer the project location.
2. Find DEM/slope tiles that intersect that footprint.
3. **Clip** the raster to the footprint (we never load whole statewide rasters).
4. Compute slope percent (gradient of elevation) and summarize mean/max.

This is why DEM processing must be **local and clipped**: statewide DEMs are
gigabytes; clipping to a field-sized window keeps each request fast and bounded.
If `DEM_TILES_DIR` is unset, the lookup returns "DEM/slope data not configured."
and the rest of the result is unaffected.

## Why AppSheet must NOT do GIS processing

* AppSheet is a no-code mobile form layer — it has no spatial engine, no PostGIS,
  no raster clipping, and no CRS transforms.
* Statewide layers (SSURGO, NHD, DEM) are far too large to ship into a mobile app.
* Spatial joins, nearest-neighbor, and raster clipping need a real backend.
* Keeping AppSheet thin (intake only) means the GIS logic is testable, versioned,
  and auditable in Python — not buried in spreadsheet formulas.

So: **AppSheet collects → Sheets stores → Apps Script forwards → Python computes.**

## Why this is only an internal screener

* Public layers are incomplete, generalized, and sometimes outdated.
* A point/boundary intersection is evidence, not a determination.
* BMP suggestions are transparent rules, not engineered designs.
* No AI invents water-quality facts — every fact is read from a loaded layer.

Therefore output is always framed as **"candidate / needs SWCD review,"** never
"approved" or "eligible." The four mandatory warnings are attached to every
result by `scoring.REQUIRED_WARNINGS`.
