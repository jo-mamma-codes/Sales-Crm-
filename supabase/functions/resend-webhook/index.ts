// Resend webhook handler — receives delivery/bounce/open/click/complaint events
// and writes them to the email_events table. Bounce/complaint events auto-flag
// the lead as do_not_email via the trigger defined in SUPABASE_SETUP_PHASE1.sql.
//
// Deploy:
//   1. Install Supabase CLI: brew install supabase/tap/supabase
//   2. From project root: supabase functions deploy resend-webhook --no-verify-jwt
//   3. Get the URL Supabase prints, then add it as a webhook in Resend:
//      https://resend.com/webhooks -> Add Webhook -> paste the URL
//      Select events: email.sent, email.delivered, email.bounced, email.opened,
//      email.clicked, email.complained
//
// Webhook payload reference: https://resend.com/docs/dashboard/webhooks/event-types

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const RESEND_WEBHOOK_SECRET = Deno.env.get("RESEND_WEBHOOK_SECRET") || ""; // optional signing secret

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

// Map Resend event types to our internal event_type
const EVENT_MAP: Record<string, string> = {
  "email.sent": "sent",
  "email.delivered": "delivered",
  "email.delivery_delayed": "delayed",
  "email.bounced": "bounced",
  "email.opened": "opened",
  "email.clicked": "clicked",
  "email.complained": "complained",
};

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  try {
    const body = await req.json();
    const eventType = EVENT_MAP[body.type] || body.type || "unknown";
    const data = body.data || {};
    const messageId = data.email_id || data.id;
    const recipientList = data.to || [];
    const recipientEmail = Array.isArray(recipientList) ? recipientList[0] : recipientList;

    // Find the lead_id + sequence_name + step by looking up message_id in sequence_queue
    let leadId: number | null = null;
    let sequenceName: string | null = null;
    let step: number | null = null;

    if (messageId) {
      const { data: queueRow } = await supabase
        .from("sequence_queue")
        .select("lead_id, sequence_name, step")
        .eq("message_id", messageId)
        .maybeSingle();
      if (queueRow) {
        leadId = queueRow.lead_id;
        sequenceName = queueRow.sequence_name;
        step = queueRow.step;
      }
    }

    // Fall back to email lookup if no message_id match
    if (leadId === null && recipientEmail) {
      const { data: leadRow } = await supabase
        .from("leads")
        .select("id")
        .ilike("email", recipientEmail)
        .limit(1)
        .maybeSingle();
      if (leadRow) leadId = leadRow.id;
    }

    // Insert event ledger row (the bounce trigger will auto-flag the lead)
    await supabase.from("email_events").insert({
      lead_id: leadId,
      sequence_name: sequenceName,
      step: step,
      event_type: eventType,
      source: "resend",
      message_id: messageId,
      recipient_email: recipientEmail,
      payload: body,
    });

    return new Response(JSON.stringify({ ok: true, lead_id: leadId, event: eventType }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error("Webhook error:", err);
    return new Response(JSON.stringify({ error: String(err) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
