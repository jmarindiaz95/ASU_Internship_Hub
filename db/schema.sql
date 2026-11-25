CREATE SCHEMA IF NOT EXISTS internship_hub;

CREATE TABLE IF NOT EXISTS internship_hub.categories (
    id text PRIMARY KEY, label text NOT NULL, sort_order integer NOT NULL DEFAULT 0
);
INSERT INTO internship_hub.categories(id,label,sort_order) VALUES
 ('accounting','Accounting & tax',1),('corporate_finance','Corporate finance',2),
 ('banking','Banking',3),('insurance','Insurance & risk',4),
 ('wealth','Wealth & financial planning',5) ON CONFLICT DO NOTHING;
INSERT INTO internship_hub.categories(id,label,sort_order) VALUES
 ('technology','Technology & software',6),('engineering','Engineering',7),('healthcare','Healthcare',8),
 ('education','Education',9),('operations','Operations & logistics',10),('sales_marketing','Sales & marketing',11),
 ('administration','Administration & customer service',12),('public_service','Public service',13),
 ('legal','Legal & compliance',14),('construction','Construction & skilled trades',15),
 ('hospitality','Hospitality & retail',16),('science','Science & research',17),('other','Other careers',18)
 ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS internship_hub.companies (
    id text PRIMARY KEY, name text NOT NULL UNIQUE, industry text, website_url text, careers_url text
);
CREATE TABLE IF NOT EXISTS internship_hub.locations (
    id text PRIMARY KEY, company_id text NOT NULL REFERENCES internship_hub.companies(id),
    address text, city text, state text, geom geometry(Point,4326) NOT NULL,
    evidence_url text
);
CREATE INDEX IF NOT EXISTS hub_locations_geog_idx ON internship_hub.locations USING gist((geom::geography));

CREATE TABLE IF NOT EXISTS internship_hub.opportunities (
    id text PRIMARY KEY, company_id text NOT NULL REFERENCES internship_hub.companies(id),
    title text NOT NULL, category_id text NOT NULL REFERENCES internship_hub.categories(id),
    job_category text, career_stage text NOT NULL DEFAULT 'unknown'
      CHECK(career_stage IN ('early','mid','senior','unknown')),
    opportunity_type text NOT NULL DEFAULT 'unknown'
      CHECK(opportunity_type IN ('internship','job','program','unknown')),
    commitment text NOT NULL DEFAULT 'unknown' CHECK(commitment IN ('full_time','part_time','unknown')),
    arrangement text NOT NULL DEFAULT 'unknown' CHECK(arrangement IN ('on_site','hybrid','remote','unknown')),
    schedule_tags jsonb NOT NULL DEFAULT '[]', description text NOT NULL DEFAULT '',
    requirements text, hours_per_week numeric CHECK(hours_per_week >= 0), program_term text,
    paid_status text NOT NULL DEFAULT 'unknown' CHECK(paid_status IN ('paid','unpaid','unknown')),
    salary_min numeric CHECK(salary_min >= 0), salary_max numeric CHECK(salary_max >= 0),
    salary_period text CHECK(salary_period IN ('hour','year') OR salary_period IS NULL),
    application_url text NOT NULL, application_method text, application_email text, source_url text NOT NULL,
    posting_date date, deadline date,
    status text NOT NULL DEFAULT 'unknown' CHECK(status IN ('open','closed','unknown','program')),
    review_state text NOT NULL DEFAULT 'needs_review' CHECK(review_state IN ('verified','needs_review')),
    review_note text, source_checked_at timestamptz, imported_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    overrides jsonb NOT NULL DEFAULT '{}',
    CHECK(salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min),
    CHECK(NOT (overrides ?| ARRAY['id','company_id','overrides','imported_at']))
);
CREATE INDEX IF NOT EXISTS hub_opportunities_company_idx ON internship_hub.opportunities(company_id);
CREATE INDEX IF NOT EXISTS hub_opportunities_search_idx ON internship_hub.opportunities USING gin(
    to_tsvector('english',title || ' ' || description));
CREATE TABLE IF NOT EXISTS internship_hub.opportunity_locations (
    opportunity_id text NOT NULL REFERENCES internship_hub.opportunities(id),
    location_id text NOT NULL REFERENCES internship_hub.locations(id),
    verified boolean NOT NULL DEFAULT false, evidence_url text,
    PRIMARY KEY(opportunity_id,location_id)
);
ALTER TABLE internship_hub.opportunities ADD COLUMN IF NOT EXISTS location_resolution jsonb NOT NULL DEFAULT '{}';
ALTER TABLE internship_hub.opportunity_locations ADD COLUMN IF NOT EXISTS basis text NOT NULL DEFAULT 'worksite';
ALTER TABLE internship_hub.opportunity_locations ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
CREATE TABLE IF NOT EXISTS internship_hub.enrichment_cache (
    cache_key text PRIMARY KEY, payload jsonb NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS internship_hub.sources (
    id text PRIMARY KEY, name text NOT NULL, kind text NOT NULL, index_url text,
    enabled boolean NOT NULL DEFAULT false, interval_hours numeric NOT NULL DEFAULT 24 CHECK(interval_hours >= 1),
    config jsonb NOT NULL DEFAULT '{}', last_attempt_at timestamptz, last_success_at timestamptz,
    last_status text NOT NULL DEFAULT 'pending', last_message text
);
CREATE TABLE IF NOT EXISTS internship_hub.collection_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL REFERENCES internship_hub.sources(id), started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz, status text NOT NULL DEFAULT 'running', discovered integer NOT NULL DEFAULT 0,
    updated integer NOT NULL DEFAULT 0, message text
);
CREATE TABLE IF NOT EXISTS internship_hub.source_listings (
    source_id text NOT NULL REFERENCES internship_hub.sources(id), external_id text NOT NULL,
    opportunity_id text NOT NULL REFERENCES internship_hub.opportunities(id), url text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
    fetched_at timestamptz, content_hash text, extracted_text text, raw_fields jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY(source_id,external_id)
);
CREATE INDEX IF NOT EXISTS hub_source_opportunity_idx ON internship_hub.source_listings(opportunity_id);
CREATE TABLE IF NOT EXISTS internship_hub.listing_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL, external_id text NOT NULL, content_hash text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(), extracted_text text NOT NULL,
    UNIQUE(source_id,external_id,content_hash),
    FOREIGN KEY(source_id,external_id) REFERENCES internship_hub.source_listings(source_id,external_id)
);
CREATE TABLE IF NOT EXISTS internship_hub.schedule_rules (
    id text PRIMARY KEY, label text NOT NULL, pattern text NOT NULL, exclusion_pattern text, enabled boolean NOT NULL DEFAULT true
);
INSERT INTO internship_hub.schedule_rules VALUES
 ('flexible_hours','Flexible hours','\mflexible\s+(hours|schedule|scheduling)\M',
  '\m(no|not|without)\s+(\w+\s+){0,3}flexible|must be flexible|flexible spending',true),
 ('coursework','Around coursework','around\s+(ASU\s+)?coursework|around\s+(your\s+)?class(es)?|flexible academic schedule',
  '\m(no|not|without)\s+(\w+\s+){0,3}(around|flexible)',true)
 ON CONFLICT DO NOTHING;

-- A refresh updates collected fields; explicit reviewer overrides always take precedence.
CREATE OR REPLACE VIEW internship_hub.effective_opportunities AS
 SELECT (jsonb_populate_record(NULL::internship_hub.opportunities, to_jsonb(o) || o.overrides)).*
 FROM internship_hub.opportunities o;
