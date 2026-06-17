# Draw Boundary setup

A simple external web map (Leaflet) lets a sales rep trace a rough field /
problem boundary. AppSheet does **not** do the polygon drawing — it just opens
this page with the lead's GPS in the URL.

## Files (`backend/web/`)
- `draw_boundary.html` — page shell, loads Leaflet + Leaflet.draw from CDN.
- `draw_boundary.js` — map, polygon draw/edit, acreage, save.
- `draw_boundary.css` — mobile-friendly styling.

Dependencies are CDN-loaded (no build step):
- Leaflet 1.9.4, Leaflet.draw 1.0.4 (from unpkg).
- Acreage uses `L.GeometryUtil.geodesicArea` (ships with Leaflet.draw).

## How it’s served
The backend serves the page as static files (mounted at `/`). With the backend
running, open: `http://localhost:8000/draw_boundary.html`.

## URL parameters
```
draw_boundary.html?lead_id=LEAD-0001&lat=42.7012&lng=-76.6543[&backend_url=...][&key=...]
```
- `lead_id` (required) — Lead key the boundary saves against.
- `lat`, `lng` (optional) — center + marker. Falls back to central NY if absent.
- `backend_url` (optional) — backend origin; defaults to where the page is served.
- `key` (optional) — API key, sent as `X-API-Key` (only if backend `API_KEY` set).

## Rep flow
1. In AppSheet, tap **Draw Boundary** (shown when ProblemLocation is set).
2. Map opens centered on the GPS point (marker shown).
3. Tap the polygon tool (top-left), trace the field, double-tap/▢ to finish.
4. Area in acres updates; edit vertices if needed.
5. Tap **Save boundary** → posts GeoJSON to `POST /save-boundary`.
6. Success message shows the saved acreage.

Only one polygon is kept — drawing a new one replaces the previous.

## What the backend does on save (`POST /save-boundary`)
1. Validates the GeoJSON polygon (auto-repairs minor self-intersections).
2. Reprojects to the equal-area CRS (EPSG:5070) and computes **acres**.
3. Computes the **centroid**.
4. Caches the boundary in `boundary_store.json` keyed by `LeadID`.
5. Returns `{ success, LeadID, BoundaryAreaAcres, BoundaryCentroidLat/Lng, message }`.

## How AppSheet learns the boundary was saved
The browser can’t write to your Sheet directly. Two options:

- **Default (simple):** the next Apps Script cycle calls `/process-lead`; the
  backend pulls the cached boundary, returns acreage + `BoundaryStatus = Drawn`,
  and Apps Script writes those into the Sheet. Set the lead’s `BoundaryStatus`
  to `Drawn` (a quick action or the rep) so it’s picked up promptly.
- **Optional (instant):** set `APPSHEET_APP_ID` + `APPSHEET_API_KEY` in the
  backend `.env`; `/save-boundary` then pushes `BoundaryStatus`/acreage straight
  into AppSheet via the AppSheet API.

## Hosting notes
- The page must be reachable from the rep’s phone. For local dev, expose the
  backend with a tunnel (e.g. `ngrok http 8000`) and use that as the domain in
  `BoundaryDrawURL`.
- Because the page and `/save-boundary` are same-origin when served by the
  backend, no CORS config is needed; CORS is open in v1 anyway for flexibility.

## Keep it simple
This is a rough-boundary tool for screening. Precise digitizing is the office’s
job (BoundaryStatus → `Office Digitized`). A loose trace is fine and SWCD review
is always required.
