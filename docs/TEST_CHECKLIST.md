# Test checklist (v0.2)

End-to-end manual tests. Backend-only tests (8, 14–17 in *Full flow*) can run
with `python scripts/test_lookup.py case.json` or by POSTing to the API;
AppSheet tests need the app + Apps Script wired up.

## Why this matters
- Sales reps should only enter **customer / problem / location** info.
- Backend fields (IDs, GPS decimals, score, cost-share, status, URLs) should be
  **hidden or read-only** — never typed by the rep.
- The app should feel like a **lead intake app, not a database editor**.
- The boundary URL is opened by a **button** (Draw Boundary), never edited by hand.

## Sales form / UX tests (run on a phone)
| # | Test | Expected |
|---|------|----------|
| 1 | Create a new lead | New Lead form opens with only the 11 sales fields. |
| 2 | LeadID auto-generates | `UNIQUEID()` sets a key (not shown/edited). |
| 3 | CreatedAt auto-generates | `NOW()` set on save (hidden). |
| 4 | SalesRepEmail auto-fills | `USEREMAIL()` populated (hidden). |
| 5 | ProblemLocation auto-fills | `HERE()` fills current GPS. |
| 6 | GPSLatitude/Longitude auto-calc | `LAT()/LONG([ProblemLocation])` computed, not typed. |
| 7 | System fields hidden on form | No LeadID/GPS/Status/score/cost-share/URL fields in the form. |
| 8 | Draw Boundary appears | Button shows once `ProblemLocation` is set; opens the map. |
| 9 | BoundaryDrawURL not a typed field | URL is never shown as an editable text box. |
| 10 | Score fields hidden until filled | CandidateScore/Class/GISConfidence appear only after backend fills them. |
| 11 | Save without backend fields | Rep can save touching only sales fields (no required hidden field blocks Save). |
| 12 | Lead on map | New lead shows on the Lead Map at its `ProblemLocation`. |
| 13 | Detail shows results when populated | Status + result/estimate fields visible on Lead Detail after processing. |

## Full flow

| # | Test | Expected |
|---|------|----------|
| 1 | Create a lead in AppSheet | Row created with `Status = New`. |
| 2 | ProblemLocation autofills | `HERE()` fills GPS on the new record. |
| 3 | LeadID generates | `UNIQUEID()` produces a key. |
| 4 | BoundaryDrawURL generates | URL contains lead_id, lat, lng. |
| 5 | Tap **Draw Boundary** | External map opens centered on the GPS point. |
| 6 | Draw polygon | Polygon drawn; area in acres shown. |
| 7 | Save boundary | Success message; `POST /save-boundary` returns success. |
| 8 | Backend calculates acres | `BoundaryAreaAcres` returned and > 0. |
| 9 | Sheet shows BoundaryStatus = Drawn | After reconcile/push, lead shows `Drawn`. |
| 10 | Run process lead | Apps Script posts to `/process-lead`. |
| 11 | Auto_Facts populated | New Auto_Facts row incl. score breakdown. |
| 12 | CandidateScore populated | `CandidateScore` + `CandidateClass` on lead. |
| 13 | Calculations populated | New Calculations row; estimates mirrored to lead. |
| 14 | No boundary | Backend uses GPS point + 500 ft buffer; `AnalysisGeometrySource` says so. |
| 15 | Missing GPS | `Status = Error`, clear `ProcessingError`, no crash. |
| 16 | No EstimatedProjectCost | Cost estimated from acreage + ProblemType placeholders. |
| 17 | EstimatedProjectCost entered | Backend uses the user value verbatim. |
| 18 | Warnings present | `HumanReviewWarnings` state SWCD/human review required + not eligibility. |

## GIS-layer resilience (from v1, still required)
- No DB / no layers → clean warnings, partial result, no crash.
- DEM unset → slope null + “DEM/slope data not configured.”
- SSURGO polygons only → “Soil polygon found, but drainage-class table not loaded.”

## Always verify
- [ ] `Status` is never “Approved”/“Eligible”.
- [ ] `CandidateScore` 0–100; `CandidateClass` in the allowed enum.
- [ ] Every BMP has `NeedsHumanReview = true`.
- [ ] Calculator outputs carry “rough estimate / not a bid” warnings.
- [ ] Response shape complete even when most layers are missing.

## Quick API checks
```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/debug/process-sample
# save a boundary
curl -X POST http://localhost:8000/save-boundary -H "Content-Type: application/json" `
  -d '{"LeadID":"LEAD-DRAW","BoundaryGeoJSON":{"type":"Polygon","coordinates":[[[-76.656,42.700],[-76.653,42.700],[-76.653,42.702],[-76.656,42.702],[-76.656,42.700]]]}}'
```
