"""Yetipay solo CRM — Streamlit dashboard (Supabase backend)."""
import html as html_mod
import os
import json
import urllib.parse
from dotenv import load_dotenv

load_dotenv()
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from supabase import create_client

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
TEMPLATES = ROOT / "templates.json"
LEADS_SRC = ROOT.parent / "data" / "ready_for_outreach.csv"
SEQUENCES = ROOT / "sequences.json"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

sb = get_supabase()

ACTIVITY_COLS = ["timestamp", "lead_id", "business_name", "type", "subject", "content"]
SEQ_QUEUE_COLS = ["lead_id", "business_name", "sequence_name", "step", "due_date", "status"]

TOKENS = {
    "{{firstname}}": ("contact_name", lambda v: (v or "there").split()[0] or "there"),
    "{{lastname}}": ("contact_name", lambda v: v.split()[-1] if v and " " in v else ""),
    "{{fullname}}": ("contact_name", lambda v: v or "there"),
    "{{company}}": ("business_name", lambda v: v or ""),
    "{{email}}": ("email", lambda v: v or ""),
    "{{phone}}": ("phone", lambda v: v or ""),
    "{{category}}": ("category", lambda v: v or "business"),
    "{{region}}": ("region", lambda v: v or ""),
}

DEFAULT_TEMPLATES = {
    "Cold intro": {
        "subject": "Quick idea for {{company}}",
        "body": (
            "Hi {{firstname}},\n\n"
            "I'm Joseph from yetipay. We give independent hospitality spots a free card terminal "
            "and lower rates than the big providers — no contracts, no setup fees.\n\n"
            "Worth a 10-minute chat to see if it'd save {{company}} money on card fees?\n\n"
            "Cheers,\nJoseph\nyetipay"
        ),
    },
    "Follow-up 1": {
        "subject": "Re: {{company}} card fees",
        "body": (
            "Hi {{firstname}},\n\n"
            "Bumping this in case it got buried. Happy to send a 30-second cost comparison "
            "against your current provider — just need a recent statement.\n\n"
            "Cheers,\nJoseph"
        ),
    },
    "Follow-up 2": {
        "subject": "Last one from me — {{company}}",
        "body": (
            "Hi {{firstname}},\n\n"
            "Totally understand if the timing isn't right. "
            "I'll leave this with you — if {{company}} ever wants to cut card fees, "
            "I'm a quick reply away.\n\n"
            "Cheers,\nJoseph"
        ),
    },
    "Demo booking": {
        "subject": "yetipay demo — {{company}}",
        "body": (
            "Hi {{firstname}},\n\n"
            "Confirming our chat. I'll walk you through the terminal + rates and we can have you "
            "live this week if it stacks up.\n\nCheers,\nJoseph"
        ),
    },
    "Referral ask": {
        "subject": "Quick favour",
        "body": (
            "Hi {{firstname}},\n\n"
            "Hope all good with the terminal. Quick ask — know any other {{category}} owners "
            "who'd want the same setup? Happy to sort a referral bonus if they sign.\n\n"
            "Cheers,\nJoseph"
        ),
    },
}


def load_templates():
    if TEMPLATES.exists():
        return json.loads(TEMPLATES.read_text())
    TEMPLATES.write_text(json.dumps(DEFAULT_TEMPLATES, indent=2))
    return DEFAULT_TEMPLATES


def save_templates(t):
    TEMPLATES.write_text(json.dumps(t, indent=2))


def load_activity():
    r = sb.table("activity").select("*").execute()
    if r.data:
        df = pd.DataFrame(r.data).fillna("")
        for c in ACTIVITY_COLS:
            if c not in df.columns:
                df[c] = ""
        return df
    return pd.DataFrame(columns=ACTIVITY_COLS)


def log_activity(lead_id, business_name, type_, subject, content):
    row = {
        "lead_id": int(lead_id),
        "business_name": business_name,
        "type": type_,
        "subject": subject,
        "content": content,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    sb.table("activity").insert(row).execute()


def load_tasks(lead_id=None):
    q = sb.table("tasks").select("*")
    if lead_id:
        q = q.eq("lead_id", int(lead_id))
    try:
        r = q.order("due_date").execute()
        return r.data or []
    except Exception:
        return []


def create_task(lead_id, title, due_date=None, assigned_to=None, notes=None):
    row = {"lead_id": int(lead_id), "title": title, "status": "pending"}
    if due_date:
        row["due_date"] = due_date
    if assigned_to:
        row["assigned_to"] = assigned_to
    if notes:
        row["notes"] = notes
    try:
        sb.table("tasks").insert(row).execute()
    except Exception:
        pass


def update_task(task_id, updates):
    try:
        sb.table("tasks").update(updates).eq("id", int(task_id)).execute()
    except Exception:
        pass


# ─── Lead Scoring ────────────────────────────────────────────────────────────
def score_lead(lead, activity_df):
    """Score a lead 0-100 based on engagement. Returns (score, label, color)."""
    score = 0
    lid = str(lead.get("id", ""))

    # Stage points
    stage_pts = {"New": 5, "Contacted": 20, "Demo Booked": 50, "Proposal": 70, "Won": 100, "Lost": 0}
    score += stage_pts.get(lead.get("stage", ""), 0)

    # Activity count
    if not activity_df.empty:
        lead_acts = activity_df[activity_df["lead_id"] == lid]
        n_acts = len(lead_acts)
        score += min(n_acts * 5, 20)  # max 20 pts from activity

        # Email replies (check notes for [REPLIED])
        if "[REPLIED]" in str(lead.get("notes", "")):
            score += 15

    # Deal value bonus
    dv = float(lead.get("deal_value", 0) or 0)
    if dv > 0:
        score += 10

    # Recency penalty
    lt = lead.get("last_touch", "")
    if lt:
        try:
            days_since = (date.today() - date.fromisoformat(str(lt)[:10])).days
            if days_since <= 3:
                score += 10
            elif days_since <= 7:
                score += 5
            elif days_since > 14:
                score -= 10
            if days_since > 30:
                score -= 15
        except Exception:
            pass

    score = max(0, min(score, 100))

    if score >= 60:
        return score, "🔥 Hot", "#ef4444"
    elif score >= 30:
        return score, "🟡 Warm", "#f59e0b"
    else:
        return score, "🧊 Cold", "#94a3b8"


def days_since_touch(lead):
    """Return days since last touch, or None."""
    lt = lead.get("last_touch", "")
    if lt:
        try:
            return (date.today() - date.fromisoformat(str(lt)[:10])).days
        except Exception:
            pass
    return None


def detect_duplicates(df):
    """Find duplicate leads by email or business name. Returns dict of {lead_id: [dup_ids]}."""
    dupes = {}
    # Email duplicates
    email_groups = df[df["email"].str.contains("@", na=False)].groupby(df["email"].str.lower())
    for email, group in email_groups:
        if len(group) > 1:
            ids = group["id"].tolist()
            for lid in ids:
                dupes.setdefault(lid, set()).update(ids)
                dupes[lid].discard(lid)

    # Business name duplicates (exact match, case-insensitive)
    biz_groups = df[df["business_name"] != ""].groupby(df["business_name"].str.lower())
    for biz, group in biz_groups:
        if len(group) > 1:
            ids = group["id"].tolist()
            for lid in ids:
                dupes.setdefault(lid, set()).update(ids)
                dupes[lid].discard(lid)

    return {k: list(v) for k, v in dupes.items() if v}


def analyze_email_ai(email_text, business_name=""):
    """Use Claude to analyze an email reply for sentiment, intent, and next action."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": f"""Analyze this sales email reply from {business_name}. Return JSON only:
{{"sentiment": "positive/neutral/negative",
"intent": "interested/maybe/not_interested/asking_questions/objection",
"urgency": "high/medium/low",
"summary": "one sentence",
"suggested_action": "what the sales rep should do next"}}

Email:
{email_text[:2000]}"""}],
        )
        import re
        text = resp.content[0].text
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass
    return None


OPT_OUT_LINE = "\n\nTo opt out, reply 'unsubscribe'."


def render_template(tmpl, lead):
    """Replace {{token}} placeholders with CRM record values."""
    subj, body = tmpl["subject"], tmpl["body"]
    for token, (field, transform) in TOKENS.items():
        val = transform(lead.get(field, ""))
        subj = subj.replace(token, val)
        body = body.replace(token, val)
    if OPT_OUT_LINE.strip() not in body:
        body += OPT_OUT_LINE
    return subj, body


def mailto_link(to, subject, body):
    q = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{to}?{q}"


SENDERS = [
    {"email": "joseph.allison@yetipay.me", "name": "Joseph Allison", "daily_cap": 100, "chrome_profile": "Profile 5"},
    {"email": "insidesales@yetipay.me", "name": "Yetipay Sales", "daily_cap": 100, "chrome_profile": "Profile 4"},
    {"email": "dominic.ritchie@yetipay.me", "name": "Dominic Ritchie", "daily_cap": 100, "chrome_profile": "Profile 1"},
    {"email": "ashley@yetipay.me", "name": "Ashley", "daily_cap": 100, "chrome_profile": "Profile 5"},  # no own profile yet
]
SENDER_PROFILE = {s["email"]: s["chrome_profile"] for s in SENDERS}
SEND_TRACKER = ROOT / "send_tracker.json"


def load_send_counts():
    today = date.today().isoformat()
    if SEND_TRACKER.exists():
        data = json.loads(SEND_TRACKER.read_text())
        if data.get("date") == today:
            return data
    return {"date": today, "counts": {s["email"]: 0 for s in SENDERS}}


def save_send_counts(data):
    SEND_TRACKER.write_text(json.dumps(data, indent=2))


def pick_sender(tracker):
    for s in SENDERS:
        used = tracker["counts"].get(s["email"], 0)
        if used < s["daily_cap"]:
            return s
    return None


def gmail_link(to, subject, body, sender_email=None):
    su = urllib.parse.quote(subject, safe="")
    bo = urllib.parse.quote(body, safe="")
    url = f"https://mail.google.com/mail/u/0/?view=cm&fs=1&to={to}&su={su}&body={bo}"
    return url

DEFAULT_SEQUENCES = {
    "Cold outreach": {
        "steps": [
            {"day": 0, "template": "Cold intro", "channel": "email"},
            {"day": 2, "template": None, "channel": "call"},
            {"day": 4, "template": "Follow-up 1", "channel": "email"},
            {"day": 7, "template": None, "channel": "call"},
            {"day": 10, "template": "Follow-up 2", "channel": "email"},
        ]
    },
    "Referral": {
        "steps": [
            {"day": 0, "template": "Referral ask", "channel": "email"},
            {"day": 5, "template": None, "channel": "call"},
        ]
    },
}


def load_sequences():
    if SEQUENCES.exists():
        return json.loads(SEQUENCES.read_text())
    SEQUENCES.write_text(json.dumps(DEFAULT_SEQUENCES, indent=2))
    return DEFAULT_SEQUENCES


def save_sequences(s):
    SEQUENCES.write_text(json.dumps(s, indent=2))


def load_seq_queue():
    r = sb.table("sequence_queue").select("*").execute()
    if r.data:
        df = pd.DataFrame(r.data)
        for c in SEQ_QUEUE_COLS:
            if c not in df.columns:
                df[c] = ""
        df = df.fillna("")
        for c in df.columns:
            df[c] = df[c].astype(str)
        return df
    return pd.DataFrame(columns=SEQ_QUEUE_COLS)


def save_seq_queue(q):
    sb.table("sequence_queue").delete().neq("lead_id", -999).execute()
    if not q.empty:
        rows = q.to_dict("records")
        for r in rows:
            if r.get("lead_id"):
                r["lead_id"] = int(r["lead_id"])
            if r.get("step"):
                r["step"] = int(r["step"])
            for k, v in r.items():
                if v == "":
                    r[k] = None
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            sb.table("sequence_queue").insert(rows[i:i+batch_size]).execute()


def enroll_lead(lead_id, business_name, seq_name, sequences):
    seq = sequences[seq_name]
    sb.table("sequence_queue").delete().eq("lead_id", int(lead_id)).eq("sequence_name", seq_name).execute()
    rows = []
    for i, step in enumerate(seq["steps"]):
        due = (date.today() + pd.Timedelta(days=step["day"])).isoformat()
        rows.append({
            "lead_id": int(lead_id),
            "business_name": business_name,
            "sequence_name": seq_name,
            "step": i,
            "due_date": due,
            "status": "pending",
        })
    sb.table("sequence_queue").insert(rows).execute()
    return len(rows)


GMAIL_CACHE = ROOT / "gmail_cache"


def cache_gmail_threads(email_addr, threads_data):
    """Cache fetched Gmail threads for a contact."""
    GMAIL_CACHE.mkdir(exist_ok=True)
    safe = email_addr.replace("@", "_at_").replace(".", "_")
    cache_file = GMAIL_CACHE / f"{safe}.json"
    cache_file.write_text(json.dumps({
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "email": email_addr,
        "threads": threads_data,
    }, indent=2))


def load_gmail_cache(email_addr):
    GMAIL_CACHE.mkdir(exist_ok=True)
    safe = email_addr.replace("@", "_at_").replace(".", "_")
    cache_file = GMAIL_CACHE / f"{safe}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


STAGES = ["New", "Contacted", "Demo Booked", "Proposal", "Won", "Lost"]
REFERRAL_STAGES = ["New Referral", "Contacted", "Referral Signed Up", "Reward Sent"]
PIPELINES = {
    "Sales": STAGES,
    "Referral": REFERRAL_STAGES,
}
DEFAULT_TARGET = 20

COLUMNS = [
    "id", "business_name", "contact_name", "phone", "email",
    "region", "category", "stage", "last_touch", "next_action_date",
    "next_action", "notes", "source", "created", "pipeline", "deal_value",
]


def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {"target": DEFAULT_TARGET, "month": date.today().strftime("%Y-%m")}


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2))


def load_crm():
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        r = sb.table("leads").select("*").range(offset, offset + page_size - 1).execute()
        if not r.data:
            break
        all_data.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
    if all_data:
        df = pd.DataFrame(all_data).fillna("")
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = ""
        for c in df.columns:
            if c != "deal_value":
                df[c] = df[c].astype(str)
        df["deal_value"] = pd.to_numeric(df.get("deal_value", 0), errors="coerce").fillna(0)
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def save_crm(df):
    rows = df.to_dict("records")
    for r in rows:
        r["id"] = int(r["id"])
        for k, v in r.items():
            if v == "":
                r[k] = None
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        sb.table("leads").upsert(rows[i:i+batch_size]).execute()


def save_lead(lead_id, updates):
    """Save specific fields for one lead (avoids full-table upsert)."""
    for k, v in updates.items():
        if v == "":
            updates[k] = None
    sb.table("leads").update(updates).eq("id", int(lead_id)).execute()


def next_id(df):
    if df.empty:
        return "1"
    return str(df["id"].astype(int).max() + 1)


def import_leads(df, src_path, region_filter=None, limit=None, pipeline="Sales", default_stage="New"):
    if not src_path.exists():
        return df, 0
    src = pd.read_csv(src_path, dtype=str).fillna("")
    if region_filter and region_filter != "All":
        src = src[src["region"].str.contains(region_filter, case=False, na=False)]
    # only personal emails — drop generic inboxes
    generic = ("info@", "hello@", "contact@", "enquiries@", "admin@", "sales@", "office@", "reception@", "bookings@")
    if "email" in src.columns:
        src = src[~src["email"].str.lower().str.startswith(generic)]
        src = src[src["email"].str.contains("@", na=False)]
    # block bounced + GDPR never-contact emails
    blocked_file = ROOT / "bounced_emails.json"
    if blocked_file.exists():
        blocked = json.loads(blocked_file.read_text())
        blocked_emails = set(e.lower() for e in blocked.get("bounced", []) + blocked.get("never_contact", []))
        if "email" in src.columns:
            src = src[~src["email"].str.lower().isin(blocked_emails)]
    existing = set(df["business_name"].str.lower()) if not df.empty else set()
    src = src[~src["business_name"].str.lower().isin(existing)]
    if limit:
        src = src.head(limit)
    rows = []
    nid = int(next_id(df)) if not df.empty else 1
    for _, r in src.iterrows():
        contact = f"{r.get('first_name','')} {r.get('last_name','')}".strip()
        rows.append({
            "id": nid,
            "business_name": r.get("business_name", "") or None,
            "contact_name": contact or None,
            "phone": r.get("phone", "") or None,
            "email": r.get("email", "") or None,
            "region": r.get("region", "") or None,
            "category": r.get("category", "") or None,
            "stage": default_stage,
            "last_touch": None,
            "next_action_date": None,
            "next_action": None,
            "notes": None,
            "source": r.get("source", "ready_for_outreach") or None,
            "created": date.today().isoformat(),
            "pipeline": pipeline,
        })
        nid += 1
    if rows:
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            sb.table("leads").insert(rows[i:i+batch_size]).execute()
        df = load_crm()
    return df, len(rows)


# ─── CSS ─────────────────────────────────────────────────────────────────────
HUBSPOT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
/* ── Base ── */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
[data-testid="stToolbar"], [data-testid="stDecoration"] {
    background-color: #f8f9fb !important;
    color: #1a1a2e !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}
section[data-testid="stSidebar"] { background: #1a1a2e !important; }
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] div { color: #c4c4d4 !important; }
label, .stMarkdown, .stCaption, p, span, div { color: #1a1a2e !important; }
input, textarea, select, [data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background-color: #fff !important; color: #1a1a2e !important;
    border: 1px solid #e2e4e9 !important; border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
}
input:focus, textarea:focus { border-color: #7c3aed !important; box-shadow: 0 0 0 3px rgba(124,58,237,0.1) !important; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { background: #fff !important; border-radius: 12px !important; }

/* ── Tabs (hidden — using sidebar nav) ── */
.stTabs { display: none !important; }

/* ── Buttons ── */
.stButton > button {
    border-radius: 8px !important; font-weight: 500 !important;
    font-size: 13px !important; padding: 8px 16px !important;
    border: 1px solid #e2e4e9 !important; background: #fff !important;
    color: #1a1a2e !important; transition: all 0.15s !important;
    font-family: 'Inter', sans-serif !important;
}
.stButton > button:hover { border-color: #7c3aed !important; color: #7c3aed !important; background: #faf5ff !important; }
.stButton > button[kind="primary"], button[data-testid="stFormSubmitButton"] {
    background: #7c3aed !important; color: #fff !important;
    border-color: #7c3aed !important;
}
.stButton > button[kind="primary"]:hover { background: #6d28d9 !important; }
.stSelectbox > div > div { background: #fff !important; color: #1a1a2e !important; border-radius: 8px !important; }

/* ── Header bar ── */
.crm-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #2d1b69 100%);
    border-radius: 16px; padding: 28px 32px; margin-bottom: 24px;
    display: flex; align-items: center; justify-content: space-between;
}
.crm-header .logo { font-size: 36px; font-weight: 700; color: #fff !important; letter-spacing: -0.5px; }
.crm-header .logo span { color: #a78bfa !important; }
.crm-header .subtitle { font-size: 14px; color: #a5a5c0 !important; margin-top: 2px; }

/* ── KPI bar ── */
.kpi-bar { display: flex; gap: 16px; margin-bottom: 20px; }
.kpi-card {
    flex: 1; background: #fff; border: 1px solid #e2e4e9; border-radius: 12px;
    padding: 20px 24px; text-align: center; transition: all 0.2s;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.kpi-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); transform: translateY(-1px); }
.kpi-card .num { font-size: 32px; font-weight: 700; color: #1a1a2e; line-height: 1.1; }
.kpi-card .label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; font-weight: 600; }
.kpi-card .sub { font-size: 11px; color: #a78bfa; font-weight: 500; margin-top: 2px; }

/* ── Progress bar ── */
.target-bar { background: #e2e4e9; border-radius: 20px; height: 8px; margin: 8px 0 24px; overflow: hidden; }
.target-fill { background: linear-gradient(90deg, #7c3aed, #a78bfa); height: 8px; border-radius: 20px; transition: width 0.5s; }

/* ── Kanban ── */
.kanban-header {
    padding: 14px 16px; font-weight: 600; font-size: 12px; color: #1a1a2e;
    border-bottom: 3px solid; text-transform: uppercase; letter-spacing: 0.8px;
    background: #fff; border-radius: 12px 12px 0 0;
}
.kanban-header .count { font-weight: 500; color: #94a3b8; margin-left: 8px; font-size: 12px; }
.deal-card {
    background: #fff; border: 1px solid #e2e4e9; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 10px;
    transition: all 0.15s; border-left: 3px solid transparent;
}
.deal-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); transform: translateY(-1px); border-left-color: #7c3aed; }
.deal-card .biz { font-weight: 600; font-size: 13px; color: #1a1a2e; margin-bottom: 3px; }
.deal-card .contact { font-size: 12px; color: #64748b; font-weight: 400; }
.deal-card .meta { font-size: 11px; color: #94a3b8; margin-top: 8px; }
.deal-card .category-tag {
    display: inline-block; background: #f1f0ff; color: #7c3aed;
    font-size: 10px; padding: 3px 10px; border-radius: 12px; margin-top: 6px; font-weight: 500;
}
.kanban-footer {
    padding: 10px 16px; font-size: 11px; color: #94a3b8; font-weight: 500;
    border-top: 1px solid #e2e4e9; background: #fafafa; border-radius: 0 0 12px 12px;
}

/* ── Stage colors ── */
.stage-new { border-color: #10b981; background: #f0fdf9; }
.stage-contacted { border-color: #3b82f6; background: #eff6ff; }
.stage-demo { border-color: #7c3aed; background: #f5f3ff; }
.stage-proposal { border-color: #f59e0b; background: #fffbeb; }
.stage-won { border-color: #10b981; background: #ecfdf5; }
.stage-lost { border-color: #ef4444; background: #fef2f2; }

/* ── Contact table ── */
.contact-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.contact-table th {
    text-align: left; padding: 12px 16px; background: #f8f9fb;
    color: #94a3b8; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.8px;
    border-bottom: 2px solid #e2e4e9;
}
.contact-table td { padding: 12px 16px; border-bottom: 1px solid #f1f5f9; color: #1a1a2e; }
.contact-table tr:hover td { background: #faf5ff; }
.contact-name { font-weight: 600; color: #7c3aed; }
.contact-email { color: #64748b; }
.contact-stage { display: inline-block; padding: 3px 12px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.stage-pill-new { background: #ecfdf5; color: #10b981; }
.stage-pill-contacted { background: #eff6ff; color: #3b82f6; }
.stage-pill-demo { background: #f5f3ff; color: #7c3aed; }
.stage-pill-proposal { background: #fffbeb; color: #d97706; }
.stage-pill-won { background: #ecfdf5; color: #059669; }
.stage-pill-lost { background: #fef2f2; color: #ef4444; }

/* ── Template editor ── */
.tmpl-editor { background: #fff; border: 1px solid #e2e4e9; border-radius: 12px; overflow: hidden; }
.tmpl-header { padding: 18px 24px; border-bottom: 1px solid #e2e4e9; }
.tmpl-header h3 { margin: 0; font-size: 16px; color: #1a1a2e; font-weight: 600; }
.tmpl-body { padding: 24px; }
.tmpl-toolbar {
    padding: 10px 24px; border-top: 1px solid #e2e4e9;
    display: flex; gap: 10px; align-items: center; background: #f8f9fb;
}
.token-btn {
    display: inline-block; background: #7c3aed; color: #fff;
    padding: 5px 14px; border-radius: 6px; font-size: 12px; font-weight: 500;
    cursor: pointer; border: none;
}
.tmpl-preview { background: #f8f9fb; border: 1px solid #e2e4e9; border-radius: 12px; padding: 24px; margin-top: 12px; }
.tmpl-preview .preview-to { color: #94a3b8; font-size: 12px; margin-bottom: 4px; }
.tmpl-preview .preview-subject { font-weight: 600; font-size: 15px; color: #1a1a2e; margin-bottom: 12px; }
.tmpl-preview .preview-body { font-size: 13px; color: #64748b; white-space: pre-wrap; line-height: 1.7; }

/* ── Profile card ── */
.profile-card {
    background: #fff; border: 1px solid #e2e4e9; border-radius: 16px;
    padding: 28px 24px; text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.profile-avatar {
    width: 80px; height: 80px; border-radius: 16px; background: linear-gradient(135deg, #7c3aed, #a78bfa);
    color: #fff; font-size: 28px; font-weight: 700; line-height: 80px;
    margin: 0 auto 16px; text-transform: uppercase; letter-spacing: 1px;
}
.profile-name { font-size: 20px; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }
.profile-role { font-size: 13px; color: #64748b; margin-bottom: 4px; font-weight: 500; }
.profile-email { font-size: 13px; color: #94a3b8; margin-bottom: 16px; }
.profile-actions { display: flex; justify-content: center; gap: 16px; margin: 16px 0; flex-wrap: wrap; }
.profile-action-item { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.profile-action-btn {
    width: 42px; height: 42px; border-radius: 12px; border: 1px solid #e2e4e9;
    background: #fff; color: #64748b; font-size: 16px; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    text-decoration: none; transition: all 0.15s;
}
.profile-action-btn:hover { background: #faf5ff; border-color: #7c3aed; color: #7c3aed; }
.profile-action-label { font-size: 10px; color: #94a3b8; font-weight: 500; }
.profile-section { text-align: left; border-top: 1px solid #f1f5f9; padding-top: 16px; margin-top: 16px; }
.profile-section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.profile-section-title { font-size: 12px; font-weight: 600; color: #1a1a2e; text-transform: uppercase; letter-spacing: 0.5px; }
.profile-section-action { font-size: 11px; color: #7c3aed; cursor: pointer; font-weight: 500; }
.profile-field { margin-bottom: 12px; }
.profile-field-label { font-size: 11px; color: #94a3b8; margin-bottom: 2px; font-weight: 500; }
.profile-field-value { font-size: 13px; color: #1a1a2e; font-weight: 500; }

/* ── Timeline ── */
.timeline { position: relative; padding-left: 28px; }
.timeline::before { content: ''; position: absolute; left: 9px; top: 0; bottom: 0; width: 2px; background: #e2e4e9; }
.timeline-item { position: relative; margin-bottom: 20px; }
.timeline-dot {
    position: absolute; left: -23px; top: 4px; width: 14px; height: 14px;
    border-radius: 50%; border: 2.5px solid #fff; box-shadow: 0 0 0 1px #e2e4e9;
}
.timeline-dot-email { background: #3b82f6; }
.timeline-dot-call { background: #10b981; }
.timeline-dot-note { background: #7c3aed; }
.timeline-dot-gmail { background: #ef4444; }
.timeline-date { font-size: 11px; color: #94a3b8; margin-bottom: 3px; font-weight: 500; }
.timeline-title { font-size: 13px; font-weight: 600; color: #1a1a2e; margin-bottom: 4px; }
.timeline-body {
    font-size: 12px; color: #64748b; background: #f8f9fb;
    border-radius: 10px; padding: 12px 16px; white-space: pre-wrap;
    max-height: 200px; overflow-y: auto; line-height: 1.6;
    border: 1px solid #e2e4e9;
}
.timeline-month {
    font-size: 13px; font-weight: 700; color: #1a1a2e; margin: 24px 0 12px;
    padding-bottom: 6px; border-bottom: 2px solid #e2e4e9;
}

/* ── Search bar ── */
.search-wrap {
    background: #fff; border-radius: 12px; border: 1px solid #e2e4e9;
    padding: 4px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}

/* ── Card containers ── */
[data-testid="stVerticalBlock"] > div[data-testid="stContainer"] {
    border: 1px solid #e2e4e9 !important; border-radius: 10px !important;
    padding: 10px 12px 6px !important; margin-bottom: 8px !important;
    background: #fff !important; transition: all 0.15s;
}
[data-testid="stVerticalBlock"] > div[data-testid="stContainer"]:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.06); border-color: #c4b5fd !important;
}
.deal-card-inner .biz { font-weight: 600; font-size: 13px; color: #1a1a2e; margin-bottom: 3px; }
.deal-card-inner .contact { font-size: 12px; color: #64748b; }
.deal-card-inner .meta { font-size: 11px; color: #94a3b8; margin-top: 6px; }
.deal-card-inner .category-tag {
    display: inline-block; background: #f1f0ff; color: #7c3aed;
    font-size: 10px; padding: 3px 10px; border-radius: 12px; margin-top: 6px; font-weight: 500;
}
/* Compact buttons inside cards */
[data-testid="stContainer"] .stButton > button {
    padding: 4px 12px !important; font-size: 11px !important;
    height: auto !important; min-height: 0 !important;
}
[data-testid="stContainer"] .stSelectbox { margin-top: -8px; }
[data-testid="stContainer"] .stSelectbox > div > div {
    min-height: 0 !important; padding: 2px 8px !important; font-size: 11px !important;
}

/* ── Misc ── */
.stDivider { border-color: #e2e4e9 !important; }
hr { border-color: #e2e4e9 !important; }
[data-testid="stForm"] { background: #fff !important; border: 1px solid #e2e4e9 !important; border-radius: 12px !important; padding: 24px !important; }
.stMultiSelect > div { border-radius: 8px !important; }

/* ── HIDE STREAMLIT CHROME ── */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stToolbar"] { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
div[data-testid="stStatusWidget"] { display: none !important; }

/* ── SMOOTH TRANSITIONS EVERYWHERE ── */
* { transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, color 0.1s ease, opacity 0.15s ease; }

/* ── REDUCE TOP PADDING (no more wasted space) ── */
.stApp > header { display: none !important; }
[data-testid="stAppViewContainer"] > div:first-child { padding-top: 0 !important; }
.block-container { padding-top: 1rem !important; padding-bottom: 0 !important; max-width: 100% !important; }

/* ── SIDEBAR NAVIGATION ── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f0f23 0%, #1a1a2e 100%) !important;
    width: 240px !important;
    min-width: 240px !important;
    border-right: 1px solid #2d2d4a !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 16px !important; }
section[data-testid="stSidebar"] .stRadio > label { display: none !important; }
section[data-testid="stSidebar"] .stRadio > div {
    flex-direction: column !important; gap: 2px !important;
}
section[data-testid="stSidebar"] .stRadio > div > label {
    padding: 10px 16px !important; border-radius: 8px !important;
    font-size: 13px !important; font-weight: 500 !important;
    cursor: pointer !important; margin: 0 8px !important;
    color: #a5a5c0 !important; transition: all 0.15s ease !important;
}
section[data-testid="stSidebar"] .stRadio > div > label:hover {
    background: rgba(124, 58, 237, 0.1) !important; color: #fff !important;
}
section[data-testid="stSidebar"] .stRadio > div > label[data-checked="true"],
section[data-testid="stSidebar"] .stRadio > div > label:has(input:checked) {
    background: rgba(124, 58, 237, 0.2) !important; color: #fff !important;
    border-left: 3px solid #7c3aed !important;
}

/* ── DENSE LAYOUT ── */
.stTabs [data-baseweb="tab-list"] { display: none !important; }
.element-container { margin-bottom: 0.25rem !important; }
[data-testid="stVerticalBlock"] > div { gap: 0.5rem !important; }

/* ── STICKY HEADER ── */
.crm-header {
    position: sticky; top: 0; z-index: 999;
    background: linear-gradient(135deg, #0f0f23 0%, #2d1b69 100%);
    border-radius: 0; padding: 16px 24px; margin: -1rem -1rem 16px -1rem;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid #2d2d4a;
    box-shadow: 0 2px 12px rgba(0,0,0,0.15);
}
.crm-header .logo { font-size: 24px; font-weight: 700; color: #fff !important; letter-spacing: -0.5px; }
.crm-header .logo span { color: #a78bfa !important; }
.crm-header .subtitle { font-size: 12px; color: #a5a5c0 !important; margin-top: 0; }

/* ── KPI CARDS - more compact ── */
.kpi-bar { display: flex; gap: 12px; margin-bottom: 16px; }
.kpi-card {
    flex: 1; background: #fff; border: 1px solid #e2e4e9; border-radius: 10px;
    padding: 14px 16px; text-align: center;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}
.kpi-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); transform: translateY(-1px); }
.kpi-card .num { font-size: 24px; font-weight: 700; line-height: 1.1; }
.kpi-card .label { font-size: 10px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.8px; margin-top: 2px; font-weight: 600; }

/* ── KANBAN - tighter cards ── */
.kanban-header {
    padding: 10px 12px; font-weight: 600; font-size: 11px;
    border-bottom: 3px solid; text-transform: uppercase; letter-spacing: 0.8px;
    background: #fff; border-radius: 10px 10px 0 0;
    position: sticky; top: 70px; z-index: 10;
}
.deal-card-inner { padding: 2px 0; }
.deal-card-inner .biz { font-weight: 600; font-size: 12px; color: #1a1a2e; margin-bottom: 2px; }
.deal-card-inner .contact { font-size: 11px; color: #64748b; }
.deal-card-inner .meta { font-size: 10px; color: #94a3b8; margin-top: 4px; }

/* ── CONTAINERS - tighter padding ── */
[data-testid="stVerticalBlock"] > div[data-testid="stContainer"] {
    border: 1px solid #e8e8ee !important; border-radius: 8px !important;
    padding: 8px 10px 4px !important; margin-bottom: 6px !important;
    background: #fff !important;
}
[data-testid="stVerticalBlock"] > div[data-testid="stContainer"]:hover {
    box-shadow: 0 2px 8px rgba(124,58,237,0.08); border-color: #c4b5fd !important;
}

/* ── BUTTONS - more compact ── */
.stButton > button {
    border-radius: 6px !important; font-weight: 500 !important;
    font-size: 12px !important; padding: 6px 14px !important;
    border: 1px solid #e2e4e9 !important; background: #fff !important;
    color: #1a1a2e !important; font-family: 'Inter', sans-serif !important;
    cursor: pointer !important;
}
.stButton > button:hover { border-color: #7c3aed !important; color: #7c3aed !important; background: #faf5ff !important; }
.stButton > button:active { transform: scale(0.97); }
.stButton > button[kind="primary"], button[data-testid="stFormSubmitButton"] {
    background: #7c3aed !important; color: #fff !important; border-color: #7c3aed !important;
}
.stButton > button[kind="primary"]:hover { background: #6d28d9 !important; }
[data-testid="stContainer"] .stButton > button {
    padding: 3px 10px !important; font-size: 10px !important;
    height: auto !important; min-height: 0 !important;
}

/* ── INPUTS - compact ── */
input, textarea, select, [data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background-color: #fff !important; color: #1a1a2e !important;
    border: 1px solid #e2e4e9 !important; border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important; font-size: 13px !important;
}
input:focus, textarea:focus { border-color: #7c3aed !important; box-shadow: 0 0 0 2px rgba(124,58,237,0.08) !important; }

/* ── CONTACT TABLE - hover row highlight ── */
.contact-table tr { transition: background 0.1s; }
.contact-table tr:hover td { background: #faf5ff; cursor: pointer; }
.contact-table td { padding: 10px 14px; font-size: 12px; }
.contact-table th { padding: 10px 14px; font-size: 10px; }

/* ── PROFILE CARD - refined ── */
.profile-card { background: #fff; border: 1px solid #e2e4e9; border-radius: 12px; padding: 24px 20px; text-align: center; }
.profile-avatar {
    width: 64px; height: 64px; border-radius: 14px;
    background: linear-gradient(135deg, #7c3aed, #a78bfa);
    color: #fff; font-size: 22px; font-weight: 700; line-height: 64px;
    margin: 0 auto 12px;
}
.profile-name { font-size: 17px; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }

/* ── TIMELINE - tighter ── */
.timeline { padding-left: 24px; }
.timeline-item { margin-bottom: 14px; }
.timeline-dot { left: -20px; top: 3px; width: 12px; height: 12px; }
.timeline-body { font-size: 11px; padding: 10px 14px; max-height: 150px; }

/* ── TOAST-STYLE NOTIFICATIONS ── */
[data-testid="stAlert"] {
    border-radius: 8px !important; font-size: 13px !important;
    padding: 10px 16px !important; border-left: 4px solid !important;
    animation: slideIn 0.2s ease-out;
}
@keyframes slideIn {
    from { opacity: 0; transform: translateY(-8px); }
    to { opacity: 1; transform: translateY(0); }
}

/* ── SKELETON LOADING FEEL ── */
[data-testid="stSpinner"] > div {
    background: #f8f9fb; border-radius: 8px; padding: 20px;
    animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #d4d4d8; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #a1a1aa; }

/* ── DATA EDITOR / TABLES ── */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    background: #fff !important; border-radius: 8px !important;
    border: 1px solid #e2e4e9 !important;
}

/* ── EXPANDER ── */
[data-testid="stExpander"] {
    background: #fff !important; border: 1px solid #e2e4e9 !important;
    border-radius: 8px !important;
}
[data-testid="stExpander"] summary { font-size: 13px !important; font-weight: 500 !important; }

/* ── SELECTBOX ── */
.stSelectbox > div > div { background: #fff !important; border-radius: 6px !important; font-size: 13px !important; }
[data-testid="stContainer"] .stSelectbox { margin-top: -4px; }
[data-testid="stContainer"] .stSelectbox > div > div {
    min-height: 0 !important; padding: 2px 8px !important; font-size: 10px !important;
}

/* ── MULTISELECT ── */
.stMultiSelect > div { border-radius: 6px !important; }
</style>
"""

# ─── UI ──────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Yetipay CRM", layout="wide", page_icon="💳", initial_sidebar_state="expanded")
st.markdown(HUBSPOT_CSS, unsafe_allow_html=True)
# Header is in sidebar now — just a thin top bar for context
st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0 12px;border-bottom:1px solid #e2e4e9;margin-bottom:16px;">
    <div style="font-size:16px;font-weight:700;color:#1a1a2e;">{nav_choice}</div>
    <div style="font-size:12px;color:#94a3b8;">{date.today().strftime('%A, %d %B %Y')}</div>
</div>""", unsafe_allow_html=True)
cfg = load_config()
df = load_crm()

# Pre-compute lead scores and duplicates (cached per session)
if "lead_scores" not in st.session_state or st.session_state.get("_score_stale", True):
    _act_for_scoring = load_activity()
    _scores = {}
    for _, _row in df.iterrows():
        _scores[str(_row["id"])] = score_lead(_row.to_dict(), _act_for_scoring)
    st.session_state["lead_scores"] = _scores
    st.session_state["_score_stale"] = False

if "duplicates" not in st.session_state or st.session_state.get("_dupes_stale", True):
    st.session_state["duplicates"] = detect_duplicates(df)
    st.session_state["_dupes_stale"] = False

LEAD_SCORES = st.session_state["lead_scores"]
DUPLICATES = st.session_state["duplicates"]

# ─── Sidebar Navigation ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""<div style="padding:8px 16px 20px;border-bottom:1px solid #2d2d4a;margin-bottom:12px;">
        <div style="font-size:20px;font-weight:700;color:#fff;letter-spacing:-0.5px;">JOE'S <span style="color:#a78bfa;">CRM</span></div>
        <div style="font-size:11px;color:#64648a;margin-top:2px;">{date.today().strftime('%A, %d %b %Y')}</div>
    </div>""", unsafe_allow_html=True)

NAV_ITEMS = ["📊 Pipeline", "👥 Contacts", "📅 Today", "✉️ Outreach", "🔄 Follow Up",
             "✅ Tasks", "📈 Reports", "➕ Add Lead", "📥 Import", "📝 Templates", "⚙️ Settings"]
with st.sidebar:
    nav_choice = st.radio("Navigation", NAV_ITEMS, key="nav", label_visibility="collapsed")

# Quick stats in sidebar
with st.sidebar:
    st.markdown(f"""<div style="border-top:1px solid #2d2d4a;padding:12px 16px;margin-top:8px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:11px;color:#64648a;">Pipeline</span>
            <span style="font-size:11px;color:#a78bfa;font-weight:600;">{len(df[~df['stage'].isin(['Won','Lost'])])} active</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
            <span style="font-size:11px;color:#64648a;">Won</span>
            <span style="font-size:11px;color:#10b981;font-weight:600;">{len(df[df['stage']=='Won'])}/{cfg['target']}</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
            <span style="font-size:11px;color:#64648a;">Today's emails</span>
            <span style="font-size:11px;color:#fff;font-weight:600;">{sum(load_send_counts()['counts'].values())}</span>
        </div>
    </div>""", unsafe_allow_html=True)

# Stale lead alerts (sidebar)
_stale_leads = []
for _, _r in df[df["stage"].isin(["Contacted", "Demo Booked", "Proposal"])].iterrows():
    _ds = days_since_touch(_r.to_dict())
    if _ds and _ds >= 7:
        _stale_leads.append((_r["business_name"], _r["stage"], _ds, _r["id"]))
_stale_leads.sort(key=lambda x: -x[2])

if _stale_leads:
    with st.sidebar:
        st.markdown(f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;margin-bottom:12px;">'
                    f'<div style="font-weight:700;color:#ef4444;font-size:14px;">⚠️ {len(_stale_leads)} Stale Leads</div>'
                    f'<div style="font-size:12px;color:#94a3b8;">No activity 7+ days</div></div>', unsafe_allow_html=True)
        for _biz, _stg, _days, _lid in _stale_leads[:10]:
            st.sidebar.button(f"🔴 {_biz} ({_days}d)", key=f"stale_{_lid}", on_click=lambda lid=_lid: st.session_state.update({"view_lead_id": str(lid)}))

if DUPLICATES:
    with st.sidebar:
        n_dupes = len(DUPLICATES)
        st.markdown(f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px;margin-bottom:12px;">'
                    f'<div style="font-weight:700;color:#d97706;font-size:14px;">⚠️ {n_dupes} Duplicate Leads</div>'
                    f'<div style="font-size:12px;color:#94a3b8;">Same email or business name</div></div>', unsafe_allow_html=True)

# Initialise profile view state
if "view_lead_id" not in st.session_state:
    st.session_state["view_lead_id"] = None


def open_profile(lead_id):
    st.session_state["view_lead_id"] = str(lead_id)


def close_profile():
    st.session_state["view_lead_id"] = None


# ─── Global search bar (shown on pipeline + contacts) ──────────────────────
gs1, gs2 = st.columns([3, 1])
global_search = gs1.text_input("🔍 Search leads by name, email, phone, or business", key="global_search", label_visibility="collapsed", placeholder="Search leads by name, email, phone, or business...")
if global_search and not df.empty:
    m = (df["business_name"].str.contains(global_search, case=False, na=False)
         | df["email"].str.contains(global_search, case=False, na=False)
         | df["contact_name"].str.contains(global_search, case=False, na=False)
         | df["phone"].str.contains(global_search, case=False, na=False)
         | df["notes"].str.contains(global_search, case=False, na=False))
    results = df[m].head(10)
    if results.empty:
        st.caption("No lead results")
    else:
        st.markdown("**Leads**")
        for _, r in results.iterrows():
            rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
            rc1.write(f"**{r['business_name']}** — {r['contact_name']}")
            rc2.write(r["email"])
            rc3.write(r["stage"])
            rc4.button("View", key=f"gs_{r['id']}", on_click=open_profile, args=(r["id"],))

    # Search activities too
    act_df = load_activity()
    if not act_df.empty:
        am = (act_df["subject"].str.contains(global_search, case=False, na=False)
              | act_df["content"].str.contains(global_search, case=False, na=False)
              | act_df["business_name"].str.contains(global_search, case=False, na=False))
        act_results = act_df[am].head(5)
        if not act_results.empty:
            st.markdown("**Activity matches**")
            for _, a in act_results.iterrows():
                ac1, ac2, ac3 = st.columns([2, 3, 1])
                ac1.write(f"**{a['business_name']}** · {a['type']}")
                ac2.caption(f"{a['subject']} — {a['timestamp'][:10]}")
                ac3.button("View", key=f"gs_act_{a['id']}", on_click=open_profile, args=(str(a["lead_id"]),))

    st.divider()

# KPI bar
won = len(df[df["stage"] == "Won"])
target = cfg["target"]
remaining = max(target - won, 0)
days_left = (date(date.today().year, date.today().month % 12 + 1, 1) - date.today()).days
in_pipe = len(df[~df["stage"].isin(["Won", "Lost"])])
pct = min(won / target, 1.0) if target else 0

contacted = len(df[df["stage"] == "Contacted"])
pipeline_rev = df[~df["stage"].isin(["Won", "Lost"])]["deal_value"].sum()
won_rev = df[df["stage"] == "Won"]["deal_value"].sum()
st.markdown(f"""
<div class="kpi-bar">
    <div class="kpi-card" style="background:#ecfdf5; border-color:#d1fae5;"><div class="num" style="color:#059669;">{won}/{target}</div><div class="label">Deals Won</div><div class="sub">{pct*100:.0f}% of target</div></div>
    <div class="kpi-card" style="background:#ecfdf5; border-color:#d1fae5;"><div class="num" style="color:#059669;">£{won_rev:,.0f}</div><div class="label">Won Revenue</div></div>
    <div class="kpi-card" style="background:#f5f3ff; border-color:#ddd6fe;"><div class="num" style="color:#7c3aed;">£{pipeline_rev:,.0f}</div><div class="label">Pipeline Value</div></div>
    <div class="kpi-card" style="background:#fef3c7; border-color:#fde68a;"><div class="num" style="color:#d97706;">{remaining}</div><div class="label">To Go</div></div>
    <div class="kpi-card" style="background:#fef2f2; border-color:#fecaca;"><div class="num" style="color:#dc2626;">{days_left}</div><div class="label">Days Left</div></div>
    <div class="kpi-card" style="background:#eff6ff; border-color:#bfdbfe;"><div class="num" style="color:#2563eb;">{contacted}</div><div class="label">Contacted</div></div>
    <div class="kpi-card" style="background:#f8f9fb; border-color:#e2e4e9;"><div class="num">{len(df):,}</div><div class="label">Total Leads</div></div>
</div>
<div class="target-bar"><div class="target-fill" style="width:{pct*100:.0f}%"></div></div>
""", unsafe_allow_html=True)

# ─── Profile view (full-screen when a lead is selected) ───────────────────
PILL_MAP = {
    "New": "stage-pill-new", "Contacted": "stage-pill-contacted",
    "Demo Booked": "stage-pill-demo", "Proposal": "stage-pill-proposal",
    "Won": "stage-pill-won", "Lost": "stage-pill-lost",
}

view_lead_id = st.session_state.get("view_lead_id")
if view_lead_id and not df.empty and (df["id"] == str(view_lead_id)).any():
    lead = df[df["id"] == str(view_lead_id)].iloc[0].to_dict()
    lead_id = str(view_lead_id)
    templates = load_templates()
    esc = html_mod.escape

    st.markdown(f"""<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;padding:16px 20px;
        background:#fff;border:1px solid #e2e4e9;border-radius:12px;">
        <div style="font-size:13px;color:#94a3b8;">Contacts &nbsp;›&nbsp; <span style="color:#1a1a2e;font-weight:600;">{html_mod.escape(lead['business_name'])}</span></div>
    </div>""", unsafe_allow_html=True)
    st.button("← Back", on_click=close_profile)

    sidebar, main = st.columns([1, 2])

    with sidebar:
        initials = "".join(w[0] for w in (lead["contact_name"] or "?").split()[:2]).upper() or "?"
        pill_cls = PILL_MAP.get(lead["stage"], "stage-pill-new")
        name_parts = (lead["contact_name"] or "").split()
        first_name = name_parts[0] if name_parts else "--"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "--"

        phone_href = f'<a href="tel:{esc(lead["phone"])}" class="profile-action-btn">📞</a>' if lead["phone"] else '<span class="profile-action-btn" style="opacity:0.3">📞</span>'
        email_href = f'<a href="mailto:{esc(lead["email"])}" class="profile-action-btn">✉️</a>' if lead["email"] else '<span class="profile-action-btn" style="opacity:0.3">✉️</span>'

        st.markdown(f"""<div class="profile-card">
            <div class="profile-avatar">{esc(initials)}</div>
            <div class="profile-name">{esc(lead['contact_name'] or 'Unknown')}</div>
            <div class="profile-role">{esc(lead['business_name'])}</div>
            <div class="profile-email">{esc(lead['email'] or 'No email')}</div>
            <div class="profile-actions">
                <div class="profile-action-item"><span class="profile-action-btn">📝</span><span class="profile-action-label">Note</span></div>
                <div class="profile-action-item">{email_href}<span class="profile-action-label">Email</span></div>
                <div class="profile-action-item">{phone_href}<span class="profile-action-label">Call</span></div>
                <div class="profile-action-item"><span class="profile-action-btn">📋</span><span class="profile-action-label">Task</span></div>
            </div>
        </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div class="profile-section">
            <div class="profile-section-header"><span class="profile-section-title">About this contact</span></div>
            <div class="profile-field"><div class="profile-field-label">First name</div><div class="profile-field-value">{esc(first_name)}</div></div>
            <div class="profile-field"><div class="profile-field-label">Last name</div><div class="profile-field-value">{esc(last_name)}</div></div>
            <div class="profile-field"><div class="profile-field-label">Email</div><div class="profile-field-value">{esc(lead['email'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Phone</div><div class="profile-field-value">{esc(lead['phone'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Company</div><div class="profile-field-value">{esc(lead['business_name'])}</div></div>
            <div class="profile-field"><div class="profile-field-label">Stage</div><div class="profile-field-value"><span class="contact-stage {pill_cls}">{esc(lead['stage'])}</span></div></div>
            <div class="profile-field"><div class="profile-field-label">Category</div><div class="profile-field-value">{esc(lead['category'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Region</div><div class="profile-field-value">{esc(lead['region'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Source</div><div class="profile-field-value">{esc(lead['source'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Created</div><div class="profile-field-value">{esc(lead['created'] or '--')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Lead score</div><div class="profile-field-value"><span style="color:{LEAD_SCORES.get(lead_id, (0,'','#94a3b8'))[2]};font-weight:600;">{LEAD_SCORES.get(lead_id, (0,'🧊 Cold','#94a3b8'))[1]} ({LEAD_SCORES.get(lead_id, (0,'',''))[0]})</span></div></div>
            <div class="profile-field"><div class="profile-field-label">Deal value</div><div class="profile-field-value">£{lead.get('deal_value', 0):,.0f}</div></div>
            <div class="profile-field"><div class="profile-field-label">Last touch</div><div class="profile-field-value">{esc(lead['last_touch'] or 'Never')}</div></div>
            <div class="profile-field"><div class="profile-field-label">Notes</div><div class="profile-field-value">{esc(lead['notes'] or '--')}</div></div>
        </div>""", unsafe_allow_html=True)

        with st.expander("Edit contact"):
            new_contact = st.text_input("Contact name", lead["contact_name"], key="pv_contact")
            new_phone = st.text_input("Phone", lead["phone"], key="pv_phone")
            new_email = st.text_input("Email", lead["email"], key="pv_email")
            lead_pipeline = lead.get("pipeline") or "Sales"
            lead_stages = PIPELINES.get(lead_pipeline, STAGES)
            new_stage = st.selectbox("Stage", lead_stages, index=lead_stages.index(lead["stage"]) if lead["stage"] in lead_stages else 0, key="pv_stage")
            nad_val = None
            if lead["next_action_date"]:
                try:
                    nad_val = datetime.fromisoformat(lead["next_action_date"]).date()
                except Exception:
                    pass
            new_nad = st.date_input("Next action date", value=nad_val, key="pv_nad")
            new_na = st.text_input("Next action", lead["next_action"], key="pv_na")
            new_deal_value = st.number_input("Deal value (£)", value=float(lead.get("deal_value") or 0), min_value=0.0, step=50.0, key="pv_deal")
            new_notes = st.text_area("Notes", lead["notes"], height=80, key="pv_notes")
            if st.button("Save", type="primary", key="pv_save"):
                save_lead(lead_id, {
                    "contact_name": new_contact, "phone": new_phone, "email": new_email,
                    "stage": new_stage, "next_action_date": new_nad.isoformat() if new_nad else "",
                    "next_action": new_na, "notes": new_notes, "deal_value": new_deal_value,
                })
                st.rerun()

    with main:
        act_tab = st.radio("", ["Email", "Call / Note", "Tasks", "Timeline"], horizontal=True, key="pv_action_tab")

        if act_tab == "Email":
            tmpl_names = list(templates.keys())
            chosen = st.selectbox("Template", ["— blank —"] + tmpl_names, key="pv_tmpl")
            if chosen != "— blank —":
                subj_init, body_init = render_template(templates[chosen], lead)
            else:
                subj_init, body_init = "", ""
            subj = st.text_input("Subject", subj_init, key="pv_subj")
            body = st.text_area("Body", body_init, height=200, key="pv_body")
            to = lead["email"]
            bc1, bc2, bc3 = st.columns([1, 1, 2])
            if to and subj:
                gm = gmail_link(to, subj, body)
                mt = mailto_link(to, subj, body)
                bc1.markdown(f"<a href='{gm}' target='_blank'><button style='padding:8px 16px;background:#00bda5;color:white;border:none;border-radius:4px;cursor:pointer;font-weight:600;width:100%'>Open in Gmail</button></a>", unsafe_allow_html=True)
                bc2.markdown(f"<a href='{mt}'><button style='padding:8px 16px;background:#33475b;color:white;border:none;border-radius:4px;cursor:pointer;font-weight:600;width:100%'>Mail client</button></a>", unsafe_allow_html=True)
            elif not to:
                st.warning("No email on lead")
            if st.button("Log as sent", key="pv_logsent"):
                log_activity(lead_id, lead["business_name"], "email", subj, body)
                updates = {"last_touch": date.today().isoformat()}
                if lead["stage"] == "New":
                    updates["stage"] = "Contacted"
                save_lead(lead_id, updates)
                st.rerun()

        elif act_tab == "Call / Note":
            call_type = st.radio("Type", ["call", "note"], horizontal=True, key="pv_ctype")
            call_subj = st.text_input("Outcome / subject", key="pv_csubj", placeholder="e.g. left voicemail, spoke to owner")
            call_body = st.text_area("Details", height=150, key="pv_cbody", placeholder="What was said, next steps...")
            if st.button("Log " + call_type, type="primary", key="pv_clog"):
                if call_subj or call_body:
                    log_activity(lead_id, lead["business_name"], call_type, call_subj, call_body)
                    updates = {"last_touch": date.today().isoformat()}
                    if call_type == "call" and lead["stage"] == "New":
                        updates["stage"] = "Contacted"
                    save_lead(lead_id, updates)
                    st.rerun()
                else:
                    st.error("Add subject or details")

        elif act_tab == "Tasks":
            lead_tasks = load_tasks(lead_id)
            pending = [t for t in lead_tasks if t.get("status") == "pending"]
            done = [t for t in lead_tasks if t.get("status") == "done"]

            st.markdown("**Add task**")
            tc1, tc2, tc3 = st.columns([3, 2, 1])
            t_title = tc1.text_input("Task", key="pv_task_title", placeholder="e.g. Call back Thursday", label_visibility="collapsed")
            t_due = tc2.date_input("Due", value=None, key="pv_task_due", label_visibility="collapsed")
            t_assigned = tc3.selectbox("Assign", [s["name"] for s in SENDERS], key="pv_task_assign", label_visibility="collapsed")
            if st.button("Add task", type="primary", key="pv_task_add"):
                if t_title:
                    create_task(lead_id, t_title, due_date=t_due.isoformat() if t_due else None, assigned_to=t_assigned)
                    st.rerun()

            if pending:
                st.markdown("**Pending**")
                for t in pending:
                    tc1, tc2, tc3 = st.columns([4, 2, 1])
                    overdue = ""
                    if t.get("due_date"):
                        try:
                            if date.fromisoformat(str(t["due_date"])) < date.today():
                                overdue = " 🔴"
                        except Exception:
                            pass
                    tc1.markdown(f"**{t['title']}**{overdue}")
                    tc2.caption(f"Due: {t.get('due_date', '--')} · {t.get('assigned_to', '')}")
                    if tc3.button("✅", key=f"pv_tdone_{t['id']}"):
                        update_task(t["id"], {"status": "done"})
                        st.rerun()

            if done:
                with st.expander(f"Completed ({len(done)})"):
                    for t in done:
                        st.caption(f"~~{t['title']}~~ — {t.get('due_date', '')}")

            if not pending and not done:
                st.caption("No tasks yet.")

        elif act_tab == "Timeline":
            timeline_items = []
            act_df = load_activity()
            lead_act = act_df[act_df["lead_id"] == str(lead_id)]
            for _, r in lead_act.iterrows():
                timeline_items.append({"timestamp": r["timestamp"], "type": r["type"], "title": r["subject"] or r["type"].title(), "body": r["content"], "source": "crm"})

            # Include tasks in timeline
            for t in load_tasks(lead_id):
                status_label = "✅ " if t.get("status") == "done" else "⏳ "
                timeline_items.append({"timestamp": t.get("created_at", ""), "type": "task", "title": f"{status_label}{t['title']}", "body": f"Due: {t.get('due_date', '--')} · {t.get('assigned_to', '')}", "source": "crm"})

            cached_gmail = load_gmail_cache(lead["email"]) if lead["email"] else None
            if cached_gmail:
                for t in cached_gmail.get("threads", []):
                    for m in t.get("messages", []):
                        sender = m.get("sender", "")
                        direction = "Sent" if "yetipay" in sender.lower() else "Received"
                        timeline_items.append({"timestamp": m.get("date", ""), "type": "gmail", "title": f"{direction}: {m.get('subject', '')}", "body": m.get("snippet", ""), "source": "gmail"})

            timeline_items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

            if not timeline_items:
                st.caption("No activity yet. Send an email or log a call to start the timeline.")
            else:
                current_month = ""
                st.markdown('<div class="timeline">', unsafe_allow_html=True)
                for item in timeline_items:
                    try:
                        dt = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
                        month_label = dt.strftime("%B %Y")
                        date_label = dt.strftime("%b %d, %Y %H:%M")
                    except Exception:
                        month_label = ""
                        date_label = item["timestamp"][:10] if item["timestamp"] else ""
                    if month_label and month_label != current_month:
                        current_month = month_label
                        st.markdown(f'<div class="timeline-month">{esc(month_label)}</div>', unsafe_allow_html=True)
                    dot_cls = {"email": "timeline-dot-email", "call": "timeline-dot-call", "note": "timeline-dot-note", "gmail": "timeline-dot-gmail", "task": "timeline-dot-note"}.get(item["type"], "timeline-dot-note")
                    type_label = {"email": "Email logged", "call": "Call logged", "note": "Note", "gmail": "Gmail", "task": "Task"}.get(item["type"], item["type"])
                    body_html = f'<div class="timeline-body">{esc(item["body"][:500])}</div>' if item["body"] else ""
                    st.markdown(f"""<div class="timeline-item">
                        <div class="timeline-dot {dot_cls}"></div>
                        <div class="timeline-date">{esc(date_label)} · {esc(type_label)}</div>
                        <div class="timeline-title">{esc(item['title'])}</div>
                        {body_html}
                    </div>""", unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

                # AI Analysis button for received emails
                received_emails = [item for item in timeline_items if item.get("type") == "gmail" and "Received" in item.get("title", "")]
                if received_emails:
                    st.divider()
                    st.markdown("**🤖 AI Email Analysis**")
                    if st.button("Analyze latest reply with AI", key="pv_ai_analyze"):
                        latest = received_emails[0]
                        with st.spinner("Analyzing with Claude..."):
                            analysis = analyze_email_ai(latest.get("body", ""), lead.get("business_name", ""))
                        if analysis:
                            sentiment_colors = {"positive": "#10b981", "neutral": "#f59e0b", "negative": "#ef4444"}
                            s_color = sentiment_colors.get(analysis.get("sentiment", ""), "#94a3b8")
                            st.markdown(f"""<div style="background:#f8f9fb;border:1px solid #e2e4e9;border-radius:12px;padding:16px;margin-top:8px;">
                                <div style="display:flex;gap:12px;margin-bottom:8px;">
                                    <span style="background:{s_color}20;color:{s_color};padding:4px 10px;border-radius:8px;font-size:12px;font-weight:600;">Sentiment: {analysis.get('sentiment','?')}</span>
                                    <span style="background:#eff6ff;color:#3b82f6;padding:4px 10px;border-radius:8px;font-size:12px;font-weight:600;">Intent: {analysis.get('intent','?')}</span>
                                    <span style="background:#f5f3ff;color:#7c3aed;padding:4px 10px;border-radius:8px;font-size:12px;font-weight:600;">Urgency: {analysis.get('urgency','?')}</span>
                                </div>
                                <div style="font-size:13px;color:#1a1a2e;margin-bottom:4px;"><strong>Summary:</strong> {html_mod.escape(analysis.get('summary',''))}</div>
                                <div style="font-size:13px;color:#7c3aed;"><strong>Suggested action:</strong> {html_mod.escape(analysis.get('suggested_action',''))}</div>
                            </div>""", unsafe_allow_html=True)
                        else:
                            st.warning("AI analysis unavailable. Set ANTHROPIC_API_KEY in .env")

    st.stop()

# ─── Page routing via sidebar nav ─────────────────────────────────────────
# Map nav labels to page keys
_NAV_MAP = {
    "📊 Pipeline": "pipeline", "👥 Contacts": "contacts", "📅 Today": "today",
    "✉️ Outreach": "outreach", "🔄 Follow Up": "followup", "✅ Tasks": "tasks",
    "📈 Reports": "reports", "➕ Add Lead": "add", "📥 Import": "import",
    "📝 Templates": "templates", "⚙️ Settings": "settings",
}
_active_page = _NAV_MAP.get(nav_choice, "pipeline")

# ─── Pipeline (Kanban) ──────────────────────────────────────────────────────
STAGE_CLASSES = {
    "New": "stage-new", "Contacted": "stage-contacted", "Demo Booked": "stage-demo",
    "Proposal": "stage-proposal", "Won": "stage-won", "Lost": "stage-lost",
    "New Referral": "stage-new", "Referral Signed Up": "stage-won", "Reward Sent": "stage-demo",
}
STAGE_TINTS = {
    "New": ("#10b981", "#f0fdf9"), "Contacted": ("#3b82f6", "#eff6ff"),
    "Demo Booked": ("#7c3aed", "#f5f3ff"), "Proposal": ("#f59e0b", "#fffbeb"),
    "Won": ("#10b981", "#ecfdf5"), "Lost": ("#ef4444", "#fef2f2"),
    "New Referral": ("#10b981", "#f0fdf9"), "Referral Signed Up": ("#10b981", "#ecfdf5"),
    "Reward Sent": ("#7c3aed", "#f5f3ff"),
}

if _active_page == "pipeline":
    ph1, ph2 = st.columns([2, 1])
    active_pipeline = ph1.selectbox("Deal pipeline", list(PIPELINES.keys()), key="pipeline_sel")
    active_stages = PIPELINES[active_pipeline]

    if ph2.button("＋ New deal", type="primary", key="new_deal_btn"):
        st.session_state["show_new_deal"] = True

    if st.session_state.get("show_new_deal"):
        with st.form("new_deal_form", clear_on_submit=True):
            st.subheader(f"New {active_pipeline} deal")
            nc1, nc2 = st.columns(2)
            nd_biz = nc1.text_input("Business name *", key="nd_biz")
            nd_contact = nc2.text_input("Contact name", key="nd_contact")
            nd_phone = nc1.text_input("Phone", key="nd_phone")
            nd_email = nc2.text_input("Email", key="nd_email")
            nd_region = nc1.text_input("Region", key="nd_region")
            nd_category = nc2.text_input("Category", key="nd_category")
            nd_stage = nc1.selectbox("Stage", active_stages, key="nd_stage")
            nd_deal_val = nc2.number_input("Deal value (£)", value=0.0, min_value=0.0, step=50.0, key="nd_deal_val")
            nd_nad = nc1.date_input("Next action date", value=None, key="nd_nad")
            nd_na = nc2.text_input("Next action", key="nd_na")
            nd_notes = st.text_area("Notes", key="nd_notes")
            fc1, fc2 = st.columns(2)
            if fc1.form_submit_button("Add deal", type="primary"):
                if not nd_biz:
                    st.error("Business name required")
                else:
                    new_deal = {
                        "id": int(next_id(df)), "business_name": nd_biz,
                        "contact_name": nd_contact or None, "phone": nd_phone or None,
                        "email": nd_email or None, "region": nd_region or None,
                        "category": nd_category or None, "stage": nd_stage,
                        "last_touch": None,
                        "next_action_date": nd_nad.isoformat() if nd_nad else None,
                        "next_action": nd_na or None, "notes": nd_notes or None,
                        "source": "manual", "created": date.today().isoformat(),
                        "pipeline": active_pipeline, "deal_value": nd_deal_val,
                    }
                    sb.table("leads").insert(new_deal).execute()
                    st.success(f"Added {nd_biz} to {active_pipeline} pipeline")
                    st.session_state["show_new_deal"] = False
                    st.rerun()
            if fc2.form_submit_button("Cancel"):
                st.session_state["show_new_deal"] = False
                st.rerun()

    pf1, pf2, pf3 = st.columns([2, 2, 2])
    p_region = pf1.text_input("Region filter", key="p_region")
    p_search = pf2.text_input("Search name/email", key="p_search")
    p_category = pf3.text_input("Category filter", key="p_category")

    view = df.copy()
    if active_pipeline == "Referral":
        referral_only_stages = [s for s in REFERRAL_STAGES if s not in STAGES]
        view = view[
            (view["source"] == "referral_import")
            | view["stage"].isin(referral_only_stages)
        ]
    else:
        referral_only_stages = [s for s in REFERRAL_STAGES if s not in STAGES]
        view = view[~view["stage"].isin(referral_only_stages)]
    if p_region:
        view = view[view["region"].str.contains(p_region, case=False, na=False)]
    if p_search:
        m = (view["business_name"].str.contains(p_search, case=False, na=False)
             | view["email"].str.contains(p_search, case=False, na=False)
             | view["contact_name"].str.contains(p_search, case=False, na=False))
        view = view[m]
    if p_category:
        view = view[view["category"].str.contains(p_category, case=False, na=False)]

    CARDS_DEFAULT = 8
    esc = html_mod.escape

    # Track expanded columns
    if "kanban_expanded" not in st.session_state:
        st.session_state["kanban_expanded"] = {}

    def move_lead(lid, new_stage):
        save_lead(lid, {"stage": new_stage})

    cols = st.columns(len(active_stages))
    for i, stage in enumerate(active_stages):
        stage_df = view[view["stage"] == stage]
        cls = STAGE_CLASSES.get(stage, "stage-new")
        expanded = st.session_state["kanban_expanded"].get(stage, False)
        show_count = len(stage_df) if expanded else CARDS_DEFAULT

        with cols[i]:
            stage_rev = stage_df["deal_value"].sum()
            rev_html = f' · <span style="font-weight:400;font-size:12px;">£{stage_rev:,.0f}</span>' if stage_rev > 0 else ""
            st.markdown(
                f'<div class="kanban-header {cls}">{stage}<span class="count"> {len(stage_df)}</span>{rev_html}</div>',
                unsafe_allow_html=True,
            )
            for _, row in stage_df.head(show_count).iterrows():
                touch = row["last_touch"] or "No activity"
                cat = esc(row["category"] or "")
                na = esc(row["next_action"] or "")
                na_line = f'<div class="meta">Next: {na}</div>' if na else ""
                cat_line = f'<span class="category-tag">{cat}</span>' if cat else ""
                s_border, s_bg = STAGE_TINTS.get(stage, ("#e2e4e9", "#fff"))
                with st.container(border=True):
                    st.markdown(f'<style>[data-testid="stContainer"]:has(#card-{row["id"]}){{background:{s_bg} !important;border-left:3px solid {s_border} !important;}}</style><span id="card-{row["id"]}" style="display:none"></span>', unsafe_allow_html=True)
                    deal_val = row.get("deal_value", 0)
                    deal_line = f'<div style="font-weight:600;color:#059669;font-size:13px;">£{deal_val:,.0f}</div>' if deal_val > 0 else ""
                    # Lead score badge
                    _sc, _sc_label, _sc_color = LEAD_SCORES.get(str(row["id"]), (0, "🧊 Cold", "#94a3b8"))
                    score_badge = f'<span style="background:{_sc_color}15;color:{_sc_color};padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600;">{_sc_label} {_sc}</span>'
                    # Stale alert
                    _ds = days_since_touch(row.to_dict())
                    stale_badge = f' <span style="color:#ef4444;font-size:10px;">🔴 {_ds}d</span>' if _ds and _ds >= 14 and stage not in ("Won", "Lost") else ""
                    # Dupe badge
                    dupe_badge = ' <span style="color:#d97706;font-size:10px;">⚠️ dupe</span>' if str(row["id"]) in DUPLICATES else ""
                    st.markdown(
                        f'<div class="deal-card-inner">'
                        f'<div class="biz">{esc(row["business_name"])}{stale_badge}{dupe_badge}</div>'
                        f'<div class="contact">{esc(row["contact_name"])} {score_badge}</div>'
                        f'{deal_line}'
                        f'<div class="meta">{esc(touch)}</div>'
                        f'{na_line}{cat_line}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    bc1, bc2 = st.columns([1, 2])
                    bc1.button("View", key=f"k_{stage}_{row['id']}", on_click=open_profile, args=(row["id"],))
                    other_stages = [s for s in active_stages if s != stage]
                    new_s = bc2.selectbox("Move", [stage] + other_stages, key=f"mv_{row['id']}", label_visibility="collapsed")
                    if new_s != stage:
                        move_lead(row["id"], new_s)
                        st.rerun()

            remaining = len(stage_df) - show_count
            if remaining > 0:
                if st.button(f"+{remaining} more", key=f"expand_{stage}"):
                    st.session_state["kanban_expanded"][stage] = True
                    st.rerun()
            elif expanded and len(stage_df) > CARDS_DEFAULT:
                if st.button("Show less", key=f"collapse_{stage}"):
                    st.session_state["kanban_expanded"][stage] = False
                    st.rerun()
            st.markdown(f'<div class="kanban-footer">Total: {len(stage_df)}</div>', unsafe_allow_html=True)

    st.divider()
    st.caption("Move deals between stages below (table editor)")
    stage_f = st.multiselect("Show stages", active_stages, default=[s for s in active_stages if s not in ("Won", "Lost", "Reward Sent")], key="p_stage_f")
    edit_view = view[view["stage"].isin(stage_f)] if stage_f else view
    edited = st.data_editor(
        edit_view[["id", "business_name", "contact_name", "stage", "next_action", "next_action_date"]],
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "stage": st.column_config.SelectboxColumn("Stage", options=active_stages, required=True),
            "id": st.column_config.TextColumn("ID", disabled=True),
        },
        height=400,
        key="pipeline_editor",
    )
    if st.button("Save changes", type="primary", key="p_save"):
        for _, row in edited.iterrows():
            save_lead(row["id"], {
                "stage": row["stage"],
                "next_action": row["next_action"],
                "next_action_date": row["next_action_date"],
            })
        st.success("Saved")
        st.rerun()

# ─── Contacts (HubSpot-style table) ─────────────────────────────────────────
if _active_page == "contacts":
    esc_c = html_mod.escape
    st.markdown("""<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div style="font-size:22px;font-weight:700;color:#1a1a2e;">Contacts</div>
    </div>""", unsafe_allow_html=True)

    cf1, cf2, cf3, cf4 = st.columns([3, 1, 1, 1])
    c_search = cf1.text_input("Search name, phone, email", key="c_search", placeholder="Search contacts...")
    c_stage = cf2.selectbox("Lead status", ["All"] + STAGES, key="c_stage")
    c_region = cf3.text_input("Region", key="c_region")
    c_category = cf4.text_input("Category", key="c_category")

    cview = df.copy()
    if c_search:
        m = (cview["business_name"].str.contains(c_search, case=False, na=False)
             | cview["email"].str.contains(c_search, case=False, na=False)
             | cview["contact_name"].str.contains(c_search, case=False, na=False)
             | cview["phone"].str.contains(c_search, case=False, na=False))
        cview = cview[m]
    if c_stage != "All":
        cview = cview[cview["stage"] == c_stage]
    if c_region:
        cview = cview[cview["region"].str.contains(c_region, case=False, na=False)]
    if c_category:
        cview = cview[cview["category"].str.contains(c_category, case=False, na=False)]

    st.markdown(f'<div style="font-size:13px;color:#64748b;margin-bottom:12px;font-weight:500;">{len(cview):,} contacts</div>', unsafe_allow_html=True)

    page_size = 25
    total_pages = max(1, (len(cview) + page_size - 1) // page_size)
    pc1, pc2, pc3 = st.columns([1, 4, 1])
    page = pc2.number_input("Page", 1, total_pages, 1, key="c_page", label_visibility="collapsed")
    page_df = cview.iloc[(page-1)*page_size : page*page_size]

    header_html = """<div style="display:grid;grid-template-columns:40px 2fr 2fr 1.5fr 1fr 1fr 60px;gap:8px;padding:10px 16px;
        background:#f8f9fb;border-radius:10px 10px 0 0;border:1px solid #e2e4e9;border-bottom:2px solid #e2e4e9;
        font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.8px;">
        <div></div><div>Name</div><div>Email</div><div>Phone</div><div>Stage</div><div>Region</div><div></div>
    </div>"""
    st.markdown(header_html, unsafe_allow_html=True)

    for idx, (_, r) in enumerate(page_df.iterrows()):
        pill_cls = PILL_MAP.get(r["stage"], "stage-pill-new")
        initials = "".join(w[0] for w in (r["contact_name"] or "?").split()[:2]).upper() or "?"
        bg = "#fff" if idx % 2 == 0 else "#f8f9fb"
        row_html = f"""<div style="display:grid;grid-template-columns:40px 2fr 2fr 1.5fr 1fr 1fr 60px;gap:8px;padding:10px 16px;
            background:{bg};border:1px solid #e2e4e9;border-top:none;align-items:center;font-size:13px;">
            <div style="width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#7c3aed,#a78bfa);
                color:#fff;font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;">{esc_c(initials)}</div>
            <div><div style="font-weight:600;color:#1a1a2e;">{esc_c(r['contact_name'] or '--')}</div>
                <div style="font-size:11px;color:#94a3b8;">{esc_c(r['business_name'])}</div></div>
            <div style="color:#64748b;">{esc_c(r['email'] or '--')}</div>
            <div style="color:#64748b;">{esc_c(r['phone'] or '--')}</div>
            <div><span class="contact-stage {pill_cls}">{esc_c(r['stage'])}</span></div>
            <div style="font-size:12px;color:#94a3b8;">{esc_c(r['region'] or '--')}</div>
        </div>"""
        rc1, rc2 = st.columns([20, 1])
        rc1.markdown(row_html, unsafe_allow_html=True)
        rc2.button("→", key=f"ct_{r['id']}", on_click=open_profile, args=(r["id"],))

    pc_b1, pc_b2, pc_b3 = st.columns([2, 3, 2])
    pc_b2.markdown(f'<div style="text-align:center;font-size:13px;color:#94a3b8;padding:12px;">Page {page} of {total_pages} &nbsp;·&nbsp; {page_size} per page</div>', unsafe_allow_html=True)

# ─── Lead detail (quick pick → profile view) ─────────────────────────────
if _active_page == "lead_detail":
    if df.empty:
        st.info("No leads yet. Import some.")
    else:
        labels = (df["id"] + " — " + df["business_name"] + " (" + df["stage"] + ")").tolist()
        sel = st.selectbox("Pick lead", labels, key="lead_select")
        lead_id = sel.split(" — ")[0]
        st.button("Open profile →", key="ld_open", on_click=open_profile, args=(lead_id,))
        lead = df[df["id"] == lead_id].iloc[0].to_dict()
        templates = load_templates()
        esc = html_mod.escape

        sidebar, main = st.columns([1, 2])

        # ── Left: Profile card ──
        with sidebar:
            initials = "".join(w[0] for w in (lead["contact_name"] or "?").split()[:2]).upper() or "?"
            pill_cls = PILL_MAP.get(lead["stage"], "stage-pill-new")
            name_parts = (lead["contact_name"] or "").split()
            first_name = name_parts[0] if name_parts else "--"
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "--"

            phone_href = f'<a href="tel:{esc(lead["phone"])}" class="profile-action-btn">📞</a>' if lead["phone"] else '<span class="profile-action-btn" style="opacity:0.3">📞</span>'
            email_href = f'<a href="mailto:{esc(lead["email"])}" class="profile-action-btn">✉️</a>' if lead["email"] else '<span class="profile-action-btn" style="opacity:0.3">✉️</span>'

            st.markdown(f"""<div class="profile-card">
                <div class="profile-avatar">{esc(initials)}</div>
                <div class="profile-name">{esc(lead['contact_name'] or 'Unknown')}</div>
                <div class="profile-role">{esc(lead['business_name'])}</div>
                <div class="profile-email">{esc(lead['email'] or 'No email')}</div>
                <div class="profile-actions">
                    <div class="profile-action-item"><span class="profile-action-btn">📝</span><span class="profile-action-label">Note</span></div>
                    <div class="profile-action-item">{email_href}<span class="profile-action-label">Email</span></div>
                    <div class="profile-action-item">{phone_href}<span class="profile-action-label">Call</span></div>
                    <div class="profile-action-item"><span class="profile-action-btn">📋</span><span class="profile-action-label">Task</span></div>
                </div>
            </div>""", unsafe_allow_html=True)

            # About this contact
            st.markdown(f"""<div class="profile-section">
                <div class="profile-section-header">
                    <span class="profile-section-title">About this contact</span>
                </div>
                <div class="profile-field"><div class="profile-field-label">First name</div><div class="profile-field-value">{esc(first_name)}</div></div>
                <div class="profile-field"><div class="profile-field-label">Last name</div><div class="profile-field-value">{esc(last_name)}</div></div>
                <div class="profile-field"><div class="profile-field-label">Email</div><div class="profile-field-value">{esc(lead['email'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Phone</div><div class="profile-field-value">{esc(lead['phone'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Company</div><div class="profile-field-value">{esc(lead['business_name'])}</div></div>
                <div class="profile-field"><div class="profile-field-label">Stage</div><div class="profile-field-value"><span class="contact-stage {pill_cls}">{esc(lead['stage'])}</span></div></div>
                <div class="profile-field"><div class="profile-field-label">Category</div><div class="profile-field-value">{esc(lead['category'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Region</div><div class="profile-field-value">{esc(lead['region'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Source</div><div class="profile-field-value">{esc(lead['source'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Created</div><div class="profile-field-value">{esc(lead['created'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Last touch</div><div class="profile-field-value">{esc(lead['last_touch'] or 'Never')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Next action</div><div class="profile-field-value">{esc(lead['next_action'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Next action date</div><div class="profile-field-value">{esc(lead['next_action_date'] or '--')}</div></div>
                <div class="profile-field"><div class="profile-field-label">Notes</div><div class="profile-field-value">{esc(lead['notes'] or '--')}</div></div>
            </div>""", unsafe_allow_html=True)

            # Edit section
            with st.expander("Edit contact"):
                new_contact = st.text_input("Contact name", lead["contact_name"], key="d_contact")
                new_phone = st.text_input("Phone", lead["phone"], key="d_phone")
                new_email = st.text_input("Email", lead["email"], key="d_email")
                d_lead_pipeline = lead.get("pipeline") or "Sales"
                d_lead_stages = PIPELINES.get(d_lead_pipeline, STAGES)
                new_stage = st.selectbox("Stage", d_lead_stages, index=d_lead_stages.index(lead["stage"]) if lead["stage"] in d_lead_stages else 0, key="d_stage")
                nad_val = None
                if lead["next_action_date"]:
                    try:
                        nad_val = datetime.fromisoformat(lead["next_action_date"]).date()
                    except Exception:
                        nad_val = None
                new_nad = st.date_input("Next action date", value=nad_val, key="d_nad")
                new_na = st.text_input("Next action", lead["next_action"], key="d_na")
                new_notes = st.text_area("Notes", lead["notes"], height=80, key="d_notes")
                if st.button("Save", type="primary", key="d_save"):
                    save_lead(lead_id, {
                        "contact_name": new_contact,
                        "phone": new_phone,
                        "email": new_email,
                        "stage": new_stage,
                        "next_action_date": new_nad.isoformat() if new_nad else "",
                        "next_action": new_na,
                        "notes": new_notes,
                    })
                    st.rerun()

        # ── Right: Actions + Timeline ──
        with main:
            # Quick actions bar
            act_tab = st.radio("", ["Email", "Call / Note", "Timeline"], horizontal=True, key="d_action_tab")

            if act_tab == "Email":
                tmpl_names = list(templates.keys())
                chosen = st.selectbox("Template", ["— blank —"] + tmpl_names, key="d_tmpl")
                if chosen != "— blank —":
                    subj_init, body_init = render_template(templates[chosen], lead)
                else:
                    subj_init, body_init = "", ""
                subj = st.text_input("Subject", subj_init, key="d_subj")
                body = st.text_area("Body", body_init, height=200, key="d_body")

                to = lead["email"]
                bc1, bc2, bc3 = st.columns([1, 1, 2])
                if to and subj:
                    gm = gmail_link(to, subj, body)
                    mt = mailto_link(to, subj, body)
                    bc1.markdown(
                        f"<a href='{gm}' target='_blank'><button style='padding:8px 16px;background:#00bda5;color:white;border:none;border-radius:4px;cursor:pointer;font-weight:600;width:100%'>Open in Gmail</button></a>",
                        unsafe_allow_html=True,
                    )
                    bc2.markdown(
                        f"<a href='{mt}'><button style='padding:8px 16px;background:#33475b;color:white;border:none;border-radius:4px;cursor:pointer;font-weight:600;width:100%'>Mail client</button></a>",
                        unsafe_allow_html=True,
                    )
                elif not to:
                    st.warning("No email on lead")
                if st.button("Log as sent", key="d_logsent"):
                    log_activity(lead_id, lead["business_name"], "email", subj, body)
                    updates = {"last_touch": date.today().isoformat()}
                    if lead["stage"] == "New":
                        updates["stage"] = "Contacted"
                    save_lead(lead_id, updates)
                    st.rerun()

            elif act_tab == "Call / Note":
                call_type = st.radio("Type", ["call", "note"], horizontal=True, key="d_ctype")
                call_subj = st.text_input("Outcome / subject", key="d_csubj",
                                           placeholder="e.g. left voicemail, spoke to owner, gatekeeper")
                call_body = st.text_area("Details", height=150, key="d_cbody",
                                          placeholder="What was said, next steps, objections...")
                if st.button("Log " + call_type, type="primary", key="d_clog"):
                    if call_subj or call_body:
                        log_activity(lead_id, lead["business_name"], call_type, call_subj, call_body)
                        updates = {"last_touch": date.today().isoformat()}
                        if call_type == "call" and lead["stage"] == "New":
                            updates["stage"] = "Contacted"
                        save_lead(lead_id, updates)
                        st.rerun()
                    else:
                        st.error("Add subject or details")

            elif act_tab == "Timeline":
                # Fetch Gmail emails for this contact
                if lead["email"]:
                    cached = load_gmail_cache(lead["email"])
                    if st.button("Refresh from Gmail", key="d_gmail_refresh"):
                        st.session_state["gmail_fetch_email"] = lead["email"]

                # Build unified timeline: CRM activity + Gmail emails
                timeline_items = []

                # CRM activity log
                act_df = load_activity()
                lead_act = act_df[act_df["lead_id"] == str(lead_id)]
                for _, r in lead_act.iterrows():
                    timeline_items.append({
                        "timestamp": r["timestamp"],
                        "type": r["type"],
                        "title": r["subject"] or r["type"].title(),
                        "body": r["content"],
                        "source": "crm",
                    })

                # Gmail cached emails
                cached = load_gmail_cache(lead["email"]) if lead["email"] else None
                if cached:
                    for t in cached.get("threads", []):
                        for m in t.get("messages", []):
                            sender = m.get("sender", "")
                            snippet = m.get("snippet", "")
                            subj_g = m.get("subject", "")
                            date_g = m.get("date", "")
                            direction = "Sent" if "yetipay" in sender.lower() else "Received"
                            timeline_items.append({
                                "timestamp": date_g,
                                "type": "gmail",
                                "title": f"{direction}: {subj_g}",
                                "body": snippet,
                                "source": "gmail",
                            })

                # Sort by timestamp desc
                timeline_items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

                if not timeline_items:
                    st.caption("No activity yet. Send an email or log a call to start the timeline.")
                else:
                    # Group by month
                    current_month = ""
                    st.markdown('<div class="timeline">', unsafe_allow_html=True)
                    for item in timeline_items:
                        ts = item["timestamp"][:10] if item["timestamp"] else ""
                        try:
                            dt = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
                            month_label = dt.strftime("%B %Y")
                            date_label = dt.strftime("%b %d, %Y %H:%M")
                        except Exception:
                            month_label = ""
                            date_label = ts

                        if month_label and month_label != current_month:
                            current_month = month_label
                            st.markdown(f'<div class="timeline-month">{esc(month_label)}</div>', unsafe_allow_html=True)

                        dot_cls = {
                            "email": "timeline-dot-email",
                            "call": "timeline-dot-call",
                            "note": "timeline-dot-note",
                            "gmail": "timeline-dot-gmail",
                        }.get(item["type"], "timeline-dot-note")

                        type_label = {"email": "Email logged", "call": "Call logged", "note": "Note", "gmail": "Gmail"}.get(item["type"], item["type"])

                        body_html = ""
                        if item["body"]:
                            body_html = f'<div class="timeline-body">{esc(item["body"][:500])}</div>'

                        st.markdown(f"""<div class="timeline-item">
                            <div class="timeline-dot {dot_cls}"></div>
                            <div class="timeline-date">{esc(date_label)} · {esc(type_label)}</div>
                            <div class="timeline-title">{esc(item['title'])}</div>
                            {body_html}
                        </div>""", unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)


# ─── Today ───────────────────────────────────────────────────────────────────
if _active_page == "today":
    today = date.today().isoformat()
    due = df[(df["next_action_date"] <= today) & (df["next_action_date"] != "")
             & (~df["stage"].isin(["Won", "Lost"]))]
    st.subheader(f"Due today or overdue ({len(due)})")
    if due.empty:
        st.info("Nothing due. Book next actions on Pipeline tab.")
    else:
        st.dataframe(
            due[["business_name", "contact_name", "phone", "email", "stage", "next_action", "next_action_date"]],
            use_container_width=True, hide_index=True,
        )

    st.subheader("No next action set")
    stale = df[(df["next_action_date"] == "") & (~df["stage"].isin(["Won", "Lost"]))]
    st.caption(f"{len(stale)} leads with no follow-up booked")
    st.dataframe(stale[["business_name", "stage", "last_touch"]].head(50),
                 use_container_width=True, hide_index=True)

# ─── Bulk email ──────────────────────────────────────────────────────────────
if _active_page == "outreach":
    templates = load_templates()
    if df.empty:
        st.info("No leads. Import first.")
    else:
        st.markdown("""<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Outreach Centre</div>
        <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Generate Gmail compose links for bulk sending</div>""", unsafe_allow_html=True)
        bc1, bc2 = st.columns(2)
        tmpl_pick = bc1.selectbox("Template", list(templates.keys()), key="b_tmpl")
        stage_pick = bc2.multiselect("Filter by stage", STAGES, default=["New"], key="b_stage")
        INDUSTRY_GROUPS = {
            "Restaurant": ["restaurant", "italian_restaurant", "pizza_restaurant", "seafood_restaurant",
                "japanese_restaurant", "chinese_restaurant", "mexican_restaurant", "indian_restaurant",
                "thai_restaurant", "american_restaurant", "british_restaurant", "sri_lankan_restaurant",
                "mediterranean_restaurant", "breakfast_restaurant", "steak_house", "hamburger_restaurant",
                "bistro", "gastropub", "deli", "catering_service"],
            "Cafe & Coffee": ["cafe", "coffee_shop", "coffee_roastery", "tea_store", "tea_house",
                "ice_cream_shop", "cake_shop", "pastry_shop", "confectionery", "chocolate_factory"],
            "Bar & Pub": ["bar", "pub", "wine_bar", "cocktail_bar", "sports_bar", "bar_and_grill",
                "night_club", "brewpub", "brewery"],
            "Beauty & Hair": ["beauty_salon", "hair_salon", "nail_salon", "barber_shop",
                "skin_care_clinic", "body_art_service", "spa", "massage", "tailor",
                "womens_clothing_store"],
            "Health & Fitness": ["gym", "fitness_center", "yoga_studio", "wellness_center",
                "physiotherapist", "chiropractor", "medical_clinic", "medical_center",
                "sports_school", "sports_complex", "sportswear_store", "health"],
            "Retail - Fashion": ["clothing_store", "shoe_store", "jewelry_store", "thrift_store"],
            "Retail - Home": ["furniture_store", "home_goods_store", "home_improvement_store",
                "building_materials_store", "garden_center", "florist", "painter"],
            "Retail - Food": ["food_store", "bakery", "butcher_shop", "grocery_store",
                "supermarket", "asian_grocery_store", "market", "farm", "liquor_store", "food"],
            "Retail - Other": ["store", "gift_shop", "book_store", "toy_store", "electronics_store",
                "sporting_goods_store", "bicycle_store", "auto_parts_store", "pet_store",
                "department_store"],
            "Art & Gallery": ["art_gallery", "art_studio"],
            "Hospitality": ["hotel", "lodging", "event_venue", "wedding_venue", "tourist_attraction",
                "tour_agency", "visitor_center", "aquarium"],
            "Professional Services": ["general_contractor", "manufacturer", "consultant",
                "corporate_office", "supplier", "wholesaler", "service", "storage", "laundry"],
            "Community & Education": ["non_profit_organization", "community_center", "school",
                "university", "research_institute", "child_care_agency", "local_government_office",
                "performing_arts_theater", "sports_school"],
            "Pets": ["pet_store", "pet_boarding_service"],
            "Other": ["establishment", "point_of_interest"],
        }
        bc3, bc4 = st.columns(2)
        region_pick = bc3.text_input("Region contains (optional)", key="b_region")
        industry_pick = bc4.multiselect("Industry", sorted(INDUSTRY_GROUPS.keys()), key="b_industry")

        pool = df[df["stage"].isin(stage_pick)] if stage_pick else df.copy()
        if region_pick:
            pool = pool[pool["region"].str.contains(region_pick, case=False, na=False)]
        if industry_pick:
            allowed_cats = set()
            for grp in industry_pick:
                allowed_cats.update(INDUSTRY_GROUPS[grp])
            pool = pool[pool["category"].isin(allowed_cats)]
        pool = pool[pool["email"].str.contains("@", na=False)]
        pool = pool[~pool["email"].str.lower().str.startswith(
            ("info@", "hello@", "contact@", "enquiries@", "admin@", "sales@", "office@", "reception@", "bookings@")
        )]
        only_new = st.checkbox("Only leads never emailed", value=True, key="b_only_new")
        if only_new:
            emailed_ids = set()
            try:
                act = sb.table("activity").select("lead_id").eq("type", "email").execute()
                emailed_ids = {str(r["lead_id"]) for r in act.data} if act.data else set()
            except Exception:
                pass
            pool = pool[~pool["id"].isin(emailed_ids)]

        st.caption(f"{len(pool)} leads match filters (personal email only)")

        limit = st.slider("How many to send", 1, min(len(pool), 200) if len(pool) > 0 else 1,
                          min(50, len(pool)) if len(pool) > 0 else 1, key="b_limit")
        skip = st.session_state.get("bulk_skip_offset", 0)
        if skip > 0:
            st.caption(f"Skipped {skip} leads from previous batches")
        batch = pool.iloc[skip:skip + limit]

        if not batch.empty:
            sample_lead = batch.iloc[0].to_dict()
            prev_s, prev_b = render_template(templates[tmpl_pick], sample_lead)
            with st.expander("Preview (first lead)", expanded=True):
                st.markdown(f"**To:** {sample_lead['email']}")
                st.markdown(f"**Subject:** {prev_s}")
                st.text(prev_b)

        tracker = load_send_counts()
        remaining_today = sum(s["daily_cap"] - tracker["counts"].get(s["email"], 0) for s in SENDERS)
        sender_html = f'<div style="background:#fff;border:1px solid #e2e4e9;border-radius:12px;padding:16px 20px;margin:16px 0;">'
        sender_html += f'<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:12px;">Sender Rotation &nbsp;<span style="color:#94a3b8;font-weight:400;">— {remaining_today} sends remaining today</span></div>'
        for s in SENDERS:
            used = tracker["counts"].get(s["email"], 0)
            left = s["daily_cap"] - used
            bar_pct = min(used / s["daily_cap"], 1.0) if s["daily_cap"] > 0 else 0
            bar_color = "#10b981" if left > 20 else "#f59e0b" if left > 5 else "#ef4444"
            sender_html += f'''<div style="margin-bottom:10px;">
                <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;">
                    <span style="font-weight:500;color:#1a1a2e;">{html_mod.escape(s["email"])}</span>
                    <span style="color:#94a3b8;">{used}/{s["daily_cap"]}</span>
                </div>
                <div style="background:#e2e4e9;border-radius:10px;height:6px;overflow:hidden;">
                    <div style="background:{bar_color};height:6px;width:{bar_pct*100:.0f}%;border-radius:10px;transition:width 0.3s;"></div>
                </div></div>'''
        sender_html += '</div>'
        st.markdown(sender_html, unsafe_allow_html=True)

        if limit > remaining_today:
            st.error(f"Only {remaining_today} sends left across all inboxes today. Lower the limit or wait til tomorrow.")

        def _generate_links():
            _links = []
            _sender_counts = {s["email"]: tracker["counts"].get(s["email"], 0) for s in SENDERS}
            for _, row in batch.iterrows():
                _sender = None
                for s in SENDERS:
                    if _sender_counts[s["email"]] < s["daily_cap"]:
                        _sender = s
                        break
                if not _sender:
                    break
                _lead = row.to_dict()
                _s, _b = render_template(templates[tmpl_pick], _lead)
                _gm = gmail_link(_lead["email"], _s, _b, sender_email=_sender["email"])
                _links.append({
                    "business": _lead["business_name"], "email": _lead["email"],
                    "link": _gm, "sender": _sender["email"],
                    "lead_id": _lead["id"], "subject": _s, "body": _b,
                })
                _sender_counts[_sender["email"]] += 1
            return _links

        if st.button(f"Generate {limit} Gmail links", type="primary", key="b_send"):
            st.session_state["bulk_links"] = _generate_links()
            st.rerun()

        if st.session_state.get("bulk_auto_regen"):
            st.session_state["bulk_auto_regen"] = False
            st.session_state["bulk_links"] = _generate_links()

        if st.session_state.get("bulk_links"):
            links = st.session_state["bulk_links"]
            if "bulk_sent" not in st.session_state:
                st.session_state["bulk_sent"] = set()
            if "bulk_opened" not in st.session_state:
                st.session_state["bulk_opened"] = set()
            sent_set = st.session_state["bulk_sent"]
            unsent = [i for i, l in enumerate(links) if i not in sent_set]
            prog_pct = len(sent_set) / len(links) * 100 if links else 0
            st.markdown(f"""<div style="background:#fff;border:1px solid #e2e4e9;border-radius:12px;padding:16px 20px;margin-bottom:16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:14px;font-weight:600;color:#1a1a2e;">{len(links)} links generated</span>
                    <span style="font-size:13px;color:#94a3b8;"><span style="color:#10b981;font-weight:600;">{len(sent_set)}</span> sent &nbsp;·&nbsp; <span style="color:#f59e0b;font-weight:600;">{len(unsent)}</span> remaining</span>
                </div>
                <div style="background:#e2e4e9;border-radius:10px;height:6px;overflow:hidden;">
                    <div style="background:linear-gradient(90deg,#10b981,#34d399);height:6px;width:{prog_pct:.0f}%;border-radius:10px;"></div>
                </div></div>""", unsafe_allow_html=True)

            import streamlit.components.v1 as components
            current_sender = None
            for i, lnk in enumerate(links):
                if lnk["sender"] != current_sender:
                    current_sender = lnk["sender"]
                    st.markdown(f'<div style="font-size:13px;font-weight:600;color:#1a1a2e;margin:16px 0 8px;padding:8px 12px;background:#f8f9fb;border-radius:8px;border-left:3px solid #7c3aed;">From: {html_mod.escape(current_sender)}</div>', unsafe_allow_html=True)
                if i in sent_set:
                    st.markdown(f'<div style="padding:6px 12px;font-size:13px;color:#94a3b8;text-decoration:line-through;">✅ {html_mod.escape(lnk["business"])} — {html_mod.escape(lnk["email"])}</div>', unsafe_allow_html=True)
                elif i in st.session_state.get("bulk_opened", set()):
                    lc1, lc2, lc3, lc4 = st.columns([3, 3, 1, 1])
                    lc1.markdown(f'<span style="font-size:13px;color:#f59e0b;font-weight:500;">📨 {html_mod.escape(lnk["business"])}</span>', unsafe_allow_html=True)
                    lc2.markdown(f'<span style="font-size:12px;color:#94a3b8;">{html_mod.escape(lnk["email"])}</span>', unsafe_allow_html=True)
                    if lc4.button("🚫", key=f"bdne_{i}", help="Do not email — move to Lost"):
                        save_lead(lnk["lead_id"], {"stage": "Lost", "notes": "Do not email"})
                        st.session_state["bulk_links"] = None
                        st.session_state["bulk_sent"] = set()
                        st.session_state["bulk_opened"] = set()
                        st.session_state["bulk_auto_regen"] = True
                        st.rerun()
                    if lc3.button("Sent ✓", key=f"bsent_{i}"):
                        tracker = load_send_counts()
                        log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                        lead_row = df[df["id"] == str(lnk["lead_id"])]
                        updates = {"last_touch": date.today().isoformat()}
                        if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                            updates["stage"] = "Contacted"
                        save_lead(lnk["lead_id"], updates)
                        tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                        save_send_counts(tracker)
                        st.session_state["bulk_links"] = None
                        st.session_state["bulk_sent"] = set()
                        st.session_state["bulk_opened"] = set()
                        st.session_state["bulk_auto_regen"] = True
                        st.rerun()
                else:
                    lc1, lc2, lc3, lc4 = st.columns([3, 3, 1, 1])
                    with lc1:
                        components.html(
                            f'<a href="{html_mod.escape(lnk["link"])}" target="_blank" '
                            f'style="color:#7c3aed;text-decoration:none;font-family:Inter,sans-serif;font-size:13px;font-weight:500;">✉ {html_mod.escape(lnk["business"])}</a>',
                            height=28,
                        )
                    lc2.markdown(f'<span style="font-size:12px;color:#94a3b8;">{html_mod.escape(lnk["email"])}</span>', unsafe_allow_html=True)
                    if lc4.button("🚫", key=f"bdne_{i}", help="Do not email — move to Lost"):
                        save_lead(lnk["lead_id"], {"stage": "Lost", "notes": "Do not email"})
                        st.session_state["bulk_links"] = None
                        st.session_state["bulk_sent"] = set()
                        st.session_state["bulk_opened"] = set()
                        st.session_state["bulk_auto_regen"] = True
                        st.rerun()
                    if lc3.button("Sent ✓", key=f"bsent_{i}"):
                        tracker = load_send_counts()
                        log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                        lead_row = df[df["id"] == str(lnk["lead_id"])]
                        updates = {"last_touch": date.today().isoformat()}
                        if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                            updates["stage"] = "Contacted"
                        save_lead(lnk["lead_id"], updates)
                        tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                        save_send_counts(tracker)
                        st.session_state["bulk_links"] = None
                        st.session_state["bulk_sent"] = set()
                        st.session_state["bulk_opened"] = set()
                        st.session_state["bulk_auto_regen"] = True
                        st.rerun()

            st.divider()
            unsent_links = [links[i]["link"] for i in unsent]
            BATCH_SIZE = 10
            opened_set = st.session_state.get("bulk_opened", set())
            # Unopened and unsent
            unopened = [i for i in unsent if i not in opened_set]
            if unopened:
                # Group by sender
                sender_groups = {}
                for idx in unopened:
                    lnk = links[idx]
                    sender_groups.setdefault(lnk["sender"], []).append(idx)

                for sender_email, sender_idxs in sender_groups.items():
                    open_count = min(BATCH_SIZE, len(sender_idxs))
                    profile_dir = SENDER_PROFILE.get(sender_email, "Profile 5")
                    if st.button(f"Open next {open_count} for {sender_email} ({len(sender_idxs)} remaining)", type="primary", key=f"b_open_{sender_email}"):
                        import subprocess, time
                        batch_to_open = sender_idxs[:BATCH_SIZE]
                        for idx in batch_to_open:
                            subprocess.Popen([
                                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                                f"--profile-directory={profile_dir}", links[idx]["link"]
                            ])
                            time.sleep(0.5)
                        st.session_state["bulk_opened"].update(batch_to_open)
                        st.rerun()

            cc1, cc2, cc3, cc4 = st.columns(4)
            if cc4.button("🔍 Check Gmail sent", key="b_check_sent"):
                try:
                    from gmail_auth import check_sent_emails
                    all_unsent_emails = [links[i]["email"] for i in unsent]
                    found = check_sent_emails(all_unsent_emails, hours_back=24) if all_unsent_emails else set()
                    tracker = load_send_counts()
                    newly_confirmed = 0
                    for i in unsent:
                        if links[i]["email"].lower() in found:
                            log_activity(links[i]["lead_id"], links[i]["business"], "email", links[i]["subject"], links[i]["body"])
                            lead_row = df[df["id"] == str(links[i]["lead_id"])]
                            updates = {"last_touch": date.today().isoformat()}
                            if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                updates["stage"] = "Contacted"
                            save_lead(links[i]["lead_id"], updates)
                            tracker["counts"][links[i]["sender"]] = tracker["counts"].get(links[i]["sender"], 0) + 1
                            st.session_state["bulk_sent"].add(i)
                            newly_confirmed += 1
                    save_send_counts(tracker)
                    st.success(f"Found {newly_confirmed} sent emails in Gmail.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gmail check failed: {e}. Run `python3 gmail_auth.py` first.")
            if cc1.button("✅ Confirm all sent", type="primary", key="b_confirm"):
                tracker = load_send_counts()
                for i, lnk in enumerate(links):
                    if i not in sent_set:
                        log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                        lead_row = df[df["id"] == str(lnk["lead_id"])]
                        updates = {"last_touch": date.today().isoformat()}
                        if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                            updates["stage"] = "Contacted"
                        save_lead(lnk["lead_id"], updates)
                        tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                save_send_counts(tracker)
                st.session_state["bulk_links"] = None
                st.session_state["bulk_sent"] = set()
                st.session_state["bulk_opened"] = set()
                st.success(f"Logged {len(links)} emails as sent.")
                st.rerun()
            if cc2.button("❌ Cancel remaining", key="b_cancel"):
                st.session_state["bulk_links"] = None
                st.session_state["bulk_sent"] = set()
                st.session_state["bulk_opened"] = set()
                st.session_state["bulk_skip_offset"] = 0
                st.info("Cancelled remaining. Already-confirmed sends kept.")
                st.rerun()
            if cc3.button("🔄 Next batch", key="b_next_batch"):
                st.session_state["bulk_skip_offset"] = st.session_state.get("bulk_skip_offset", 0) + limit
                st.session_state["bulk_links"] = None
                st.session_state["bulk_sent"] = set()
                st.session_state["bulk_opened"] = set()
                st.rerun()


# ─── Follow Up ───────────────────────────────────────────────────────────────
if _active_page == "followup":
    contacted = df[df["stage"] == "Contacted"].copy()
    if contacted.empty:
        st.info("No contacted leads to follow up with yet.")
    else:
        st.markdown("""<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Follow Up Centre</div>
        <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Follow up with leads you've already emailed — detect bounces & replies</div>""", unsafe_allow_html=True)

        # Status tags stored in notes field: [BOUNCED] [REPLIED]
        def _fu_status(row):
            notes = str(row.get("notes", "") or "")
            if "[BOUNCED]" in notes:
                return "Bounced"
            if "[REPLIED]" in notes:
                return "Replied"
            return "Awaiting Reply"

        contacted["fu_status"] = contacted.apply(_fu_status, axis=1)

        # KPI row
        n_awaiting = len(contacted[contacted["fu_status"] == "Awaiting Reply"])
        n_replied = len(contacted[contacted["fu_status"] == "Replied"])
        n_bounced = len(contacted[contacted["fu_status"] == "Bounced"])
        st.markdown(f"""<div style="display:flex;gap:12px;margin-bottom:16px;">
            <div class="kpi-card" style="background:#eff6ff;border-color:#bfdbfe;"><div class="num" style="color:#2563eb;">{n_awaiting}</div><div class="label">Awaiting Reply</div></div>
            <div class="kpi-card" style="background:#f0fdf9;border-color:#bbf7d0;"><div class="num" style="color:#10b981;">{n_replied}</div><div class="label">Replied</div></div>
            <div class="kpi-card" style="background:#fef2f2;border-color:#fecaca;"><div class="num" style="color:#ef4444;">{n_bounced}</div><div class="label">Bounced</div></div>
        </div>""", unsafe_allow_html=True)

        # Scan Gmail for bounces & replies
        fu_c1, fu_c2, fu_c3 = st.columns(3)
        if fu_c1.button("🔍 Scan for bounces & replies", type="primary", key="fu_scan"):
            try:
                from gmail_auth import check_bounces, check_replies
                emails_to_check = contacted[contacted["fu_status"] == "Awaiting Reply"]["email"].dropna().str.lower().tolist()
                if emails_to_check:
                    with st.spinner(f"Scanning {len(emails_to_check)} emails..."):
                        bounced = check_bounces(emails_to_check, hours_back=168)
                        replied = check_replies(emails_to_check, hours_back=168)
                    tagged = 0
                    for _, row in contacted.iterrows():
                        e = str(row["email"]).lower()
                        notes = str(row.get("notes", "") or "")
                        if e in bounced and "[BOUNCED]" not in notes:
                            save_lead(row["id"], {"notes": notes + " [BOUNCED]", "stage": "Lost"})
                            tagged += 1
                        elif e in replied and "[REPLIED]" not in notes:
                            save_lead(row["id"], {"notes": notes + " [REPLIED]"})
                            tagged += 1
                    st.success(f"Found {len(bounced)} bounces, {len(replied)} replies. Tagged {tagged} leads.")
                    st.rerun()
                else:
                    st.info("No awaiting-reply leads to scan.")
            except Exception as e:
                st.error(f"Gmail scan failed: {e}")

        # Filter
        fu_filter = fu_c2.selectbox("Show", ["Awaiting Reply", "All", "Replied", "Bounced"], key="fu_filter")
        if fu_filter != "All":
            show_fu = contacted[contacted["fu_status"] == fu_filter]
        else:
            show_fu = contacted

        # Follow-up template
        templates = load_templates()
        fu_tmpl = fu_c3.selectbox("Follow-up template", list(templates.keys()), key="fu_tmpl")

        st.caption(f"Showing {len(show_fu)} leads")

        # Days since last touch
        today_dt = date.today()
        for idx, row in show_fu.iterrows():
            lead = row.to_dict()
            lt = lead.get("last_touch", "")
            if lt:
                try:
                    days_ago = (today_dt - date.fromisoformat(str(lt)[:10])).days
                except Exception:
                    days_ago = "?"
            else:
                days_ago = "?"

            status = lead.get("fu_status", "Awaiting Reply")
            if status == "Bounced":
                badge = '<span style="background:#fef2f2;color:#ef4444;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;">⛔ Bounced</span>'
            elif status == "Replied":
                badge = '<span style="background:#f0fdf9;color:#10b981;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;">✅ Replied</span>'
            else:
                badge = '<span style="background:#eff6ff;color:#3b82f6;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;">⏳ Awaiting</span>'

            with st.container(border=True):
                fc1, fc2, fc3, fc4, fc5 = st.columns([3, 2, 1, 1, 1])
                fc1.markdown(f"**{html_mod.escape(str(lead.get('business_name', '')))}** &nbsp; {badge}", unsafe_allow_html=True)
                fc2.markdown(f'<span style="font-size:12px;color:#94a3b8;">{html_mod.escape(str(lead.get("email", "")))}</span>', unsafe_allow_html=True)
                fc3.markdown(f'<span style="font-size:12px;color:#64748b;">{days_ago}d ago</span>', unsafe_allow_html=True)

                if status == "Awaiting Reply":
                    # Generate follow-up link
                    subj, body = render_template(templates[fu_tmpl], lead)
                    link = gmail_link(lead["email"], subj, body)
                    if fc4.button("✉️ Follow up", key=f"fu_send_{lead['id']}"):
                        import subprocess
                        profile_dir = SENDER_PROFILE.get(SENDERS[0]["email"], "Profile 5")
                        subprocess.Popen([
                            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                            f"--profile-directory={profile_dir}", link
                        ])
                    if fc5.button("🚫", key=f"fu_dne_{lead['id']}", help="Do not email — move to Lost"):
                        save_lead(lead["id"], {"stage": "Lost", "notes": str(lead.get("notes", "") or "") + " [DO NOT EMAIL]"})
                        st.rerun()
                elif status == "Replied":
                    if fc4.button("📞 Book demo", key=f"fu_demo_{lead['id']}"):
                        save_lead(lead["id"], {"stage": "Demo Booked"})
                        st.rerun()


# ─── Tasks ───────────────────────────────────────────────────────────────────
if _active_page == "tasks":
    st.markdown("""<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Task Manager</div>
    <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Track follow-ups, calls, and to-dos across all leads</div>""", unsafe_allow_html=True)

    all_tasks = load_tasks()
    today_dt = date.today()

    # Auto-create follow-up tasks for stale leads (7+ days no reply)
    if "automations_ran" not in st.session_state:
        st.session_state["automations_ran"] = True
        try:
            stale = df[(df["stage"] == "Contacted") & (df["last_touch"] != "")]
            auto_created = 0
            existing_task_leads = {t["lead_id"] for t in all_tasks if t.get("status") == "pending"}
            for _, row in stale.iterrows():
                try:
                    lt = date.fromisoformat(str(row["last_touch"])[:10])
                    if (today_dt - lt).days >= 7 and int(row["id"]) not in existing_task_leads:
                        create_task(row["id"], f"Follow up — no reply in {(today_dt - lt).days}d",
                                    due_date=today_dt.isoformat(), assigned_to=SENDERS[0]["name"])
                        auto_created += 1
                except Exception:
                    continue
            if auto_created:
                st.toast(f"🤖 Auto-created {auto_created} follow-up tasks for stale leads")
                all_tasks = load_tasks()  # reload
        except Exception:
            pass

    # KPIs
    pending_tasks = [t for t in all_tasks if t.get("status") == "pending"]
    overdue_tasks = [t for t in pending_tasks if t.get("due_date") and t["due_date"] < today_dt.isoformat()]
    today_tasks = [t for t in pending_tasks if t.get("due_date") == today_dt.isoformat()]
    done_tasks = [t for t in all_tasks if t.get("status") == "done"]

    st.markdown(f"""<div style="display:flex;gap:12px;margin-bottom:16px;">
        <div class="kpi-card" style="background:#fef2f2;border-color:#fecaca;"><div class="num" style="color:#ef4444;">{len(overdue_tasks)}</div><div class="label">Overdue</div></div>
        <div class="kpi-card" style="background:#eff6ff;border-color:#bfdbfe;"><div class="num" style="color:#3b82f6;">{len(today_tasks)}</div><div class="label">Due Today</div></div>
        <div class="kpi-card" style="background:#f5f3ff;border-color:#ddd6fe;"><div class="num" style="color:#7c3aed;">{len(pending_tasks)}</div><div class="label">Pending</div></div>
        <div class="kpi-card" style="background:#f0fdf9;border-color:#bbf7d0;"><div class="num" style="color:#10b981;">{len(done_tasks)}</div><div class="label">Done</div></div>
    </div>""", unsafe_allow_html=True)

    # New task form
    with st.expander("➕ New task", expanded=False):
        ntc1, ntc2 = st.columns(2)
        nt_lead = ntc1.selectbox("Lead", df["business_name"].tolist(), key="nt_lead_pick")
        nt_title = ntc2.text_input("Task title", key="nt_title", placeholder="e.g. Call back Thursday")
        ntc3, ntc4 = st.columns(2)
        nt_due = ntc3.date_input("Due date", value=today_dt, key="nt_due")
        nt_assign = ntc4.selectbox("Assign to", [s["name"] for s in SENDERS], key="nt_assign")
        if st.button("Create task", type="primary", key="nt_create"):
            if nt_title:
                lead_match = df[df["business_name"] == nt_lead]
                if not lead_match.empty:
                    create_task(lead_match.iloc[0]["id"], nt_title, due_date=nt_due.isoformat(), assigned_to=nt_assign)
                    st.success(f"Task created: {nt_title}")
                    st.rerun()

    # Task filter
    task_filter = st.radio("Show", ["Overdue", "Today", "All Pending", "Completed"], horizontal=True, key="task_filter")

    if task_filter == "Overdue":
        show_tasks = overdue_tasks
    elif task_filter == "Today":
        show_tasks = today_tasks
    elif task_filter == "All Pending":
        show_tasks = pending_tasks
    else:
        show_tasks = done_tasks

    if not show_tasks:
        st.caption("No tasks in this view.")
    else:
        for t in show_tasks:
            lead_name = ""
            lead_match = df[df["id"] == str(t["lead_id"])]
            if not lead_match.empty:
                lead_name = lead_match.iloc[0]["business_name"]

            overdue_badge = ""
            if t.get("due_date") and t["due_date"] < today_dt.isoformat() and t.get("status") == "pending":
                overdue_badge = ' <span style="background:#fef2f2;color:#ef4444;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;">OVERDUE</span>'

            with st.container(border=True):
                tc1, tc2, tc3, tc4 = st.columns([3, 2, 2, 1])
                tc1.markdown(f"**{t['title']}**{overdue_badge}", unsafe_allow_html=True)
                tc2.caption(f"📍 {lead_name}")
                tc3.caption(f"📅 {t.get('due_date', '--')} · 👤 {t.get('assigned_to', '')}")
                if t.get("status") == "pending":
                    if tc4.button("✅ Done", key=f"t_done_{t['id']}"):
                        update_task(t["id"], {"status": "done"})
                        st.rerun()
                else:
                    tc4.caption("✅")


# ─── Sequences ───────────────────────────────────────────────────────────────
if _active_page == "sequences":
    sequences = load_sequences()
    templates = load_templates()
    seq_q = load_seq_queue()

    seq_sub = st.radio("", ["Today's tasks", "Enroll leads", "Manage sequences"], horizontal=True, key="seq_sub")

    if seq_sub == "Today's tasks":
        st.subheader("Sequence tasks due today")
        if seq_q.empty:
            st.info("No leads enrolled in sequences yet.")
        else:
            today_q = seq_q[(seq_q["due_date"] <= date.today().isoformat()) & (seq_q["status"] == "pending")]
            if today_q.empty:
                st.success("All caught up. No sequence tasks due.")
            else:
                for i, (_, task) in enumerate(today_q.iterrows()):
                    seq_def = sequences.get(task["sequence_name"], {})
                    steps = seq_def.get("steps", [])
                    step_idx = int(task["step"])
                    step = steps[step_idx] if step_idx < len(steps) else {}
                    channel = step.get("channel", "?")
                    tmpl_name = step.get("template")

                    lead_row = df[df["id"] == task["lead_id"]]
                    if lead_row.empty:
                        continue
                    lead = lead_row.iloc[0].to_dict()

                    with st.container():
                        col1, col2, col3 = st.columns([3, 2, 1])
                        col1.markdown(f"**{task['business_name']}** — Step {step_idx + 1}: **{channel.upper()}**")
                        if channel == "email" and tmpl_name and tmpl_name in templates:
                            s, b = render_template(templates[tmpl_name], lead)
                            col2.markdown(f"[Open Gmail]({gmail_link(lead['email'], s, b)})")
                        elif channel == "call" and lead.get("phone"):
                            col2.markdown(f"[Call {lead['phone']}](tel:{lead['phone']})")
                        if col3.button("Done", key=f"seq_done_{i}"):
                            sb.table("sequence_queue").update({"status": "done"}).eq(
                                "lead_id", int(task["lead_id"])
                            ).eq("sequence_name", task["sequence_name"]).eq(
                                "step", int(task["step"])
                            ).execute()
                            if channel == "email" and tmpl_name and tmpl_name in templates:
                                s, b = render_template(templates[tmpl_name], lead)
                                log_activity(lead["id"], lead["business_name"], "email", s, b)
                            elif channel == "call":
                                log_activity(lead["id"], lead["business_name"], "call", f"Sequence {task['sequence_name']} step {step_idx+1}", "")
                            updates = {"last_touch": date.today().isoformat()}
                            if lead.get("stage") == "New":
                                updates["stage"] = "Contacted"
                            save_lead(task["lead_id"], updates)
                            st.rerun()
                        st.divider()

    elif seq_sub == "Enroll leads":
        st.subheader("Enroll leads into sequence")
        seq_name = st.selectbox("Sequence", list(sequences.keys()), key="seq_enroll_name")
        seq_def = sequences[seq_name]
        st.caption("Steps: " + " → ".join(
            [f"Day {s['day']}: {s['channel']}" + (f" ({s['template']})" if s.get('template') else "") for s in seq_def["steps"]]
        ))
        ec1, ec2 = st.columns(2)
        e_stage = ec1.multiselect("Stage filter", STAGES, default=["New"], key="seq_e_stage")
        e_region = ec2.text_input("Region filter", key="seq_e_region")
        pool = df[df["stage"].isin(e_stage)] if e_stage else df.copy()
        if e_region:
            pool = pool[pool["region"].str.contains(e_region, case=False, na=False)]
        pool = pool[pool["email"].str.contains("@", na=False)]
        already = set(seq_q[seq_q["sequence_name"] == seq_name]["lead_id"].unique()) if not seq_q.empty else set()
        pool = pool[~pool["id"].isin(already)]
        st.caption(f"{len(pool)} eligible leads (not already enrolled)")
        e_limit = st.slider("Enroll how many", 1, min(len(pool), 500) if len(pool) > 0 else 1,
                            min(50, len(pool)) if len(pool) > 0 else 1, key="seq_e_limit")
        if st.button(f"Enroll {e_limit} leads", type="primary", key="seq_e_go"):
            enrolled = 0
            for _, row in pool.head(e_limit).iterrows():
                enroll_lead(row["id"], row["business_name"], seq_name, sequences)
                enrolled += 1
            st.success(f"Enrolled {enrolled} leads into '{seq_name}'")
            st.rerun()

    elif seq_sub == "Manage sequences":
        st.subheader("Create / edit sequences")
        seq_pick = st.selectbox("Sequence", ["+ New"] + list(sequences.keys()), key="seq_m_pick")
        if seq_pick == "+ New":
            new_name = st.text_input("Sequence name", key="seq_m_name")
            st.caption("Add steps below. Day = days after enrollment. Channel = email or call. Template = optional.")
            n_steps = st.number_input("Number of steps", 1, 10, 3, key="seq_m_nsteps")
            steps = []
            for si in range(int(n_steps)):
                sc1, sc2, sc3 = st.columns(3)
                d = sc1.number_input(f"Step {si+1} day", 0, 60, si * 3, key=f"seq_m_d{si}")
                ch = sc2.selectbox(f"Step {si+1} channel", ["email", "call"], key=f"seq_m_ch{si}")
                tm = sc3.selectbox(f"Step {si+1} template", ["(none)"] + list(templates.keys()), key=f"seq_m_tm{si}")
                steps.append({"day": int(d), "channel": ch, "template": tm if tm != "(none)" else None})
            if st.button("Create sequence", type="primary", key="seq_m_create"):
                if new_name:
                    sequences[new_name] = {"steps": steps}
                    save_sequences(sequences)
                    st.success(f"Created '{new_name}'")
                    st.rerun()
        else:
            cur = sequences[seq_pick]
            st.json(cur)
            if st.button("Delete sequence", type="secondary", key="seq_m_del"):
                del sequences[seq_pick]
                save_sequences(sequences)
                seq_q = seq_q[seq_q["sequence_name"] != seq_pick]
                save_seq_queue(seq_q)
                st.success("Deleted")
                st.rerun()


# ─── Reports ─────────────────────────────────────────────────────────────────
if _active_page == "reports":
    if df.empty:
        st.info("No data yet.")
    else:
        st.markdown("""<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Reports & Analytics</div>
        <div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Pipeline health, conversion rates, and activity trends</div>""", unsafe_allow_html=True)

        # ── Conversion Funnel ──
        st.markdown("### Conversion Funnel")
        funnel_stages = ["New", "Contacted", "Demo Booked", "Proposal", "Won"]
        funnel_counts = [len(df[df["stage"] == s]) for s in funnel_stages]
        funnel_data = pd.DataFrame({"Stage": funnel_stages, "Leads": funnel_counts})
        st.bar_chart(funnel_data.set_index("Stage"))

        # Conversion rates between stages
        conv_html = '<div style="display:flex;gap:8px;margin:12px 0 24px 0;">'
        for i in range(len(funnel_stages) - 1):
            if funnel_counts[i] > 0:
                rate = (funnel_counts[i + 1] / funnel_counts[i]) * 100
            else:
                rate = 0
            color = "#10b981" if rate >= 30 else "#f59e0b" if rate >= 10 else "#ef4444"
            conv_html += f'<div style="background:#f8f9fb;border:1px solid #e2e4e9;border-radius:8px;padding:8px 12px;text-align:center;flex:1;">'
            conv_html += f'<div style="font-size:11px;color:#94a3b8;">{funnel_stages[i]} → {funnel_stages[i+1]}</div>'
            conv_html += f'<div style="font-size:18px;font-weight:700;color:{color};">{rate:.0f}%</div></div>'
        conv_html += '</div>'
        st.markdown(conv_html, unsafe_allow_html=True)

        # ── Revenue ──
        rp1, rp2, rp3, rp4 = st.columns(4)
        won_rev = df[df["stage"] == "Won"]["deal_value"].sum()
        pipe_rev = df[~df["stage"].isin(["Won", "Lost"])]["deal_value"].sum()
        proposal_rev = df[df["stage"] == "Proposal"]["deal_value"].sum()
        demo_rev = df[df["stage"] == "Demo Booked"]["deal_value"].sum()
        rp1.metric("Won Revenue", f"£{won_rev:,.0f}")
        rp2.metric("Pipeline Value", f"£{pipe_rev:,.0f}")
        rp3.metric("In Proposal", f"£{proposal_rev:,.0f}")
        rp4.metric("In Demo", f"£{demo_rev:,.0f}")

        # Revenue forecast (weighted by stage probability)
        stage_prob = {"New": 0.05, "Contacted": 0.1, "Demo Booked": 0.3, "Proposal": 0.6, "Won": 1.0, "Lost": 0}
        forecast = sum(
            float(row.get("deal_value", 0) or 0) * stage_prob.get(row.get("stage", ""), 0)
            for _, row in df.iterrows()
        )
        st.markdown(f'<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:12px;padding:16px;margin:16px 0;">'
                    f'<div style="font-size:14px;color:#94a3b8;">Weighted Revenue Forecast</div>'
                    f'<div style="font-size:28px;font-weight:700;color:#7c3aed;">£{forecast:,.0f}</div>'
                    f'<div style="font-size:12px;color:#94a3b8;">Based on stage probability × deal value</div></div>', unsafe_allow_html=True)

        st.divider()

        # ── Activity Stats ──
        st.markdown("### Activity")
        act = load_activity()
        if not act.empty:
            act["date"] = pd.to_datetime(act["timestamp"], errors="coerce").dt.date
            act["week"] = pd.to_datetime(act["timestamp"], errors="coerce").dt.isocalendar().week

            ac1, ac2, ac3, ac4 = st.columns(4)
            n_emails = len(act[act["type"] == "email"])
            n_calls = len(act[act["type"] == "call"])
            n_notes = len(act[act["type"] == "note"])
            this_week = act[act["date"] >= (date.today() - pd.Timedelta(days=7))]
            ac1.metric("Total Emails", n_emails)
            ac2.metric("Total Calls", n_calls)
            ac3.metric("Total Notes", n_notes)
            ac4.metric("This Week", len(this_week))

            # Daily activity chart
            daily = act.groupby(["date", "type"]).size().unstack(fill_value=0)
            st.line_chart(daily)
            st.caption("Activity logged per day (emails, calls, notes)")

            # Emails per sender (from bulk outreach)
            if "email" in act["type"].values:
                st.markdown("### Emails by Week")
                email_acts = act[act["type"] == "email"].copy()
                email_acts["week_start"] = pd.to_datetime(email_acts["date"]) - pd.to_timedelta(pd.to_datetime(email_acts["date"]).dt.dayofweek, unit="d")
                weekly = email_acts.groupby("week_start").size()
                if not weekly.empty:
                    st.bar_chart(weekly)
        else:
            st.caption("No activity logged yet.")

        st.divider()

        # ── Lead Score Distribution ──
        st.markdown("### Lead Score Distribution")
        score_labels = {"🔥 Hot": 0, "🟡 Warm": 0, "🧊 Cold": 0}
        for lid, (sc, label, color) in LEAD_SCORES.items():
            if label in score_labels:
                score_labels[label] += 1
        sc_df = pd.DataFrame({"Category": score_labels.keys(), "Count": score_labels.values()})
        st.bar_chart(sc_df.set_index("Category"))

        # ── Top regions ──
        ch1, ch2 = st.columns(2)
        with ch1:
            st.markdown("### Top Regions")
            region_counts = df["region"].value_counts().head(15)
            if not region_counts.empty:
                st.bar_chart(region_counts)
        with ch2:
            st.markdown("### Top Categories")
            cat_counts = df["category"].value_counts().head(15)
            if not cat_counts.empty:
                st.bar_chart(cat_counts)

        # ── Stale leads ──
        st.markdown("### Stale Leads (7+ days no activity)")
        if _stale_leads:
            stale_df = pd.DataFrame(_stale_leads, columns=["Business", "Stage", "Days Since Touch", "ID"])
            st.dataframe(stale_df[["Business", "Stage", "Days Since Touch"]], use_container_width=True, height=300)
        else:
            st.success("No stale leads! All leads are being worked.")


# ─── Add lead ────────────────────────────────────────────────────────────────
if _active_page == "add":
    with st.form("add_lead", clear_on_submit=True):
        c1, c2 = st.columns(2)
        biz = c1.text_input("Business name *")
        contact = c2.text_input("Contact name")
        phone = c1.text_input("Phone")
        email = c2.text_input("Email")
        region = c1.text_input("Region")
        category = c2.text_input("Category")
        pl = c1.selectbox("Pipeline", list(PIPELINES.keys()), key="add_pipeline")
        stage = c2.selectbox("Stage", PIPELINES[pl], key="add_stage")
        nad = c1.date_input("Next action date", value=None)
        na = c2.text_input("Next action")
        notes = st.text_area("Notes")
        if st.form_submit_button("Add", type="primary"):
            if not biz:
                st.error("Business name required")
            else:
                new = {
                    "id": int(next_id(df)), "business_name": biz, "contact_name": contact or None,
                    "phone": phone or None, "email": email or None, "region": region or None,
                    "category": category or None, "stage": stage,
                    "last_touch": None,
                    "next_action_date": nad.isoformat() if nad else None,
                    "next_action": na or None, "notes": notes or None, "source": "manual",
                    "created": date.today().isoformat(), "pipeline": pl,
                }
                sb.table("leads").insert(new).execute()
                st.success(f"Added {biz}")

# ─── Import ──────────────────────────────────────────────────────────────────
if _active_page == "import":
    st.write(f"Source: `{LEADS_SRC}`")
    if LEADS_SRC.exists():
        src = pd.read_csv(LEADS_SRC, dtype=str).fillna("")
        regions = ["All"] + sorted(src["region"].dropna().unique().tolist())
        c1, c2 = st.columns(2)
        rf = c1.selectbox("Region", regions)
        lim = c2.number_input("Limit", min_value=1, max_value=5000, value=100, step=50)
        st.caption("Generic inbox emails (info@/hello@ etc) auto-filtered. Duplicates by business name skipped.")
        if st.button("Import", type="primary"):
            df, added = import_leads(df, LEADS_SRC, rf, lim)
            st.success(f"Imported {added} new leads")
            st.rerun()
    else:
        st.error("Source CSV not found")

    st.divider()
    st.subheader("Upload custom CSV")
    st.caption("CSV needs `business_name` column. Optional: `email`, `phone`, `first_name`, `last_name`, `region`, `category`, `source`.")
    up = st.file_uploader("Choose CSV file", type="csv", key="csv_upload")
    if up:
        tmp_df = pd.read_csv(up, dtype=str).fillna("")
        st.write(f"**{len(tmp_df)} rows** found. Columns: {', '.join(tmp_df.columns.tolist())}")
        st.dataframe(tmp_df.head(10), use_container_width=True, height=200)
        uc1, uc2 = st.columns(2)
        up_pipeline = uc1.selectbox("Import to pipeline", list(PIPELINES.keys()), key="up_pipeline")
        up_stage = uc2.selectbox("Default stage", PIPELINES[up_pipeline], key="up_stage")
        if st.button("Import uploaded", type="primary"):
            tmp = ROOT / "_upload.csv"
            tmp.write_bytes(up.getvalue())
            df, added = import_leads(df, tmp, None, None, pipeline=up_pipeline, default_stage=up_stage)
            tmp.unlink()
            st.success(f"Imported {added} new leads to {up_pipeline} pipeline")
            st.rerun()

# ─── Templates (HubSpot-style) ──────────────────────────────────────────────
if _active_page == "templates":
    templates = load_templates()
    names = list(templates.keys())

    tmpl_list, tmpl_editor = st.columns([1, 2])

    with tmpl_list:
        st.markdown("**Your templates**")
        pick = st.selectbox("Template", ["+ Create new template"] + names, key="t_pick", label_visibility="collapsed")
        st.divider()
        st.markdown("**Personalize** — insert tokens:")
        token_md = ""
        for tok in TOKENS:
            token_md += f"`{tok}` "
        st.markdown(token_md)
        st.caption("Tokens auto-replace with CRM data when you send.")

    with tmpl_editor:
        if pick == "+ Create new template":
            st.markdown("""<div class="tmpl-editor">
                <div class="tmpl-header"><h3>Create new template</h3></div>
                </div>""", unsafe_allow_html=True)
            tname = st.text_input("Name", key="t_new_name", placeholder="e.g. Cold intro, Follow-up 1")
            tsubj = st.text_input("Subject", key="t_new_subj", placeholder="e.g. Quick idea for {{company}}")
            st.caption("Hi `{{firstname}}`")
            tbody = st.text_area("Body", height=280, key="t_new_body",
                                  placeholder="Write your template here...\n\nUse {{firstname}}, {{company}}, {{category}} etc.")
            tc1, tc2 = st.columns([1, 3])
            if tc1.button("Save template", type="primary"):
                if tname and tsubj and tbody:
                    templates[tname] = {"subject": tsubj, "body": tbody}
                    save_templates(templates)
                    st.success(f"Created '{tname}'")
                    st.rerun()
                else:
                    st.error("All fields required")
            tc2.caption("Your signature will be included when you use this template.")
        else:
            cur = templates[pick]
            st.markdown(f"""<div class="tmpl-editor">
                <div class="tmpl-header"><h3>{pick}</h3></div>
                </div>""", unsafe_allow_html=True)
            tsubj = st.text_input("Subject", cur["subject"], key="t_subj")
            tbody = st.text_area("Body", cur["body"], height=280, key="t_body")
            tc1, tc2, tc3 = st.columns([1, 1, 2])
            if tc1.button("Save template", type="primary"):
                templates[pick] = {"subject": tsubj, "body": tbody}
                save_templates(templates)
                st.success("Saved")
            if tc2.button("Delete", type="secondary"):
                del templates[pick]
                save_templates(templates)
                st.success("Deleted")
                st.rerun()

        # Live preview
        st.markdown("---")
        if pick == "+ Create new template":
            p_subj, p_body = tsubj if 'tsubj' in dir() else "", tbody if 'tbody' in dir() else ""
        else:
            p_subj, p_body = tsubj, tbody
        sample = {
            "contact_name": "Sarah Johnson", "business_name": "The Rose & Crown",
            "email": "sarah@roseandcrown.co.uk", "phone": "07700 900123",
            "category": "pub", "region": "Manchester",
        }
        if not df.empty:
            use_real = st.checkbox("Preview with real lead", key="t_prev_real")
            if use_real:
                prev_labels = (df["id"] + " — " + df["business_name"]).tolist()
                prev_sel = st.selectbox("Lead", prev_labels, key="t_prev_lead")
                prev_id = prev_sel.split(" — ")[0]
                sample = df[df["id"] == prev_id].iloc[0].to_dict()
        rendered_s, rendered_b = render_template({"subject": p_subj, "body": p_body}, sample)
        st.markdown(f"""<div class="tmpl-preview">
            <div class="preview-to">To: {sample.get('email','sarah@roseandcrown.co.uk')}</div>
            <div class="preview-subject">{html_mod.escape(rendered_s)}</div>
            <div class="preview-body">{html_mod.escape(rendered_b)}</div>
        </div>""", unsafe_allow_html=True)


# ─── Settings ────────────────────────────────────────────────────────────────
if _active_page == "settings":
    new_target = st.number_input("Monthly target (terminals)", min_value=1, value=cfg["target"])
    if st.button("Save target"):
        cfg["target"] = int(new_target)
        save_config(cfg)
        st.success("Saved")

    st.divider()
    st.subheader("Export")
    st.download_button("Download CRM CSV", df.to_csv(index=False), "crm_export.csv")

    st.divider()
    st.subheader("Danger")
    if st.checkbox("I want to wipe all CRM data"):
        if st.button("WIPE", type="primary"):
            sb.table("leads").delete().neq("id", -999).execute()
            sb.table("activity").delete().neq("id", -999).execute()
            sb.table("sequence_queue").delete().neq("lead_id", -999).execute()
            st.success("Wiped. Reload.")
