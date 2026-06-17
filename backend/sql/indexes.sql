-- Indexes for the application tables defined in schema.sql.
-- For indexes on the loaded statewide GIS layers, see scripts/create_indexes.sql.

-- leads
CREATE INDEX IF NOT EXISTS idx_leads_status        ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_boundary_stat ON leads (boundary_status);
CREATE INDEX IF NOT EXISTS idx_leads_rep_email     ON leads (sales_rep_email);
CREATE INDEX IF NOT EXISTS idx_leads_class         ON leads (candidate_class);
CREATE INDEX IF NOT EXISTS idx_leads_geom          ON leads USING GIST (geom);

-- field_boundaries
CREATE INDEX IF NOT EXISTS idx_boundaries_lead     ON field_boundaries (lead_id);
CREATE INDEX IF NOT EXISTS idx_boundaries_geom     ON field_boundaries USING GIST (geom);

-- auto_facts
CREATE INDEX IF NOT EXISTS idx_auto_facts_lead     ON auto_facts (lead_id);
CREATE INDEX IF NOT EXISTS idx_auto_facts_huc12    ON auto_facts (huc12);

-- bmp_candidates
CREATE INDEX IF NOT EXISTS idx_bmp_lead            ON bmp_candidates (lead_id);
CREATE INDEX IF NOT EXISTS idx_bmp_name            ON bmp_candidates (bmp_name);

-- calculations
CREATE INDEX IF NOT EXISTS idx_calc_lead           ON calculations (lead_id);

-- swcd_contacts
CREATE INDEX IF NOT EXISTS idx_swcd_county         ON swcd_contacts (county);
