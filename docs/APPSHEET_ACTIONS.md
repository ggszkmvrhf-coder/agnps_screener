# AppSheet Actions

Create these under **Behavior > Actions** in the AppSheet editor. Sales reps
should use buttons, not typed backend fields.

## 1. Draw Boundary

Opens the external boundary-drawing map for the current lead.

| Setting | Value |
| --- | --- |
| For a record of this table | `Leads` |
| Action name | `Draw Boundary` |
| Do this | `External: go to a website` |
| Target | `[BoundaryDrawURL]` |
| Only if this condition is true | `ISNOTBLANK([ProblemLocation])` |
| Prominence | Display prominently on Detail view |

Notes:

- `BoundaryDrawURL` is an App formula on the column.
- The sales rep never edits the URL directly.
- The backend stores the drawn boundary by `LeadID`.
- Processing later uses that stored boundary if the AppSheet payload does not include `BoundaryGeoJSON`.

## 2. Submit for Processing

This is the main "get outputs" action. It flags the lead for Apps Script, which
then calls the Python backend and writes results back to the Sheet/AppSheet
tables.

| Setting | Value |
| --- | --- |
| For a record of this table | `Leads` |
| Action name | `Submit for Processing` |
| Do this | `Data: set the values of some columns in this row` |
| Set these columns | `Status` = `"Processing"`, `NextAction` = `"Processing GIS lookup"` |
| Only if this condition is true | `AND(ISNOTBLANK([ProblemLocation]), IN([Status], LIST("New", "Needs Review", "Boundary Drawn")))` |
| Prominence | Display prominently on Detail view |

Apps Script now processes rows where `Status = "Processing"`. On success, it
sets `Status = "Report Ready"`. On backend or input error, it sets
`Status = "Needs Review"` and writes the error into `InternalNotes`.

## 3. Open Report

Later, when PDF reports exist:

| Setting | Value |
| --- | --- |
| For a record of this table | `Leads` |
| Action name | `Open Report` |
| Do this | `External: go to a website` |
| Target | `[ReportURL]` |
| Only if this condition is true | `ISNOTBLANK([ReportURL])` |

## Placement

- Put `Draw Boundary` and `Submit for Processing` on the Lead Detail view.
- Do not show these actions on the New Lead form.
- Do not add actions that let reps directly edit score, cost-share, GIS facts, or report fields.

