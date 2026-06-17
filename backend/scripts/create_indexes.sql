-- Spatial + attribute indexes for the loaded statewide GIS layers.
-- load_layers.py already creates a GIST index per layer on load; run this after
-- a bulk load to guarantee every geometry column and common lookup key is
-- indexed. Safe to re-run (IF NOT EXISTS). Column-dependent B-tree indexes are
-- wrapped so a dataset that lacks a column is skipped, not an error.

-- =================== GIST indexes on every geometry column ===================
CREATE INDEX IF NOT EXISTS idx_huc12_geom                ON huc12                USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_wipwl_waterbodies_geom    ON wipwl_waterbodies    USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_dac_areas_geom            ON dac_areas            USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_ny_counties_geom          ON ny_counties          USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_ny_towns_geom             ON ny_towns             USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_streams_waterbodies_geom  ON streams_waterbodies  USING GIST (geom);
-- Optional layers (created only if the table exists):
DO $$
BEGIN
    IF to_regclass('ssurgo_soils') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_ssurgo_soils_geom ON ssurgo_soils USING GIST (geom);
    END IF;
    IF to_regclass('huc8')  IS NOT NULL THEN CREATE INDEX IF NOT EXISTS idx_huc8_geom  ON huc8  USING GIST (geom); END IF;
    IF to_regclass('huc10') IS NOT NULL THEN CREATE INDEX IF NOT EXISTS idx_huc10_geom ON huc10 USING GIST (geom); END IF;
END$$;

-- =================== B-tree indexes on common lookup keys ====================
-- HUC codes
DO $$
BEGIN
    BEGIN CREATE INDEX IF NOT EXISTS idx_huc12_code ON huc12 (huc12); EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_huc12_name ON huc12 (name);  EXCEPTION WHEN undefined_column THEN NULL; END;
END$$;

-- Waterbody names (NHD/NY hydrography + WI/PWL)
DO $$
BEGIN
    BEGIN CREATE INDEX IF NOT EXISTS idx_streams_gnis ON streams_waterbodies (gnis_name); EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_streams_name ON streams_waterbodies (name);      EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_wipwl_name   ON wipwl_waterbodies (water_name);  EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_wipwl_name2  ON wipwl_waterbodies (name);        EXCEPTION WHEN undefined_column THEN NULL; END;
END$$;

-- County / town names
DO $$
BEGIN
    BEGIN CREATE INDEX IF NOT EXISTS idx_counties_name ON ny_counties (name);     EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_counties_fips ON ny_counties (statefp);  EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_towns_name    ON ny_towns (name);        EXCEPTION WHEN undefined_column THEN NULL; END;
END$$;

-- Soil keys
DO $$
BEGIN
    BEGIN CREATE INDEX IF NOT EXISTS idx_ssurgo_mukey  ON ssurgo_soils (mukey);   EXCEPTION WHEN undefined_column THEN NULL; END;
    BEGIN CREATE INDEX IF NOT EXISTS idx_ssurgo_musym  ON ssurgo_soils (musym);   EXCEPTION WHEN undefined_column THEN NULL; END;
END$$;

ANALYZE;
