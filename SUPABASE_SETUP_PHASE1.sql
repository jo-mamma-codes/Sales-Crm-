-- Phase 1 schema upgrades. Run in Supabase SQL Editor.
-- Idempotent (safe to re-run).

-- 1) Track when each sequence task was sent + Resend message ID
ALTER TABLE sequence_queue
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS message_id TEXT,
    ADD COLUMN IF NOT EXISTS sender_used TEXT,
    ADD COLUMN IF NOT EXISTS attempt_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error TEXT;

-- 2) Per-lead suppression flag (replaces file-based DNC list)
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS do_not_email BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS bounced BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS bounced_at TIMESTAMPTZ;

-- 3) Event ledger — Resend webhooks land here. Single source of truth for
-- delivery, bounce, open, click, complaint, reply events.
CREATE TABLE IF NOT EXISTS email_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id BIGINT,
    sequence_name TEXT,
    step INT,
    event_type TEXT NOT NULL,  -- sent, delivered, bounced, opened, clicked, complained, replied
    occurred_at TIMESTAMPTZ DEFAULT now(),
    source TEXT,  -- resend, gmail, manual
    message_id TEXT,
    recipient_email TEXT,
    payload JSONB
);

CREATE INDEX IF NOT EXISTS email_events_lead_id_idx ON email_events(lead_id);
CREATE INDEX IF NOT EXISTS email_events_message_id_idx ON email_events(message_id);
CREATE INDEX IF NOT EXISTS email_events_event_type_idx ON email_events(event_type);
CREATE INDEX IF NOT EXISTS email_events_occurred_at_idx ON email_events(occurred_at DESC);

-- 4) Disable RLS on new objects (we're using anon key, internal admin tool)
ALTER TABLE email_events DISABLE ROW LEVEL SECURITY;

-- 5) Backfill do_not_email from existing bounced_emails.json contents
-- (You'll run a one-time Python migration script — not in SQL)

-- 6) Trigger: auto-set do_not_email when bounce event fires
CREATE OR REPLACE FUNCTION mark_bounced_lead()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.event_type IN ('bounced', 'complained') THEN
        UPDATE leads
        SET do_not_email = true,
            bounced = (NEW.event_type = 'bounced'),
            bounced_at = CASE WHEN NEW.event_type = 'bounced' THEN NEW.occurred_at ELSE bounced_at END
        WHERE id = NEW.lead_id;
    END IF;
    IF NEW.event_type = 'unsubscribed' THEN
        UPDATE leads
        SET do_not_email = true,
            unsubscribed_at = NEW.occurred_at
        WHERE id = NEW.lead_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS email_events_bounce_trigger ON email_events;
CREATE TRIGGER email_events_bounce_trigger
    AFTER INSERT ON email_events
    FOR EACH ROW EXECUTE FUNCTION mark_bounced_lead();

-- Done. After running:
-- - sequence_queue has sent_at, message_id, sender_used, attempt_count, last_error
-- - leads has do_not_email, bounced, unsubscribed_at, bounced_at
-- - email_events table ready for Resend webhook posts
-- - Bounce/complaint events auto-flag the lead as do_not_email
