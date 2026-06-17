# Automation Plan

This is the flow from a saved AppSheet lead to visible output fields.

```text
Sales rep saves Lead
  -> optional Draw Boundary
  -> Submit for Processing sets Status = "Processing"
  -> Apps Script processLeads() picks up the row
  -> Apps Script POSTs to backend /process-lead
  -> Backend runs boundary/GPS geometry, GIS lookups, score, BMP rules, calculators
  -> Backend returns JSON
  -> Apps Script writes output to Leads, Auto_Facts, BMP_Candidates, Calculations
  -> AppSheet syncs and shows Report Ready results
```

## What Apps Script Processes

`processLeads()` scans the `Leads` sheet and processes a row when either of
these is true:

- `Status = "Processing"`
- `BoundaryStatus = "Drawn"` and the row is not already `Report Ready`

The `Submit for Processing` AppSheet action exists so the user can explicitly
start the output workflow instead of waiting for an automatic pickup.

Plain `Status = "New"` leads are intentionally not processed. That keeps the app
from generating early GPS-buffer outputs before the rep has had a chance to draw
a boundary.

## Script Properties

In Apps Script project settings, set:

| Property | Required | Example |
| --- | --- | --- |
| `BACKEND_URL` | yes | `https://your-backend.example.com` |
| `BACKEND_TOKEN` | optional | same value as backend `API_KEY` |

`BACKEND_URL` should not end with a slash.

If `BACKEND_TOKEN` is set, Apps Script sends it as:

```text
X-API-Key: BACKEND_TOKEN
```

## Write-Back Outputs

`Leads` receives summary output:

- `Status`
- `CandidateScore`
- `CandidateClass`
- `GISConfidence`
- `BoundaryStatus`
- `BoundarySource`
- `BoundaryAreaAcres`
- `EstimatedProjectCost`
- `EstimatedCostShareLow`
- `EstimatedCostShareHigh`
- `EstimatedFarmerCostLow`
- `EstimatedFarmerCostHigh`
- `EstimatedCompanyRevenue`
- `NextAction`
- `UpdatedAt`

`Auto_Facts` receives the GIS facts and score breakdown. With no `DATABASE_URL`,
the backend calls live public GIS APIs instead of PostGIS.

`BMP_Candidates` receives one row per suggested BMP.

`Calculations` receives cost-share and project-value assumptions.

## Failure Behavior

- HTTP/backend failure: `Status = "Needs Review"` and error text is written to `InternalNotes`.
- Backend returns `Status = "Error"`: `Status = "Needs Review"` and `ProcessingError` is written to `InternalNotes`.
- Public API or missing GIS layer issue: not an error. The backend returns any
  facts it could collect plus human-review warnings.

## Local Development

AppSheet and Apps Script cannot call your laptop's `localhost`. For testing from
AppSheet, use a tunnel or deploy the backend and set `BACKEND_URL` to that public
URL.
