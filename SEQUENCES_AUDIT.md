# Sequences Architecture Audit

**Date:** 2026-05-21
**Author:** Claude (paired with Joseph)
**Scope:** Full sequences subsystem — enrollment, sending, state, reporting, UX

---

## 1. Current Architecture (as-is)

### Components
- **Frontend:** Streamlit single-page app, all state in `st.session_state` + `@st.cache_data`
- **DB:** Supabase (Postgres) — tables: `leads`, `sequence_queue`, `activity_log`
- **Sending:** Mix of three mechanisms layered on top of each other:
  - Manual Gmail tab links (`mailto:` style URLs)
  - Resend API (recently added, untested)
  - Gmail API "check sent" inference
- **State source of truth:** Ambiguous — split across `sequence_queue.status`, `activity_log`, `st.session_state["seq_bulk_sent"]`, and Streamlit's caches.

### Data flow on "send an email"
```
User clicks row
  → Streamlit button callback
    → Insert activity_log row
    → Update lead (stage=Contacted, last_touch=today)
    → Update sequence_queue (status=done)
    → Increment send_tracker.json counter
    → Set session_state[seq_bulk_sent].add(i)
    → Trigger window.open(gmail_link) via components.html
    → st.rerun()
```
Five writes, one popup, no transaction, no idempotency key.

---

## 2. Trust-Breaking Issues (root causes)

### 2.1 No single source of truth
`activity_log` records sends.
`sequence_queue.status` records sends.
`session_state.seq_bulk_sent` records sends.
`send_tracker.json` records sends.
Any one of these can drift from the others — and they have. When the Gmail-check sees an old activity_log entry from a different sequence, the enroll filter incorrectly excludes the lead. When session_state is cleared on rerun, the UI thinks rows are unsent.

**Fix direction:** `sequence_queue` rows are the canonical record. Every other view derives from them. Drop `send_tracker.json`. `activity_log` becomes append-only history (for UI timeline only).

### 2.2 No idempotency on send
The "send" flow has no idempotency key. A double-click or a network retry produces two activity_log rows + two Gmail tabs. The earlier 3x-send-to-Laura incident was exactly this.

**Fix direction:** Each `sequence_queue` row gets a unique `task_uuid` (already has PK). Send action requires `task_uuid` + checks `status='pending'` in the UPDATE — if it returns 0 rows, the click was a duplicate, refuse.

### 2.3 No transactional integrity
`save_lead`, `log_activity`, `seq_q.update` are three separate Supabase calls. If any fails mid-way, the system is inconsistent.

**Fix direction:** Move to a Postgres function (`send_email_atomic(task_id, message_id)`) called via Supabase RPC. Single transaction, all-or-nothing.

### 2.4 Type mismatch (already partially fixed)
`sequence_queue.lead_id` is `int`. `leads.id` is `text` in the dataframe but `bigint` in Postgres. Comparisons fail silently. Already patched with `.astype(str)` but the underlying type drift remains.

**Fix direction:** Force `leads.id` to `text` in Postgres (or `bigint` everywhere). Drop pandas-side coercion.

### 2.5 Streamlit session state is per-tab
Opening the CRM in two tabs gives each tab its own `seq_bulk_sent` set. They diverge. User in tab A sends an email; tab B still shows the row as unsent and lets the user re-send.

**Fix direction:** UI state must derive from DB every render. Drop `seq_bulk_sent` from session_state; query `sequence_queue.status` instead.

### 2.6 No background workers / no async / no retries
Every operation is synchronous in the request thread. Sending 100 emails via Resend in a row blocks the UI for ~30 seconds and any failure mid-batch leaves a partial state.

**Fix direction:** Move sends to a queue (Supabase queue table + a separate worker process, or just an `await` chain with proper retry).

### 2.7 Caching is brittle
`@st.cache_data(ttl=30)` on `load_crm()` + `st.session_state["_df_cache"]` + ad-hoc `.clear()` calls scattered through the code. Updates frequently don't propagate because one cache layer is stale.

**Fix direction:** One cache layer. Either fully cached with explicit invalidation on writes, or no cache (Postgres can handle 7k row pulls).

### 2.8 No event ledger
There is no immutable event log for sends, bounces, replies, opens. Resend webhook events have nowhere to land.

**Fix direction:** New `email_events` table: `(event_id, task_id, lead_id, event_type, occurred_at, payload jsonb)`. Webhook from Resend writes here. UI reads from here.

### 2.9 Pool filtering is procedural, not declarative
The "who is eligible to enroll" logic is 30 lines of pandas manipulation reconstructed every render. Each tweak produces new edge cases. The recent "137 enrolled but 50 sent" confusion was a symptom — UI showed a count from one source, logic excluded based on another.

**Fix direction:** One SQL view (or function) `eligible_for_sequence(seq_name, source_filter, region_filter, stage_filter)` returns the pool. UI just renders the result.

### 2.10 No bounce/unsubscribe handling
`bounced_emails.json` exists locally but is unused on cloud. Unsubscribe flow doesn't exist beyond the "reply 'unsubscribe'" line in templates.

**Fix direction:** Resend webhook → `email_events` → mark lead `do_not_email=true`. Unsubscribe link in body, public endpoint writes the same flag.

---

## 3. Proposed Target Architecture

### Tables (Postgres)

```sql
-- Existing, cleaned up
leads (
  id bigint PK,
  ...existing columns...,
  do_not_email boolean default false,
  bounced_at timestamptz,
  source_batch_id uuid  -- new: links to import_batches
)

-- New
import_batches (
  id uuid PK,
  name text,
  uploaded_at timestamptz,
  uploaded_by text,
  source_type text,  -- 'cornwall', 'airscale', 'manual'
  row_count int,
  metadata jsonb
)

-- Replaces sequence_queue with stricter schema
sequence_tasks (
  id uuid PK,
  lead_id bigint FK,
  sequence_name text,
  step int,
  due_at timestamptz,
  status text CHECK (status IN ('pending','sending','sent','failed','skipped','cancelled')),
  sender_email text,
  template_name text,
  enrolled_at timestamptz default now(),
  sent_at timestamptz,
  message_id text,  -- Resend message ID for webhook correlation
  attempt_count int default 0,
  last_error text,
  UNIQUE (lead_id, sequence_name, step)  -- idempotency
)

-- New: event ledger
email_events (
  id uuid PK,
  task_id uuid FK,
  lead_id bigint FK,
  event_type text,  -- sent, delivered, bounced, opened, clicked, complained, replied
  occurred_at timestamptz default now(),
  source text,  -- resend, gmail, manual
  payload jsonb
)

-- Activity log stays for UI timeline (notes, calls, manual entries)
activity_log (...)
```

### Send flow (target)

```
User clicks "Send all due"
  → POST to RPC: send_due_tasks(limit=20)
    → Postgres function:
        1. SELECT FOR UPDATE SKIP LOCKED 20 rows from sequence_tasks WHERE status='pending' AND due_at <= now()
        2. UPDATE those to status='sending'
        3. Return list of (task_id, lead_id, email, subject, body, from_email)
    → Streamlit calls Resend.batch_send() for the list
    → For each result:
        - On success: RPC mark_sent(task_id, message_id) — updates status=sent, sent_at, message_id
        - On failure: RPC mark_failed(task_id, error) — increments attempt_count, sets last_error
    → Resend webhook later writes email_events for delivered/bounced/opened
```

Idempotent. Transactional. Retryable (failed tasks reset to pending after delay). No duplicates possible because `SKIP LOCKED` and unique constraint.

### Pool logic (target)
```sql
CREATE VIEW eligible_for_sequence AS
SELECT l.*
FROM leads l
WHERE l.do_not_email = false
  AND l.email LIKE '%@%'
  AND NOT EXISTS (
    SELECT 1 FROM sequence_tasks t
    WHERE t.lead_id = l.id AND t.status IN ('sent','sending')
  );

-- UI calls with filter:
SELECT * FROM eligible_for_sequence
WHERE source_batch_id = $1 AND stage = ANY($2);
```

One query, no pandas, deterministic.

---

## 4. UX Rebuild Outline

### Section structure
```
Sequences
├── Overview           (analytics dashboard)
├── Today's queue      (what to send now)
├── Enrollment         (build pools, enroll)
├── Sequences          (CRUD)
└── Events log         (filterable history)
```

### Today's queue (the operational page)
- Single big "Send next 20" button (sends via Resend, transactional)
- Or "Open in Gmail tabs" for manual review
- Each task has clear status pill: Pending / Sending / Sent / Bounced / Replied
- Cannot interact with a sent task — read-only with timestamp
- Real-time refresh every 10s

### Enrollment
- Step 1: choose sequence (with preview)
- Step 2: select pool (filters: import batch, source, region, stage, last_emailed_age)
- Step 3: dry-run preview (first 3 leads with rendered emails)
- Step 4: confirm with explicit count and exclusion summary

### Contacts/leads filter additions
- Filter by import batch (dropdown of named batches)
- Filter by enrolled status (in sequence X / any sequence / none)
- Filter by email sent status (sent / queued / bounced / replied / never)
- Filter by uploaded date range
- Filter by tags (new column)

---

## 5. Phased Plan

### Phase 0 — Stabilise (1-2 days, do now)
- Make sequence_queue the only source of "sent" truth
- Remove session_state.seq_bulk_sent from logic (use DB query)
- Add `sent_at` and `message_id` columns to sequence_queue
- Idempotency check on send (refuse if status != pending)
- Migrate send_tracker.json into a computed view of sequence_queue
- Fix Gmail check token deployment (or drop it, use Resend)

### Phase 1 — Resend as primary sender (2 days)
- Verify domain or get team Resend access
- Wire Resend webhook → Supabase function → email_events table
- Replace Gmail-link UX with Resend-send-via-button as default
- Manual Gmail open stays as fallback
- Real delivery + bounce tracking

### Phase 2 — Schema cleanup (2 days)
- Rename `sequence_queue` → `sequence_tasks` with full target schema
- Add `import_batches` table, backfill from existing `source` column
- Add `email_events` table
- Add `do_not_email` flag
- Migrate existing data

### Phase 3 — Postgres-driven pool logic (1 day)
- Create eligible_for_sequence view
- Refactor Enroll page to query view directly
- Drop the pandas filter chain

### Phase 4 — UX rebuild (2-3 days)
- New navigation structure (Overview / Today / Enrollment / Sequences / Events)
- Analytics dashboard with consistent counts
- Contacts filter additions
- Per-contact timeline pulling from email_events

### Phase 5 — Background processing (optional, scales >1k/day)
- Supabase Edge Function or external worker (Render/Fly) pulls due tasks
- User clicks "queue 100 sends" → worker processes asynchronously
- Status polling in UI

---

## 6. What I recommend right now

**Don't keep patching.** Each fix opens a new edge case because the foundation is wrong.

**Pick a starting point:**
- (A) **Pragmatic:** Do Phase 0 + Phase 1 only. ~3 days. You get reliable sending + bounce tracking. Most operational pain solved. UX stays as-is.
- (B) **Full rebuild:** Phases 0-4. ~7-10 days. Enterprise-grade.

I'd start with (A). Phase 0 alone removes most of the "can't trust it" feeling.

Tell me which path and I'll start.
