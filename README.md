# AgNPS Candidate Lead Screener with NY GIS Lookups

A prototype where sales reps collect farm/project leads in **Google AppSheet**,
and a **Python backend** automatically performs New York statewide GIS lookups
and returns an internal **AgNPS candidate summary**.

> ⚠️ This is an **internal screening tool only**. It produces a *candidate that
> needs SWCD/NRCS review* — it does **not** determine official grant eligibility,
> does not submit applications, and never uses AI to invent water-quality facts.

## Architecture

```
AppSheet (mobile intake)  →  Google Sheets (storage)  →  Apps Script (bridge)
        →  Python FastAPI backend (GIS lookups + rules)  →  back into Sheets
```

* **AppSheet** = mobile form only. No GIS processing.
* **Google Sheets** = system of record for leads + results.
* **Apps Script** = forwards new leads to the backend, writes results back.
* **Python / FastAPI** = all GIS work (county/town, HUC, waterbody, WI/PWL, DAC,
  soil, slope), transparent BMP rules, and an internal candidate score.
* **PostGIS** (preferred) or **GeoPackage**-loaded layers store NY GIS data.

See [docs/GIS_LOOKUP_PLAN.md](docs/GIS_LOOKUP_PLAN.md) for why the split is this way.

## Repository layout

```
agnps_appsheet_gis_lookup_prototype/
  README.md                  ← you are here
  schema/                    AppSheet table definitions (CSV headers + example rows)
  apps_script/               Code.gs + appsscript.json (Sheets ⇆ backend bridge)
  backend/                   FastAPI app, GIS lookups, rules, scoring, loaders, SQL
  docs/                      setup, data sources, GIS plan, tests, views, automation
```

## Tech stack

Python 3.11+ · FastAPI · Uvicorn · GeoPandas · Shapely · pyproj · pyogrio/Fiona ·
Rasterio · SQLAlchemy · GeoAlchemy2 · psycopg2 · PostGIS (GeoPackage fallback) ·
Pydantic · python-dotenv.

---

## Local run — exact steps

### 1. Create a virtual environment & install requirements (Windows PowerShell)

```powershell
cd "agnps_appsheet_gis_lookup_prototype\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> GeoPandas/Rasterio wheels install cleanly on modern pip. If a GDAL-related
> wheel fails on your platform, install via conda-forge instead.

### 2. (Optional) configure

```powershell
copy config.example.env .env
# edit .env: set DATABASE_URL and/or DEM_TILES_DIR. Both are optional.
```

The backend **runs with no database**. Without one, every vector lookup is
skipped with a clear warning — useful for first-run smoke testing.

### 3. Run the FastAPI backend

```powershell
uvicorn main:app --reload --port 8000
```

### 4. Test the sample payload

```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/debug/process-sample
# or the in-process pipeline (no server needed):
python scripts\test_lookup.py
```

### 5. Set up PostGIS and load example layers (when you have data)

```powershell
# create the app tables (optional persistence layer)
psql "$env:DATABASE_PSQL_URL" -f sql\schema.sql
psql "$env:DATABASE_PSQL_URL" -f sql\indexes.sql

# load a layer (repeat per layer; see scripts\download_notes.md)
python scripts\load_layers.py `
  --db-url "postgresql+psycopg2://agnps:agnps@localhost:5432/agnps" `
  --source-file "C:\agnps_data\ny_counties.gpkg" `
  --table-name ny_counties --target-crs 5070

# spatial + attribute indexes for the loaded layers
psql "$env:DATABASE_PSQL_URL" -f scripts\create_indexes.sql
```

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for where to get the layers and
[backend/scripts/download_notes.md](backend/scripts/download_notes.md) for load commands.

### 6. Connect the Apps Script webhook

1. Open the Google Sheet backing your AppSheet app → **Extensions → Apps Script**.
2. Paste [apps_script/Code.gs](apps_script/Code.gs); copy settings from `appsscript.json`.
3. **Project Settings → Script Properties**: set `BACKEND_URL` (e.g. an ngrok URL
   to your local backend) and optionally `BACKEND_TOKEN`.
4. Run `setUpTrigger()` once (5-minute trigger), or use the **AgNPS** menu.

Full UI/automation walkthrough: [docs/APPSHEET_SETUP.md](docs/APPSHEET_SETUP.md),
[docs/AUTOMATION_PLAN.md](docs/AUTOMATION_PLAN.md), [docs/APP_VIEWS.md](docs/APP_VIEWS.md).

---

## API summary

**Endpoints:** `GET /health` · `POST /save-boundary` · `POST /process-lead` ·
`POST /debug/process-sample` · `GET /draw_boundary.html` (Leaflet draw page).

`POST /process-lead` input: `LeadID`, intake fields, `ProblemLocation` or
`GPSLatitude`/`GPSLongitude`, optional `BoundaryGeoJSON`/`BoundaryWKT`, optional
`EstimatedProjectCost`. Output (`LeadProcessResponse`): `Status`,
`CandidateScore`, `CandidateClass`, `GISConfidence`, `BoundaryStatus`,
`BoundaryAreaAcres`, `NextAction`, and nested `AutoFacts` (GIS facts + score
breakdown), `BMPCandidates[]`, and `Calculations` (project cost + cost-share
range + farmer cost range + company revenue/margin).

`POST /save-boundary` input: `LeadID`, `BoundaryGeoJSON`, `BoundarySource`;
returns `BoundaryAreaAcres` + centroid. If `API_KEY` is set, `/save-boundary`
and `/process-lead` require it (`X-API-Key` header or `?key=`).

New v0.2 docs: [DRAW_BOUNDARY_SETUP.md](docs/DRAW_BOUNDARY_SETUP.md),
[CALCULATORS.md](docs/CALCULATORS.md).

## Acceptance criteria → where they're met

* Backend runs locally → `uvicorn main:app` (step 3).
* `/health` works → returns status + which layers are loaded.
* `/process-lead` accepts the sample payload → `/debug/process-sample`.
* No layers loaded → clean warnings, no crash → see [docs/TEST_CHECKLIST.md](docs/TEST_CHECKLIST.md) #6.
* Layers loaded → county/HUC/waterbody/DAC/soil lookups run.
* Apps Script writes results into Sheets → [apps_script/Code.gs](apps_script/Code.gs).
* AppSheet build docs are manual-build ready → [docs/APPSHEET_SETUP.md](docs/APPSHEET_SETUP.md).

## What this project deliberately does NOT do

No custom mobile app · no grant eligibility claims · no application submission ·
no AI-invented water-quality facts · not hardcoded to one county · no required
layers (optional layers can be missing) · never crashes on missing optional data.
