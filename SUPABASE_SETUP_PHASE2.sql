-- Phase 2: import_batches table + reply status for sequence_queue.
-- Run in Supabase SQL Editor. Idempotent.

CREATE TABLE IF NOT EXISTS import_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    uploaded_by TEXT,
    source_type TEXT,
    row_count INT,
    metadata JSONB
);

ALTER TABLE import_batches DISABLE ROW LEVEL SECURITY;

-- Link leads to a batch (nullable for legacy leads)
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES import_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS leads_batch_id_idx ON leads(batch_id);

-- Allow 'paused', 'replied', 'cancelled' as status values on sequence_queue
-- (Postgres TEXT column has no enum, but we document the new valid values)
-- Status state machine:
--   pending → sending → done|failed
--   pending → paused (via pause sequence) → pending (via resume)
--   pending → replied (via reply detection) — terminal
--   pending → cancelled (via bulk move A→B source) — terminal
--   pending → skipped (via manual skip or DNC auto-skip) — terminal
