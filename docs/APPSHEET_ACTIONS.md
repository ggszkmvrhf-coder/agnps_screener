# AppSheet actions

Create these under **Behavior → Actions** in the AppSheet editor. They keep the
sales rep on buttons (not typed fields) and drive the workflow.

## 1. Draw Boundary (primary rep action)

Opens the external boundary-drawing map for the current lead.

| Setting | Value |
|---------|-------|
| For a record of this table | **Leads** |
| Action name | **Draw Boundary** |
| Do this | **External: go to a website** |
| Target | `[BoundaryDrawURL]` |
| Only if this condition is true | `ISNOTBLANK([ProblemLocation])` |
| Prominence | Display prominently (Detail view) |

Notes:
- `BoundaryDrawURL` is an App formula on the column (see APPSHEET_SETUP.md). The
  rep never sees or edits the URL — they only tap this button.
- The button is hidden until `ProblemLocation` exists, so it appears after the
  lead is saved with a GPS point.

## 2. Submit for Processing (optional placeholder)

Lets a rep (or manager) explicitly flag a lead for the backend. Optional — the
Apps Script automation already picks up `New`/`Boundary Drawn` leads on a timer.

| Setting | Value |
|---------|-------|
| For a record of this table | **Leads** |
| Action name | **Submit for Processing** |
| Do this | **Data: set the values of some columns in this row** |
| Set columns | `Status` = `"Processing"` |
| Only if this condition is true | `AND(ISNOTBLANK([ProblemLocation]), [Status] = "New")` |
| Prominence | Display prominently (Detail view) |

Notes:
- This is just a status nudge; the actual GIS work happens in the Python backend
  via Apps Script. Don’t set scores or estimates from AppSheet.
- After processing, Apps Script sets `Status` to `Report Ready` (or `Needs
  Review` on error).

## Action placement
- Put **Draw Boundary** and **Submit for Processing** on the **Lead Detail**
  view (prominent), not on the New Lead form.
- Keep the system **read-only**: do not add actions that let reps edit
  CandidateScore, cost-share outputs, BoundaryDrawURL, or Status (beyond the
  controlled “Processing” nudge above).
