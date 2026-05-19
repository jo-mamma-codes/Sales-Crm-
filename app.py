"""Yetipay solo CRM — Streamlit dashboard (Supabase backend)."""
import html as html_mod
import os
import json
import urllib.parse
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

SUPABASE_URL = "https://ndfgaqefiekgtcyzmmuj.supabase.co"
SUPABASE_KEY = "sb_publishable_Okz9BDOjHA6rkAo6YVja5w_loiV4Unq"

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


def render_template(tmpl, lead):
    """Replace {{token}} placeholders with CRM record values."""
    subj, body = tmpl["subject"], tmpl["body"]
    for token, (field, transform) in TOKENS.items():
        val = transform(lead.get(field, ""))
        subj = subj.replace(token, val)
        body = body.replace(token, val)
    return subj, body


def mailto_link(to, subject, body):
    q = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{to}?{q}"


SENDERS = [
    {"email": "joseph.allison@yetipay.me", "name": "Joseph Allison", "daily_cap": 40},
    {"email": "insidesales@yetipay.me", "name": "Yetipay Sales", "daily_cap": 40},
    {"email": "dominic.ritchie@yetipay.me", "name": "Dominic Ritchie", "daily_cap": 40},
    {"email": "ashley@yetipay.me", "name": "Ashley", "daily_cap": 40},
]
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
    if sender_email:
        url += f"&authuser={sender_email}"
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
    "next_action", "notes", "source", "created", "pipeline",
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
            df[c] = df[c].astype(str)
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
/* Force light mode */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
[data-testid="stToolbar"], [data-testid="stDecoration"] {
    background-color: #ffffff !important;
    color: #33475b !important;
    font-family: 'Lexend Deca', 'Inter', sans-serif;
}
section[data-testid="stSidebar"] { background: #f5f8fa !important; }
label, .stMarkdown, .stCaption, p, span, div { color: #33475b !important; }
input, textarea, select, [data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background-color: #fff !important; color: #33475b !important;
    border-color: #dfe3eb !important;
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    background: #fff !important;
}
.stTabs [data-baseweb="tab-list"] { background: #f5f8fa !important; }
.stTabs [data-baseweb="tab"] { color: #33475b !important; }
.stSelectbox > div > div { background: #fff !important; color: #33475b !important; }

/* KPI bar */
.kpi-bar { display:flex; gap:12px; margin-bottom:16px; }
.kpi-card {
    flex:1; background:#fff; border:1px solid #dfe3eb; border-radius:8px;
    padding:16px 20px; text-align:center;
}
.kpi-card .num { font-size:28px; font-weight:700; color:#33475b; }
.kpi-card .label { font-size:12px; color:#7c98b6; text-transform:uppercase; letter-spacing:0.5px; }
.kpi-card .sub { font-size:11px; color:#99acc2; }

/* Kanban board */
.kanban-board { display:flex; gap:12px; overflow-x:auto; padding-bottom:12px; }
.kanban-col {
    min-width:220px; max-width:260px; flex:1;
    background:#f5f8fa; border-radius:8px; padding:0;
}
.kanban-header {
    padding:12px 16px; font-weight:600; font-size:13px; color:#33475b;
    border-bottom:2px solid; text-transform:uppercase; letter-spacing:0.3px;
}
.kanban-header .count { font-weight:400; color:#7c98b6; margin-left:6px; }
.kanban-cards { padding:8px; max-height:520px; overflow-y:auto; }
.deal-card {
    background:#fff; border:1px solid #dfe3eb; border-radius:6px;
    padding:12px 14px; margin-bottom:8px; cursor:pointer;
    transition: box-shadow 0.15s;
}
.deal-card:hover { box-shadow:0 2px 8px rgba(0,0,0,0.1); }
.deal-card .biz { font-weight:600; font-size:13px; color:#33475b; margin-bottom:4px; }
.deal-card .contact { font-size:12px; color:#516f90; }
.deal-card .meta { font-size:11px; color:#99acc2; margin-top:6px; }
.deal-card .category-tag {
    display:inline-block; background:#eaf0f6; color:#516f90;
    font-size:10px; padding:2px 8px; border-radius:10px; margin-top:4px;
}
.kanban-footer {
    padding:8px 16px; font-size:11px; color:#7c98b6;
    border-top:1px solid #dfe3eb;
}

/* Stage colors */
.stage-new { border-color:#00bda5; }
.stage-contacted { border-color:#00a4bd; }
.stage-demo { border-color:#6a78d1; }
.stage-proposal { border-color:#f5c26b; }
.stage-won { border-color:#00bda5; }
.stage-lost { border-color:#f2545b; }

/* Contact table */
.contact-table { border-collapse:collapse; width:100%; font-size:13px; }
.contact-table th {
    text-align:left; padding:10px 14px; background:#f5f8fa;
    color:#7c98b6; font-weight:600; font-size:11px;
    text-transform:uppercase; letter-spacing:0.5px;
    border-bottom:2px solid #dfe3eb;
}
.contact-table td {
    padding:10px 14px; border-bottom:1px solid #eaf0f6; color:#33475b;
}
.contact-table tr:hover td { background:#f5f8fa; }
.contact-name { font-weight:600; color:#0091ae; }
.contact-email { color:#516f90; }
.contact-stage {
    display:inline-block; padding:2px 10px; border-radius:10px;
    font-size:11px; font-weight:600;
}
.stage-pill-new { background:#e5f5f3; color:#00bda5; }
.stage-pill-contacted { background:#e5f4f7; color:#00a4bd; }
.stage-pill-demo { background:#ededf9; color:#6a78d1; }
.stage-pill-proposal { background:#fef5e5; color:#d4940a; }
.stage-pill-won { background:#e5f5f3; color:#00875a; }
.stage-pill-lost { background:#fde8e9; color:#f2545b; }

/* Template editor */
.tmpl-editor {
    background:#fff; border:1px solid #dfe3eb; border-radius:8px;
    overflow:hidden;
}
.tmpl-header {
    padding:16px 20px; border-bottom:1px solid #dfe3eb;
    display:flex; justify-content:space-between; align-items:center;
}
.tmpl-header h3 { margin:0; font-size:16px; color:#33475b; }
.tmpl-body { padding:20px; }
.tmpl-toolbar {
    padding:8px 20px; border-top:1px solid #dfe3eb;
    display:flex; gap:12px; align-items:center; background:#f5f8fa;
}
.token-btn {
    display:inline-block; background:#00bda5; color:#fff;
    padding:4px 12px; border-radius:4px; font-size:12px;
    cursor:pointer; border:none;
}
.tmpl-preview {
    background:#f5f8fa; border:1px solid #dfe3eb; border-radius:8px;
    padding:20px; margin-top:12px;
}
.tmpl-preview .preview-to { color:#7c98b6; font-size:12px; margin-bottom:4px; }
.tmpl-preview .preview-subject { font-weight:600; font-size:14px; color:#33475b; margin-bottom:12px; }
.tmpl-preview .preview-body { font-size:13px; color:#516f90; white-space:pre-wrap; line-height:1.6; }

/* Progress bar */
.target-bar { background:#eaf0f6; border-radius:20px; height:10px; margin:8px 0; }
.target-fill { background:linear-gradient(90deg,#00bda5,#00a4bd); height:10px; border-radius:20px; transition:width 0.5s; }

/* Contact profile */
.profile-card {
    background:#fff; border:1px solid #dfe3eb; border-radius:8px;
    padding:24px 20px; text-align:center;
}
.profile-avatar {
    width:88px; height:88px; border-radius:50%; background:#7c98b6;
    color:#fff; font-size:34px; font-weight:700; line-height:88px;
    margin:0 auto 14px; text-transform:uppercase; letter-spacing:1px;
}
.profile-name { font-size:20px; font-weight:700; color:#33475b; margin-bottom:2px; }
.profile-role { font-size:13px; color:#516f90; margin-bottom:4px; }
.profile-email { font-size:13px; color:#516f90; margin-bottom:14px; }
.profile-actions { display:flex; justify-content:center; gap:14px; margin:14px 0; flex-wrap:wrap; }
.profile-action-item { display:flex; flex-direction:column; align-items:center; gap:4px; }
.profile-action-btn {
    width:38px; height:38px; border-radius:50%; border:1px solid #dfe3eb;
    background:#fff; color:#516f90; font-size:16px; cursor:pointer;
    display:inline-flex; align-items:center; justify-content:center;
    text-decoration:none; transition:all 0.15s;
}
.profile-action-btn:hover { background:#eaf0f6; border-color:#00bda5; color:#00bda5; }
.profile-action-label { font-size:10px; color:#7c98b6; }
.profile-section {
    text-align:left; border-top:1px solid #eaf0f6; padding-top:14px; margin-top:14px;
}
.profile-section-header {
    display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;
}
.profile-section-title {
    font-size:12px; font-weight:600; color:#33475b;
}
.profile-section-action { font-size:11px; color:#0091ae; cursor:pointer; }
.profile-field { margin-bottom:10px; }
.profile-field-label { font-size:11px; color:#7c98b6; margin-bottom:1px; }
.profile-field-value { font-size:13px; color:#33475b; font-weight:500; }

/* Timeline */
.timeline { position:relative; padding-left:24px; }
.timeline::before {
    content:''; position:absolute; left:8px; top:0; bottom:0;
    width:2px; background:#dfe3eb;
}
.timeline-item { position:relative; margin-bottom:20px; }
.timeline-dot {
    position:absolute; left:-20px; top:4px; width:12px; height:12px;
    border-radius:50%; border:2px solid #fff;
}
.timeline-dot-email { background:#00a4bd; }
.timeline-dot-call { background:#00bda5; }
.timeline-dot-note { background:#6a78d1; }
.timeline-dot-gmail { background:#ea4335; }
.timeline-date { font-size:11px; color:#7c98b6; margin-bottom:2px; }
.timeline-title { font-size:13px; font-weight:600; color:#33475b; margin-bottom:4px; }
.timeline-body {
    font-size:12px; color:#516f90; background:#f5f8fa;
    border-radius:6px; padding:10px 14px; white-space:pre-wrap;
    max-height:200px; overflow-y:auto; line-height:1.5;
}
.timeline-month {
    font-size:13px; font-weight:700; color:#33475b; margin:20px 0 12px;
    padding-bottom:4px; border-bottom:1px solid #eaf0f6;
}
</style>
"""

# ─── UI ──────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Yetipay CRM", layout="wide")
st.markdown(HUBSPOT_CSS, unsafe_allow_html=True)
cfg = load_config()
df = load_crm()

# Initialise profile view state
if "view_lead_id" not in st.session_state:
    st.session_state["view_lead_id"] = None


def open_profile(lead_id):
    st.session_state["view_lead_id"] = str(lead_id)


def close_profile():
    st.session_state["view_lead_id"] = None


# ─── Global search bar ─────────────────────────────────────────────────────
gs1, gs2 = st.columns([3, 1])
global_search = gs1.text_input("🔍 Search leads by name, email, phone, or business", key="global_search", label_visibility="collapsed", placeholder="Search leads by name, email, phone, or business...")
if global_search and not df.empty:
    m = (df["business_name"].str.contains(global_search, case=False, na=False)
         | df["email"].str.contains(global_search, case=False, na=False)
         | df["contact_name"].str.contains(global_search, case=False, na=False)
         | df["phone"].str.contains(global_search, case=False, na=False))
    results = df[m].head(10)
    if results.empty:
        st.caption("No results")
    else:
        for _, r in results.iterrows():
            rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
            rc1.write(f"**{r['business_name']}** — {r['contact_name']}")
            rc2.write(r["email"])
            rc3.write(r["stage"])
            rc4.button("View", key=f"gs_{r['id']}", on_click=open_profile, args=(r["id"],))
        st.divider()

# KPI bar
won = len(df[df["stage"] == "Won"])
target = cfg["target"]
remaining = max(target - won, 0)
days_left = (date(date.today().year, date.today().month % 12 + 1, 1) - date.today()).days
in_pipe = len(df[~df["stage"].isin(["Won", "Lost"])])
pct = min(won / target, 1.0) if target else 0

st.markdown(f"""
<div class="kpi-bar">
    <div class="kpi-card"><div class="num">{won}</div><div class="label">Won</div><div class="sub">target {target}</div></div>
    <div class="kpi-card"><div class="num">{remaining}</div><div class="label">Remaining</div></div>
    <div class="kpi-card"><div class="num">{days_left}</div><div class="label">Days left</div></div>
    <div class="kpi-card"><div class="num">{in_pipe}</div><div class="label">Pipeline</div></div>
    <div class="kpi-card"><div class="num">{len(df)}</div><div class="label">Total leads</div></div>
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

    st.button("← Back to CRM", on_click=close_profile)

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
            new_notes = st.text_area("Notes", lead["notes"], height=80, key="pv_notes")
            if st.button("Save", type="primary", key="pv_save"):
                save_lead(lead_id, {
                    "contact_name": new_contact, "phone": new_phone, "email": new_email,
                    "stage": new_stage, "next_action_date": new_nad.isoformat() if new_nad else "",
                    "next_action": new_na, "notes": new_notes,
                })
                st.rerun()

    with main:
        act_tab = st.radio("", ["Email", "Call / Note", "Timeline"], horizontal=True, key="pv_action_tab")

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

        elif act_tab == "Timeline":
            timeline_items = []
            act_df = load_activity()
            lead_act = act_df[act_df["lead_id"] == str(lead_id)]
            for _, r in lead_act.iterrows():
                timeline_items.append({"timestamp": r["timestamp"], "type": r["type"], "title": r["subject"] or r["type"].title(), "body": r["content"], "source": "crm"})

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
                    dot_cls = {"email": "timeline-dot-email", "call": "timeline-dot-call", "note": "timeline-dot-note", "gmail": "timeline-dot-gmail"}.get(item["type"], "timeline-dot-note")
                    type_label = {"email": "Email logged", "call": "Call logged", "note": "Note", "gmail": "Gmail"}.get(item["type"], item["type"])
                    body_html = f'<div class="timeline-body">{esc(item["body"][:500])}</div>' if item["body"] else ""
                    st.markdown(f"""<div class="timeline-item">
                        <div class="timeline-dot {dot_cls}"></div>
                        <div class="timeline-date">{esc(date_label)} · {esc(type_label)}</div>
                        <div class="timeline-title">{esc(item['title'])}</div>
                        {body_html}
                    </div>""", unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

    st.stop()

# ─── Tabs (normal view) ───────────────────────────────────────────────────
tab_pipeline, tab_contacts, tab_lead, tab_today, tab_bulk, tab_sequences, tab_charts, tab_add, tab_import, tab_templates, tab_settings = st.tabs(
    ["Deals", "Contacts", "Lead detail", "Today", "Bulk email", "Sequences", "Dashboard", "Add lead", "Import", "Templates", "Settings"]
)

# ─── Pipeline (Kanban) ──────────────────────────────────────────────────────
STAGE_CLASSES = {
    "New": "stage-new", "Contacted": "stage-contacted", "Demo Booked": "stage-demo",
    "Proposal": "stage-proposal", "Won": "stage-won", "Lost": "stage-lost",
    "New Referral": "stage-new", "Referral Signed Up": "stage-won", "Reward Sent": "stage-demo",
}

with tab_pipeline:
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
            nd_nad = nc2.date_input("Next action date", value=None, key="nd_nad")
            nd_na = st.text_input("Next action", key="nd_na")
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
                        "pipeline": active_pipeline,
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
    view = view[view["pipeline"].isin([active_pipeline, ""])]
    if active_pipeline == "Sales":
        view = view[~view["stage"].isin(REFERRAL_STAGES)]
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
            st.markdown(
                f'<div class="kanban-header {cls}">{stage}<span class="count"> {len(stage_df)}</span></div>',
                unsafe_allow_html=True,
            )
            for _, row in stage_df.head(show_count).iterrows():
                touch = row["last_touch"] or "No activity"
                cat = esc(row["category"] or "")
                na = esc(row["next_action"] or "")
                na_line = f'<div class="meta">Next: {na}</div>' if na else ""
                cat_line = f'<span class="category-tag">{cat}</span>' if cat else ""
                st.markdown(
                    f'<div class="deal-card">'
                    f'<div class="biz">{esc(row["business_name"])}</div>'
                    f'<div class="contact">{esc(row["contact_name"])}</div>'
                    f'<div class="meta">{esc(touch)}</div>'
                    f'{na_line}{cat_line}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                bc1, bc2 = st.columns(2)
                bc1.button("View", key=f"k_{stage}_{row['id']}", on_click=open_profile, args=(row["id"],))
                other_stages = [s for s in active_stages if s != stage]
                new_s = bc2.selectbox("Move →", [stage] + other_stages, key=f"mv_{row['id']}", label_visibility="collapsed")
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
with tab_contacts:
    cf1, cf2, cf3, cf4 = st.columns([2, 1, 1, 1])
    c_search = cf1.text_input("Search name, phone, email", key="c_search")
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

    st.caption(f"{len(cview)} contacts")

    page_size = 50
    total_pages = max(1, (len(cview) + page_size - 1) // page_size)
    page = st.number_input("Page", 1, total_pages, 1, key="c_page")
    page_df = cview.iloc[(page-1)*page_size : page*page_size]

    for _, r in page_df.iterrows():
        pill_cls = PILL_MAP.get(r["stage"], "stage-pill-new")
        cc1, cc2, cc3, cc4, cc5 = st.columns([2, 2, 1, 1, 0.5])
        cc1.markdown(f"**{r['contact_name'] or '--'}**  \n{r['business_name']}")
        cc2.markdown(f"{r['email'] or '--'}  \n{r['phone'] or '--'}")
        cc3.markdown(f"<span class='contact-stage {pill_cls}'>{r['stage']}</span>", unsafe_allow_html=True)
        cc4.caption(f"{r['region'] or '--'} · {r['category'] or '--'}")
        cc5.button("View", key=f"ct_{r['id']}", on_click=open_profile, args=(r["id"],))

    st.caption(f"Page {page} of {total_pages}")

# ─── Lead detail (quick pick → profile view) ─────────────────────────────
with tab_lead:
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
with tab_today:
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
with tab_bulk:
    templates = load_templates()
    if df.empty:
        st.info("No leads. Import first.")
    else:
        st.subheader("Send template to multiple leads")
        bc1, bc2 = st.columns(2)
        tmpl_pick = bc1.selectbox("Template", list(templates.keys()), key="b_tmpl")
        stage_pick = bc2.multiselect("Filter by stage", STAGES, default=["New"], key="b_stage")
        region_pick = st.text_input("Region contains (optional)", key="b_region")

        pool = df[df["stage"].isin(stage_pick)] if stage_pick else df.copy()
        if region_pick:
            pool = pool[pool["region"].str.contains(region_pick, case=False, na=False)]
        pool = pool[pool["email"].str.contains("@", na=False)]
        pool = pool[~pool["email"].str.lower().str.startswith(
            ("info@", "hello@", "contact@", "enquiries@", "admin@", "sales@", "office@", "reception@", "bookings@")
        )]

        st.caption(f"{len(pool)} leads match filters (personal email only)")

        limit = st.slider("How many to send", 1, min(len(pool), 200) if len(pool) > 0 else 1,
                          min(50, len(pool)) if len(pool) > 0 else 1, key="b_limit")
        batch = pool.head(limit)

        if not batch.empty:
            sample_lead = batch.iloc[0].to_dict()
            prev_s, prev_b = render_template(templates[tmpl_pick], sample_lead)
            with st.expander("Preview (first lead)", expanded=True):
                st.markdown(f"**To:** {sample_lead['email']}")
                st.markdown(f"**Subject:** {prev_s}")
                st.text(prev_b)

        tracker = load_send_counts()
        remaining_today = sum(s["daily_cap"] - tracker["counts"].get(s["email"], 0) for s in SENDERS)
        st.markdown(f"**Sender rotation active** — {len(SENDERS)} inboxes, **{remaining_today}** sends left today")
        for s in SENDERS:
            used = tracker["counts"].get(s["email"], 0)
            left = s["daily_cap"] - used
            bar_pct = used / s["daily_cap"] if s["daily_cap"] > 0 else 0
            color = "🟢" if left > 20 else "🟡" if left > 5 else "🔴"
            st.caption(f"{color} {s['email']}: {used}/{s['daily_cap']} sent ({left} left)")

        if limit > remaining_today:
            st.error(f"Only {remaining_today} sends left across all inboxes today. Lower the limit or wait til tomorrow.")

        if st.button(f"Generate {limit} Gmail links", type="primary", key="b_send"):
            links = []
            skipped = 0
            for _, row in batch.iterrows():
                sender = pick_sender(tracker)
                if not sender:
                    skipped += 1
                    continue
                lead = row.to_dict()
                s, b = render_template(templates[tmpl_pick], lead)
                gm = gmail_link(lead["email"], s, b, sender_email=sender["email"])
                links.append({
                    "business": lead["business_name"], "email": lead["email"],
                    "link": gm, "sender": sender["email"],
                    "lead_id": lead["id"], "subject": s, "body": b,
                })
                tracker["counts"][sender["email"]] = tracker["counts"].get(sender["email"], 0) + 1
            st.session_state["bulk_links"] = links
            st.session_state["bulk_skipped"] = skipped
            save_send_counts(tracker)
            st.rerun()

        if st.session_state.get("bulk_links"):
            links = st.session_state["bulk_links"]
            skipped = st.session_state.get("bulk_skipped", 0)
            if skipped:
                st.warning(f"Skipped {skipped} — all inboxes hit daily cap.")
            st.info(f"{len(links)} compose links ready. Open them, send, then click **Confirm all sent** below.")
            rows_html = ""
            current_sender = None
            for lnk in links:
                if lnk["sender"] != current_sender:
                    current_sender = lnk["sender"]
                    rows_html += f'<tr><td colspan="2" style="padding:10px 0 4px;font-weight:bold;font-size:14px;">From: {html_mod.escape(current_sender)}</td></tr>'
                rows_html += (
                    f'<tr>'
                    f'<td style="padding:3px 8px 3px 0;"><a href="{html_mod.escape(lnk["link"])}" target="_blank" '
                    f'rel="noopener" style="color:#0d6efd;text-decoration:none;">✉ {html_mod.escape(lnk["business"])}</a></td>'
                    f'<td style="padding:3px 0;color:#666;">{html_mod.escape(lnk["email"])}</td>'
                    f'</tr>'
                )
            import streamlit.components.v1 as components
            components.html(
                f'<table style="font-family:sans-serif;font-size:13px;width:100%;">{rows_html}</table>',
                height=min(len(links) * 32 + 80, 800),
                scrolling=True,
            )
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Confirm all sent", type="primary", key="b_confirm"):
                for lnk in links:
                    log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                    updates = {"last_touch": date.today().isoformat()}
                    save_lead(lnk["lead_id"], updates)
                st.session_state["bulk_links"] = None
                st.success(f"Logged {len(links)} emails as sent.")
                st.rerun()
            if cc2.button("❌ Cancel (not sent)", key="b_cancel"):
                tracker = load_send_counts()
                for lnk in links:
                    tracker["counts"][lnk["sender"]] = max(0, tracker["counts"].get(lnk["sender"], 0) - 1)
                save_send_counts(tracker)
                st.session_state["bulk_links"] = None
                st.info("Cancelled. Send counts rolled back.")
                st.rerun()


# ─── Sequences ───────────────────────────────────────────────────────────────
with tab_sequences:
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


# ─── Dashboard charts ────────────────────────────────────────────────────────
with tab_charts:
    if df.empty:
        st.info("No data yet.")
    else:
        st.subheader("Pipeline funnel")
        stage_counts = df["stage"].value_counts().reindex(STAGES, fill_value=0)
        st.bar_chart(stage_counts)

        ch1, ch2 = st.columns(2)
        with ch1:
            st.subheader("Leads by region")
            region_counts = df["region"].value_counts().head(15)
            if not region_counts.empty:
                st.bar_chart(region_counts)

        with ch2:
            st.subheader("Leads by category")
            cat_counts = df["category"].value_counts().head(15)
            if not cat_counts.empty:
                st.bar_chart(cat_counts)

        st.subheader("Activity over time")
        act = load_activity()
        if not act.empty:
            act["date"] = pd.to_datetime(act["timestamp"]).dt.date
            daily = act.groupby(["date", "type"]).size().unstack(fill_value=0)
            st.line_chart(daily)
            st.caption("Emails + calls logged per day")
        else:
            st.caption("No activity logged yet. Start sending emails and logging calls.")

        st.subheader("Sequence progress")
        sq = load_seq_queue()
        if not sq.empty:
            done_ct = len(sq[sq["status"] == "done"])
            pend_ct = len(sq[sq["status"] == "pending"])
            sq_c1, sq_c2 = st.columns(2)
            sq_c1.metric("Steps completed", done_ct)
            sq_c2.metric("Steps pending", pend_ct)
            by_seq = sq.groupby(["sequence_name", "status"]).size().unstack(fill_value=0)
            st.bar_chart(by_seq)
        else:
            st.caption("No sequences enrolled yet.")


# ─── Add lead ────────────────────────────────────────────────────────────────
with tab_add:
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
with tab_import:
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
with tab_templates:
    templates = load_templates()
    names = list(templates.keys())

    tmpl_list, tmpl_editor = st.columns([1, 2])

    with tmpl_list:
        st.markdown("**Your templates**")
        pick = st.selectbox("", ["+ Create new template"] + names, key="t_pick", label_visibility="collapsed")
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
with tab_settings:
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
