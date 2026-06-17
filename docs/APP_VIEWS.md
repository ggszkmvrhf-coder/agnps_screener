# AppSheet views (v0.2 — sales-friendly)

Simple, sales-rep-first layout. Intake + review, not a GIS workstation. Column
visibility is enforced at the column level (Show_If/Editable_If, see
APPSHEET_SETUP.md) **and** by limiting each view’s included columns.

## Views

### 1. New Lead — Form
- Table: **Leads**, View type: **Form**.
- Include ONLY: CustomerName, FarmName, FieldName, ProblemType, ProblemDescription,
  ProblemLocation, FarmerInterestedInCostShare, PermissionToShareWithSWCD, Urgency,
  EstimatedProjectCost, InternalNotes.
- Optional: related **Photos** inline table after the lead is saved.
- No system/backend fields appear here.

### 2. My Leads — Deck (or Table)
- Table: **Leads**, filtered by the **My Leads** slice (`[SalesRepEmail] = USEREMAIL()`).
- Primary header: **LeadDisplayName** (see APPSHEET_LEAD_DISPLAY_NAME.md).
- Secondary header: **ProblemType**.
- Summary columns: **ProblemType**, **Status**, **CandidateScore**.

### 3. Lead Detail — Detail
- Table: **Leads**, View type: **Detail**.
- Show useful result fields; **CandidateScore / CandidateClass / GISConfidence**
  appear only once populated (their Show_If = `ISNOTBLANK(...)`).
- Put the **Draw Boundary** and **Submit for Processing** actions prominently.
- Suggested grouping: Lead info → Location & boundary (Draw Boundary, BoundaryStatus,
  BoundaryAreaAcres) → Screening result (score/class/confidence/NextAction) →
  Estimates (cost-share outputs, shown after processing) → Auto facts / BMPs (inline refs).

### 4. Lead Map — Map
- Table: **Leads**, View type: **Map**.
- Location column: **ProblemLocation**.

### 5. Report Ready — Table/Deck
- Table: **Leads**, filtered by the **Report Ready** slice (`[Status] = "Report Ready"`).
- Review queue for completed screenings.

### 6. Manager Pipeline — Table
- Table: **Leads**, View type: **Table**.
- Columns: CustomerName, County, ProblemType, Status, CandidateScore,
  EstimatedProjectCost, NextAction.
- Group by Status or CandidateClass for a pipeline overview.

## Slices (Data → Slices)

| Slice | Row filter | Used by |
|-------|------------|---------|
| **My Leads** | `[SalesRepEmail] = USEREMAIL()` | My Leads view |
| **Report Ready** | `[Status] = "Report Ready"` | Report Ready view |
| **Needs Review** | `OR([Status] = "Needs Review", [Status] = "Processing")` | review/triage |

## Display rules
- Never show “Approved”/“Eligible”. Use **Candidate / Needs SWCD review**.
- All Auto_Facts, BMP_Candidates, Calculations, boundary geometry, and score
  fields are **read-only** in the UI (system-generated).
- The boundary URL is opened by the **Draw Boundary** button — never shown as an
  editable field.
