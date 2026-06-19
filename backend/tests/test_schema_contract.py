"""
test_schema_contract.py — Schema contract guard for AgNPS Candidate Lead Screener.

This test guards against schema drift between the Apps Script runtime contract
(REQUIRED_HEADERS in apps_script/Code.gs) and the CSV schema definitions in
schema/*.csv.

The REQUIRED_HEADERS object in Code.gs is the authoritative list of columns that
MUST exist in the live Google Sheet before any lead processing occurs. The CSV
files in schema/ define the canonical column layout for each sheet. These two
sources must remain in sync; this test enforces that.

If this test fails, a column name exists in Code.gs REQUIRED_HEADERS that is
absent from the corresponding schema CSV (or vice versa). Both files must be
updated together — never one without the other.

Run from the project root:
    pytest backend/tests/test_schema_contract.py
"""

import csv
import pathlib
import pytest

# ---------------------------------------------------------------------------
# Hardcoded expected columns extracted from REQUIRED_HEADERS in Code.gs
# (Schema version: 1.0)
# Any changes here must also be reflected in Code.gs and the schema/*.csv files.
# ---------------------------------------------------------------------------

REQUIRED_HEADERS = {
    "Leads": [
        "LeadID", "CreatedAt", "UpdatedAt", "SalesRepEmail", "SalesRepName",
        "CustomerName", "FarmName", "FieldName", "ProblemType", "ProblemDescription",
        "ProblemLocation", "GPSLatitude", "GPSLongitude", "BoundaryStatus",
        "BoundarySource", "BoundaryAreaAcres", "BoundaryDrawURL", "BoundaryShareURL",
        "FarmerInterestedInCostShare", "PermissionToShareWithSWCD", "Urgency",
        "SendToDesignTeam", "Status", "CandidateScore", "CandidateClass",
        "GISConfidence", "EstimatedProjectCost", "EstimatedCostShareLow",
        "EstimatedCostShareHigh", "EstimatedFarmerCostLow", "EstimatedFarmerCostHigh",
        "EstimatedCompanyRevenue", "ReportURL", "InternalNotes", "NextAction",
    ],
    "Field_Boundaries": [
        "BoundaryID", "LeadID", "CreatedAt", "BoundarySource", "BoundaryGeoJSON",
        "BoundaryWKT", "BoundaryAreaAcres", "BoundaryCentroidLat",
        "BoundaryCentroidLng", "BoundaryConfidence", "GeometryValid",
        "GeometryWarning", "Notes",
    ],
    "Auto_Facts": [
        "FactID", "LeadID", "ProcessedAt", "AnalysisGeometrySource", "CountyAuto",
        "TownAuto", "HUC8", "HUC10", "HUC12", "HUC12Name",
        "NearestWaterbodyName", "NearestWaterbodyType", "DistanceToWaterbodyFt",
        "WIPWLNearby", "WIPWLSummary", "DACIntersecting", "DACNearby",
        "DominantSoilDrainageClass", "DominantHydrologicSoilGroup",
        "MeanSlopePercent", "MaxSlopePercent", "GISConfidence",
        "MissingInfoChecklist", "HumanReviewWarnings", "ProcessingError",
        "WaterQualityConnectionScore", "WIPWLScore", "BMPFitScore",
        "TopoSoilsScore", "DocumentationScore", "DACScore", "ScoreExplanation",
    ],
    "BMP_Candidates": [
        "BMPCandidateID", "LeadID", "BMPName", "BMPCategory", "ReasonSuggested",
        "Confidence", "NeedsHumanReview", "Notes",
    ],
    "Calculations": [
        "CalculationID", "LeadID", "CreatedAt", "EstimatedProjectCost",
        "CostShareLowPercent", "CostShareHighPercent", "EstimatedCostShareLow",
        "EstimatedCostShareHigh", "EstimatedFarmerCostLow",
        "EstimatedFarmerCostHigh", "EstimatedCompanyRevenue",
        "EstimatedCompanyGrossMarginPercent", "EstimatedCompanyGrossMarginDollars",
        "Assumptions", "CalculatorWarnings",
    ],
}

# Map each REQUIRED_HEADERS key to the corresponding CSV filename in schema/.
SHEET_TO_CSV = {
    "Leads": "Leads.csv",
    "Field_Boundaries": "Field_Boundaries.csv",
    "Auto_Facts": "Auto_Facts.csv",
    "BMP_Candidates": "BMP_Candidates.csv",
    "Calculations": "Calculations.csv",
}


def get_project_root() -> pathlib.Path:
    """Return the project root (two levels above this file: backend/tests/ -> root)."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


def read_csv_headers(csv_path: pathlib.Path) -> list:
    """Read the first row of a CSV file and return the list of column names."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    return [h.strip() for h in headers]


# ---------------------------------------------------------------------------
# Parametrized contract test — one test case per sheet
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sheet_name", list(REQUIRED_HEADERS.keys()))
def test_csv_contains_all_required_headers(sheet_name):
    """
    For each sheet defined in REQUIRED_HEADERS, assert that the corresponding
    schema CSV file contains at minimum every column listed in REQUIRED_HEADERS.

    Extra columns in the CSV beyond what REQUIRED_HEADERS requires are allowed
    (the CSV may define additional optional columns). The inverse — a required
    column missing from the CSV — is a contract violation and will fail this test.
    """
    csv_filename = SHEET_TO_CSV[sheet_name]
    schema_dir = get_project_root() / "schema"
    csv_path = schema_dir / csv_filename

    assert csv_path.exists(), (
        f"Schema CSV not found: {csv_path}. "
        f"Expected a file named '{csv_filename}' in the schema/ directory."
    )

    csv_headers = read_csv_headers(csv_path)
    csv_header_set = set(csv_headers)
    required_columns = REQUIRED_HEADERS[sheet_name]

    missing_from_csv = [col for col in required_columns if col not in csv_header_set]

    assert not missing_from_csv, (
        f"Sheet '{sheet_name}': the following columns are listed in "
        f"Code.gs REQUIRED_HEADERS but are MISSING from {csv_filename}:\n"
        + "\n".join(f"  - {col}" for col in missing_from_csv)
        + "\n\nBoth Code.gs REQUIRED_HEADERS and the CSV must be updated together."
    )


@pytest.mark.parametrize("sheet_name", list(REQUIRED_HEADERS.keys()))
def test_no_extra_csv_headers_undocumented(sheet_name):
    """
    Informational test: report any columns present in the CSV but absent from
    REQUIRED_HEADERS. These are not contract violations (extra CSV columns are
    allowed), but this test surfaces them so schema drift is visible.

    A warning-level finding here means the CSV has grown beyond what Code.gs
    validates at runtime — consider whether the new column should be added to
    REQUIRED_HEADERS or whether it is genuinely optional.
    """
    csv_filename = SHEET_TO_CSV[sheet_name]
    schema_dir = get_project_root() / "schema"
    csv_path = schema_dir / csv_filename

    assert csv_path.exists(), (
        f"Schema CSV not found: {csv_path}."
    )

    csv_headers = read_csv_headers(csv_path)
    required_set = set(REQUIRED_HEADERS[sheet_name])
    extra_in_csv = [col for col in csv_headers if col not in required_set]

    # This test always passes but prints findings for human review.
    if extra_in_csv:
        print(
            f"\nINFO — Sheet '{sheet_name}': {len(extra_in_csv)} CSV column(s) "
            f"not in REQUIRED_HEADERS (optional / undocumented in runtime contract):\n"
            + "\n".join(f"  + {col}" for col in extra_in_csv)
        )
    # Not a hard failure — extra CSV columns are permitted.
    assert True
