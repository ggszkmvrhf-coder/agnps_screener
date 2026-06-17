# AppSheet Lead Display Name Fix

If the Leads panel shows `LeadID` or a blank title instead of the client/farm
name, AppSheet is using the key column as the display label.

## Recommended fix

Create a virtual column on the `Leads` table:

| Setting | Value |
| --- | --- |
| Name | `LeadDisplayName` |
| Type | Text |
| App formula | formula below |

```appsheet
IF(
  ISNOTBLANK([CustomerName]),
  CONCATENATE(
    [CustomerName],
    IF(ISNOTBLANK([FarmName]), CONCATENATE(" - ", [FarmName]), ""),
    IF(ISNOTBLANK([FieldName]), CONCATENATE(" / ", [FieldName]), "")
  ),
  CONCATENATE("Lead ", [LeadID])
)
```

Then go to **Data > Columns > Leads**:

- Keep `LeadID` as the **Key**.
- Mark `LeadDisplayName` as the **Label**.

## View settings

For the `My Leads` deck/table view:

- Primary header: `LeadDisplayName`
- Secondary header: `ProblemType`
- Summary columns: `Status`, `CandidateScore`, `CandidateClass`

For the `Lead Detail` view, make sure the header/title also uses
`LeadDisplayName` if AppSheet exposes that setting in the view editor.

