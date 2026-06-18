"""Pydantic request/response models for the API.

Optional fields are genuinely optional -- a missing GPS or boundary must NOT
raise a 422; it is handled inside processing and surfaced as ProcessingError +
warnings so the caller always gets a complete, well-shaped response.
"""
from typing import Any, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------- requests ---
class LeadProcessRequest(BaseModel):
    LeadID: str
    CustomerName: Optional[str] = None
    FarmName: Optional[str] = None
    FieldName: Optional[str] = None
    ProblemType: Optional[str] = None
    ProblemDescription: Optional[str] = None
    # Location: either ProblemLocation ("lat, lng") or explicit lat/lng.
    ProblemLocation: Optional[Any] = None
    GPSLatitude: Optional[float] = None
    GPSLongitude: Optional[float] = None
    # Boundary (optional): GeoJSON object/string or WKT.
    BoundaryGeoJSON: Optional[Any] = None
    BoundaryWKT: Optional[str] = None
    BoundarySource: Optional[str] = None
    # Calculator inputs (all optional).
    EstimatedProjectCost: Optional[float] = None
    CostShareLowPercent: Optional[float] = None
    CostShareHighPercent: Optional[float] = None
    EstimatedCompanyGrossMarginPercent: Optional[float] = None
    # Intake flags used for scoring.
    FarmerInterestedInCostShare: Optional[Any] = None
    PermissionToShareWithSWCD: Optional[Any] = None
    Photos: Optional[Any] = None
    PhotoCount: Optional[Any] = None

    model_config = {"extra": "ignore"}


class BoundarySaveRequest(BaseModel):
    LeadID: str
    BoundaryGeoJSON: Any
    BoundaryAnnotationsGeoJSON: Optional[Any] = None
    BoundarySource: str = "Sales drawn boundary"

    model_config = {"extra": "ignore"}


# -------------------------------------------------------------- responses ---
class BMPCandidateResponse(BaseModel):
    BMPName: str
    BMPCategory: str
    ReasonSuggested: str
    Confidence: str
    NeedsHumanReview: bool
    Notes: str = ""


class AutoFactsResponse(BaseModel):
    AnalysisGeometrySource: Optional[str] = None
    CountyAuto: Optional[str] = None
    TownAuto: Optional[str] = None
    HUC8: Optional[str] = None
    HUC10: Optional[str] = None
    HUC12: Optional[str] = None
    HUC12Name: Optional[str] = None
    NearestWaterbodyName: Optional[str] = None
    NearestWaterbodyType: Optional[str] = None
    DistanceToWaterbodyFt: Optional[float] = None
    WIPWLNearby: bool = False
    WIPWLSummary: Optional[str] = None
    DACIntersecting: bool = False
    DACNearby: bool = False
    DominantSoilDrainageClass: Optional[str] = None
    DominantHydrologicSoilGroup: Optional[str] = None
    MeanSlopePercent: Optional[float] = None
    MaxSlopePercent: Optional[float] = None
    GISConfidence: str = "Low"
    MissingInfoChecklist: List[str] = Field(default_factory=list)
    HumanReviewWarnings: List[str] = Field(default_factory=list)
    ProcessingError: Optional[str] = None
    # Score breakdown
    WaterQualityConnectionScore: int = 0
    WIPWLScore: int = 0
    BMPFitScore: int = 0
    TopoSoilsScore: int = 0
    DocumentationScore: int = 0
    DACScore: int = 0
    ScoreExplanation: Optional[str] = None


class CalculationResponse(BaseModel):
    EstimatedProjectCost: float = 0
    CostShareLowPercent: float = 0
    CostShareHighPercent: float = 0
    EstimatedCostShareLow: float = 0
    EstimatedCostShareHigh: float = 0
    EstimatedFarmerCostLow: float = 0
    EstimatedFarmerCostHigh: float = 0
    EstimatedCompanyRevenue: float = 0
    EstimatedCompanyGrossMarginPercent: float = 0
    EstimatedCompanyGrossMarginDollars: float = 0
    Assumptions: str = ""
    CalculatorWarnings: str = ""


class BoundarySaveResponse(BaseModel):
    success: bool
    LeadID: Optional[str] = None
    BoundaryAreaAcres: Optional[float] = None
    BoundaryCentroidLat: Optional[float] = None
    BoundaryCentroidLng: Optional[float] = None
    message: str = ""
    warnings: List[str] = Field(default_factory=list)


class LeadProcessResponse(BaseModel):
    LeadID: Optional[str] = None
    Status: str
    CandidateScore: int = 0
    CandidateClass: str = "Needs More Info"
    GISConfidence: str = "Low"
    BoundaryStatus: Optional[str] = None
    BoundarySource: Optional[str] = None
    BoundaryAreaAcres: Optional[float] = None
    BoundaryShareURL: Optional[str] = None
    NextAction: Optional[str] = None
    ProcessingError: Optional[str] = None

    AutoFacts: AutoFactsResponse
    BMPCandidates: List[BMPCandidateResponse] = Field(default_factory=list)
    Calculations: CalculationResponse
