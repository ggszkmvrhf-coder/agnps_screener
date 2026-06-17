"""Internal Candidate Score (0-100) + breakdown, class, and required warnings.

This is an INTERNAL prioritization aid. It is NOT an official grant score and
never determines eligibility. The breakdown is returned so the result is fully
explainable to a SWCD reviewer.
"""
from typing import Any, Dict, List, Tuple

from .settings import Settings

REQUIRED_WARNINGS = [
    "Internal screening only -- this is a candidate, not an approval.",
    "SWCD / NRCS or other qualified human review is required.",
    "This is NOT an official grant eligibility determination.",
    "Public GIS layers may be incomplete or outdated.",
]

# Component caps.
MAX = {
    "WaterQualityConnectionScore": 25,
    "WIPWLScore": 20,
    "BMPFitScore": 20,
    "TopoSoilsScore": 15,
    "DocumentationScore": 10,
    "DACScore": 10,
}


def _water_quality(facts: Dict[str, Any]) -> Tuple[int, str]:
    dist = facts.get("DistanceToWaterbodyFt")
    if dist is None:
        # No mapped waterbody distance: small credit if any water context exists.
        if facts.get("WIPWLNearby"):
            return 5, "No distance, but a WI/PWL feature is nearby."
        return 0, "No mapped waterbody connection established."
    if dist <= 250:
        return 25, f"Mapped waterbody very close (~{dist:.0f} ft)."
    if dist <= 1000:
        return 20, f"Mapped waterbody close (~{dist:.0f} ft)."
    if dist <= 2500:
        return 15, f"Mapped waterbody nearby (~{dist:.0f} ft)."
    if dist <= 5000:
        return 8, f"Mapped waterbody at moderate distance (~{dist:.0f} ft)."
    return 5, f"Mapped waterbody far (~{dist:.0f} ft)."


def _wipwl(facts: Dict[str, Any]) -> Tuple[int, str]:
    if not facts.get("WIPWLNearby"):
        return 0, "No WI/PWL feature nearby."
    summary = (facts.get("WIPWLSummary") or "").lower()
    if "status" in summary or "pollutant" in summary or "assessment" in summary:
        return 20, "Relevant WI/PWL record nearby with assessment/pollutant detail."
    if "waterbody" in summary or "name" in summary:
        return 12, "WI/PWL feature nearby; relevance unclear."
    return 8, "WI/PWL feature nearby with weak attributes."


def _bmp_fit(match_strength: str) -> Tuple[int, str]:
    return {
        "strong": (20, "Strong problem/BMP match."),
        "moderate": (12, "Moderate problem/BMP match."),
        "weak": (5, "Weak problem/BMP match."),
        "none": (0, "No clear problem/BMP match."),
    }.get(match_strength, (0, "No clear problem/BMP match."))


def _topo_soils(facts: Dict[str, Any], settings: Settings) -> Tuple[int, str]:
    slope = facts.get("MeanSlopePercent")
    has_soil = bool(facts.get("DominantHydrologicSoilGroup") or facts.get("DominantSoilDrainageClass"))
    if slope is not None and has_soil:
        if slope >= settings.slope_high_threshold_pct:
            return 15, f"Strong physical evidence (slope ~{slope:.1f}% + soils)."
        return 12, f"Slope (~{slope:.1f}%) and soils available."
    if slope is not None or has_soil:
        return 8, "Partial topography/soils evidence."
    return 0, "No topography/soils evidence available."


def _documentation(lead: Dict[str, Any], geom_kind: str) -> Tuple[int, List[str]]:
    pts = 0
    notes = []
    if lead.get("GPSLatitude") is not None or lead.get("ProblemLocation"):
        pts += 2
        notes.append("GPS point provided (+2)")
    photos = lead.get("Photos") or lead.get("PhotoCount")
    photo_n = len(photos) if isinstance(photos, (list, tuple)) else (int(photos) if str(photos or "").isdigit() else 0)
    if photo_n >= 2:
        pts += 2
        notes.append("2+ photos (+2)")
    desc = lead.get("ProblemDescription") or ""
    if len(str(desc)) >= 40:
        pts += 2
        notes.append("Good problem description (+2)")
    if geom_kind == "boundary":
        pts += 2
        notes.append("Boundary drawn/uploaded (+2)")
    if lead.get("FarmerInterestedInCostShare") not in (None, "") or lead.get("PermissionToShareWithSWCD") not in (None, ""):
        pts += 2
        notes.append("Farmer interest / SWCD permission known (+2)")
    return min(pts, 10), notes


def _dac(facts: Dict[str, Any]) -> Tuple[int, str]:
    if facts.get("DACIntersecting"):
        return 10, "Project intersects a Disadvantaged Community."
    if facts.get("DACNearby"):
        return 6, "DAC nearby / possible downstream relevance."
    return 0, "No DAC context."


def _classify(score: int) -> str:
    if score >= 80:
        return "Strong Candidate"
    if score >= 60:
        return "Possible Candidate"
    if score >= 40:
        return "Weak Candidate"
    return "Poor Candidate"


def _gis_confidence(facts: Dict[str, Any], engine_present: bool) -> str:
    if not engine_present:
        return "Low"
    core = [
        facts.get("CountyAuto"), facts.get("HUC12"), facts.get("NearestWaterbodyName"),
        facts.get("DominantHydrologicSoilGroup") or facts.get("DominantSoilDrainageClass"),
        facts.get("MeanSlopePercent"),
    ]
    present = sum(1 for v in core if v not in (None, ""))
    return "High" if present >= 4 else "Medium" if present >= 2 else "Low"


def _missing_info(lead: Dict[str, Any], facts: Dict[str, Any], geom_kind: str) -> List[str]:
    missing = []
    if geom_kind != "boundary":
        missing.append("No field boundary -- only a GPS point + buffer was used.")
    if not (lead.get("Photos") or lead.get("PhotoCount")):
        missing.append("No photos attached.")
    if lead.get("PermissionToShareWithSWCD") in (None, ""):
        missing.append("SWCD-sharing permission not recorded.")
    if facts.get("NearestWaterbodyName") is None and facts.get("DistanceToWaterbodyFt") is None:
        missing.append("No mapped waterbody connection established.")
    if facts.get("MeanSlopePercent") is None:
        missing.append("Slope not available (DEM not configured or no coverage).")
    return missing


def evaluate(
    lead: Dict[str, Any],
    facts: Dict[str, Any],
    match_strength: str,
    geom_kind: str,
    warnings: List[str],
    engine_present: bool,
    settings: Settings,
) -> Dict[str, Any]:
    wq, wq_why = _water_quality(facts)
    wi, wi_why = _wipwl(facts)
    bf, bf_why = _bmp_fit(match_strength)
    ts, ts_why = _topo_soils(facts, settings)
    doc, doc_notes = _documentation(lead, geom_kind)
    dac, dac_why = _dac(facts)

    breakdown = {
        "WaterQualityConnectionScore": wq,
        "WIPWLScore": wi,
        "BMPFitScore": bf,
        "TopoSoilsScore": ts,
        "DocumentationScore": doc,
        "DACScore": dac,
    }
    score = min(sum(breakdown.values()), 100)
    candidate_class = _classify(score)

    explanation = " | ".join([
        f"Water-quality {wq}/25: {wq_why}",
        f"WI/PWL {wi}/20: {wi_why}",
        f"BMP fit {bf}/20: {bf_why}",
        f"Topo/soils {ts}/15: {ts_why}",
        f"Documentation {doc}/10: {', '.join(doc_notes) or 'none'}",
        f"DAC {dac}/10: {dac_why}",
    ])

    human_warnings = list(REQUIRED_WARNINGS)
    for w in warnings:
        if w not in human_warnings:
            human_warnings.append(w)

    return {
        "CandidateScore": score,
        "CandidateClass": candidate_class,
        "GISConfidence": _gis_confidence(facts, engine_present),
        "MissingInfoChecklist": _missing_info(lead, facts, geom_kind),
        "HumanReviewWarnings": human_warnings,
        "ScoreExplanation": explanation,
        **breakdown,
    }
