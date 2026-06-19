# AppSheet form fixes — editor checklist

Follow this top-to-bottom in the AppSheet editor to turn the current
“database editor” form into a clean sales intake form. **No schema changes**:
every column already exists in `schema/Leads.csv`; we only change settings and
which fields the New Lead form shows.

## A. Column settings (Data → Columns → Leads)

- [ ] 1. Open **Data → Columns → Leads**.
- [ ] 2. **LeadID**: Key = on, Initial value = `UNIQUEID()`, Editable_If `FALSE`, Show_If `FALSE`.
- [ ] 3. **ProblemLocation**: Type = **LatLong**, Required = on. (This is the main location field.)
- [ ] 4. **ProblemLocation**: Initial value = `HERE()`.
- [ ] 5. **GPSLatitude**: App formula = `LAT([ProblemLocation])`, Editable_If `FALSE`, Show_If `FALSE`, Required off.
- [ ] 6. **GPSLongitude**: App formula = `LONG([ProblemLocation])`, Editable_If `FALSE`, Show_If `FALSE`, Required off.
      *(If your account uses `LATITUDE()`/`LONGITUDE()`, use those instead.)*
- [ ] 7. **Hide backend fields** — set `Show_If = FALSE` (or the expression in the table below) for:
      LeadID, CreatedAt, UpdatedAt, SalesRepEmail, SalesRepName, GPSLatitude, GPSLongitude,
      LocationAccuracyFt, BoundaryStatus, BoundarySource, BoundaryAreaAcres, BoundaryDrawURL,
      Status, CandidateScore, CandidateClass, GISConfidence, EstimatedCostShareLow/High,
      EstimatedFarmerCostLow/High, EstimatedCompanyRevenue, ReportURL, NextAction.
- [ ] 8. **Remove Required** from every backend/output field (a required hidden field blocks Save).
      Required stays ON only for: CustomerName, ProblemType, ProblemDescription, ProblemLocation,
      FarmerInterestedInCostShare, Urgency.
- [ ] 9. **Make score/calculation/report fields read-only** — `Editable_If = FALSE` for
      CandidateScore, CandidateClass, GISConfidence, all Estimated* fields, ReportURL,
      NextAction, BoundaryStatus, BoundarySource, BoundaryAreaAcres, BoundaryDrawURL, Status.
- [ ] 10. **CreatedAt** `NOW()` / **SalesRepEmail** `USEREMAIL()` initial values set, both hidden + read-only.

### Show_If quick reference
| Field(s) | Show_If |
|----------|---------|
| LeadID, CreatedAt, UpdatedAt, SalesRepEmail, SalesRepName, GPSLatitude, GPSLongitude, LocationAccuracyFt, BoundaryDrawURL | `FALSE` |
| Status, BoundaryStatus, BoundarySource | `CONTEXT("ViewType") <> "Form"` |
| BoundaryAreaAcres | `ISNOTBLANK([BoundaryAreaAcres])` |
| CandidateScore / CandidateClass / GISConfidence | `ISNOTBLANK([<that field>])` |
| ReportURL / NextAction | `ISNOTBLANK([<that field>])` |
| Estimated* cost-share outputs | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` |

## B. New Lead form view (UX → Views)

- [ ] 11. Create/edit a **Form** view named **New Lead** on table **Leads**.
- [ ] 12. In the view’s **Column order / Include**, keep ONLY:
      CustomerName, FarmName, FieldName, ProblemType, ProblemDescription, ProblemLocation,
      FarmerInterestedInCostShare, Urgency, EstimatedProjectCost,
      InternalNotes. *(Optional: related Photos inline after save.)*
- [ ] 13. Confirm no system field shows in the form preview.

## C. Action

- [ ] 14. Add the **Draw Boundary** action (see APPSHEET_ACTIONS.md): External → website,
      Target `[BoundaryDrawURL]`, only if `ISNOTBLANK([ProblemLocation])`.
- [ ] 15. Add **Submit for Processing** action.

## D. Test

- [ ] 16. **Test on a phone** (not just the editor): create a lead, confirm GPS autofills,
      Save works without touching any backend field, Draw Boundary button appears, and the
      detail view shows results only after the backend fills them. See TEST_CHECKLIST.md.

## Notes / future
- `UpdatedAt` is read-only for now; add a real update timestamp later if needed.
- `SalesRepName` should eventually come from a **Users** table keyed by `USEREMAIL()`.
- `LocationAccuracyFt` isn’t auto-captured by AppSheet; leave blank unless you add custom capture.
