"""Assemble the final, report-ready LeadProcessResponse.

Single place that decides output shape, Status, CandidateClass-driven NextAction,
and the boundary summary -- so the success and error paths stay consistent.
"""
from typing import Any, Dict, List, Optional

from .scoring import REQUIRED_WARNINGS

_BREAKDOWN_KEYS = [
    "WaterQualityConnectionScore", "WIPWLScore", "BMPFitScore",
    "TopoSoilsScore", "DocumentationScore", "DACScore", "ScoreExplanation",
]
_SCORE_META = ["GISConfidence", "MissingInfoChecklist", "HumanReviewWarnings"]


def next_action(candidate_class: str, has_error: bool) -> str:
    if has_error:
        return "Fix input and reprocess"
    return {
        "Strong Candidate": "Send to SWCD for review",
        "Possible Candidate": "Gather more info, then contact SWCD",
        "Weak Candidate": "Review internally; likely needs more info",
        "Poor Candidate": "Low priority; revisit if conditions change",
    }.get(candidate_class, "Needs review")


def _auto_facts(facts: Dict[str, Any], scoring_result: Dict[str, Any]) -> Dict[str, Any]:
    af = dict(facts)  # all GIS facts + AnalysisGeometrySource + ProcessingError
    for k in _SCORE_META + _BREAKDOWN_KEYS:
        af[k] = scoring_result.get(k)
    return af


def build_response(
    lead: Dict[str, Any],
    facts: Dict[str, Any],
    bmps: List[Dict[str, Any]],
    scoring_result: Dict[str, Any],
    calculation: Dict[str, Any],
    boundary_info: Dict[str, Any],
) -> Dict[str, Any]:
    has_error = bool(facts.get("ProcessingError"))
    candidate_class = scoring_result.get("CandidateClass", "Needs More Info")

    return {
        "LeadID": lead.get("LeadID"),
        "Status": "Error" if has_error else "Report Ready",
        "CandidateScore": scoring_result.get("CandidateScore", 0),
        "CandidateClass": candidate_class,
        "GISConfidence": scoring_result.get("GISConfidence", "Low"),
        "BoundaryStatus": boundary_info.get("BoundaryStatus"),
        "BoundarySource": boundary_info.get("BoundarySource"),
        "BoundaryAreaAcres": boundary_info.get("BoundaryAreaAcres"),
        "NextAction": next_action(candidate_class, has_error),
        "ProcessingError": facts.get("ProcessingError"),
        "AutoFacts": _auto_facts(facts, scoring_result),
        "BMPCandidates": bmps,
        "Calculations": calculation,
    }


def error_response(lead: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Last-resort response when processing raised unexpectedly."""
    return {
        "LeadID": lead.get("LeadID"),
        "Status": "Error",
        "CandidateScore": 0,
        "CandidateClass": "Needs More Info",
        "GISConfidence": "Low",
        "BoundaryStatus": None,
        "BoundarySource": None,
        "BoundaryAreaAcres": None,
        "NextAction": "Fix input and reprocess",
        "ProcessingError": message,
        "AutoFacts": {
            "ProcessingError": message,
            "GISConfidence": "Low",
            "MissingInfoChecklist": ["Processing failed before completion -- see ProcessingError."],
            "HumanReviewWarnings": list(REQUIRED_WARNINGS),
        },
        "BMPCandidates": [],
        "Calculations": {},
    }
