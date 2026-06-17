-- Application schema for the AgNPS Candidate Lead Screener (v0.2).
--
-- Google Sheets remains the system of record for the AppSheet front end. These
-- tables are the optional PostGIS-side mirror for persistence/reporting.
-- The statewide GIS LAYER tables (ny_counties, huc12, ssurgo_soils, ...) are
-- created dynamically by scripts/load_layers.py and are NOT defined here.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- leads ----
CREATE TABLE IF NOT EXISTS leads (
    lead_id                  TEXT PRIMARY KEY,
    created_at               TIMESTAMPTZ DEFAULT now(),
    updated_at               TIMESTAMPTZ DEFAULT now(),
    sales_rep_email          TEXT,
    sales_rep_name           TEXT,
    customer_name            TEXT,
    farm_name                TEXT,
    field_name               TEXT,
    problem_type             TEXT,
    problem_description      TEXT,
    gps_latitude             DOUBLE PRECISION,
    gps_longitude            DOUBLE PRECISION,
    location_accuracy_ft     DOUBLE PRECISION,
    boundary_status          TEXT DEFAULT 'Not Started',
    boundary_source          TEXT,
    boundary_area_acres      DOUBLE PRECISION,
    boundary_draw_url        TEXT,
    interested_costshare     TEXT,
    permission_swcd          TEXT,
    urgency                  TEXT,
    status                   TEXT DEFAULT 'New',
    candidate_score          INTEGER,
    candidate_class          TEXT,
    gis_confidence           TEXT,
    estimated_project_cost   NUMERIC,
    estimated_costshare_low  NUMERIC,
    estimated_costshare_high NUMERIC,
    estimated_farmer_low     NUMERIC,
    estimated_farmer_high    NUMERIC,
    estimated_company_rev    NUMERIC,
    report_url               TEXT,
    internal_notes           TEXT,
    next_action              TEXT,
    geom                     geometry(Geometry, 5070)   -- project location (analysis CRS)
);

-- ----------------------------------------------------- field_boundaries ----
CREATE TABLE IF NOT EXISTS field_boundaries (
    boundary_id           TEXT PRIMARY KEY,
    lead_id               TEXT REFERENCES leads(lead_id) ON DELETE CASCADE,
    created_at            TIMESTAMPTZ DEFAULT now(),
    boundary_source       TEXT,
    boundary_geojson      TEXT,
    boundary_wkt          TEXT,
    boundary_area_acres   DOUBLE PRECISION,
    boundary_centroid_lat DOUBLE PRECISION,
    boundary_centroid_lng DOUBLE PRECISION,
    boundary_confidence   TEXT,
    geometry_valid        BOOLEAN,
    geometry_warning      TEXT,
    notes                 TEXT,
    geom                  geometry(Geometry, 5070)
);

-- ----------------------------------------------------------- auto_facts ----
CREATE TABLE IF NOT EXISTS auto_facts (
    fact_id                  BIGSERIAL PRIMARY KEY,
    lead_id                  TEXT REFERENCES leads(lead_id) ON DELETE CASCADE,
    processed_at             TIMESTAMPTZ DEFAULT now(),
    analysis_geometry_source TEXT,
    county_auto              TEXT,
    town_auto                TEXT,
    huc8                     TEXT,
    huc10                    TEXT,
    huc12                    TEXT,
    huc12_name               TEXT,
    nearest_waterbody_name   TEXT,
    nearest_waterbody_type   TEXT,
    distance_to_waterbody_ft DOUBLE PRECISION,
    wipwl_nearby             BOOLEAN,
    wipwl_summary            TEXT,
    dac_intersecting         BOOLEAN,
    dac_nearby               BOOLEAN,
    soil_drainage_class      TEXT,
    hydrologic_soil_group    TEXT,
    mean_slope_percent       DOUBLE PRECISION,
    max_slope_percent        DOUBLE PRECISION,
    gis_confidence           TEXT,
    missing_info_checklist   TEXT,
    human_review_warnings    TEXT,
    processing_error         TEXT,
    -- score breakdown
    wq_connection_score      INTEGER,
    wipwl_score              INTEGER,
    bmp_fit_score            INTEGER,
    topo_soils_score         INTEGER,
    documentation_score      INTEGER,
    dac_score                INTEGER,
    score_explanation        TEXT
);

-- ------------------------------------------------------- bmp_candidates ----
CREATE TABLE IF NOT EXISTS bmp_candidates (
    bmp_candidate_id   BIGSERIAL PRIMARY KEY,
    lead_id            TEXT REFERENCES leads(lead_id) ON DELETE CASCADE,
    bmp_name           TEXT,
    bmp_category       TEXT,
    reason_suggested   TEXT,
    confidence         TEXT,
    needs_human_review BOOLEAN DEFAULT TRUE,
    notes              TEXT
);

-- --------------------------------------------------------- calculations ----
CREATE TABLE IF NOT EXISTS calculations (
    calculation_id              TEXT PRIMARY KEY,
    lead_id                     TEXT REFERENCES leads(lead_id) ON DELETE CASCADE,
    created_at                  TIMESTAMPTZ DEFAULT now(),
    estimated_project_cost      NUMERIC,
    costshare_low_percent       NUMERIC,
    costshare_high_percent      NUMERIC,
    estimated_costshare_low     NUMERIC,
    estimated_costshare_high    NUMERIC,
    estimated_farmer_cost_low   NUMERIC,
    estimated_farmer_cost_high  NUMERIC,
    estimated_company_revenue   NUMERIC,
    company_gross_margin_pct    NUMERIC,
    company_gross_margin_dollars NUMERIC,
    assumptions                 TEXT,
    calculator_warnings         TEXT
);

-- ------------------------------------------------- gis_layers_metadata ----
CREATE TABLE IF NOT EXISTS gis_layers_metadata (
    layer_id      BIGSERIAL PRIMARY KEY,
    table_name    TEXT UNIQUE,
    source_file   TEXT,
    layer_name    TEXT,
    feature_count INTEGER,
    crs_epsg      INTEGER,
    loaded_at     TIMESTAMPTZ DEFAULT now(),
    notes         TEXT
);

-- ----------------------------------------------------- swcd_contacts ----
CREATE TABLE IF NOT EXISTS swcd_contacts (
    contact_id   BIGSERIAL PRIMARY KEY,
    county       TEXT,
    swcd_name    TEXT,
    contact_name TEXT,
    email        TEXT,
    phone        TEXT,
    notes        TEXT
);
