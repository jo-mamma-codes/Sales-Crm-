-- Phase 3: audit_log table. Run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ DEFAULT now(),
    actor TEXT,           -- user identifier (email or name)
    action TEXT NOT NULL, -- enroll, send, dnc, bulk_dnc, sequence_edit, etc
    target_type TEXT,     -- lead, sequence, template, task
    target_id TEXT,
    metadata JSONB
);

ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS audit_log_occurred_idx ON audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx ON audit_log(actor);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log(action);
