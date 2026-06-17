"""Application settings, loaded from environment variables / a local .env file.

Everything that controls GIS behaviour, scoring, and the calculators lives here
so the backend is never hardcoded to one county, one CRS, or one cost table.
Nothing is required for the app to *start* -- a missing value just disables the
related lookup/feature and is reported as a warning.
"""
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict

# Feet <-> meters; m^2 -> acres.
FT_PER_M = 3.280839895
SQM_PER_ACRE = 4046.8564224

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ------------------------------------------------------------------ DB ---
    database_url: Optional[str] = None

    # ----------------------------------------------------- live public GIS ---
    # If DATABASE_URL is empty, /process-lead can still enrich leads by calling
    # USGS/NYSDEC/NY Open Data/Census/USDA public services at processing time.
    public_gis_lookups_enabled: bool = True
    public_api_timeout_sec: float = 12.0

    # ------------------------------------------------------- API security ---
    # If set, /save-boundary and /process-lead require this key
    # (header X-API-Key, or ?key= for the browser draw page).
    api_key: Optional[str] = None

    # ----------------------------------------------- shareable boundary link ---
    # Public base URL of this backend, used to build absolute boundary share
    # links for emails. Override with PUBLIC_BASE_URL if the domain changes.
    public_base_url: str = "https://agnps-backend.onrender.com"
    # Signed boundary KML links expire after this many hours (then the link dies).
    share_link_ttl_hours: int = 24

    # ---------------------------------------------------------- web / files ---
    web_dir: str = str(BACKEND_DIR / "web")
    # Backend-side cache that bridges /save-boundary (browser) and /process-lead
    # (Apps Script). Simple JSON file; no database required.
    boundary_store_path: str = str(BACKEND_DIR / "boundary_store.json")

    # ----------------------------------------------------------------- CRS ---
    input_crs: int = 4326          # API input coordinates (lat/lon)
    projected_crs: int = 5070      # distance/slope math (meters)
    area_crs: int = 5070           # acreage (EPSG:5070 = Albers equal-area)
    geom_column: str = "geom"

    # ------------------------------------------------------ search radii (ft) ---
    waterbody_search_radius_ft: float = 5280.0
    wipwl_search_radius_ft: float = 5280.0
    dac_nearby_distance_ft: float = 2640.0
    # Default buffer applied to a GPS point when no boundary is available.
    point_buffer_ft: float = 500.0

    # ------------------------------------------------- BMP rule thresholds ---
    waterbody_close_threshold_ft: float = 1000.0
    slope_low_threshold_pct: float = 2.0
    slope_moderate_threshold_pct: float = 4.0
    slope_high_threshold_pct: float = 8.0

    # --------------------------------------------------------- raster / DEM ---
    dem_tiles_dir: Optional[str] = None
    dem_is_slope: bool = False

    # --------------------------------------------------------- table names ---
    counties_table: str = "ny_counties"
    towns_table: str = "ny_towns"
    huc8_table: str = "huc8"
    huc10_table: str = "huc10"
    huc12_table: str = "huc12"
    streams_table: str = "streams_waterbodies"
    wipwl_table: str = "wipwl_waterbodies"
    dac_table: str = "dac_areas"
    ssurgo_table: str = "ssurgo_soils"

    # ------------------------------------------------ cost-share calculator ---
    costshare_low_pct: float = 0.75
    costshare_high_pct: float = 0.875
    company_margin_pct: float = 0.20

    # ------------------------------------- optional AppSheet API push (off) ---
    # If both are set, /save-boundary will push BoundaryStatus/acreage straight
    # into AppSheet. Otherwise Apps Script reconciles on the next /process-lead.
    appsheet_app_id: Optional[str] = None
    appsheet_api_key: Optional[str] = None
    appsheet_region: str = "www"  # www or eu

    # Convenience -----------------------------------------------------------
    @property
    def waterbody_search_radius_m(self) -> float:
        return self.waterbody_search_radius_ft / FT_PER_M

    @property
    def wipwl_search_radius_m(self) -> float:
        return self.wipwl_search_radius_ft / FT_PER_M

    @property
    def dac_nearby_distance_m(self) -> float:
        return self.dac_nearby_distance_ft / FT_PER_M

    @property
    def point_buffer_m(self) -> float:
        return self.point_buffer_ft / FT_PER_M

    # Rough project-cost placeholders: ProblemType -> (base $, $/acre).
    # PLACEHOLDERS ONLY -- edit freely; the company owns these numbers.
    @property
    def project_cost_table(self) -> Dict[str, Tuple[float, float]]:
        return {
            "bad outlet": (8000.0, 300.0),
            "ditch or stream erosion": (10000.0, 400.0),
            "surface erosion": (10000.0, 400.0),
            "surface runoff": (10000.0, 400.0),
            "possible controlled drainage": (12000.0, 250.0),
            "wet field": (7500.0, 250.0),
            "unknown old tile": (7500.0, 250.0),
            "other": (5000.0, 200.0),
            "unknown": (5000.0, 200.0),
        }

    project_cost_default: Tuple[float, float] = (5000.0, 200.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
