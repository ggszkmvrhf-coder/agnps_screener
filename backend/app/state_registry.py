"""State registry for multi-state GIS routing in AgNPS.

This is a pure leaf module: it imports ONLY stdlib, dataclasses, and typing.
It has NO imports from public_gis, gis_lookup, settings, or main, eliminating
any risk of circular imports.

How to add a third state (e.g. Pennsylvania, abbr "PA"):
1. Add PA=42 to STATE_FIPS_TO_ABBR.
2. Add "PA" to SUPPORTED_STATES.
3. Build a WaterbodySource with the PA waterbody endpoint details:
   - If it is an ArcGIS FeatureServer, set provider="arcgis_featureserver".
   - If it is an ArcGIS MapServer, set provider="arcgis_mapserver".
   - Populate layers, name_fields, category_fields, etc. from the PA schema.
4. Build a DacSource with the PA DAC endpoint:
   - If Socrata, set provider="socrata".
   - If ArcGIS FeatureServer, set provider="arcgis_featureserver".
5. Build a StateConfig(abbr="PA", name="Pennsylvania", waterbody=..., dac=...).
6. Add it to _REGISTRY["PA"].
No other files need to change for the routing to pick up the new state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes — all frozen (pure value objects).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WaterbodySource:
    """Describes one state's waterbody lookup endpoint.

    provider: "arcgis_featureserver" or "arcgis_mapserver"
    base_url: root URL (no layer or /query suffix).
    layers: ordered tuple of (layer_id, display_type_label).
    where: optional server-side WHERE clause appended to every query.
    name_fields: candidate attribute names for the waterbody's display name.
    category_fields: candidate attribute names for assessment category.
    class_fields: candidate attribute names for water-quality class.
    status_fields: candidate attribute names for overall status.
    cycle_fields: candidate attribute names for reporting cycle / year.
    date_fields: candidate attribute names for last-assessed date.
    factsheet_fields: candidate attribute names for factsheet URL.
    dedupe_field: if set, deduplicate candidates on this attribute key.
    max_sample_points: cap on how many sample points drive HTTP calls.
    """
    provider: str                          # "arcgis_featureserver" | "arcgis_mapserver"
    base_url: str
    layers: Tuple[Tuple[int, str], ...]    # ((layer_id, type_label), ...)
    where: Optional[str]
    name_fields: Tuple[str, ...]
    category_fields: Tuple[str, ...]
    class_fields: Tuple[str, ...]
    status_fields: Tuple[str, ...]
    cycle_fields: Tuple[str, ...]
    date_fields: Tuple[str, ...]
    factsheet_fields: Tuple[str, ...]
    dedupe_field: Optional[str]
    max_sample_points: int


@dataclass(frozen=True)
class DacSource:
    """Describes one state's DAC/EJ lookup endpoint.

    provider: "socrata" or "arcgis_featureserver"
    base_url: full query URL (for Socrata) or base service URL (for ArcGIS).
    designation_rule: human-readable description of the designation logic.
    designation_field: the attribute field used to determine designation
                       (Socrata: "dac_designation"; ArcGIS EJ: field name).
    layer_id: ArcGIS layer number (None for Socrata).
    """
    provider: str                    # "socrata" | "arcgis_featureserver"
    base_url: str
    designation_rule: str
    designation_field: Optional[str]
    layer_id: Optional[int]


@dataclass(frozen=True)
class StateConfig:
    """Bundles all routing info for one US state."""
    abbr: str           # 2-letter postal abbreviation, upper-case
    name: str           # Full state name
    waterbody: WaterbodySource
    dac: DacSource


# ---------------------------------------------------------------------------
# Registry constants
# ---------------------------------------------------------------------------

DEFAULT_STATE: str = "NY"
SUPPORTED_STATES: FrozenSet[str] = frozenset({"NY", "DE"})

# At minimum NY (36) and DE (10); a full 50-state map would go here.
STATE_FIPS_TO_ABBR: Dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}

# ---------------------------------------------------------------------------
# State configurations
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------- NY
# These values mirror the module-level constants in public_gis.py exactly so
# that routing through the registry produces byte-for-byte identical output.
_NY_WATERBODY = WaterbodySource(
    provider="arcgis_featureserver",
    base_url=(
        "https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/"
        "Waterbody_Inventory_List/FeatureServer"
    ),
    layers=(
        (2, "Lake"),
        (3, "Estuary"),
        (4, "Stream"),
        (5, "Shoreline"),
    ),
    where=None,
    name_fields=("WATERBODY", "WATER_NAME", "NAME", "PWL_NAME", "WB_NAME"),
    category_fields=("WATERBODY_CATEGORY", "CATEGORY", "ASSESSMENT", "STATUS"),
    class_fields=("CLASS", "WQS_CLASS"),
    status_fields=(),
    cycle_fields=(),
    date_fields=("CYCLE_LAST_ASSESSED", "LAST_ASSESSED"),
    factsheet_fields=("FACTSHEET", "FACTSHEET_URL"),
    dedupe_field=None,
    max_sample_points=3,
)

_NY_DAC = DacSource(
    provider="socrata",
    base_url="https://data.ny.gov/resource/2e6c-s6fp.json",
    designation_rule="dac_designation contains 'designated' (case-insensitive, not 'not designated')",
    designation_field="dac_designation",
    layer_id=None,
)

_NY = StateConfig(
    abbr="NY",
    name="New York",
    waterbody=_NY_WATERBODY,
    dac=_NY_DAC,
)

# ---------------------------------------------------------------------- DE
# Delaware waterbody: EPA ATTAINS MapServer (layer 1 = lines, layer 2 = areas).
# Organization filter: organizationid='21DELAWQ'.
# Fields: assessmentunitname, ircategory, overallstatus, reportingcycle.
# No CLASS field, no date field; dedup on assessmentunitidentifier.
_DE_WATERBODY = WaterbodySource(
    provider="arcgis_mapserver",
    base_url="https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer",
    layers=(
        (1, "Stream"),
        (2, "Lake"),
    ),
    where="organizationid='21DELAWQ'",
    name_fields=("assessmentunitname",),
    category_fields=("ircategory",),
    class_fields=(),
    status_fields=("overallstatus",),
    cycle_fields=("reportingcycle",),
    date_fields=(),
    factsheet_fields=(),
    dedupe_field="assessmentunitidentifier",
    max_sample_points=3,
)

_DE_DAC = DacSource(
    provider="arcgis_featureserver",
    base_url=(
        "https://enterprise.firstmap.delaware.gov/arcgis/rest/services/"
        "Society/DE_EJScreen/FeatureServer/5/query"
    ),
    designation_rule="EXCEED_COUNT_80 > 0",
    designation_field="EXCEED_COUNT_80",
    layer_id=5,
)

_DE = StateConfig(
    abbr="DE",
    name="Delaware",
    waterbody=_DE_WATERBODY,
    dac=_DE_DAC,
)

# ---------------------------------------------------------------------------
# Internal registry dict
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, StateConfig] = {
    "NY": _NY,
    "DE": _DE,
}

# ---------------------------------------------------------------------------
# Pure public functions — never raise, always return a valid value.
# ---------------------------------------------------------------------------

def normalize_state(abbr: Optional[str]) -> str:
    """Return an upper-cased, supported state abbreviation.

    If abbr is None, empty, or not in SUPPORTED_STATES, returns DEFAULT_STATE.
    """
    if not abbr:
        return DEFAULT_STATE
    normalized = str(abbr).strip().upper()
    return normalized if normalized in SUPPORTED_STATES else DEFAULT_STATE


def get_state_config(abbr: Optional[str]) -> StateConfig:
    """Return the StateConfig for abbr, falling back to NY on any miss."""
    return _REGISTRY.get(normalize_state(abbr), _REGISTRY[DEFAULT_STATE])


def is_supported(abbr: Optional[str]) -> bool:
    """Return True iff abbr (after normalization) is a known supported state."""
    if not abbr:
        return False
    return str(abbr).strip().upper() in SUPPORTED_STATES
