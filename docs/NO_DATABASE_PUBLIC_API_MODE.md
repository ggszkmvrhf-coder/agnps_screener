# No-Database Public API Mode

This is the recommended v1 deployment to reduce cost.

## Flow

```text
AppSheet Lead
  -> optional Draw Boundary
  -> /save-boundary writes Field_Boundaries through AppSheet API
  -> Submit for Processing
  -> Apps Script sends Lead + BoundaryGeoJSON to /process-lead
  -> backend calls live public GIS APIs
  -> backend scores, suggests BMPs, runs calculator
  -> Apps Script writes Leads, Auto_Facts, BMP_Candidates, Calculations
```

## Backend Settings

Leave this unset:

```text
DATABASE_URL
```

Set this:

```text
PUBLIC_GIS_LOOKUPS_ENABLED=true
```

Recommended on Render:

```text
APPSHEET_APP_ID=...
APPSHEET_API_KEY=...
APPSHEET_REGION=www
```

## Live Sources

| Output facts | Source |
| --- | --- |
| County, town | US Census Geocoder |
| HUC8, HUC10, HUC12, HUC12 name | USGS WBD ArcGIS REST |
| Nearest waterbody, WI/PWL nearby, WI/PWL summary | NYSDEC WI/PWL FeatureServer |
| DAC intersecting/nearby | NY Open Data DAC 2023 Socrata API |
| Soil drainage class, hydrologic soil group | USDA NRCS Soil Data Access |
| Slope | Local DEM only, if `DEM_TILES_DIR` is configured |

## Important Limits

- Public APIs can be slower than PostGIS.
- Public APIs can occasionally fail or change fields.
- The backend handles that by returning partial facts and human-review warnings.
- This is still an internal screening tool, not an eligibility determination.

## When To Add PostGIS

Add PostGIS later if you need:

- faster batch processing,
- stable snapshots of GIS layers,
- custom layer QA,
- offline processing,
- full SSURGO/DAC/WI-PWL spatial joins at scale.
