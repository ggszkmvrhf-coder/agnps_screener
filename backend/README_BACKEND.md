# Backend - AgNPS Candidate Lead Screener

FastAPI service that turns a submitted lead into an internal AgNPS candidate
summary: GIS facts, an internal candidate score, rule-based BMP suggestions,
and rough cost/cost-share calculations. It also serves the Leaflet draw-boundary
page and accepts drawn polygons.

This does not determine official grant eligibility.

## Layout

```text
backend/
  main.py                FastAPI app and pipeline orchestration
  requirements.txt
  requirements-web.txt   slim Render/web requirements
  sample_payload.json
  config.example.env
  web/                   draw_boundary.html, .js, .css
  app/
    settings.py          config: CRS, radii, API keys, public GIS, cost tables
    database.py          optional PostGIS access
    public_gis.py        live public GIS API lookups for no-database mode
    geometry_utils.py    parse, buffer, validate, area, centroid
    boundary.py          /save-boundary logic and persistence hooks
    gis_lookup.py        PostGIS lookups plus public API fallback
    bmp_rules.py         transparent BMP rules
    scoring.py           internal score, breakdown, class, warnings
    calculators.py       project-cost and cost-share calculator
    report_data.py       final LeadProcessResponse assembly
    schemas.py           Pydantic request/response models
  scripts/               data download/load/verify helpers
  sql/                   optional PostGIS mirror tables
```

## Run Locally

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config.example.env .env
uvicorn main:app --reload --port 8000
```

Smoke test:

```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/debug/process-sample
start http://localhost:8000/draw_boundary.html?lead_id=TEST&lat=42.7&lng=-76.65
```

## No-Database Mode

With `DATABASE_URL` unset and `PUBLIC_GIS_LOOKUPS_ENABLED=true`, `/process-lead`
calls live public APIs:

- US Census Geocoder for county/town.
- USGS WBD for HUC12, HUC10, HUC8.
- NYSDEC WI/PWL for nearby waterbody context.
- NY Open Data DAC 2023 for disadvantaged community context.
- USDA Soil Data Access for drainage class and hydrologic soil group.

Each lookup fails soft. The response still comes back with whatever facts were
available plus human-review warnings.

## Optional PostGIS Mode

If `DATABASE_URL` is set and the layer tables exist, the backend uses PostGIS
for vector GIS lookups. This is useful later for speed, repeatability, and
hosted snapshots of large GIS layers.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | status, DB reachability, public GIS mode, layer status |
| POST | `/save-boundary` | validate GeoJSON, acreage, centroid, AppSheet push |
| POST | `/process-lead` | full pipeline; returns `LeadProcessResponse` |
| POST | `/debug/process-sample` | runs `sample_payload.json` |
| GET | `/draw_boundary.html` | static Leaflet draw page |

## Geometry Selection

1. Use `BoundaryGeoJSON` or `BoundaryWKT` in the request if present.
2. Else use a PostGIS/AppSheet/cache boundary saved by `/save-boundary`.
3. Else use the GPS point plus `POINT_BUFFER_FT`.

`AutoFacts.AnalysisGeometrySource` records which geometry was used.

## Security

Set `API_KEY` to require a key on `/save-boundary` and `/process-lead`.

- Browser draw page can pass `?key=...`.
- Apps Script sends `X-API-Key` from `BACKEND_TOKEN`.

## Behavior Contract

- Missing GPS and boundary returns `Status="Error"` with HTTP 200.
- A failed GIS lookup returns blank facts plus warnings, not a crashed request.
- Calculators still run when GIS facts are partial.
- Output always includes mandatory human-review warnings.
