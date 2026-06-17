"""Rough internal calculators: project cost estimate + cost-share split.

Every number here is a ROUGH PLANNING ESTIMATE, not a bid and not an awarded
amount. The cost placeholders live in settings.project_cost_table so the company
can change them without touching code.
"""
from typing import Any, Dict, List, Optional

from .settings import Settings


def estimate_project_cost(
    acres: Optional[float],
    problem_type: Optional[str],
    user_cost: Optional[float],
    settings: Settings,
) -> (float, List[str], List[str]):
    """Return (cost, assumptions, warnings).

    If the user entered a cost, use it. Otherwise estimate from acreage + the
    ProblemType placeholder table.
    """
    assumptions: List[str] = []
    warnings: List[str] = []

    if user_cost is not None and float(user_cost) > 0:
        assumptions.append(f"Used user-entered EstimatedProjectCost = ${float(user_cost):,.0f}.")
        return float(user_cost), assumptions, warnings

    key = (problem_type or "unknown").strip().lower()
    base, per_acre = settings.project_cost_table.get(key, settings.project_cost_default)
    acres_used = acres if (acres is not None and acres > 0) else 1.0
    if acres is None or acres <= 0:
        warnings.append("No acreage available; assumed 1 acre for the rough cost estimate.")
    cost = base + per_acre * acres_used

    assumptions.append(
        f"Placeholder estimate for '{key}': ${base:,.0f} base + ${per_acre:,.0f}/acre "
        f"x {acres_used:.2f} acres = ${cost:,.0f}."
    )
    warnings.append("Rough planning estimate only -- not a bid.")
    warnings.append("Estimate must be reviewed before customer or SWCD use.")
    return round(cost, 2), assumptions, warnings


def cost_share(
    project_cost: float,
    settings: Settings,
    low_pct: Optional[float] = None,
    high_pct: Optional[float] = None,
    margin_pct: Optional[float] = None,
) -> Dict[str, Any]:
    low_pct = settings.costshare_low_pct if low_pct is None else float(low_pct)
    high_pct = settings.costshare_high_pct if high_pct is None else float(high_pct)
    margin_pct = settings.company_margin_pct if margin_pct is None else float(margin_pct)

    cs_low = project_cost * low_pct
    cs_high = project_cost * high_pct
    return {
        "CostShareLowPercent": round(low_pct, 4),
        "CostShareHighPercent": round(high_pct, 4),
        "EstimatedCostShareLow": round(cs_low, 2),
        "EstimatedCostShareHigh": round(cs_high, 2),
        # Higher cost share -> lower farmer cost, and vice-versa.
        "EstimatedFarmerCostLow": round(project_cost - cs_high, 2),
        "EstimatedFarmerCostHigh": round(project_cost - cs_low, 2),
        "EstimatedCompanyRevenue": round(project_cost, 2),
        "EstimatedCompanyGrossMarginPercent": round(margin_pct, 4),
        "EstimatedCompanyGrossMarginDollars": round(project_cost * margin_pct, 2),
    }


def build_calculation(
    lead: Dict[str, Any],
    acres: Optional[float],
    settings: Settings,
) -> Dict[str, Any]:
    """Assemble the full Calculations record for one lead."""
    cost, assumptions, warnings = estimate_project_cost(
        acres, lead.get("ProblemType"), lead.get("EstimatedProjectCost"), settings
    )
    split = cost_share(
        cost,
        settings,
        low_pct=lead.get("CostShareLowPercent"),
        high_pct=lead.get("CostShareHighPercent"),
        margin_pct=lead.get("EstimatedCompanyGrossMarginPercent"),
    )

    warnings.append(
        "Cost-share estimate is rough and depends on program rules, farmer "
        "contribution, SWCD review, and final award."
    )

    result: Dict[str, Any] = {
        "EstimatedProjectCost": round(cost, 2),
        "Assumptions": " ".join(assumptions),
        "CalculatorWarnings": " | ".join(warnings),
    }
    result.update(split)
    return result
