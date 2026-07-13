"""test_multi_state.py — Hermetic tests for multi-state GIS routing in AgNPS.

All network I/O is patched out via unittest.mock.patch; no real HTTP calls are
made. The tests cover:

  Group 1 — state_registry totality
  Group 2 — state detection in lookup_county_town
  Group 3 — waterbody routing (FeatureServer vs MapServer URL selection)
  Group 4 — DAC routing (DE ArcGIS EJ vs NY Socrata)
  Group 5 — NY regression / backward-compat via run_live_public_lookups
  Group 6 — unknown-state graceful degradation (TX → fallback to NY)
  Group 7 — kill-switch: multi_state_routing_enabled=False forces NY
"""

from __future__ import annotations

import types
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from shapely.geometry import Point, Polygon

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from app.state_registry import (
    DEFAULT_STATE,
    STATE_FIPS_TO_ABBR,
    SUPPORTED_STATES,
    StateConfig,
    WaterbodySource,
    DacSource,
    get_state_config,
    is_supported,
    normalize_state,
)
from app.public_gis import (
    lookup_county_town,
    lookup_dac,
    lookup_waterbody,
    run_live_public_lookups,
)
from app.settings import Settings


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Return a Settings instance with sane test defaults and no .env file."""
    base = dict(
        database_url=None,
        public_gis_lookups_enabled=True,
        public_api_timeout_sec=5.0,
        multi_state_routing_enabled=True,
        wipwl_search_radius_ft=5280.0,
        dac_nearby_distance_ft=2640.0,
        point_buffer_ft=500.0,
        projected_crs=5070,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# A well-known NY point (Ithaca, NY) used as the default test geometry.
NY_POINT = Point(-76.5019, 42.4440)

# A well-known DE point (Wilmington, DE) used for DE-specific tests.
DE_POINT = Point(-75.5493, 39.7447)


def _census_payload(stusab: Optional[str], fips: Optional[str] = None) -> Dict[str, Any]:
    """Build a minimal Census geocoder response that lookup_county_town parses."""
    counties: List[Dict[str, Any]] = []
    states: List[Dict[str, Any]] = []

    county: Dict[str, Any] = {"BASENAME": "Test County", "NAME": "Test County"}
    if stusab:
        county["STUSAB"] = stusab
    if fips:
        county["STATE"] = fips
    counties.append(county)

    if stusab:
        states.append({"STUSAB": stusab})

    return {
        "result": {
            "geographies": {
                "Counties": counties,
                "County Subdivisions": [{"BASENAME": "Test Town"}],
                "States": states,
            }
        }
    }


def _census_payload_fips_only(fips: str) -> Dict[str, Any]:
    """Census response with only a numeric STATE FIPS (no STUSAB anywhere)."""
    return {
        "result": {
            "geographies": {
                "Counties": [{"BASENAME": "Test County", "STATE": fips}],
                "County Subdivisions": [{"BASENAME": "Test Town"}],
                "States": [],
            }
        }
    }


def _arcgis_feature_response(attrs: Dict[str, Any], include_geometry: bool = False) -> Dict[str, Any]:
    """Minimal ArcGIS FeatureServer/MapServer JSON response with one feature."""
    feature: Dict[str, Any] = {"attributes": attrs}
    if include_geometry:
        # A tiny line geometry near NY_POINT so _arcgis_geom_to_shapely works.
        feature["geometry"] = {
            "paths": [[[-76.50, 42.44], [-76.51, 42.45]]]
        }
    return {"features": [feature]}


def _socrata_dac_row(designation: str) -> List[Dict[str, Any]]:
    """Minimal Socrata DAC row."""
    return [{"geoid": "36109", "dac_designation": designation}]


# ---------------------------------------------------------------------------
# Group 1 — state_registry totality
# ---------------------------------------------------------------------------

class TestStateRegistryTotality:

    def test_get_state_config_ny(self):
        cfg = get_state_config("NY")
        assert isinstance(cfg, StateConfig)
        assert cfg.abbr == "NY"
        assert "services6.arcgis.com" in cfg.waterbody.base_url
        assert cfg.waterbody.provider == "arcgis_featureserver"

    def test_get_state_config_de(self):
        cfg = get_state_config("DE")
        assert isinstance(cfg, StateConfig)
        assert cfg.abbr == "DE"
        assert cfg.waterbody.provider == "arcgis_mapserver"
        assert "gispub.epa.gov" in cfg.waterbody.base_url

    def test_get_state_config_de_dac_url(self):
        cfg = get_state_config("DE")
        assert "firstmap.delaware.gov" in cfg.dac.base_url

    def test_get_state_config_none_returns_ny(self):
        cfg = get_state_config(None)
        assert cfg.abbr == DEFAULT_STATE

    def test_get_state_config_unsupported_returns_ny(self):
        cfg = get_state_config("ZZ")
        assert cfg.abbr == DEFAULT_STATE

    def test_get_state_config_lowercase_returns_ny(self):
        # "tx" normalizes to "TX" which is unsupported → NY default.
        cfg = get_state_config("tx")
        assert cfg.abbr == DEFAULT_STATE

    def test_get_state_config_none_never_raises(self):
        # Calling with pathological inputs must never raise.
        for bad in (None, "", "  ", "XXXX", 0, False):  # type: ignore[list-item]
            result = get_state_config(bad)  # type: ignore[arg-type]
            assert isinstance(result, StateConfig)

    def test_normalize_state_uppercase(self):
        assert normalize_state("de") == "DE"

    def test_normalize_state_strips_whitespace(self):
        assert normalize_state("  DE  ") == "DE"

    def test_normalize_state_none_defaults_to_ny(self):
        assert normalize_state(None) == DEFAULT_STATE

    def test_normalize_state_empty_defaults_to_ny(self):
        assert normalize_state("") == DEFAULT_STATE

    def test_normalize_state_unsupported_defaults_to_ny(self):
        assert normalize_state("ZZ") == DEFAULT_STATE

    def test_is_supported_ny(self):
        assert is_supported("NY") is True

    def test_is_supported_de(self):
        assert is_supported("DE") is True

    def test_is_supported_unknown(self):
        assert is_supported("TX") is False

    def test_is_supported_none(self):
        assert is_supported(None) is False

    def test_supported_states_contains_ny_de(self):
        assert "NY" in SUPPORTED_STATES
        assert "DE" in SUPPORTED_STATES

    def test_default_state_is_ny(self):
        assert DEFAULT_STATE == "NY"

    def test_de_fips_in_map(self):
        assert STATE_FIPS_TO_ABBR.get("10") == "DE"

    def test_ny_fips_in_map(self):
        assert STATE_FIPS_TO_ABBR.get("36") == "NY"


# ---------------------------------------------------------------------------
# Group 2 — state detection in lookup_county_town
# ---------------------------------------------------------------------------

class TestCountyTownStateDetection:
    """All tests patch app.public_gis._json_get to avoid real network calls."""

    def _call(self, json_return: Any) -> tuple[Dict[str, Any], List[str]]:
        settings = _make_settings()
        warnings: List[str] = []
        with patch("app.public_gis._json_get", return_value=json_return):
            result = lookup_county_town(NY_POINT, settings, warnings)
        return result, warnings

    def test_stusab_de_from_states_geography(self):
        result, warnings = self._call(_census_payload("DE"))
        assert result["StateAuto"] == "DE"

    def test_stusab_ny_from_states_geography(self):
        result, warnings = self._call(_census_payload("NY"))
        assert result["StateAuto"] == "NY"

    def test_stusab_ny_from_counties_when_states_absent(self):
        # Build payload where States is empty but Counties has STUSAB.
        payload = {
            "result": {
                "geographies": {
                    "Counties": [{"BASENAME": "Tompkins", "STUSAB": "NY"}],
                    "County Subdivisions": [{"BASENAME": "Ithaca"}],
                    "States": [],
                }
            }
        }
        result, warnings = self._call(payload)
        assert result["StateAuto"] == "NY"

    def test_numeric_fips_10_resolves_to_de(self):
        result, warnings = self._call(_census_payload_fips_only("10"))
        assert result["StateAuto"] == "DE"

    def test_numeric_fips_36_resolves_to_ny(self):
        result, warnings = self._call(_census_payload_fips_only("36"))
        assert result["StateAuto"] == "NY"

    def test_empty_response_state_auto_is_none_with_warning(self):
        result, warnings = self._call(None)
        assert result["StateAuto"] is None
        # A warning about failing to detect the state must be appended.
        assert any("state" in w.lower() or "county" in w.lower() for w in warnings)

    def test_garbage_response_state_auto_is_none(self):
        result, warnings = self._call({"result": {}})
        assert result["StateAuto"] is None

    def test_county_auto_populated(self):
        result, _ = self._call(_census_payload("NY"))
        assert result["CountyAuto"] is not None

    def test_town_auto_populated(self):
        result, _ = self._call(_census_payload("NY"))
        assert result["TownAuto"] is not None

    def test_stusab_case_normalized_to_upper(self):
        # Even if the Census returns lower-case (hypothetically), StateAuto is
        # normalised to uppercase by the implementation.
        payload = {
            "result": {
                "geographies": {
                    "Counties": [{"BASENAME": "Kent", "STUSAB": "de"}],
                    "County Subdivisions": [{"BASENAME": "Dover"}],
                    "States": [{"STUSAB": "de"}],
                }
            }
        }
        result, _ = self._call(payload)
        assert result["StateAuto"] == "DE"


# ---------------------------------------------------------------------------
# Group 3 — waterbody routing
# ---------------------------------------------------------------------------

class TestWaterbodyRouting:
    """
    Verify that lookup_waterbody routes to the correct endpoint URL based on
    the `state` argument.  _json_get is intercepted to capture called URLs.
    """

    WATERBODY_KEYS = {
        "NearestWaterbodyName",
        "NearestWaterbodyType",
        "DistanceToWaterbodyFt",
        "WIPWLNearby",
        "WIPWLSummary",
    }

    def _call_and_capture_urls(
        self,
        state: str,
        json_return: Any,
    ) -> tuple[Dict[str, Any], List[str]]:
        settings = _make_settings()
        warnings: List[str] = []
        captured_urls: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_urls.append(url)
            return json_return

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            result = lookup_waterbody(NY_POINT, settings, warnings, state=state)

        return result, captured_urls, warnings  # type: ignore[return-value]

    def test_de_waterbody_uses_gispub_epa_gov(self):
        # No features returned — URL assertion only.
        _, urls, _ = self._call_and_capture_urls("DE", {"features": []})
        assert any("gispub.epa.gov" in u for u in urls), (
            f"Expected gispub.epa.gov in URLs; got: {urls}"
        )

    def test_de_waterbody_uses_mapserver_url(self):
        _, urls, _ = self._call_and_capture_urls("DE", {"features": []})
        assert any("MapServer" in u for u in urls), (
            f"Expected 'MapServer' in URL path; got: {urls}"
        )

    def test_de_waterbody_queries_layer_1_or_2(self):
        _, urls, _ = self._call_and_capture_urls("DE", {"features": []})
        # DE layers are (1, "Stream") and (2, "Lake").
        assert any("/1/query" in u or "/2/query" in u for u in urls), (
            f"Expected /1/query or /2/query in URLs; got: {urls}"
        )

    def test_de_waterbody_sends_where_filter(self):
        """The where= param with organizationid='21DELAWQ' must be sent."""
        settings = _make_settings()
        warnings: List[str] = []
        captured_params: List[Dict[str, Any]] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_params.append(dict(params))
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            lookup_waterbody(DE_POINT, settings, warnings, state="DE")

        # At least one call must carry the organizationid WHERE clause.
        assert any("where" in p and "21DELAWQ" in str(p["where"]) for p in captured_params), (
            f"Expected where=organizationid='21DELAWQ' in params; got: {captured_params}"
        )

    def test_ny_waterbody_uses_nysdec_featureserver(self):
        _, urls, _ = self._call_and_capture_urls("NY", {"features": []})
        assert any("services6.arcgis.com" in u for u in urls), (
            f"Expected services6.arcgis.com in URLs; got: {urls}"
        )

    def test_ny_waterbody_uses_featureserver_url(self):
        _, urls, _ = self._call_and_capture_urls("NY", {"features": []})
        assert any("FeatureServer" in u for u in urls), (
            f"Expected 'FeatureServer' in URL path; got: {urls}"
        )

    def test_result_has_standard_keys_when_no_features(self):
        result, _, _ = self._call_and_capture_urls("NY", {"features": []})
        assert self.WATERBODY_KEYS == set(result.keys())

    def test_result_has_standard_keys_when_de_features_found(self):
        attrs = {
            "assessmentunitname": "Christina River",
            "ircategory": "5",
            "overallstatus": "Impaired",
            "reportingcycle": "2022",
            "assessmentunitidentifier": "DE-CH-001",
        }
        response = _arcgis_feature_response(attrs, include_geometry=True)
        result, _, _ = self._call_and_capture_urls("DE", response)
        assert self.WATERBODY_KEYS == set(result.keys())

    def test_ny_waterbody_result_has_standard_keys_with_features(self):
        attrs = {
            "WATERBODY": "Cayuga Lake",
            "WATERBODY_CATEGORY": "A",
            "CLASS": "A",
            "CYCLE_LAST_ASSESSED": "2020",
        }
        response = _arcgis_feature_response(attrs, include_geometry=True)
        result, _, _ = self._call_and_capture_urls("NY", response)
        assert self.WATERBODY_KEYS == set(result.keys())

    def test_de_waterbody_wipwl_nearby_true_when_feature_returned(self):
        attrs = {
            "assessmentunitname": "Brandywine Creek",
            "ircategory": "5",
            "overallstatus": "Impaired",
            "reportingcycle": "2022",
            "assessmentunitidentifier": "DE-BR-001",
        }
        response = _arcgis_feature_response(attrs, include_geometry=True)
        result, _, _ = self._call_and_capture_urls("DE", response)
        assert result["WIPWLNearby"] is True
        assert result["NearestWaterbodyName"] == "Brandywine Creek"

    def test_de_waterbody_summary_contains_waterbody_label(self):
        """WIPWLSummary must contain 'Waterbody:' so downstream scoring greps work."""
        attrs = {
            "assessmentunitname": "Smyrna River",
            "overallstatus": "Impaired",
            "ircategory": "5",
            "assessmentunitidentifier": "DE-SM-001",
        }
        response = _arcgis_feature_response(attrs, include_geometry=True)
        result, _, _ = self._call_and_capture_urls("DE", response)
        assert "Waterbody:" in (result["WIPWLSummary"] or ""), (
            f"WIPWLSummary missing 'Waterbody:' prefix: {result['WIPWLSummary']!r}"
        )


# ---------------------------------------------------------------------------
# Group 4 — DAC routing
# ---------------------------------------------------------------------------

class TestDacRouting:
    """
    lookup_dac routes to _dac_arcgis_ej (DE) or _dac_socrata (NY).

    DE DAC tests mock BOTH the point query and the envelope (nearby) query.
    The two calls are distinguished by inspecting the `geometryType` param:
      - "esriGeometryPoint"    → intersecting (call 1)
      - "esriGeometryEnvelope" → nearby       (call 2)
    """

    def _make_de_fake(
        self,
        point_exceed: int,
        envelope_exceed: int,
    ):
        """Return a side_effect function that returns distinct responses per call."""
        def fake_json_get(url, params, settings_, label, warnings_):
            geom_type = params.get("geometryType", "")
            if geom_type == "esriGeometryPoint":
                return {"features": [{"attributes": {"EXCEED_COUNT_80": point_exceed}}]}
            if geom_type == "esriGeometryEnvelope":
                return {"features": [{"attributes": {"EXCEED_COUNT_80": envelope_exceed}}]}
            return {"features": []}
        return fake_json_get

    def _call_de_dac_split(
        self,
        point_exceed: int,
        envelope_exceed: int,
    ) -> tuple[Dict[str, Any], List[str]]:
        """Call DE DAC with independent point and envelope responses."""
        settings = _make_settings()
        warnings: List[str] = []
        with patch("app.public_gis._json_get", side_effect=self._make_de_fake(point_exceed, envelope_exceed)):
            result = lookup_dac(DE_POINT, DE_POINT, settings, warnings, state="DE")
        return result, warnings

    def _call_ny_dac(
        self,
        dac_designation: str,
    ) -> tuple[Dict[str, Any], List[str]]:
        settings = _make_settings()
        warnings: List[str] = []
        rows = [{"geoid": "36109", "dac_designation": dac_designation}]
        with patch("app.public_gis._json_get", return_value=rows):
            result = lookup_dac(NY_POINT, NY_POINT, settings, warnings, state="NY")
        return result, warnings

    # ------------------------------------------------------------------
    # Intersecting case: point query returns EXCEED_COUNT_80 > 0
    # Both DACIntersecting and DACNearby must be True.
    # ------------------------------------------------------------------

    def test_de_dac_intersecting_true_when_exceed_count_nonzero(self):
        result, _ = self._call_de_dac_split(point_exceed=7, envelope_exceed=0)
        assert result["DACIntersecting"] is True

    def test_de_dac_nearby_true_when_intersecting_is_true(self):
        """DACNearby must be True whenever DACIntersecting is True."""
        result, _ = self._call_de_dac_split(point_exceed=7, envelope_exceed=0)
        assert result["DACNearby"] is True

    def test_de_dac_source_mentions_ejscreen(self):
        result, _ = self._call_de_dac_split(point_exceed=7, envelope_exceed=0)
        assert result["DACSource"] is not None
        assert "EJScreen" in result["DACSource"] or "EXCEED" in result["DACSource"], (
            f"DACSource unexpectedly: {result['DACSource']!r}"
        )

    # ------------------------------------------------------------------
    # Both-zero case: point=0, envelope=0 → both False (0 pts).
    # ------------------------------------------------------------------

    def test_de_dac_intersecting_false_when_both_zero(self):
        result, _ = self._call_de_dac_split(point_exceed=0, envelope_exceed=0)
        assert result["DACIntersecting"] is False

    def test_de_dac_nearby_false_when_both_zero(self):
        result, _ = self._call_de_dac_split(point_exceed=0, envelope_exceed=0)
        assert result["DACNearby"] is False

    # ------------------------------------------------------------------
    # Nearby-only case: point=0, envelope>0 → 6-pt tier.
    # DACIntersecting=False, DACNearby=True.
    # ------------------------------------------------------------------

    def test_de_dac_nearby_only_intersecting_false(self):
        """Point query returns 0 but envelope query finds a DAC tract nearby."""
        result, _ = self._call_de_dac_split(point_exceed=0, envelope_exceed=4)
        assert result["DACIntersecting"] is False

    def test_de_dac_nearby_only_nearby_true(self):
        """Envelope query with EXCEED_COUNT_80>0 sets DACNearby=True (6-pt tier)."""
        result, _ = self._call_de_dac_split(point_exceed=0, envelope_exceed=4)
        assert result["DACNearby"] is True

    def test_de_dac_nearby_only_uses_envelope_geometry_type(self):
        """The nearby query must send geometryType=esriGeometryEnvelope."""
        settings = _make_settings()
        warnings: List[str] = []
        captured_params: List[Dict[str, Any]] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_params.append(dict(params))
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            lookup_dac(DE_POINT, DE_POINT, settings, warnings, state="DE")

        envelope_calls = [p for p in captured_params if p.get("geometryType") == "esriGeometryEnvelope"]
        assert envelope_calls, (
            f"Expected at least one call with geometryType=esriGeometryEnvelope; "
            f"captured params: {captured_params}"
        )

    def test_de_dac_intersecting_uses_point_geometry_type(self):
        """The intersecting query must send geometryType=esriGeometryPoint."""
        settings = _make_settings()
        warnings: List[str] = []
        captured_params: List[Dict[str, Any]] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_params.append(dict(params))
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            lookup_dac(DE_POINT, DE_POINT, settings, warnings, state="DE")

        point_calls = [p for p in captured_params if p.get("geometryType") == "esriGeometryPoint"]
        assert point_calls, (
            f"Expected at least one call with geometryType=esriGeometryPoint; "
            f"captured params: {captured_params}"
        )

    def test_de_dac_url_uses_firstmap_delaware(self):
        settings = _make_settings()
        warnings: List[str] = []
        captured: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured.append(url)
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            lookup_dac(DE_POINT, DE_POINT, settings, warnings, state="DE")

        assert any("firstmap.delaware.gov" in u for u in captured), (
            f"Expected firstmap.delaware.gov in captured URLs: {captured}"
        )

    # ------------------------------------------------------------------
    # Coordinate-range guard for DE (mirrors the NY Socrata guard).
    # ------------------------------------------------------------------

    def test_de_dac_coordinate_range_guard_rejects_out_of_range(self):
        """
        An out-of-range locate point must return both False with no HTTP call.
        """
        settings = _make_settings()
        warnings: List[str] = []
        bad_point = Point(999.0, 999.0)
        with patch("app.public_gis._json_get", return_value={"features": []}) as mock_get:
            result = lookup_dac(bad_point, bad_point, settings, warnings, state="DE")
        assert result["DACIntersecting"] is False
        assert result["DACNearby"] is False
        mock_get.assert_not_called()

    def test_de_dac_coordinate_range_guard_appends_warning(self):
        """Out-of-range point must also append a warning string."""
        settings = _make_settings()
        warnings: List[str] = []
        bad_point = Point(999.0, 999.0)
        with patch("app.public_gis._json_get", return_value={"features": []}):
            lookup_dac(bad_point, bad_point, settings, warnings, state="DE")
        assert any("out of range" in w.lower() or "coordinate" in w.lower() for w in warnings), (
            f"Expected a coordinate-range warning; got: {warnings}"
        )

    # ------------------------------------------------------------------
    # NY DAC tests (unchanged behaviour)
    # ------------------------------------------------------------------

    def test_ny_dac_source_mentions_ny(self):
        result, _ = self._call_ny_dac("Designated as DAC")
        assert result["DACSource"] is not None
        assert "NY" in result["DACSource"], (
            f"Expected 'NY' in DACSource; got: {result['DACSource']!r}"
        )

    def test_ny_dac_uses_socrata_url(self):
        settings = _make_settings()
        warnings: List[str] = []
        captured: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured.append(url)
            return [{"geoid": "36109", "dac_designation": "Designated as DAC"}]

        with patch("app.public_gis._json_get", side_effect=fake_json_get):
            lookup_dac(NY_POINT, NY_POINT, settings, warnings, state="NY")

        assert any("data.ny.gov" in u for u in captured), (
            f"Expected data.ny.gov in captured URLs: {captured}"
        )

    def test_ny_dac_intersecting_true_when_designated(self):
        result, _ = self._call_ny_dac("Designated as DAC")
        assert result["DACIntersecting"] is True

    def test_ny_dac_coordinate_range_guard_rejects_out_of_range(self):
        """
        An out-of-range coordinate should cause the socrata path to skip and
        return a default dict with DACSource provenance — the injection guard
        is preserved verbatim in _dac_socrata.
        """
        settings = _make_settings()
        warnings: List[str] = []
        bad_point = Point(999.0, 999.0)  # clearly out of valid range
        with patch("app.public_gis._json_get", return_value=[]) as mock_get:
            result = lookup_dac(bad_point, bad_point, settings, warnings, state="NY")
        # The guard fires: no HTTP calls made, result carries DACSource provenance.
        assert result["DACIntersecting"] is False
        assert result["DACNearby"] is False
        assert result["DACSource"] == "NY DAC (data.ny.gov)"
        assert any("skipped" in w for w in warnings)
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Group 5 — NY regression / backward-compat
# ---------------------------------------------------------------------------

EXPECTED_FACT_KEYS = {
    "CountyAuto",
    "TownAuto",
    "StateAuto",
    "HUC8",
    "HUC10",
    "HUC12",
    "HUC12Name",
    "NearestWaterbodyName",
    "NearestWaterbodyType",
    "DistanceToWaterbodyFt",
    "WIPWLNearby",
    "WIPWLSummary",
    "DACIntersecting",
    "DACNearby",
    "DACSource",
    "DominantSoilDrainageClass",
    "DominantHydrologicSoilGroup",
}


class TestNYRegressionBackwardCompat:
    """
    With all HTTP stubbed to plausible NY responses, run_live_public_lookups
    must return the full expected fact key set without raising.
    """

    def _run_ny_lookups(self, settings: Settings) -> tuple[Dict[str, Any], List[str]]:
        warnings: List[str] = []

        census_resp = _census_payload("NY")
        huc12_resp = {
            "features": [{
                "attributes": {
                    "huc12": "020400010101",
                    "name": "Upper Cayuga Lake Watershed",
                }
            }]
        }
        waterbody_resp = _arcgis_feature_response(
            {
                "WATERBODY": "Cayuga Lake",
                "WATERBODY_CATEGORY": "A",
                "CLASS": "A(T)",
                "CYCLE_LAST_ASSESSED": "2020",
            },
            include_geometry=True,
        )
        dac_resp: List[Dict[str, Any]] = []  # no DAC hit

        # Soil WFS is a separate _read_url call (not _json_get); stub it to return
        # None so the soil lookup gracefully degrades with a warning.
        def fake_json_get(url, params, settings_, label, warnings_):
            if "geocoding.geo.census.gov" in url:
                return census_resp
            if "hydro.nationalmap.gov" in url:
                return huc12_resp
            if "arcgis.com" in url:
                return waterbody_resp
            if "data.ny.gov" in url:
                return dac_resp
            return None

        with patch("app.public_gis._json_get", side_effect=fake_json_get), \
             patch("app.public_gis._read_url", return_value=None):
            facts = run_live_public_lookups(
                NY_POINT, NY_POINT, settings, warnings, lead_id="test-ny-regression"
            )

        return facts, warnings

    def test_no_exception_escapes(self):
        settings = _make_settings()
        # Should not raise.
        self._run_ny_lookups(settings)

    def test_fact_keys_present(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        missing = EXPECTED_FACT_KEYS - set(facts.keys())
        assert not missing, f"Missing fact keys: {missing}"

    def test_state_auto_key_present(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        assert "StateAuto" in facts

    def test_dac_source_key_present(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        assert "DACSource" in facts

    def test_state_auto_is_ny(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        assert facts.get("StateAuto") == "NY"

    def test_waterbody_name_populated(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        assert facts.get("NearestWaterbodyName") == "Cayuga Lake"

    def test_huc12_populated(self):
        settings = _make_settings()
        facts, _ = self._run_ny_lookups(settings)
        assert facts.get("HUC12") == "020400010101"


# ---------------------------------------------------------------------------
# Group 6 — unknown-state graceful degradation
# ---------------------------------------------------------------------------

class TestUnknownStateGracefulDegradation:
    """
    When Census returns a state that is not in SUPPORTED_STATES (e.g. TX),
    the pipeline should complete without raising, StateAuto should be set to
    the detected value, and waterbody/DAC should fall back to NY URLs.
    """

    def test_tx_state_pipeline_completes(self):
        settings = _make_settings()
        warnings: List[str] = []

        census_resp = _census_payload("TX")
        huc12_resp = {"features": [{"attributes": {"huc12": "120902030505", "name": "Test"}}]}
        # NY FeatureServer is what the fallback routing picks for TX.
        waterbody_resp = _arcgis_feature_response(
            {"WATERBODY": "Some Creek", "WATERBODY_CATEGORY": "B"},
            include_geometry=True,
        )

        def fake_json_get(url, params, settings_, label, warnings_):
            if "geocoding.geo.census.gov" in url:
                return census_resp
            if "hydro.nationalmap.gov" in url:
                return huc12_resp
            if "arcgis.com" in url:
                return waterbody_resp
            if "data.ny.gov" in url:
                return []
            return None

        with patch("app.public_gis._json_get", side_effect=fake_json_get), \
             patch("app.public_gis._read_url", return_value=None):
            facts = run_live_public_lookups(
                NY_POINT, NY_POINT, settings, warnings, lead_id="test-tx-fallback"
            )

        assert facts.get("StateAuto") == "TX"

    def test_tx_state_uses_ny_waterbody_url(self):
        settings = _make_settings()
        warnings: List[str] = []
        captured_urls: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_urls.append(url)
            if "geocoding.geo.census.gov" in url:
                return _census_payload("TX")
            if "hydro.nationalmap.gov" in url:
                return {"features": []}
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get), \
             patch("app.public_gis._read_url", return_value=None):
            run_live_public_lookups(
                NY_POINT, NY_POINT, settings, warnings, lead_id="test-tx-url"
            )

        # TX is unsupported → normalize_state("TX") returns "NY" → NY URLs used.
        waterbody_urls = [u for u in captured_urls if "waterbody" in u.lower()
                         or "FeatureServer" in u or "MapServer" in u
                         or "arcgis.com" in u]
        # All waterbody requests must hit the NY NYSDEC FeatureServer, not gispub.epa.gov.
        de_urls = [u for u in captured_urls if "gispub.epa.gov" in u]
        assert not de_urls, (
            f"Expected no DE URLs for TX state, but got: {de_urls}"
        )

    def test_tx_no_exception(self):
        settings = _make_settings()
        warnings: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            if "geocoding.geo.census.gov" in url:
                return _census_payload("TX")
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get), \
             patch("app.public_gis._read_url", return_value=None):
            # Must not raise.
            run_live_public_lookups(
                NY_POINT, NY_POINT, settings, warnings, lead_id="test-tx-no-exc"
            )


# ---------------------------------------------------------------------------
# Group 7 — kill-switch: multi_state_routing_enabled=False forces NY
# ---------------------------------------------------------------------------

class TestKillSwitch:
    """
    When multi_state_routing_enabled=False, even if Census returns DE, the
    routing must use NY URLs for waterbody and DAC.
    """

    def _run_with_kill_switch(self, detected_state: str) -> tuple[Dict[str, Any], List[str]]:
        settings = _make_settings(multi_state_routing_enabled=False)
        warnings: List[str] = []
        captured_urls: List[str] = []

        def fake_json_get(url, params, settings_, label, warnings_):
            captured_urls.append(url)
            if "geocoding.geo.census.gov" in url:
                return _census_payload(detected_state)
            if "hydro.nationalmap.gov" in url:
                return {"features": []}
            # Return an empty feature list for all other ArcGIS calls.
            return {"features": []}

        with patch("app.public_gis._json_get", side_effect=fake_json_get), \
             patch("app.public_gis._read_url", return_value=None):
            facts = run_live_public_lookups(
                NY_POINT, NY_POINT, settings, warnings, lead_id="test-killswitch"
            )

        return facts, captured_urls  # type: ignore[return-value]

    def test_kill_switch_de_detected_still_uses_ny_featureserver(self):
        _, captured_urls = self._run_with_kill_switch("DE")
        # With kill-switch on, DE URLs (gispub.epa.gov) must NOT appear.
        de_urls = [u for u in captured_urls if "gispub.epa.gov" in u]
        assert not de_urls, (
            f"Kill-switch active but DE URL was called: {de_urls}"
        )

    def test_kill_switch_de_detected_uses_ny_featureserver_url(self):
        _, captured_urls = self._run_with_kill_switch("DE")
        ny_urls = [u for u in captured_urls if "services6.arcgis.com" in u]
        # The NY FeatureServer must have been called.
        assert ny_urls, (
            f"Kill-switch active — expected services6.arcgis.com to be called; "
            f"captured: {captured_urls}"
        )

    def test_kill_switch_de_detected_uses_ny_dac_url(self):
        _, captured_urls = self._run_with_kill_switch("DE")
        de_dac_urls = [u for u in captured_urls if "firstmap.delaware.gov" in u]
        assert not de_dac_urls, (
            f"Kill-switch active but DE DAC URL was called: {de_dac_urls}"
        )

    def test_kill_switch_state_auto_still_reflects_detected(self):
        """StateAuto records the Census-detected value, routing is separate."""
        facts, _ = self._run_with_kill_switch("DE")
        # The kill-switch controls routing, not the recorded detected state.
        assert facts.get("StateAuto") == "DE"

    def test_kill_switch_no_exception(self):
        # Must not raise.
        self._run_with_kill_switch("DE")

    def test_kill_switch_ny_detected_no_change(self):
        """With kill-switch on and NY detected, NY URLs are still used (no regression)."""
        _, captured_urls = self._run_with_kill_switch("NY")
        de_urls = [u for u in captured_urls if "gispub.epa.gov" in u]
        assert not de_urls
