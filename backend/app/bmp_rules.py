"""Transparent, rule-based candidate BMP suggestions.

NOT AI-driven. Simple, auditable if/then rules a SWCD planner can read and
override. Every suggestion is flagged NeedsHumanReview = True with a
plain-language reason. Returns (bmps, match_strength) where match_strength is
consumed by scoring: "strong" | "moderate" | "weak" | "none".

BMPCategory values are constrained to the AppSheet enum:
  Drainage Water Management, Underground Outlet, Outlet Stabilization,
  Grassed Waterway, Erosion Control, Water Control Structure, Saturated Buffer,
  Bioreactor, Other.
"""
from typing import Any, Dict, List, Tuple

from .settings import Settings


def _norm(value: Any) -> str:
    return (value or "").strip().lower()


# ProblemType -> list of (BMPName, BMPCategory). BMPName mirrors category here
# for v1 simplicity; planners refine during review.
_PROBLEM_BMP_MAP: Dict[str, List[Tuple[str, str]]] = {
    "wet field": [
        ("Drainage Water Management", "Drainage Water Management"),
        ("Water Control Structure", "Water Control Structure"),
    ],
    "surface erosion": [
        ("Grassed Waterway", "Grassed Waterway"),
        ("Erosion Control", "Erosion Control"),
    ],
    "surface runoff": [
        ("Grassed Waterway", "Grassed Waterway"),
        ("Erosion Control", "Erosion Control"),
    ],
    "bad outlet": [
        ("Underground Outlet", "Underground Outlet"),
        ("Outlet Stabilization", "Outlet Stabilization"),
    ],
    "ditch or stream erosion": [
        ("Outlet Stabilization", "Outlet Stabilization"),
        ("Grassed Waterway", "Grassed Waterway"),
        ("Underground Outlet", "Underground Outlet"),
    ],
    "possible controlled drainage": [
        ("Drainage Water Management", "Drainage Water Management"),
        ("Water Control Structure", "Water Control Structure"),
    ],
    "unknown old tile": [
        ("Underground Outlet", "Underground Outlet"),
        ("Water Control Structure", "Water Control Structure"),
    ],
}

# Problems where proximity to water strengthens the match.
_WATER_RELATED = {"bad outlet", "ditch or stream erosion", "surface runoff"}


def suggest_bmps(
    lead: Dict[str, Any], facts: Dict[str, Any], settings: Settings
) -> Tuple[List[Dict[str, Any]], str]:
    problem = _norm(lead.get("ProblemType"))
    dist = facts.get("DistanceToWaterbodyFt")
    mean_slope = facts.get("MeanSlopePercent")
    close_to_water = dist is not None and dist <= settings.waterbody_close_threshold_ft

    out: List[Dict[str, Any]] = []
    seen = set()

    def add(name: str, category: str, reason: str, confidence: str = "Medium", notes: str = "") -> None:
        if name in seen:
            return
        seen.add(name)
        out.append({
            "BMPName": name,
            "BMPCategory": category,
            "ReasonSuggested": reason,
            "Confidence": confidence,
            "NeedsHumanReview": True,
            "Notes": notes,
        })

    mapped = _PROBLEM_BMP_MAP.get(problem)
    if mapped:
        # Determine match strength.
        if problem in _WATER_RELATED:
            if close_to_water:
                strength = "strong"
            elif dist is not None:
                strength = "moderate"
            else:
                strength = "weak"
        elif problem in ("surface erosion",):
            if mean_slope is not None and mean_slope >= settings.slope_moderate_threshold_pct:
                strength = "strong"
            elif mean_slope is not None:
                strength = "moderate"
            else:
                strength = "weak"
        else:
            strength = "moderate"

        conf = {"strong": "High", "moderate": "Medium", "weak": "Low"}[strength]
        ctx = []
        if dist is not None:
            ctx.append(f"waterbody ~{dist:.0f} ft away")
        if mean_slope is not None:
            ctx.append(f"mean slope ~{mean_slope:.1f}%")
        ctx_str = (" (" + ", ".join(ctx) + ")") if ctx else ""
        for name, category in mapped:
            add(name, category, f"ProblemType '{lead.get('ProblemType')}'{ctx_str}.", confidence=conf)
    else:
        strength = "none"

    # Water-quality / DAC context always warrants a review flag.
    if close_to_water or facts.get("WIPWLNearby") or facts.get("DACIntersecting"):
        add("Other", "Other",
            "Waterbody / WI/PWL / DAC context present.", confidence="High",
            notes="Route to SWCD; consider a conservation BMP package.")

    if not out:
        add("Other", "Other",
            "No automatic rule matched; manual review recommended.",
            confidence="Low",
            notes="Add a boundary, photos, and/or slope/soil data to improve suggestions.")

    return out, strength
