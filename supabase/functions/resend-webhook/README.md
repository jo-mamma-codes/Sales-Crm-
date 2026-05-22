# Resend Webhook → Supabase Edge Function

This function receives Resend webhook events and writes them to the `email_events` table.

## Deploy (one-time, ~5 min)

### 1. Install Supabase CLI

```bash
brew install supabase/tap/supabase
```

### 2. Login and link your project

```bash
supabase login
cd ~/Documents/yetipay-leads-v2/crm
supabase link --project-ref ndfgaqefiekgtcyzmmuj
```

(Replace `ndfgaqefiekgtcyzmmuj` with your actual project ref — it's in your SUPABASE_URL.)

### 3. Deploy the function

```bash
supabase functions deploy resend-webhook --no-verify-jwt
```

You'll get back a URL like:
```
https://ndfgaqefiekgtcyzmmuj.functions.supabase.co/resend-webhook
```

### 4. Add the webhook in Resend

1. Go to https://resend.com/webhooks
2. Click **Add Webhook**
3. URL: paste the function URL from step 3
4. Events: select all of:
   - email.sent
   - email.delivered
   - email.bounced
   - email.opened
   - email.clicked
   - email.complained
5. Save

### 5. Test

Send a test email via your CRM (Sequences → Today's tasks → 🚀 Send via Resend).
Then check Supabase Studio → Table Editor → email_events. You should see rows
appearing as Resend reports delivery/open/etc.

## What happens

- Every Resend event → POST to your edge function
- Function looks up the lead by `message_id` (matches `sequence_queue.message_id`)
- Falls back to recipient email lookup if no message_id match
- Inserts a row into `email_events` with the event type and payload
- DB trigger auto-flags `leads.do_not_email = true` on bounce or complaint
- CRM Reports page shows live bounce/open/click rates per sequence
- Lead profile timeline shows ✅ delivered, 👁 opened, ⚠️ bounced events

## Cost

Supabase edge functions: 500K invocations/month free. Resend at typical
volumes = ~10 events per sent email = covers 50K sends/mo free. Sufficient.
