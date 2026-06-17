# AppSheet setup (v0.2 — sales-friendly cleanup)

AppSheet is the **mobile intake app only**. A sales rep should feel like they’re
filling in a short lead form — not editing a database. All backend/system fields
(IDs, GPS decimals, boundary URL, score, cost-share outputs, status, report URL)
are **hidden or read-only**; the Python backend fills them in later.

> This is a UX/configuration cleanup. The data model is unchanged — every column
> still exists in `schema/Leads.csv`. We only change AppSheet column settings and
> which fields appear in the New Lead form.

## The sales workflow this produces
1. Rep opens the app → **New Lead**.
2. Enters customer / farm / field / problem info.
3. `ProblemLocation` auto-fills from phone GPS (`HERE()`).
4. Rep saves the lead.
5. Rep taps **Draw Boundary** → external map opens via `BoundaryDrawURL`.
6. Rep draws a rough boundary; backend saves it and later computes score/report.
7. AppSheet shows the results (score, class, estimates, next action) on the
   detail view **after** processing.

## 1. Data source
One Google Sheet, one tab per table (import `AgNPS_Screener.xlsx`, or the CSVs in
`schema/`). In AppSheet: **Create → App → Start with existing data** → pick the
Sheet, then **Data → Add Table** for the rest.

## 2. New Lead form — show ONLY these fields
CustomerName · FarmName · FieldName · ProblemType · ProblemDescription ·
ProblemLocation · FarmerInterestedInCostShare · PermissionToShareWithSWCD ·
Urgency · EstimatedProjectCost · InternalNotes
*(optional)* a related **Photos** inline table shown after the lead is saved.

Everything else is hidden from the form (see the table below).

## 3. Leads column configuration

Set these in **Data → Columns → Leads**. Two ways to hide from the form:
- **Column-level** `Show_If` / `Editable_If` (data-level, applies everywhere), and
- the **New Lead form view** “Column order / Include” list (view-level).
Use both; the form view is the most reliable per-view control (see APP_VIEWS.md).

> AppSheet location functions: this project uses `LAT()` / `LONG()`. If your
> account only recognizes `LATITUDE()` / `LONGITUDE()`, substitute those.
> `CONTEXT("ViewType") <> "Form"` = “show everywhere except entry forms”.

### Hidden / system fields

| Column | Type | Key | Initial value / App formula | Editable_If | Show_If | Required |
|--------|------|:---:|------------------------------|:-----------:|---------|:--------:|
| LeadID | Text | ✅ | `UNIQUEID()` | `FALSE` | `FALSE` | — |
| CreatedAt | DateTime | | `NOW()` | `FALSE` | `FALSE` | — |
| UpdatedAt | DateTime | | (see note) | `FALSE` | `FALSE` | — |
| SalesRepEmail | Email | | `USEREMAIL()` | `FALSE` | `FALSE` | — |
| SalesRepName | Text | | (see note) | `FALSE` | `FALSE` | — |
| GPSLatitude | Decimal | | `LAT([ProblemLocation])` | `FALSE` | `FALSE` | FALSE |
| GPSLongitude | Decimal | | `LONG([ProblemLocation])` | `FALSE` | `FALSE` | FALSE |
| LocationAccuracyFt | Decimal | | (see note) | `FALSE` | `FALSE` | FALSE |
| BoundaryStatus | Enum | | `"Not Started"` | `FALSE` | `CONTEXT("ViewType") <> "Form"` | — |
| BoundarySource | Enum | | `"GPS point only"` | `FALSE` | `CONTEXT("ViewType") <> "Form"` | — |
| BoundaryAreaAcres | Decimal | | (backend) | `FALSE` | `ISNOTBLANK([BoundaryAreaAcres])` | — |
| BoundaryDrawURL | Url | | *(formula below)* | `FALSE` | `FALSE` | FALSE |
| Status | Enum | | `"New"` | `FALSE` | `CONTEXT("ViewType") <> "Form"` | — |
| CandidateScore | Number | | (backend) | `FALSE` | `ISNOTBLANK([CandidateScore])` | — |
| CandidateClass | Enum/Text | | (backend) | `FALSE` | `ISNOTBLANK([CandidateClass])` | — |
| GISConfidence | Enum/Text | | (backend) | `FALSE` | `ISNOTBLANK([GISConfidence])` | — |
| EstimatedCostShareLow | Price/Decimal | | (backend) | `FALSE` | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` | — |
| EstimatedCostShareHigh | Price/Decimal | | (backend) | `FALSE` | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` | — |
| EstimatedFarmerCostLow | Price/Decimal | | (backend) | `FALSE` | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` | — |
| EstimatedFarmerCostHigh | Price/Decimal | | (backend) | `FALSE` | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` | — |
| EstimatedCompanyRevenue | Price/Decimal | | (backend) | `FALSE` | `AND(ISNOTBLANK([EstimatedProjectCost]), [Status] <> "New")` | — |
| ReportURL | Url | | (backend) | `FALSE` | `ISNOTBLANK([ReportURL])` | — |
| NextAction | Text | | (backend) | `FALSE` | `ISNOTBLANK([NextAction])` | — |

`BoundaryDrawURL` App formula:
```
CONCATENATE(
  "https://YOUR_BACKEND_DOMAIN/draw_boundary.html?lead_id=", [LeadID],
  "&lat=", LAT([ProblemLocation]),
  "&lng=", LONG([ProblemLocation])
)
```

**Notes**
- **UpdatedAt:** left read-only for now. A proper “last updated” timestamp can be
  added later (e.g. a change-triggered workflow or `NOW()` in an edit action).
- **SalesRepName:** read-only and hidden on the New Lead form. Eventually this
  should be looked up from a **Users** table keyed by `USEREMAIL()`.
- **LocationAccuracyFt:** true phone GPS accuracy is **not** captured automatically
  by AppSheet; leave it blank/read-only unless you build a custom capture method.
- **BoundaryDrawURL:** replace `YOUR_BACKEND_DOMAIN` with your real backend domain
  (or ngrok URL for testing). Append `&key=YOUR_API_KEY` only if the backend has `API_KEY` set.

### Sales-entry fields (shown on the New Lead form)

| Column | Type | Initial value | Required |
|--------|------|----------------|:--------:|
| ProblemLocation | **LatLong** | `HERE()` | **TRUE** (main location field) |
| CustomerName | Text | — | **TRUE** |
| ProblemType | Enum | — | **TRUE** |
| ProblemDescription | LongText | — | **TRUE** |
| FarmerInterestedInCostShare | Enum | — | **TRUE** |
| PermissionToShareWithSWCD | Enum | — | **TRUE** |
| Urgency | Enum | — | **TRUE** |
| FarmName | Text | — | FALSE |
| FieldName | Text | — | FALSE |
| EstimatedProjectCost | Price/Decimal | — | FALSE |
| InternalNotes | LongText | — | FALSE |

**Required = only:** CustomerName, ProblemType, ProblemDescription,
ProblemLocation, FarmerInterestedInCostShare, PermissionToShareWithSWCD, Urgency.
Make sure **no backend/output field is marked Required** (a required hidden field
blocks the rep from saving).

### Enum values
- **ProblemType:** Wet field · Surface erosion · Bad outlet · Ditch or stream erosion · Surface runoff · Possible controlled drainage · Unknown old tile · Other
- **BoundaryStatus:** Not Started · Drawn · Needs Office Review · Uploaded · Office Digitized · Processing · Error
- **BoundarySource:** GPS point only · Sales drawn boundary · Uploaded boundary · Office digitized boundary
- **Status:** New · Needs Review · Processing · Boundary Drawn · Report Ready · Sent to SWCD · Estimate Needed · Application Pending · Awarded · Not Funded · Installed · Closed
- **CandidateClass:** Strong Candidate · Possible Candidate · Weak Candidate · Poor Candidate · Needs More Info
- **GISConfidence:** Low · Medium · High

## 4. References
Make `LeadID` a **Ref** target in Photos / Field_Boundaries / Auto_Facts /
BMP_Candidates / Calculations / Status_History for inline related lists.

## 5. Actions & views
- Draw Boundary + Submit for Processing actions → **APPSHEET_ACTIONS.md**.
- New Lead / My Leads / Lead Detail / Lead Map / Report Ready / Manager Pipeline
  + slices → **APP_VIEWS.md**.
- Step-by-step editor checklist → **APPSHEET_FORM_FIXES.md**.

## 6. Wording rules
Never display “Approved”/“Eligible”. Use Internal Candidate Score, Candidate /
Needs SWCD review, and label the score: “Internal screening score — not an
official grant score.”
