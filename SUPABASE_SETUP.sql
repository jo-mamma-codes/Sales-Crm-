-- Run this in Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor → New query)
-- Creates a persistent key-value store for sequences, templates, and other config
-- that would otherwise be lost on every Streamlit Cloud redeploy.

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Optional: trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_app_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS app_config_updated_at ON app_config;
CREATE TRIGGER app_config_updated_at
    BEFORE UPDATE ON app_config
    FOR EACH ROW EXECUTE FUNCTION update_app_config_timestamp();

-- After running this, the CRM will auto-sync sequences and templates to this table.
-- Local files become the cache; Supabase is the source of truth.
