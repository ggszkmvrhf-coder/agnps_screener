# Draw Boundary Setup

A simple external web map lets a sales rep trace a rough field or problem
boundary. AppSheet opens this page; the backend validates and saves the polygon.

## Files

- `backend/web/draw_boundary.html`: page shell, Leaflet, and Leaflet.draw.
- `backend/web/draw_boundary.js`: map, polygon draw/edit, acreage, save.
- `backend/web/draw_boundary.css`: mobile-friendly styling.

Dependencies are loaded from CDNs, so there is no frontend build step.

## How It Is Served

The backend serves the page as static files. With the backend running, open:

```text
http://localhost:8000/draw_boundary.html
```

## URL Parameters

```text
draw_boundary.html?lead_id=LEAD-0001&lat=42.7012&lng=-76.6543[&backend_url=...][&key=...]
```

- `lead_id` is required and must match the `Leads[LeadID]` key.
- `lat` and `lng` center the map and show the starting marker.
- `backend_url` is optional; it defaults to the same origin as the page.
- `key` is optional; it is sent as `X-API-Key` when backend `API_KEY` is set.

## Rep Flow

1. In AppSheet, tap **Draw Boundary**.
2. The map opens centered on the lead's GPS point.
3. Tap the polygon tool, trace the field, and finish the shape.
4. Edit vertices if needed.
5. Tap **Save boundary**.
6. The page posts GeoJSON to `POST /save-boundary`.
7. The success message shows the saved acreage.

Only one polygon is kept for the lead; drawing a new one replaces the previous
rough boundary.

## What The Backend Saves

`POST /save-boundary`:

1. Validates the GeoJSON polygon.
2. Repairs minor topology issues when possible.
3. Reprojects to EPSG:5070 and computes acres.
4. Computes the centroid.
5. Caches the boundary in `boundary_store.json`.
6. If AppSheet API settings are present, updates `Leads`.
7. If AppSheet API settings are present, upserts `Field_Boundaries` with:
   `BoundaryGeoJSON`, `BoundaryWKT`, centroid, acreage, confidence, and validity.

## Recommended Render Setup

Set these backend environment variables:

```text
APPSHEET_APP_ID=your-app-id
APPSHEET_API_KEY=your-application-access-key
APPSHEET_REGION=www
```

That makes AppSheet/Google Sheets the durable boundary store. This matters on
Render because normal service filesystem changes can disappear after restarts
or redeploys.

## Local Fallback

If AppSheet API credentials are not set, the backend uses `boundary_store.json`.
That is fine for local testing, but not durable enough for real Render use.

## Keep It Simple

This is a rough-boundary tool for screening. Precise digitizing is an office or
SWCD review task. A loose trace is fine, and human review is always required.
