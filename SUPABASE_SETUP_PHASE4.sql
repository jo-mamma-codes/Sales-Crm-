-- Phase 4: HubSpot-style object model — companies, contacts, deals, associations.
-- Run in Supabase SQL Editor.

-- ─── Companies ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    industry TEXT,
    region TEXT,
    address TEXT,
    phone TEXT,
    website TEXT,
    employee_count INT,
    annual_revenue NUMERIC,
    -- Hospitality / commercial properties
    num_locations INT,
    num_terminals INT,
    pos_system TEXT,
    integrations TEXT[],
    current_rates NUMERIC,
    -- Lifecycle
    lifecycle_stage TEXT DEFAULT 'lead',
    health_score INT,
    owner TEXT,
    -- Provenance
    source TEXT,
    batch_id UUID REFERENCES import_batches(id) ON DELETE SET NULL,
    -- Generic JSONB for extension without migration
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE companies DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS companies_name_idx ON companies(lower(name));
CREATE INDEX IF NOT EXISTS companies_owner_idx ON companies(owner);
CREATE INDEX IF NOT EXISTS companies_stage_idx ON companies(lifecycle_stage);


-- ─── Contacts (people) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    title TEXT,
    role TEXT,
    linkedin TEXT,
    -- Lifecycle
    lifecycle_stage TEXT DEFAULT 'lead',
    owner TEXT,
    do_not_email BOOLEAN DEFAULT false,
    bounced BOOLEAN DEFAULT false,
    unsubscribed_at TIMESTAMPTZ,
    bounced_at TIMESTAMPTZ,
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE contacts DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS contacts_email_idx ON contacts(lower(email));
CREATE INDEX IF NOT EXISTS contacts_name_idx ON contacts(lower(first_name || ' ' || last_name));


-- ─── Deals ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deals (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    pipeline TEXT DEFAULT 'Sales',
    stage TEXT,
    amount NUMERIC,
    currency TEXT DEFAULT 'GBP',
    probability NUMERIC,
    close_date DATE,
    owner TEXT,
    -- Hospitality-specific
    num_terminals INT,
    rates_offered NUMERIC,
    contract_term_months INT,
    -- Provenance
    source TEXT,
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ
);

ALTER TABLE deals DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS deals_pipeline_idx ON deals(pipeline);
CREATE INDEX IF NOT EXISTS deals_stage_idx ON deals(stage);


-- ─── Associations (polymorphic many-to-many) ──────────────────────────
CREATE TABLE IF NOT EXISTS associations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_object_type TEXT NOT NULL,  -- 'company','contact','deal'
    from_object_id BIGINT NOT NULL,
    to_object_type TEXT NOT NULL,
    to_object_id BIGINT NOT NULL,
    association_label TEXT,           -- 'primary_contact','decision_maker','signer','related', etc
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (from_object_type, from_object_id, to_object_type, to_object_id, association_label)
);

ALTER TABLE associations DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS assoc_from_idx ON associations(from_object_type, from_object_id);
CREATE INDEX IF NOT EXISTS assoc_to_idx ON associations(to_object_type, to_object_id);


-- ─── Tags (lightweight visual labels) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    color TEXT DEFAULT '#7c3aed',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS object_tags (
    id BIGSERIAL PRIMARY KEY,
    object_type TEXT NOT NULL,  -- 'company','contact','deal'
    object_id BIGINT NOT NULL,
    tag_id INT REFERENCES tags(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (object_type, object_id, tag_id)
);

ALTER TABLE tags DISABLE ROW LEVEL SECURITY;
ALTER TABLE object_tags DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS object_tags_lookup_idx ON object_tags(object_type, object_id);


-- ─── Property definitions (schema-driven, per-object) ─────────────────
CREATE TABLE IF NOT EXISTS property_definitions (
    id SERIAL PRIMARY KEY,
    object_type TEXT NOT NULL,  -- 'company','contact','deal'
    name TEXT NOT NULL,          -- e.g. 'num_locations'
    label TEXT,                   -- e.g. 'Number of Locations'
    data_type TEXT NOT NULL,      -- 'text','number','currency','percent','select','multi_select','boolean','date','relation','computed'
    options JSONB,                -- for select/multi_select: list of values
    group_name TEXT,              -- 'Company Info','Commercial','Technical','Stakeholders','Internal'
    display_order INT DEFAULT 0,
    required BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (object_type, name)
);

ALTER TABLE property_definitions DISABLE ROW LEVEL SECURITY;


-- ─── Activities (unified timeline) ──────────────────────────────────────
-- We keep activity table you already have, but extend it to point
-- at any object via object_type + object_id (currently only lead_id).
ALTER TABLE activity
    ADD COLUMN IF NOT EXISTS object_type TEXT,
    ADD COLUMN IF NOT EXISTS object_id BIGINT;

CREATE INDEX IF NOT EXISTS activity_object_idx ON activity(object_type, object_id);


-- ─── Seed default property definitions ─────────────────────────────────
INSERT INTO property_definitions (object_type, name, label, data_type, group_name, display_order) VALUES
    -- Company Info
    ('company', 'website', 'Website', 'text', 'Company Info', 1),
    ('company', 'industry', 'Industry', 'select', 'Company Info', 2),
    ('company', 'address', 'HQ Address', 'text', 'Company Info', 3),
    ('company', 'employee_count', 'Employees', 'number', 'Company Info', 4),
    -- Commercial
    ('company', 'num_locations', 'Number of Locations', 'number', 'Commercial', 10),
    ('company', 'num_terminals', 'Number of Terminals', 'number', 'Commercial', 11),
    ('company', 'current_rates', 'Current Rates (%)', 'percent', 'Commercial', 12),
    ('company', 'annual_revenue', 'Annual Revenue', 'currency', 'Commercial', 13),
    -- Technical
    ('company', 'pos_system', 'POS System', 'select', 'Technical', 20),
    ('company', 'integrations', 'Integrations', 'multi_select', 'Technical', 21),
    -- Internal
    ('company', 'owner', 'Owner', 'select', 'Internal', 30),
    ('company', 'lifecycle_stage', 'Lifecycle Stage', 'select', 'Internal', 31),
    ('company', 'health_score', 'Health Score', 'number', 'Internal', 32),

    -- Contact properties
    ('contact', 'email', 'Email', 'text', 'Contact Info', 1),
    ('contact', 'phone', 'Phone', 'text', 'Contact Info', 2),
    ('contact', 'title', 'Job Title', 'text', 'Contact Info', 3),
    ('contact', 'linkedin', 'LinkedIn', 'text', 'Contact Info', 4),
    ('contact', 'role', 'Role', 'select', 'Internal', 10),

    -- Deal properties
    ('deal', 'amount', 'Amount', 'currency', 'Deal Info', 1),
    ('deal', 'close_date', 'Close Date', 'date', 'Deal Info', 2),
    ('deal', 'probability', 'Probability %', 'percent', 'Deal Info', 3),
    ('deal', 'pipeline', 'Pipeline', 'select', 'Deal Info', 4),
    ('deal', 'stage', 'Stage', 'select', 'Deal Info', 5),
    ('deal', 'num_terminals', 'Terminals', 'number', 'Commercial', 10),
    ('deal', 'rates_offered', 'Rates Offered (%)', 'percent', 'Commercial', 11),
    ('deal', 'contract_term_months', 'Contract Term (months)', 'number', 'Commercial', 12),
    ('deal', 'owner', 'Owner', 'select', 'Internal', 20)
ON CONFLICT (object_type, name) DO NOTHING;
