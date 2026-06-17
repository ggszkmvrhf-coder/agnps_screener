# Backend — AgNPS Candidate Lead Screener (v0.2)

FastAPI service that turns a submitted lead into an internal AgNPS **candidate**
summary: NY GIS lookups, an Internal Candidate Score, rule-based BMP
suggestions, and rough cost/cost-share calculators. It also serves the Leaflet
**Draw Boundary** page and accepts the drawn boundary. It does **not** determine
official grant eligibility.

## Layout
```
backend/
  main.py                FastAPI app + pipeline orchestration
  requirements.txt
  sample_payload.json
  config.example.env     copy to .env
  web/                   draw_boundary.html / .js / .css (Leaflet draw page)
  app/
    settings.py          config: CRS, radii, cost tables, API key, web/store paths
    database.py          fail-soft PostGIS access
    geometry_utils.py    parse/buffer/validate/area/centroid/choose-geometry
    boundary.py          /save-boundary logic + JSON boundary cache
    gis_lookup.py        county/town, HUC, waterbody, WI/PWL, DAC, soil, slope
    bmp_rules.py         transparent BMP rules (+ match strength for scoring)
    scoring.py           Internal Candidate Score + breakdown + class + warnings
    calculators.py       project-cost estimator + cost-share calculator
    report_data.py       final LeadProcessResponse assembly + NextAction
    schemas.py           Pydantic request/response models
  scripts/               load_layers.py, create_indexes.sql, test_lookup.py, ...
  sql/                   schema.sql, indexes.sql (leads/boundaries/facts/bmp/calc)
```

## Run (Windows PowerShell)
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config.example.env .env        # optional; edit DATABASE_URL / DEM_TILES_DIR / API_KEY
uvicorn main:app --reload --port 8000
```

Smoke test:
```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/debug/process-sample
# Draw page (open in a browser):
start http://localhost:8000/draw_boundary.html?lead_id=TEST&lat=42.7&lng=-76.65
```

Runs with **no database** — every vector lookup is skipped with a clear warning.

## Endpoints
| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | status, DB reachability, layers loaded, api_key_required |
| POST | `/save-boundary` | validate GeoJSON polygon → acres/centroid → cache |
| POST | `/process-lead` | full pipeline; returns `LeadProcessResponse` |
| POST | `/debug/process-sample` | runs `sample_payload.json` |
| GET | `/draw_boundary.html` (+ .js/.css) | static Leaflet draw page |

### Geometry selection in /process-lead
1. Valid `Field_Boundary` (payload GeoJSON/WKT, or the cached boundary from
   `/save-boundary`) → use the **boundary polygon**.
2. Otherwise use the **GPS point + 500 ft buffer** (`POINT_BUFFER_FT`).
3. `AutoFacts.AnalysisGeometrySource` records which was used.

## Security (optional API key)
Set `API_KEY` in `.env` to require a key on `/save-boundary` and `/process-lead`:
- header `X-API-Key: <key>`, or `?key=<key>` for the browser draw page.
- Apps Script sends it via the `BACKEND_TOKEN` script property.
- If `API_KEY` is unset (default), those endpoints are open — fine for local dev.

## Behavior contract
- Missing GPS *and* boundary → `Status="Error"` + `ProcessingError` (HTTP 200).
- Any single missing layer → that fact null/false + a warning; others still run.
- DB unreachable → vector lookups skipped with one warning; slope still runs if `DEM_TILES_DIR` set.
- Calculators always run; outputs are flagged rough estimates, never bids/awards.
- Output always includes the four mandatory human-review warnings.

See the repo root `README.md` and `docs/` for AppSheet, Draw Boundary, and
calculator details.
