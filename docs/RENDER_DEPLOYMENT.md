# Render Deployment

Use the cheap v1 setup first:

1. A Render **Web Service** runs FastAPI, the draw-boundary page, and processing.
2. AppSheet/Google Sheets store leads, boundaries, facts, BMPs, and calculations.
3. The backend calls live public GIS APIs at processing time.
4. PostGIS stays optional for later.

Do not create a paid Render database until the workflow is worth scaling.

## 1. Deploy The Backend Web Service

Use the Blueprint in `render.yaml`, or configure manually:

| Render setting | Value |
| --- | --- |
| Service type | Web Service |
| Runtime | Python |
| Root directory | `backend` |
| Build command | `pip install -r requirements-web.txt` |
| Start command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |

Environment variables:

| Key | Required | Value |
| --- | --- | --- |
| `PUBLIC_GIS_LOOKUPS_ENABLED` | yes | `true` |
| `API_KEY` | recommended | Shared secret for AppSheet/Apps Script and draw-boundary URL |
| `APPSHEET_APP_ID` | recommended | AppSheet app ID |
| `APPSHEET_API_KEY` | recommended | AppSheet application access key |
| `APPSHEET_REGION` | yes | `www` unless your AppSheet account is EU |

Leave `DATABASE_URL` unset for the no-database v1 deployment.

## 2. Point AppSheet And Apps Script At Render

Set Apps Script project properties:

```text
BACKEND_URL=https://YOUR-RENDER-SERVICE.onrender.com
BACKEND_TOKEN=YOUR_API_KEY
```

Update AppSheet `BoundaryDrawURL`:

```appsheet
CONCATENATE(
  "https://YOUR-RENDER-SERVICE.onrender.com/draw_boundary.html?lead_id=",
  [LeadID],
  "&lat=",
  LAT([ProblemLocation]),
  "&lng=",
  LONG([ProblemLocation]),
  "&key=YOUR_API_KEY"
)
```

If you leave backend `API_KEY` blank, omit the `&key=...` part.

## 3. Boundary Persistence

Render's normal service filesystem is temporary across restarts and redeploys.
That is why the no-database setup should set:

```text
APPSHEET_APP_ID=...
APPSHEET_API_KEY=...
```

When those are set, `POST /save-boundary` updates:

- `Leads`: `BoundaryStatus`, `BoundarySource`, `BoundaryAreaAcres`
- `Field_Boundaries`: `BoundaryGeoJSON`, `BoundaryWKT`, centroid, acreage, validity

That makes Google Sheets/AppSheet the durable store for drawn boundaries.

## 4. Live GIS Processing

With `DATABASE_URL` unset and `PUBLIC_GIS_LOOKUPS_ENABLED=true`, `/process-lead`
uses public APIs:

| Fact | Live source |
| --- | --- |
| County and town | US Census Geocoder |
| HUC8/HUC10/HUC12 | USGS WBD ArcGIS REST service |
| Waterbody and WI/PWL | NYSDEC WI/PWL FeatureServer |
| DAC context | NY Open Data DAC 2023 Socrata API |
| Soil drainage and HSG | USDA NRCS Soil Data Access |

Each lookup fails soft. If one public service is unavailable, the backend still
returns a report-ready response with a human-review warning.

## 5. Optional PostGIS Later

Add PostGIS only when you need faster, repeatable, offline-style GIS lookups.
Then create a database, enable PostGIS, set `DATABASE_URL`, load layers from
your PC, and run `scripts/verify_layers.py`.

The no-database public API mode remains the best first deployment because it
avoids paying for storage while the workflow is still being tested.

## 6. Render Plan Notes

- Free web services can cold start after inactivity.
- Do not store production data only in Render's temporary filesystem.
- Full GIS layers can become large quickly. WI/PWL and SSURGO are the storage
  drivers, so postpone hosted PostGIS until needed.
