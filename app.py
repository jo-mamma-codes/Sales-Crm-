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

# Support both .env (local) and st.secrets (Streamlit Cloud)
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

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


@st.cache_data(ttl=300, show_spinner=False)
def load_templates():
    if TEMPLATES.exists():
        return json.loads(TEMPLATES.read_text())
    TEMPLATES.write_text(json.dumps(DEFAULT_TEMPLATES, indent=2))
    return DEFAULT_TEMPLATES


def save_templates(t):
    TEMPLATES.write_text(json.dumps(t, indent=2))
    load_templates.clear()


@st.cache_data(ttl=60, show_spinner=False)
def load_activity():
    r = sb.table("activity").select("*").execute()
    if r.data:
        adf = pd.DataFrame(r.data).fillna("")
        for c in ACTIVITY_COLS:
            if c not in adf.columns:
                adf[c] = ""
        return adf
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
    load_activity.clear()


@st.cache_data(ttl=60, show_spinner=False)
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
        load_tasks.clear()
    except Exception:
        pass


def update_task(task_id, updates):
    try:
        sb.table("tasks").update(updates).eq("id", int(task_id)).execute()
        load_tasks.clear()
    except Exception:
        pass


# ─── Lead Scoring ────────────────────────────────────────────────────────────
def score_lead(lead, act_counts):
    """Score a lead 0-100 based on engagement. Returns (score, label, color).
    act_counts: dict of {lead_id_str: activity_count} (pre-computed for speed).
    """
    score = 0
    lid = str(lead.get("id", ""))

    # Stage points
    stage_pts = {"New": 5, "Contacted": 20, "Demo Booked": 50, "Proposal": 70, "Won": 100, "Lost": 0}
    score += stage_pts.get(lead.get("stage", ""), 0)

    # Activity count (from pre-computed dict)
    n_acts = act_counts.get(lid, 0)
    score += min(n_acts * 5, 20)

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
        api_key = os.environ.get("ANTHROPIC_API_KEY") or st.secrets.get("ANTHROPIC_API_KEY", "")
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

# Sender email signatures (plain text for Gmail compose URLs)
SIGNATURES = {
    "joseph.allison@yetipay.me": (
        "\n\n--\n"
        "Joseph Allison\n"
        "Senior Sales Manager\n"
        "joseph@yetipay.me\n"
        "www.yetipay.me\n"
        "17 St Anne's Court, London, W1F 0BQ"
    ),
    "dominic.ritchie@yetipay.me": (
        "\n\n--\n"
        "Dominic Ritchie\n"
        "Yetipay\n"
        "dominic.ritchie@yetipay.me\n"
        "www.yetipay.me\n"
        "17 St Anne's Court, London, W1F 0BQ"
    ),
    "insidesales@yetipay.me": (
        "\n\n--\n"
        "Yetipay Sales Team\n"
        "insidesales@yetipay.me\n"
        "www.yetipay.me\n"
        "17 St Anne's Court, London, W1F 0BQ"
    ),
    "ashley@yetipay.me": (
        "\n\n--\n"
        "Ashley\n"
        "Yetipay\n"
        "ashley@yetipay.me\n"
        "www.yetipay.me\n"
        "17 St Anne's Court, London, W1F 0BQ"
    ),
}
DEFAULT_SIGNATURE = SIGNATURES["joseph.allison@yetipay.me"]


def render_template(tmpl, lead, sender_email=None):
    """Replace {{token}} placeholders with CRM record values."""
    subj, body = tmpl["subject"], tmpl["body"]
    for token, (field, transform) in TOKENS.items():
        val = transform(lead.get(field, ""))
        subj = subj.replace(token, val)
        body = body.replace(token, val)
    if OPT_OUT_LINE.strip() not in body:
        body += OPT_OUT_LINE
    # Append sender signature (skip if body already has signature-like content)
    sig = SIGNATURES.get(sender_email, DEFAULT_SIGNATURE)
    _has_sig = any(marker in body.lower() for marker in ["st anne's court", "www.yetipay.me", "best regards"])
    if not _has_sig:
        body += sig
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


def send_via_resend(from_email, from_name, to_email, subject, body):
    """Send via Resend API. Returns (success: bool, message_id_or_error: str)."""
    api_key = os.environ.get("RESEND_API_KEY") or st.secrets.get("RESEND_API_KEY", "")
    if not api_key:
        return False, "RESEND_API_KEY not configured"
    try:
        import resend
        resend.api_key = api_key
        # Body sent as plain text — convert newlines to <br> for HTML version
        html_body = body.replace("\n", "<br>")
        params = {
            "from": f"{from_name} <{from_email}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
            "text": body,
        }
        r = resend.Emails.send(params)
        msg_id = r.get("id") if isinstance(r, dict) else str(r)
        return True, msg_id
    except Exception as e:
        return False, str(e)

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


def fetch_gmail_for_contact(contact_email):
    """Fetch sent & received emails for a contact via Gmail API and cache them."""
    try:
        from gmail_auth import get_gmail_service
        import base64
        service = get_gmail_service()
        if not service:
            return None

        threads_data = []
        # Search sent + received for this contact
        query = f"to:{contact_email} OR from:{contact_email}"
        results = service.users().messages().list(
            userId="me", q=query, maxResults=30
        ).execute()

        messages = results.get("messages", [])
        for msg_ref in messages:
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"]
                ).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                threads_data.append({
                    "messages": [{
                        "id": msg["id"],
                        "sender": headers.get("From", ""),
                        "to": headers.get("To", ""),
                        "subject": headers.get("Subject", ""),
                        "date": headers.get("Date", ""),
                        "snippet": msg.get("snippet", ""),
                    }]
                })
            except Exception:
                continue

        cache_gmail_threads(contact_email, threads_data)
        return len(threads_data)
    except Exception as e:
        return None


# ─── Region → County mapping ─────────────────────────────────────────────────
REGION_TO_COUNTY = {
    # Major cities
    "aberdeen": "Aberdeenshire", "bath": "Somerset", "belfast": "Northern Ireland",
    "birmingham": "West Midlands", "bournemouth": "Dorset", "brighton-and-hove": "East Sussex",
    "cambridge": "Cambridgeshire", "cardiff": "South Glamorgan", "cheltenham": "Gloucestershire",
    "city-of-bristol": "Bristol", "coventry": "West Midlands", "derby": "Derbyshire",
    "dudley": "West Midlands", "dundee": "Angus", "edinburgh": "Lothian",
    "exeter": "Devon", "glasgow": "Lanarkshire", "gloucester": "Gloucestershire",
    "hull": "East Yorkshire", "leicester": "Leicestershire", "liverpool": "Merseyside",
    "manchester": "Greater Manchester", "milton-keynes": "Buckinghamshire", "york": "North Yorkshire",
    # Cornwall towns
    "newquay": "Cornwall", "bodmin": "Cornwall", "truro": "Cornwall", "padstow": "Cornwall",
    "st-ives": "Cornwall", "falmouth": "Cornwall", "penzance": "Cornwall", "bude": "Cornwall",
    "launceston": "Cornwall", "liskeard": "Cornwall", "helston": "Cornwall", "redruth": "Cornwall",
    "camborne": "Cornwall", "wadebridge": "Cornwall", "looe": "Cornwall", "fowey": "Cornwall",
    "st-austell": "Cornwall", "saltash": "Cornwall", "torpoint": "Cornwall", "hayle": "Cornwall",
    "perranporth": "Cornwall", "mevagissey": "Cornwall", "porthleven": "Cornwall",
    "st-just": "Cornwall", "marazion": "Cornwall", "mousehole": "Cornwall",
    # Devon towns
    "plymouth": "Devon", "torquay": "Devon", "paignton": "Devon", "barnstaple": "Devon",
    "tiverton": "Devon", "dartmouth": "Devon", "totnes": "Devon", "sidmouth": "Devon",
    # Somerset towns
    "taunton": "Somerset", "wells": "Somerset", "glastonbury": "Somerset", "frome": "Somerset",
    # Dorset towns
    "poole": "Dorset", "weymouth": "Dorset", "dorchester": "Dorset",
}


def get_county(region):
    """Map a region/town to its county. Falls back to title-cased region."""
    if not region:
        return "Unknown"
    return REGION_TO_COUNTY.get(region.lower().strip(), region.replace("-", " ").title())


CATEGORY_TO_INDUSTRY = {
    # Restaurants & Food Service
    "american_restaurant": "Restaurants & Food", "asian_grocery_store": "Restaurants & Food",
    "bakery": "Restaurants & Food", "bistro": "Restaurants & Food", "breakfast_restaurant": "Restaurants & Food",
    "british_restaurant": "Restaurants & Food", "cafe": "Restaurants & Food", "cake_shop": "Restaurants & Food",
    "catering_service": "Restaurants & Food", "chocolate_factory": "Restaurants & Food",
    "chocolate_shop": "Restaurants & Food", "coffee_roastery": "Restaurants & Food",
    "coffee_shop": "Restaurants & Food", "confectionery": "Restaurants & Food", "deli": "Restaurants & Food",
    "food": "Restaurants & Food", "food_store": "Restaurants & Food",
    "hamburger_restaurant": "Restaurants & Food", "ice_cream_shop": "Restaurants & Food",
    "indian_restaurant": "Restaurants & Food", "italian_restaurant": "Restaurants & Food",
    "japanese_restaurant": "Restaurants & Food", "mexican_restaurant": "Restaurants & Food",
    "pastry_shop": "Restaurants & Food", "pizza_restaurant": "Restaurants & Food",
    "restaurant": "Restaurants & Food", "seafood_restaurant": "Restaurants & Food",
    "sri_lankan_restaurant": "Restaurants & Food", "steak_house": "Restaurants & Food",
    "thai_restaurant": "Restaurants & Food", "tea_store": "Restaurants & Food",
    # Bars & Nightlife
    "bar": "Bars & Nightlife", "brewery": "Bars & Nightlife", "brewpub": "Bars & Nightlife",
    "gastropub": "Bars & Nightlife", "liquor_store": "Bars & Nightlife", "night_club": "Bars & Nightlife",
    "pub": "Bars & Nightlife", "sports_bar": "Bars & Nightlife", "wine_bar": "Bars & Nightlife",
    # Beauty & Wellness
    "barber_shop": "Beauty & Wellness", "beautician": "Beauty & Wellness", "beauty_salon": "Beauty & Wellness",
    "body_art_service": "Beauty & Wellness", "hair_care": "Beauty & Wellness", "hair_salon": "Beauty & Wellness",
    "massage": "Beauty & Wellness", "nail_salon": "Beauty & Wellness", "tanning_studio": "Beauty & Wellness",
    "wellness_center": "Beauty & Wellness", "yoga_studio": "Beauty & Wellness",
    # Health & Fitness
    "chiropractor": "Health & Fitness", "fitness_center": "Health & Fitness", "gym": "Health & Fitness",
    "health": "Health & Fitness", "medical_center": "Health & Fitness", "medical_clinic": "Health & Fitness",
    "physiotherapist": "Health & Fitness", "sports_complex": "Health & Fitness",
    "sports_school": "Health & Fitness",
    # Retail & Shopping
    "auto_parts_store": "Retail & Shopping", "bicycle_store": "Retail & Shopping",
    "book_store": "Retail & Shopping", "building_materials_store": "Retail & Shopping",
    "butcher_shop": "Retail & Shopping", "clothing_store": "Retail & Shopping",
    "department_store": "Retail & Shopping", "electronics_store": "Retail & Shopping",
    "florist": "Retail & Shopping", "furniture_store": "Retail & Shopping",
    "garden_center": "Retail & Shopping", "gift_shop": "Retail & Shopping",
    "grocery_store": "Retail & Shopping", "home_goods_store": "Retail & Shopping",
    "home_improvement_store": "Retail & Shopping", "jewelry_store": "Retail & Shopping",
    "market": "Retail & Shopping", "pet_store": "Retail & Shopping",
    "shoe_store": "Retail & Shopping", "sporting_goods_store": "Retail & Shopping",
    "sportswear_store": "Retail & Shopping", "store": "Retail & Shopping",
    "supermarket": "Retail & Shopping", "thrift_store": "Retail & Shopping",
    "toy_store": "Retail & Shopping", "womens_clothing_store": "Retail & Shopping",
    # Hospitality & Events
    "event_venue": "Hospitality & Events", "hotel": "Hospitality & Events",
    "lodging": "Hospitality & Events", "wedding_venue": "Hospitality & Events",
    "tour_agency": "Hospitality & Events", "tourist_attraction": "Hospitality & Events",
    "visitor_center": "Hospitality & Events",
    # Arts & Entertainment
    "art_gallery": "Arts & Entertainment", "art_studio": "Arts & Entertainment",
    "performing_arts_theater": "Arts & Entertainment",
    # Professional Services
    "consultant": "Professional Services", "corporate_office": "Professional Services",
    "general_contractor": "Professional Services", "laundry": "Professional Services",
    "manufacturer": "Professional Services", "painter": "Professional Services",
    "supplier": "Professional Services", "tailor": "Professional Services",
    "wholesaler": "Professional Services", "storage": "Professional Services",
    "service": "Professional Services",
    # Education & Community
    "child_care_agency": "Education & Community", "community_center": "Education & Community",
    "local_government_office": "Education & Community", "non_profit_organization": "Education & Community",
    "research_institute": "Education & Community", "school": "Education & Community",
    "university": "Education & Community",
    # Pets
    "pet_boarding_service": "Pets", "pet_store": "Pets",
    # Other
    "apartment_building": "Other", "establishment": "Other", "farm": "Other",
    "point_of_interest": "Other",
}

def get_industry(category):
    if not category:
        return "Other"
    return CATEGORY_TO_INDUSTRY.get(category.lower().strip(), category.replace("_", " ").title())


STAGES = ["New", "Contacted", "Demo Booked", "Proposal", "Won", "Lost"]
REFERRAL_STAGES = ["New Referral", "Contacted", "Referral Signed Up", "Reward Sent"]
POS_STAGES = ["New", "Onboarding", "Training", "Live", "Churned"]

DEFAULT_PIPELINES = {
    "Sales": STAGES,
    "POS Customers": POS_STAGES,
    "Referral": REFERRAL_STAGES,
}

PIPELINES_FILE = ROOT / "pipelines.json"

def load_pipelines():
    if PIPELINES_FILE.exists():
        return json.loads(PIPELINES_FILE.read_text())
    return DEFAULT_PIPELINES

def save_pipelines(p):
    PIPELINES_FILE.write_text(json.dumps(p, indent=2))

PIPELINES = load_pipelines()  # initial load; also reloaded on rerun since module re-executes
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


@st.cache_data(ttl=30, show_spinner="Loading leads...")
def load_crm():
    import time
    all_data = []
    page_size = 1000
    offset = 0
    max_retries = 3
    while True:
        for attempt in range(max_retries):
            try:
                r = sb.table("leads").select("*").range(offset, offset + page_size - 1).execute()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                else:
                    raise e
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
    """Save specific fields for one lead. Updates local cache instantly, writes to Supabase async."""
    for k, v in updates.items():
        if v == "":
            updates[k] = None
    sb.table("leads").update(updates).eq("id", int(lead_id)).execute()
    # Update local cached df instantly instead of full reload
    _cached_df = st.session_state.get("_df_cache")
    if _cached_df is not None:
        mask = _cached_df["id"] == str(lead_id)
        for k, v in updates.items():
            if k in _cached_df.columns:
                _cached_df.loc[mask, k] = str(v) if v is not None and k != "deal_value" else v
        st.session_state["_df_cache"] = _cached_df
    else:
        load_crm.clear()
        st.session_state.pop("_df_cache", None)
    if "stage" in updates:
        st.session_state["_score_stale"] = True


def next_id(df):
    if df.empty:
        return "1"
    return str(df["id"].astype(int).max() + 1)


def import_leads(df, src_path, region_filter=None, limit=None, pipeline="Sales", default_stage="New", import_name=None):
    if not src_path.exists():
        return df, 0, {"total": 0, "skipped_generic_email": 0, "skipped_no_email": 0, "skipped_blocked": 0, "skipped_existing": 0, "inserted": 0}
    src = pd.read_csv(src_path, dtype=str).fillna("")
    stats = {"total": len(src), "skipped_generic_email": 0, "skipped_no_email": 0, "skipped_blocked": 0, "skipped_existing": 0, "inserted": 0}
    if region_filter and region_filter != "All":
        src = src[src["region"].str.contains(region_filter, case=False, na=False)]
    # only personal emails — drop generic inboxes
    generic = ("info@", "hello@", "contact@", "enquiries@", "admin@", "sales@", "office@", "reception@", "bookings@")
    if "email" in src.columns:
        before = len(src)
        src = src[~src["email"].str.lower().str.startswith(generic)]
        stats["skipped_generic_email"] = before - len(src)
        before = len(src)
        src = src[src["email"].str.contains("@", na=False)]
        stats["skipped_no_email"] = before - len(src)
    # block bounced + GDPR never-contact emails
    blocked_file = ROOT / "bounced_emails.json"
    if blocked_file.exists():
        blocked = json.loads(blocked_file.read_text())
        blocked_emails = set(e.lower() for e in blocked.get("bounced", []) + blocked.get("never_contact", []))
        if "email" in src.columns:
            before = len(src)
            src = src[~src["email"].str.lower().isin(blocked_emails)]
            stats["skipped_blocked"] = before - len(src)
    existing = set(df["business_name"].str.lower()) if not df.empty else set()
    before = len(src)
    src = src[~src["business_name"].str.lower().isin(existing)]
    stats["skipped_existing"] = before - len(src)
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
            "source": (import_name or r.get("source", "ready_for_outreach")) or None,
            "created": date.today().isoformat(),
            "pipeline": pipeline,
            "deal_value": 0,
        })
        nid += 1
    stats["inserted"] = len(rows)
    if rows:
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            sb.table("leads").insert(rows[i:i+batch_size]).execute()
        load_crm.clear()
        st.session_state.pop("_df_cache", None)
        df = load_crm()
    return df, len(rows), stats


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

/* ── Buttons (base — overridden by compact section below) ── */
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

/* ── Profile page — HubSpot-inspired ── */
.profile-card {
    background: #fff; border: 1px solid #e2e4e9; border-radius: 12px;
    padding: 24px 20px; text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.profile-avatar {
    width: 72px; height: 72px; border-radius: 50%; background: linear-gradient(135deg, #7c3aed, #a78bfa);
    color: #fff; font-size: 24px; font-weight: 700; line-height: 72px;
    margin: 0 auto 12px; text-transform: uppercase; letter-spacing: 1px;
}
.profile-name { font-size: 18px; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }
.profile-role { font-size: 13px; color: #64748b; margin-bottom: 2px; font-weight: 500; }
.profile-email { font-size: 12px; color: #94a3b8; margin-bottom: 14px; }
.profile-actions { display: flex; justify-content: center; gap: 12px; margin: 14px 0 4px; flex-wrap: wrap; }
.profile-action-item { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.profile-action-btn {
    width: 40px; height: 40px; border-radius: 50%; border: 1.5px solid #e2e4e9;
    background: #fff; color: #64748b; font-size: 15px; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
    text-decoration: none; transition: all 0.15s;
}
.profile-action-btn:hover { background: #faf5ff; border-color: #7c3aed; color: #7c3aed; transform: translateY(-1px); }
.profile-action-label { font-size: 10px; color: #94a3b8; font-weight: 500; }

/* About section */
.profile-section { text-align: left; background: #fff; border: 1px solid #e2e4e9; border-radius: 12px; padding: 16px 20px; margin-top: 12px; }
.profile-section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #f1f5f9; }
.profile-section-title { font-size: 13px; font-weight: 700; color: #1a1a2e; text-transform: uppercase; letter-spacing: 0.3px; }
.profile-section-action { font-size: 11px; color: #7c3aed; cursor: pointer; font-weight: 500; }
.profile-field { margin-bottom: 14px; display: flex; flex-direction: column; }
.profile-field-label { font-size: 11px; color: #94a3b8; margin-bottom: 3px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; }
.profile-field-value { font-size: 13px; color: #1a1a2e; font-weight: 500; }

/* Right sidebar cards */
.profile-sidebar-card {
    background: #fff; border: 1px solid #e2e4e9; border-radius: 12px;
    padding: 16px 18px; margin-bottom: 12px;
}
.profile-sidebar-card h4 {
    font-size: 13px; font-weight: 700; color: #1a1a2e; margin: 0 0 12px 0;
    text-transform: uppercase; letter-spacing: 0.3px;
    padding-bottom: 8px; border-bottom: 1px solid #f1f5f9;
}
.profile-sidebar-card .sidebar-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 0; font-size: 12px;
}
.profile-sidebar-card .sidebar-row .label { color: #94a3b8; font-weight: 500; }
.profile-sidebar-card .sidebar-row .value { color: #1a1a2e; font-weight: 600; }
.profile-sidebar-card .sidebar-company {
    display: flex; align-items: center; gap: 10px; padding: 8px 0;
}
.profile-sidebar-card .sidebar-company .company-icon {
    width: 36px; height: 36px; border-radius: 8px; background: #f1f0ff;
    color: #7c3aed; font-size: 14px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}
.profile-sidebar-card .sidebar-company .company-info { font-size: 13px; }
.profile-sidebar-card .sidebar-company .company-name { font-weight: 600; color: #1a1a2e; }
.profile-sidebar-card .sidebar-company .company-detail { font-size: 11px; color: #94a3b8; }

/* Center activity panel */
.profile-activity-header {
    background: #fff; border: 1px solid #e2e4e9; border-radius: 12px;
    padding: 12px 16px; margin-bottom: 12px;
}

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
/* Kanban card arrow buttons — compact */
[data-testid="stContainer"] [data-testid="stColumns"] .stButton > button {
    min-height: 28px !important; padding: 2px 8px !important; font-size: 12px !important;
}
/* Sidebar card link buttons */
.profile-sidebar-card + div .stButton > button {
    background: none !important; border: none !important; box-shadow: none !important;
    color: #3b82f6 !important; font-weight: 600 !important; font-size: 13px !important;
    text-align: left !important; padding: 0 !important; margin: -8px 0 8px !important;
    cursor: pointer !important;
}
.profile-sidebar-card + div .stButton > button:hover {
    color: #7c3aed !important; text-decoration: underline !important;
}
/* Card buttons — ultra compact (override in later block) */
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

/* ── BUTTONS - clear, visible, professional ── */
.stButton > button {
    border-radius: 8px !important; font-weight: 600 !important;
    font-size: 13px !important; padding: 8px 18px !important;
    border: 1.5px solid #d4d4d8 !important; background: #fff !important;
    color: #1a1a2e !important; font-family: 'Inter', sans-serif !important;
    cursor: pointer !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
    transition: all 0.15s ease !important;
}
.stButton > button:hover {
    border-color: #7c3aed !important; color: #7c3aed !important;
    background: #faf5ff !important; box-shadow: 0 2px 6px rgba(124,58,237,0.12) !important;
    transform: translateY(-1px);
}
.stButton > button:active { transform: scale(0.97) translateY(0); }
.stButton > button[kind="primary"], button[data-testid="stFormSubmitButton"] {
    background: linear-gradient(135deg, #7c3aed, #6d28d9) !important;
    color: #fff !important; border-color: #7c3aed !important;
    box-shadow: 0 2px 8px rgba(124,58,237,0.25) !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #6d28d9, #5b21b6) !important;
    box-shadow: 0 4px 12px rgba(124,58,237,0.35) !important;
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

/* ── COMMAND PALETTE ── */
.cmd-overlay {
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); backdrop-filter: blur(4px);
    z-index: 99999; justify-content: center; padding-top: 15vh;
    animation: fadeIn 0.1s ease-out;
}
.cmd-overlay.active { display: flex; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.cmd-palette {
    background: #fff; border-radius: 14px; width: 560px; max-height: 420px;
    box-shadow: 0 24px 80px rgba(0,0,0,0.25); overflow: hidden;
    animation: cmdSlideIn 0.15s ease-out;
}
@keyframes cmdSlideIn { from { opacity: 0; transform: translateY(-12px) scale(0.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
.cmd-input-wrap {
    display: flex; align-items: center; padding: 14px 20px;
    border-bottom: 1px solid #e2e4e9; gap: 10px;
}
.cmd-input-wrap .cmd-icon { color: #94a3b8; font-size: 18px; }
.cmd-input {
    flex: 1; border: none !important; outline: none !important;
    font-size: 15px !important; font-family: 'Inter', sans-serif !important;
    color: #1a1a2e !important; background: transparent !important;
    box-shadow: none !important; padding: 0 !important;
}
.cmd-input::placeholder { color: #c4c4d4; }
.cmd-hint { font-size: 11px; color: #c4c4d4; background: #f1f5f9; padding: 2px 8px; border-radius: 4px; }
.cmd-results { max-height: 320px; overflow-y: auto; padding: 8px; }
.cmd-item {
    display: flex; align-items: center; padding: 10px 14px; border-radius: 8px;
    cursor: pointer; gap: 12px; transition: background 0.1s;
}
.cmd-item:hover, .cmd-item.selected { background: #f5f3ff; }
.cmd-item-icon { font-size: 16px; width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; background: #f1f5f9; }
.cmd-item-text { flex: 1; }
.cmd-item-title { font-size: 13px; font-weight: 500; color: #1a1a2e; }
.cmd-item-sub { font-size: 11px; color: #94a3b8; }
.cmd-item-badge { font-size: 10px; padding: 2px 8px; border-radius: 6px; font-weight: 500; }
.cmd-empty { padding: 32px; text-align: center; color: #94a3b8; font-size: 13px; }
.cmd-footer {
    border-top: 1px solid #e2e4e9; padding: 8px 16px; display: flex;
    gap: 16px; background: #f8f9fb; border-radius: 0 0 14px 14px;
}
.cmd-footer-item { font-size: 10px; color: #94a3b8; display: flex; align-items: center; gap: 4px; }
.cmd-footer-item kbd {
    background: #e2e4e9; padding: 1px 5px; border-radius: 3px; font-size: 10px;
    font-family: monospace; color: #64748b;
}

/* ── KEYBOARD SHORTCUT HINTS ── */
.kbd-hint {
    position: fixed; bottom: 16px; right: 16px; z-index: 9998;
    background: #1a1a2e; color: #a5a5c0; padding: 8px 14px; border-radius: 8px;
    font-size: 11px; font-family: 'Inter', sans-serif;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2); display: flex; gap: 16px;
}
.kbd-hint kbd { background: #2d2d4a; padding: 1px 6px; border-radius: 3px; color: #fff; font-family: monospace; }
</style>
"""

# ─── UI ──────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Yetipay CRM", layout="wide", page_icon="💳", initial_sidebar_state="expanded")
st.markdown(HUBSPOT_CSS, unsafe_allow_html=True)
# Command palette JS can't run in st.markdown (Streamlit strips <script> tags)
# Using keyboard hint CSS only — the global search bar at top serves as command palette

# ─── Sidebar Navigation (must be before header that uses nav_choice) ─────────
NAV_ITEMS = ["📊 Pipeline", "👥 Contacts", "📅 Today", "✉️ Outreach", "🔄 Follow Up",
             "✅ Tasks", "🔗 Sequences", "📈 Reports", "➕ Add Lead", "📥 Import", "📝 Templates", "⚙️ Settings"]

# Restore nav from URL query param on refresh
_qp = st.query_params
_default_nav_idx = 0
if "page" in _qp:
    _page_from_url = _qp["page"]
    for _i, _item in enumerate(NAV_ITEMS):
        if _page_from_url in _item.lower() or _page_from_url == _item:
            _default_nav_idx = _i
            break

with st.sidebar:
    st.markdown(f"""<div style="padding:8px 16px 20px;border-bottom:1px solid #2d2d4a;margin-bottom:12px;">
        <div style="font-size:20px;font-weight:700;color:#fff;letter-spacing:-0.5px;">JOE'S <span style="color:#a78bfa;">CRM</span></div>
        <div style="font-size:11px;color:#64648a;margin-top:2px;">{date.today().strftime('%A, %d %b %Y')}</div>
    </div>""", unsafe_allow_html=True)
    def _nav_changed():
        st.session_state["view_lead_id"] = None
    nav_choice = st.radio("Navigation", NAV_ITEMS, index=_default_nav_idx, key="nav", label_visibility="collapsed", on_change=_nav_changed)

# Sync nav choice back to URL so refresh preserves page
st.query_params["page"] = nav_choice

# Header — thin top bar for context
st.markdown(f"""<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0 12px;border-bottom:1px solid #e2e4e9;margin-bottom:16px;">
    <div style="font-size:16px;font-weight:700;color:#1a1a2e;">{nav_choice}</div>
    <div style="font-size:12px;color:#94a3b8;">{date.today().strftime('%A, %d %B %Y')}</div>
</div>""", unsafe_allow_html=True)
cfg = load_config()
# Use local cache if available (avoids full Supabase re-fetch on every rerun)
# Auto-refresh every 120s to stay in sync with DB
import time as _time
_cache_age = _time.time() - st.session_state.get("_df_cache_ts", 0)
# 30s TTL — short enough that updates appear quickly, long enough to skip re-fetch on every interaction
if "_df_cache" in st.session_state and _cache_age < 30:
    df = st.session_state["_df_cache"]
else:
    df = load_crm()
    st.session_state["_df_cache"] = df
    st.session_state["_df_cache_ts"] = _time.time()

# Pre-compute lead scores and duplicates (cached per session)
if "lead_scores" not in st.session_state or st.session_state.get("_score_stale", True):
    _act_for_scoring = load_activity()
    # Pre-compute activity counts per lead (O(n) instead of O(n*m))
    _act_counts = {}
    if not _act_for_scoring.empty and "lead_id" in _act_for_scoring.columns:
        _act_counts = _act_for_scoring.groupby("lead_id").size().to_dict()
    _scores = {}
    for _, _row in df.iterrows():
        _scores[str(_row["id"])] = score_lead(_row.to_dict(), _act_counts)
    st.session_state["lead_scores"] = _scores
    st.session_state["_score_stale"] = False

if "duplicates" not in st.session_state or st.session_state.get("_dupes_stale", True):
    st.session_state["duplicates"] = detect_duplicates(df)
    st.session_state["_dupes_stale"] = False

LEAD_SCORES = st.session_state["lead_scores"]
DUPLICATES = st.session_state["duplicates"]

# Refresh button in sidebar
with st.sidebar:
    if st.button("🔄 Refresh data", key="refresh_cache", use_container_width=True):
        load_crm.clear()
        load_activity.clear()
        load_tasks.clear()
        load_templates.clear()
        st.session_state.pop("_df_cache", None)
        st.session_state.pop("_df_cache_ts", None)
        st.session_state["_score_stale"] = True
        st.session_state["_dupes_stale"] = True
        st.rerun()

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
_next_month_year = date.today().year + (1 if date.today().month == 12 else 0)
days_left = (date(_next_month_year, date.today().month % 12 + 1, 1) - date.today()).days
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

    # ── Precompute common values ──
    initials = "".join(w[0] for w in (lead["contact_name"] or "?").split()[:2]).upper() or "?"
    pill_cls = PILL_MAP.get(lead["stage"], "stage-pill-new")
    name_parts = (lead["contact_name"] or "").split()
    first_name = name_parts[0] if name_parts else "--"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "--"
    _lead_county = get_county(lead.get("region"))
    _lead_industry = get_industry(lead.get("category"))
    _deal_val = lead.get("deal_value", 0) or 0
    _score, _score_label, _score_color = LEAD_SCORES.get(lead_id, (0, "🧊 Cold", "#94a3b8"))
    _biz_initials = "".join(w[0] for w in (lead["business_name"] or "?").split()[:2]).upper()
    lead_pipeline = lead.get("pipeline") or "Sales"
    lead_stages = PIPELINES.get(lead_pipeline, STAGES)
    phone_href = f'<a href="tel:{esc(lead["phone"])}" style="color:#3b82f6;text-decoration:none;">{esc(lead["phone"])}</a>' if lead["phone"] else '--'
    email_href = f'<a href="mailto:{esc(lead["email"])}" style="color:#3b82f6;text-decoration:none;">{esc(lead["email"])}</a>' if lead["email"] else '--'

    # ── Top bar: breadcrumb + deal name + stage + actions ──
    _hdr1, _hdr2 = st.columns([4, 2])
    with _hdr1:
        st.button("← Back", on_click=close_profile, key="pv_back")
        st.markdown(f"""<div style="padding:4px 0 12px;">
            <div style="font-size:12px;color:#94a3b8;margin-bottom:4px;">
                Pipeline › {esc(lead_pipeline)} › {esc(lead['stage'])}
            </div>
            <div style="font-size:24px;font-weight:800;color:#1a1a2e;letter-spacing:-0.5px;">{esc(lead['business_name'])}</div>
            <div style="font-size:13px;color:#64748b;margin-top:2px;">{esc(lead['contact_name'] or 'No contact')} · {esc(_lead_industry)} · {esc(_lead_county)}</div>
        </div>""", unsafe_allow_html=True)
    with _hdr2:
        st.markdown("<div style='height:36px'></div>", unsafe_allow_html=True)
        _hc1, _hc2, _hc3 = st.columns(3)
        _stage_idx = lead_stages.index(lead["stage"]) if lead["stage"] in lead_stages else 0
        _new_stage = _hc1.selectbox("Stage", lead_stages, index=_stage_idx, key="pv_stage_quick", label_visibility="collapsed")
        if _new_stage != lead["stage"]:
            save_lead(lead_id, {"stage": _new_stage})
            st.rerun()
        st.markdown(f"""<div style="display:flex;gap:8px;align-items:center;justify-content:flex-end;">
            <span style="font-size:24px;font-weight:800;color:#059669;">£{_deal_val:,.0f}</span>
            <span style="color:{_score_color};font-size:12px;font-weight:600;background:{_score_color}15;padding:3px 8px;border-radius:6px;">{_score_label}</span>
        </div>""", unsafe_allow_html=True)

    # ── Stage progress bar ──
    _stage_dots = ""
    for si, sname in enumerate(lead_stages):
        _active = "background:#7c3aed;color:#fff;border-color:#7c3aed;" if sname == lead["stage"] else ("background:#e8f5e9;color:#059669;border-color:#059669;" if si < _stage_idx else "background:#f8f9fb;color:#94a3b8;border-color:#e2e4e9;")
        _stage_dots += f'<span style="padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;border:1.5px solid;{_active}">{esc(sname)}</span>'
    st.markdown(f'<div style="display:flex;gap:4px;flex-wrap:wrap;padding:8px 0 16px;border-bottom:1px solid #e2e4e9;">{_stage_dots}</div>', unsafe_allow_html=True)

    # ── 2 column layout: main content | properties sidebar ──
    main_col, props_col = st.columns([2.5, 1.5])

    # ━━━ RIGHT: Properties sidebar ━━━
    with props_col:
        # Deal Properties
        st.markdown(f"""<div class="profile-sidebar-card">
            <h4>Deal Properties</h4>
            <div class="sidebar-row"><span class="label">Deal Value</span><span class="value" style="color:#059669;">£{_deal_val:,.0f}</span></div>
            <div class="sidebar-row"><span class="label">Pipeline</span><span class="value">{esc(lead_pipeline)}</span></div>
            <div class="sidebar-row"><span class="label">Stage</span><span class="value"><span class="contact-stage {pill_cls}">{esc(lead['stage'])}</span></span></div>
            <div class="sidebar-row"><span class="label">Lead Score</span><span class="value" style="color:{_score_color};">{_score} — {_score_label}</span></div>
            <div class="sidebar-row"><span class="label">Next Action</span><span class="value">{esc(lead.get('next_action') or '--')}</span></div>
            <div class="sidebar-row"><span class="label">Next Date</span><span class="value">{esc(lead.get('next_action_date') or '--')}</span></div>
            <div class="sidebar-row"><span class="label">Last Touch</span><span class="value">{esc(lead['last_touch'] or 'Never')}</span></div>
            <div class="sidebar-row"><span class="label">Created</span><span class="value">{esc(lead['created'] or '--')}</span></div>
            <div class="sidebar-row"><span class="label">Source</span><span class="value">{esc(lead['source'] or '--')}</span></div>
        </div>""", unsafe_allow_html=True)

        # Contact card — clickable name
        st.markdown(f"""<div class="profile-sidebar-card">
            <h4>Contact</h4>
            <div class="sidebar-company">
                <div class="company-icon" style="border-radius:50%;">{esc(initials)}</div>
                <div class="company-info">
                    <div class="company-detail">{esc(lead['business_name'])}</div>
                </div>
            </div>
            <div class="sidebar-row"><span class="label">Email</span><span class="value">{email_href}</span></div>
            <div class="sidebar-row"><span class="label">Phone</span><span class="value">{phone_href}</span></div>
        </div>""", unsafe_allow_html=True)
        # Contact name as clickable button (styled as link)
        st.button(f"👤 {lead['contact_name'] or 'Unknown'}", key="pv_contact_link", on_click=open_profile, args=(lead_id,))

        # Company card — clickable name shows all deals for this company
        _company_deals = df[df["business_name"].str.lower() == lead["business_name"].lower()] if lead["business_name"] else pd.DataFrame()
        _n_company_deals = len(_company_deals)
        st.markdown(f"""<div class="profile-sidebar-card">
            <h4>Company</h4>
            <div class="sidebar-company">
                <div class="company-icon">{esc(_biz_initials)}</div>
                <div class="company-info">
                    <div class="company-detail">{esc(_lead_industry)} · {esc(_lead_county)}</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)
        # Company name button — opens company view
        st.button(f"🏢 {lead['business_name']} ({_n_company_deals} deal{'s' if _n_company_deals != 1 else ''})", key="pv_company_link", on_click=lambda: st.session_state.update({"view_company": lead["business_name"]}))
        # Show other deals from same company
        if _n_company_deals > 1:
            st.caption(f"Other deals at {lead['business_name']}:")
            for _, _cd in _company_deals.iterrows():
                if str(_cd["id"]) != lead_id:
                    st.button(f"→ {_cd['contact_name']} · {_cd['stage']}", key=f"pv_cd_{_cd['id']}", on_click=open_profile, args=(_cd["id"],))

        # Notes card
        _notes_preview = (lead.get("notes") or "--")[:300]
        st.markdown(f"""<div class="profile-sidebar-card">
            <h4>Notes</h4>
            <div style="font-size:12px;color:#64748b;white-space:pre-wrap;line-height:1.5;">{esc(_notes_preview)}</div>
        </div>""", unsafe_allow_html=True)

        # Edit deal
        with st.expander("✏️ Edit Deal"):
            new_contact = st.text_input("Contact name", lead["contact_name"], key="pv_contact")
            new_phone = st.text_input("Phone", lead["phone"], key="pv_phone")
            new_email = st.text_input("Email", lead["email"], key="pv_email")
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
            if st.button("Save Changes", type="primary", key="pv_save", use_container_width=True):
                save_lead(lead_id, {
                    "contact_name": new_contact, "phone": new_phone, "email": new_email,
                    "stage": new_stage, "next_action_date": new_nad.isoformat() if new_nad else "",
                    "next_action": new_na, "notes": new_notes, "deal_value": new_deal_value,
                })
                st.rerun()

        # Delete deal — confirmation modal
        with st.expander("🗑️ Delete Deal"):
            st.warning("This permanently deletes this deal and all associated data.")
            _confirm_name = st.text_input("Type the business name to confirm:", key="pv_del_confirm", placeholder=lead["business_name"])
            if st.button("Delete permanently", type="primary", key="pv_del_btn", use_container_width=True):
                if _confirm_name.strip().lower() == lead["business_name"].strip().lower():
                    sb.table("leads").delete().eq("id", lead_id).execute()
                    load_crm.clear()
                    st.session_state.pop("_df_cache", None)
                    close_profile()
                    st.rerun()
                else:
                    st.error("Business name doesn't match. Type it exactly to confirm.")

    # ━━━ LEFT: Activity tabs ━━━
    with main_col:
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
                st.markdown("""<div style="text-align:center;padding:20px 12px;">
                    <div style="font-size:28px;margin-bottom:6px;">📝</div>
                    <div style="font-size:12px;color:#94a3b8;">No tasks yet. Add one above.</div>
                </div>""", unsafe_allow_html=True)

        elif act_tab == "Timeline":
            if lead["email"] and st.button("🔄 Refresh from Gmail", key="pv_gmail_refresh"):
                with st.spinner("Fetching emails..."):
                    count = fetch_gmail_for_contact(lead["email"])
                if count is not None:
                    st.success(f"Found {count} emails")
                    st.rerun()
                else:
                    st.error("Gmail fetch failed. Run `python3 gmail_auth.py` first.")
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
                st.markdown("""<div style="text-align:center;padding:32px 16px;">
                    <div style="font-size:36px;margin-bottom:8px;">📭</div>
                    <div style="font-size:14px;font-weight:600;color:#1a1a2e;">No activity yet</div>
                    <div style="font-size:12px;color:#94a3b8;">Send an email or log a call to start the timeline.</div>
                </div>""", unsafe_allow_html=True)
            else:
                current_month = ""
                st.markdown('<div class="timeline">', unsafe_allow_html=True)
                for item in timeline_items:
                    try:
                        dt = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
                        month_label = dt.strftime("%B %Y")
                        date_label = dt.strftime("%b %d, %Y %H:%M")
                    except Exception:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(item["timestamp"])
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

    st.markdown("---")
    st.button("← Back to list", on_click=close_profile, key="pv_back_bottom", type="primary")
    st.stop()

# ─── Page routing via sidebar nav ─────────────────────────────────────────
# Map nav labels to page keys
_NAV_MAP = {
    "📊 Pipeline": "pipeline", "👥 Contacts": "contacts", "📅 Today": "today",
    "✉️ Outreach": "outreach", "🔄 Follow Up": "followup", "✅ Tasks": "tasks",
    "📈 Reports": "reports", "🔗 Sequences": "sequences", "➕ Add Lead": "add",
    "📥 Import": "import", "📝 Templates": "templates", "⚙️ Settings": "settings",
}
_active_page = _NAV_MAP.get(nav_choice, "pipeline")

# ─── Pipeline (Kanban) ──────────────────────────────────────────────────────
STAGE_CLASSES = {
    "New": "stage-new", "Contacted": "stage-contacted", "Demo Booked": "stage-demo",
    "Proposal": "stage-proposal", "Won": "stage-won", "Lost": "stage-lost",
    "New Referral": "stage-new", "Referral Signed Up": "stage-won", "Reward Sent": "stage-demo",
    "Onboarding": "stage-contacted", "Training": "stage-demo", "Live": "stage-won", "Churned": "stage-lost",
}
STAGE_TINTS = {
    "New": ("#10b981", "#f0fdf9"), "Contacted": ("#3b82f6", "#eff6ff"),
    "Demo Booked": ("#7c3aed", "#f5f3ff"), "Proposal": ("#f59e0b", "#fffbeb"),
    "Won": ("#10b981", "#ecfdf5"), "Lost": ("#ef4444", "#fef2f2"),
    "New Referral": ("#10b981", "#f0fdf9"), "Referral Signed Up": ("#10b981", "#ecfdf5"),
    "Reward Sent": ("#7c3aed", "#f5f3ff"),
    "Onboarding": ("#3b82f6", "#eff6ff"), "Training": ("#7c3aed", "#f5f3ff"),
    "Live": ("#10b981", "#ecfdf5"), "Churned": ("#ef4444", "#fef2f2"),
}

if _active_page == "pipeline":
    # ── Header row ──
    _ph1, _ph2, _ph3, _ph4 = st.columns([4, 2, 1.5, 1.5])
    _ph1.markdown("""<div><div style="font-size:24px;font-weight:700;color:#1a1a2e;">Pipeline</div>
        <div style="font-size:13px;color:#94a3b8;">Track and manage your deals through each stage.</div></div>""", unsafe_allow_html=True)
    active_pipeline = _ph2.selectbox("Pipeline", list(PIPELINES.keys()), key="pipeline_sel", label_visibility="collapsed")
    active_stages = PIPELINES[active_pipeline]

    with _ph3.popover("➕ New Deal", use_container_width=True):
        st.markdown(f"**Add to {active_pipeline}**")
        nd_biz = st.text_input("Business name *", key="nd_biz")
        nc1, nc2 = st.columns(2)
        nd_contact = nc1.text_input("Contact", key="nd_contact")
        nd_email = nc2.text_input("Email", key="nd_email")
        nd_phone = nc1.text_input("Phone", key="nd_phone")
        nd_category = nc2.text_input("Category", key="nd_category")
        nd_stage = st.selectbox("Stage", active_stages, key="nd_stage")
        nd_deal_val = st.number_input("Deal value (£)", value=0.0, min_value=0.0, step=50.0, key="nd_deal_val")
        if st.button("Add Deal", type="primary", key="nd_submit", use_container_width=True):
            if not nd_biz:
                st.error("Business name required")
            else:
                new_deal = {
                    "id": int(next_id(df)),
                    "business_name": nd_biz,
                    "contact_name": nd_contact or None, "phone": nd_phone or None,
                    "email": nd_email or None, "region": None,
                    "category": nd_category or None, "stage": nd_stage,
                    "last_touch": None, "next_action_date": None,
                    "next_action": None, "notes": None,
                    "source": "manual", "created": date.today().isoformat(),
                    "pipeline": active_pipeline, "deal_value": nd_deal_val,
                }
                sb.table("leads").insert(new_deal).execute()
                load_crm.clear()
                st.session_state.pop("_df_cache", None)
                st.success(f"Added {nd_biz}")
                st.rerun()

    with _ph4.popover("⚙️ Edit Stages", use_container_width=True):
        st.markdown(f"**Stages for {active_pipeline}**")
        st.caption("One stage per line. Order = left to right on board.")
        _current = "\n".join(active_stages)
        _new_text = st.text_area("Stages", value=_current, height=200, key=f"edit_stages_{active_pipeline}", label_visibility="collapsed")
        _new_stages = [s.strip() for s in _new_text.strip().split("\n") if s.strip()]
        if st.button("Save Stages", type="primary", use_container_width=True, key="save_stages_btn"):
            _p = load_pipelines()
            _old_stages = _p.get(active_pipeline, [])
            _p[active_pipeline] = _new_stages
            save_pipelines(_p)
            # Remap leads whose stage was renamed (match by position)
            if len(_old_stages) == len(_new_stages):
                for old_s, new_s in zip(_old_stages, _new_stages):
                    if old_s != new_s:
                        try:
                            sb.table("leads").update({"stage": new_s}).eq("stage", old_s).eq("pipeline", active_pipeline).execute()
                        except Exception:
                            pass
            else:
                # Stage count changed — move orphaned leads to first new stage
                _orphans = set(_old_stages) - set(_new_stages)
                for orphan in _orphans:
                    try:
                        sb.table("leads").update({"stage": _new_stages[0]}).eq("stage", orphan).eq("pipeline", active_pipeline).execute()
                    except Exception:
                        pass
            load_crm.clear()
            st.session_state.pop("_df_cache", None)
            st.session_state["_stages_saved"] = True
        st.divider()
        st.caption("Add new pipeline")
        _new_pipe = st.text_input("Name", key="new_pipe_name_inline")
        if st.button("Create Pipeline", key="create_pipe_inline", use_container_width=True) and _new_pipe:
            _p = load_pipelines()
            _p[_new_pipe] = ["New", "In Progress", "Done"]
            save_pipelines(_p)
            st.session_state["_stages_saved"] = True

    if st.session_state.pop("_stages_saved", False):
        st.success("Stages updated!")
        st.rerun()

    # ── Filters in card ──
    st.markdown('<div style="background:#fff;border:1px solid #e2e4e9;border-radius:12px;padding:12px 20px;margin:8px 0 16px;">', unsafe_allow_html=True)
    pf1, pf2, pf3 = st.columns(3)
    p_search = pf1.text_input("Search", key="p_search", placeholder="🔍 Search deals...", label_visibility="collapsed")
    _p_counties = sorted(set(get_county(r) for r in df["region"].dropna().unique().tolist() if r))
    p_county = pf2.selectbox("County", ["All Counties"] + _p_counties, key="p_region", label_visibility="collapsed")
    _p_industries = sorted(set(get_industry(c) for c in df["category"].dropna().unique().tolist() if c))
    p_industry = pf3.selectbox("Industry", ["All Industries"] + _p_industries, key="p_industry", label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)

    view = df.copy()
    # Filter by pipeline column (default to "Sales" for old leads without pipeline set)
    view["_pipeline"] = view["pipeline"].fillna("Sales").replace("", "Sales")
    if active_pipeline == "Referral":
        referral_only_stages = [s for s in REFERRAL_STAGES if s not in STAGES]
        view = view[
            (view["_pipeline"] == "Referral")
            | (view["source"] == "referral_import")
            | view["stage"].isin(referral_only_stages)
        ]
    else:
        view = view[view["_pipeline"] == active_pipeline]
    if p_county and p_county != "All Counties":
        view = view[view["region"].apply(get_county) == p_county]
    if p_search:
        m = (view["business_name"].str.contains(p_search, case=False, na=False)
             | view["email"].str.contains(p_search, case=False, na=False)
             | view["contact_name"].str.contains(p_search, case=False, na=False))
        view = view[m]
    if p_industry and p_industry != "All Industries":
        view = view[view["category"].apply(get_industry) == p_industry]

    CARDS_DEFAULT = 8
    esc = html_mod.escape

    # Track expanded columns
    if "kanban_expanded" not in st.session_state:
        st.session_state["kanban_expanded"] = {}

    def move_lead(lid, new_stage):
        save_lead(lid, {"stage": new_stage})

    def _move_and_rerun(lid, new_stage):
        move_lead(lid, new_stage)
        # no st.rerun() — on_click callbacks trigger rerun automatically

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
            stage_idx = active_stages.index(stage)
            for _, row in stage_df.head(show_count).iterrows():
                touch = row["last_touch"] or ""
                cat = esc(get_industry(row["category"]))
                s_border, s_bg = STAGE_TINTS.get(stage, ("#e2e4e9", "#fff"))
                deal_val = row.get("deal_value", 0)
                _sc, _sc_label, _sc_color = LEAD_SCORES.get(str(row["id"]), (0, "🧊 Cold", "#94a3b8"))
                _ds = days_since_touch(row.to_dict())
                stale = f'<span style="color:#ef4444;font-size:9px;">🔴{_ds}d</span>' if _ds and _ds >= 14 and stage not in ("Won", "Lost") else ""
                deal_html = f'<span style="color:#059669;font-weight:600;font-size:11px;">£{deal_val:,.0f}</span>' if deal_val > 0 else ""
                cat_html = f'<span style="background:#f1f0ff;color:#7c3aed;font-size:9px;padding:1px 6px;border-radius:8px;">{cat}</span>' if cat and cat != "Other" else ""
                score_html = f'<span style="color:{_sc_color};font-size:9px;font-weight:600;">{_sc_label}</span>'

                with st.container(border=True):
                    st.markdown(
                        f'<style>[data-testid="stContainer"]:has(#c{row["id"]}){{background:{s_bg}!important;border-left:3px solid {s_border}!important;padding:6px 8px 2px!important;margin-bottom:4px!important;}}</style>'
                        f'<span id="c{row["id"]}" style="display:none"></span>',
                        unsafe_allow_html=True,
                    )
                    # Business name IS the button (styled as plain text via .deal-name-btn + div CSS)
                    st.button(row["business_name"][:36], key=f"k_{stage}_{row['id']}", on_click=open_profile, args=(row["id"],), use_container_width=True)
                    st.markdown(
                        f'<div style="font-size:10px;color:#64748b;margin:-8px 0 2px;">{esc(row["contact_name"])} {score_html} {stale} {deal_html}</div>'
                        f'<div style="display:flex;gap:4px;align-items:center;">{cat_html}'
                        f'<span style="font-size:9px;color:#b0b0c0;">{esc(touch)}</span></div>',
                        unsafe_allow_html=True,
                    )
                    # Move arrows
                    _ac1, _ac2 = st.columns(2)
                    if stage_idx > 0:
                        _ac1.button("◀", key=f"mvl_{row['id']}", on_click=_move_and_rerun, args=(row["id"], active_stages[stage_idx - 1]), use_container_width=True)
                    if stage_idx < len(active_stages) - 1:
                        _ac2.button("▶", key=f"mvr_{row['id']}", on_click=_move_and_rerun, args=(row["id"], active_stages[stage_idx + 1]), use_container_width=True)

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

    # Header row
    ch1, ch2, ch3 = st.columns([4, 1, 1])
    ch1.markdown("""<div><div style="font-size:24px;font-weight:700;color:#1a1a2e;">Contacts</div>
        <div style="font-size:13px;color:#94a3b8;">Manage and organize your contacts.</div></div>""", unsafe_allow_html=True)
    ch2.button("📥 Import", key="ct_import_btn", on_click=lambda: st.session_state.update({"nav": "📥 Import"}), use_container_width=True)
    ch3.button("➕ Add Contact", key="ct_add_btn", on_click=lambda: st.session_state.update({"nav": "➕ Add Lead"}), use_container_width=True, type="primary")

    # Filters in a clean card
    st.markdown('<div style="background:#fff;border:1px solid #e2e4e9;border-radius:12px;padding:16px 20px;margin:12px 0;">', unsafe_allow_html=True)
    cf1, cf2, cf3, cf4, cf5 = st.columns([3, 1.5, 1.5, 1.5, 1.5])
    c_search = cf1.text_input("Search", key="c_search", placeholder="🔍 Search by name, phone, or email...", label_visibility="collapsed")
    _all_stages = list(set(s for stages in PIPELINES.values() for s in stages))
    c_stage = cf2.selectbox("Lead Status", ["All"] + sorted(_all_stages), key="c_stage")
    _c_counties = sorted(set(get_county(r) for r in df["region"].dropna().unique().tolist() if r))
    c_region = cf3.selectbox("County", ["All Counties"] + _c_counties, key="c_region")
    _c_industries = sorted(set(get_industry(c) for c in df["category"].dropna().unique().tolist() if c))
    c_industry = cf4.selectbox("Industry", ["All Industries"] + _c_industries, key="c_industry")
    c_import_options = ["All imports"] + sorted([s for s in df["source"].dropna().unique().tolist() if s])
    c_import = cf5.selectbox("Import CSV", c_import_options, key="c_import")
    st.markdown('</div>', unsafe_allow_html=True)

    # Apply filters
    cview = df.copy()
    if c_search:
        m = (cview["business_name"].str.contains(c_search, case=False, na=False)
             | cview["email"].str.contains(c_search, case=False, na=False)
             | cview["contact_name"].str.contains(c_search, case=False, na=False)
             | cview["phone"].str.contains(c_search, case=False, na=False))
        cview = cview[m]
    if c_stage != "All":
        cview = cview[cview["stage"] == c_stage]
    if c_region != "All Counties":
        cview = cview[cview["region"].apply(get_county) == c_region]
    if c_industry != "All Industries":
        cview = cview[cview["category"].apply(get_industry) == c_industry]
    if c_import != "All imports":
        cview = cview[cview["source"] == c_import]

    # Pagination header
    page_size = 25
    total_pages = max(1, (len(cview) + page_size - 1) // page_size)
    pg1, pg2, pg3, pg4, pg5 = st.columns([3, 1, 1, 1, 2])
    pg1.markdown(f'<div style="font-size:14px;color:#1a1a2e;font-weight:600;padding:8px 0;"><span style="color:#7c3aed;">{len(cview):,}</span> contacts found</div>', unsafe_allow_html=True)
    if pg2.button("‹", key="c_prev", use_container_width=True):
        if st.session_state.get("c_page", 1) > 1:
            st.session_state["c_page"] = st.session_state.get("c_page", 1) - 1
            st.rerun()
    pg3.markdown(f'<div style="text-align:center;background:#7c3aed;color:#fff;border-radius:8px;padding:6px 0;font-size:13px;font-weight:600;">{st.session_state.get("c_page", 1)}</div>', unsafe_allow_html=True)
    if pg4.button("›", key="c_next", use_container_width=True):
        if st.session_state.get("c_page", 1) < total_pages:
            st.session_state["c_page"] = st.session_state.get("c_page", 1) + 1
            st.rerun()
    _page_sizes = [25, 50, 100]
    pg5.selectbox("Per page", _page_sizes, key="c_page_size", label_visibility="collapsed")
    page_size = st.session_state.get("c_page_size", 25)
    total_pages = max(1, (len(cview) + page_size - 1) // page_size)
    page = min(st.session_state.get("c_page", 1), total_pages)
    page_df = cview.iloc[(page-1)*page_size : page*page_size]

    # Table header
    st.markdown("""<div style="display:grid;grid-template-columns:40px 2fr 2.5fr 1.5fr 1fr 1fr 1.2fr 80px;gap:8px;padding:12px 16px;
        background:#f8f9fb;border-radius:10px 10px 0 0;border:1px solid #e2e4e9;border-bottom:2px solid #e2e4e9;
        font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;">
        <div></div><div>Name</div><div>Email</div><div>Phone</div><div>Stage</div><div>County</div><div>Last Contact</div><div>Actions</div>
    </div>""", unsafe_allow_html=True)

    # Table rows
    for idx, (_, r) in enumerate(page_df.iterrows()):
        pill_cls = PILL_MAP.get(r["stage"], "stage-pill-new")
        initials = "".join(w[0] for w in (r["contact_name"] or "?").split()[:2]).upper() or "?"
        bg = "#fff" if idx % 2 == 0 else "#fafbfc"
        touch = r.get("last_touch") or ""
        row_html = f"""<div style="display:grid;grid-template-columns:40px 2fr 2.5fr 1.5fr 1fr 1fr 1.2fr 80px;gap:8px;padding:10px 16px;
            background:{bg};border:1px solid #e2e4e9;border-top:none;align-items:center;font-size:13px;">
            <div style="width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#7c3aed,#a78bfa);
                color:#fff;font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center;">{esc_c(initials)}</div>
            <div><div style="font-weight:600;color:#1a1a2e;font-size:13px;">{esc_c(r['contact_name'] or '--')}</div>
                <div style="font-size:11px;color:#94a3b8;">{esc_c(r['business_name'])}</div></div>
            <div style="color:#64748b;font-size:12px;">{esc_c(r['email'] or '--')}</div>
            <div style="color:#64748b;font-size:12px;">{esc_c(r['phone'] or '--')}</div>
            <div><span class="contact-stage {pill_cls}">{esc_c(r['stage'])}</span></div>
            <div style="font-size:11px;color:#94a3b8;">{esc_c(get_county(r['region']))}</div>
            <div style="font-size:11px;color:#94a3b8;">{esc_c(touch[:10])}</div>
        </div>"""
        rc1, rc2 = st.columns([20, 1])
        rc1.markdown(row_html, unsafe_allow_html=True)
        rc2.button("Open", key=f"ct_{r['id']}", on_click=open_profile, args=(r["id"],))

    # Bottom pagination
    st.markdown(f'<div style="text-align:center;font-size:12px;color:#94a3b8;padding:12px;border:1px solid #e2e4e9;border-top:none;border-radius:0 0 10px 10px;background:#fafbfc;">Page {page} of {total_pages} &nbsp;·&nbsp; {page_size} per page &nbsp;·&nbsp; {len(cview):,} total</div>', unsafe_allow_html=True)

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
                    if st.button("🔄 Refresh from Gmail", key="d_gmail_refresh"):
                        with st.spinner("Fetching emails from Gmail..."):
                            count = fetch_gmail_for_contact(lead["email"])
                        if count is not None:
                            st.success(f"Found {count} emails")
                            st.rerun()
                        else:
                            st.error("Gmail fetch failed. Run `python3 gmail_auth.py` first.")

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
                    st.markdown("""<div style="text-align:center;padding:32px 16px;">
                    <div style="font-size:36px;margin-bottom:8px;">📭</div>
                    <div style="font-size:14px;font-weight:600;color:#1a1a2e;">No activity yet</div>
                    <div style="font-size:12px;color:#94a3b8;">Send an email or log a call to start the timeline.</div>
                </div>""", unsafe_allow_html=True)
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
                            # Gmail dates: "Tue, 20 May 2025 14:30:00 +0000"
                            try:
                                from email.utils import parsedate_to_datetime
                                dt = parsedate_to_datetime(item["timestamp"])
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
        st.markdown("""<div style="text-align:center;padding:48px 24px;">
            <div style="font-size:48px;margin-bottom:12px;">🎯</div>
            <div style="font-size:16px;font-weight:600;color:#1a1a2e;margin-bottom:4px;">All clear!</div>
            <div style="font-size:13px;color:#94a3b8;">No tasks due today. Book next actions on Pipeline.</div>
        </div>""", unsafe_allow_html=True)
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
                _s, _b = render_template(templates[tmpl_pick], _lead, sender_email=_sender["email"])
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

    seq_sub = st.radio("", ["Today's tasks", "Enroll leads", "Enrolled leads", "Manage sequences"], horizontal=True, key="seq_sub")

    if seq_sub == "Today's tasks":
        st.subheader("Sequence tasks due today")
        # Auto-open Gmail tab if user just clicked a row
        if st.session_state.get("_seq_auto_open"):
            _open_url = st.session_state.pop("_seq_auto_open")
            import streamlit.components.v1 as components
            components.html(
                f'<script>window.open({json.dumps(_open_url)}, "_blank");</script>'
                f'<div style="font-size:13px;color:#10b981;padding:8px;">Opening Gmail tab... if blocked, click the row again after allowing popups.</div>',
                height=40,
            )
        # Auto-mark sent when user clicks a Gmail link (link's onclick sets ?seq_opened=N)
        _qp = st.query_params
        _opened_idx = _qp.get("seq_opened")
        if _opened_idx is not None:
            try:
                _opened_i = int(_opened_idx)
                _pending_open = st.session_state.get("_seq_pending_open", [])
                # Use stored links list from prior render to look up which task to mark
                _stored_links = st.session_state.get("_seq_links_cache", [])
                if 0 <= _opened_i < len(_stored_links):
                    _lnk = _stored_links[_opened_i]
                    sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(_lnk["lead_id"])).eq("sequence_name", _lnk["sequence_name"]).eq("step", _lnk["step"]).execute()
                    log_activity(_lnk["lead_id"], _lnk["business"], "email", _lnk["subject"], _lnk["body"])
                    _lead_row = df[df["id"] == str(_lnk["lead_id"])]
                    _updates = {"last_touch": date.today().isoformat()}
                    if not _lead_row.empty and _lead_row.iloc[0]["stage"] == "New":
                        _updates["stage"] = "Contacted"
                    save_lead(_lnk["lead_id"], _updates)
                    _tracker = load_send_counts()
                    _tracker["counts"][_lnk["sender"]] = _tracker["counts"].get(_lnk["sender"], 0) + 1
                    save_send_counts(_tracker)
                    if "seq_bulk_sent" not in st.session_state:
                        st.session_state["seq_bulk_sent"] = set()
                    st.session_state["seq_bulk_sent"].add(_opened_i)
            except (ValueError, KeyError):
                pass
            # Clear the query param so it doesn't fire again on refresh
            st.query_params.clear()
        if seq_q.empty:
            st.info("No leads enrolled in sequences yet.")
        else:
            today_q = seq_q[(seq_q["due_date"] <= date.today().isoformat()) & (seq_q["status"] == "pending")]
            if today_q.empty:
                st.success("All caught up. No sequence tasks due.")
            else:
                # ─── Build links list (mirror Outreach.bulk_links structure) ───
                tracker = load_send_counts()
                sender_counts = {s["email"]: tracker["counts"].get(s["email"], 0) for s in SENDERS}

                # Build a set of recently-emailed lead IDs (last 7 days) to skip dupes
                _act = load_activity()
                _already_emailed_ids = set()
                if not _act.empty and "type" in _act.columns and "lead_id" in _act.columns:
                    _email_acts = _act[_act["type"] == "email"].copy()
                    if "date" in _email_acts.columns:
                        _email_acts["date"] = pd.to_datetime(_email_acts["date"], errors="coerce")
                        _cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
                        _email_acts = _email_acts[_email_acts["date"] >= _cutoff]
                    _already_emailed_ids = set(str(x) for x in _email_acts["lead_id"].unique())

                links = []
                call_tasks = []
                for _, task in today_q.iterrows():
                    seq_def = sequences.get(task["sequence_name"], {})
                    steps = seq_def.get("steps", [])
                    step_idx = int(task["step"])
                    step = steps[step_idx] if step_idx < len(steps) else {}
                    channel = step.get("channel", "?")
                    tmpl_name = step.get("template")
                    # Force string comparison — sequence_queue stores int, df stores str
                    _task_lid = str(task["lead_id"])
                    lead_row = df[df["id"].astype(str) == _task_lid]
                    if lead_row.empty:
                        continue
                    lead = lead_row.iloc[0].to_dict()
                    # Skip if already emailed this lead (prevents duplicate sends)
                    if channel == "email" and _task_lid in _already_emailed_ids:
                        # Auto-mark this step as done since email already went out
                        sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(task["lead_id"])).eq("sequence_name", task["sequence_name"]).eq("step", step_idx).execute()
                        continue

                    if channel == "email" and tmpl_name and tmpl_name in templates and lead.get("email"):
                        sender = None
                        for s in SENDERS:
                            if sender_counts[s["email"]] < s["daily_cap"]:
                                sender = s
                                break
                        if not sender:
                            continue
                        subj, body = render_template(templates[tmpl_name], lead, sender_email=sender["email"])
                        links.append({
                            "business": task["business_name"], "email": lead["email"],
                            "link": gmail_link(lead["email"], subj, body, sender_email=sender["email"]),
                            "sender": sender["email"], "lead_id": lead["id"],
                            "subject": subj, "body": body,
                            "sequence_name": task["sequence_name"], "step": step_idx,
                        })
                        sender_counts[sender["email"]] += 1
                    elif channel == "call":
                        call_tasks.append({
                            "lead_id": task["lead_id"], "business": task["business_name"],
                            "phone": lead.get("phone", ""), "sequence_name": task["sequence_name"], "step": step_idx,
                        })

                # ─── Sender rotation summary (same as Outreach) ───
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

                # Cache for query-param click handler to lookup task on next rerun
                st.session_state["_seq_links_cache"] = links

                if links:
                    # Mirror Outreach session state keys (prefix seq_)
                    if "seq_bulk_sent" not in st.session_state:
                        st.session_state["seq_bulk_sent"] = set()
                    if "seq_bulk_opened" not in st.session_state:
                        st.session_state["seq_bulk_opened"] = set()
                    sent_set = st.session_state["seq_bulk_sent"]
                    unsent = [i for i, l in enumerate(links) if i not in sent_set]
                    prog_pct = len(sent_set) / len(links) * 100 if links else 0

                    # ─── Top: JS bulk-open buttons (work on cloud) ───
                    import streamlit.components.v1 as components
                    opened_set = st.session_state.get("seq_bulk_opened", set())
                    unopened = [i for i in unsent if i not in opened_set]
                    if unopened:
                        # ─── RESEND BULK SEND (auto, no tabs) ───
                        rs1, rs2 = st.columns([1, 3])
                        n_send = rs1.number_input("Send N", 1, 50, 20, key="seq_resend_n")
                        to_send = unopened[:int(n_send)]
                        if rs2.button(f"🚀 Send {len(to_send)} via Resend (auto)", type="primary", key="seq_resend_send", use_container_width=True):
                            tracker = load_send_counts()
                            sent_ok, failed = 0, 0
                            errors = []
                            progress = st.progress(0, text=f"Sending {len(to_send)} via Resend...")
                            for n, i in enumerate(to_send):
                                lnk = links[i]
                                sender_info = next((s for s in SENDERS if s["email"] == lnk["sender"]), None)
                                from_name = sender_info["name"] if sender_info else "Sales"
                                ok, msg = send_via_resend(lnk["sender"], from_name, lnk["email"], lnk["subject"], lnk["body"])
                                if ok:
                                    sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(lnk["lead_id"])).eq("sequence_name", lnk["sequence_name"]).eq("step", lnk["step"]).execute()
                                    log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                                    lead_row = df[df["id"] == str(lnk["lead_id"])]
                                    updates = {"last_touch": date.today().isoformat()}
                                    if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                        updates["stage"] = "Contacted"
                                    save_lead(lnk["lead_id"], updates)
                                    tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                                    st.session_state["seq_bulk_sent"].add(i)
                                    sent_ok += 1
                                else:
                                    failed += 1
                                    errors.append(f"{lnk['business']}: {msg}")
                                progress.progress(min(99, int((n+1) / len(to_send) * 100)), text=f"Sent {n+1}/{len(to_send)}...")
                            save_send_counts(tracker)
                            progress.progress(100, text="Done!")
                            st.session_state["_last_resend_msg"] = f"✅ Resend complete: {sent_ok} sent, {failed} failed"
                            if errors:
                                st.session_state["_last_resend_errors"] = errors[:10]
                            st.rerun()

                        if st.session_state.get("_last_resend_msg"):
                            st.success(st.session_state.pop("_last_resend_msg"))
                        if st.session_state.get("_last_resend_errors"):
                            with st.expander("⚠️ Failed sends"):
                                for e in st.session_state["_last_resend_errors"]:
                                    st.write(f"- {e}")
                            del st.session_state["_last_resend_errors"]

                        st.markdown("---")
                        st.caption("Or open in Gmail tabs manually:")

                        tc1, tc2, tc3 = st.columns([1, 2, 2])
                        n_open = tc1.number_input("Open N", 1, 50, 20, key="seq_open_n_top")
                        to_open = unopened[:int(n_open)]

                        # Option 1: In-browser bulk-open (anchor clicks) — works if popups allowed
                        anchors = "".join(f'<a id="lnk_{j}" href="{l}" target="_blank" rel="noopener" style="display:none">x</a>' for j, l in enumerate([links[i]["link"] for i in to_open]))
                        js = "function openAll(){for(var j=0;j<" + str(len(to_open)) + ";j++){document.getElementById('lnk_'+j).click();}}"
                        button_html = f"""
                        <div>
                          {anchors}
                          <button onclick="openAll()" style="background:#7c3aed;color:#fff;border:none;padding:10px 16px;border-radius:8px;font-weight:600;font-size:13px;cursor:pointer;width:100%;">📨 Open {len(to_open)} tabs (allow popups)</button>
                          <script>{js}</script>
                        </div>
                        """
                        with tc2:
                            components.html(button_html, height=50)

                        # Option 2: Download HTML opener — guaranteed to work (bypasses popup blocker)
                        opener_html = f"""<!DOCTYPE html>
<html><head><title>Open {len(to_open)} Gmail tabs</title></head>
<body style="font-family:sans-serif;padding:40px;">
<h1>Click button to open {len(to_open)} Gmail tabs</h1>
<button onclick="openAll()" style="background:#7c3aed;color:#fff;border:none;padding:20px 40px;border-radius:8px;font-size:18px;cursor:pointer;">Open all {len(to_open)} tabs</button>
{anchors}
<script>{js}</script>
<p style="margin-top:30px;color:#888;">After tabs open, return to CRM and click "Mark {len(to_open)} as opened".</p>
</body></html>"""
                        with tc3:
                            st.download_button(
                                label=f"⬇️ Download opener.html ({len(to_open)} tabs)",
                                data=opener_html,
                                file_name="open_gmail_tabs.html",
                                mime="text/html",
                                key="seq_download_opener",
                                use_container_width=True,
                                help="Download, double-click to open in browser, click the button — bypasses popup blocker",
                            )

                        if st.button(f"✓ Mark {len(to_open)} as opened", key="seq_mark_opened"):
                            for i in to_open:
                                st.session_state["seq_bulk_opened"].add(i)
                            st.rerun()
                        if st.button(f"🔍 Check Gmail sent (auto-mark)", key="seq_check_top"):
                            try:
                                from gmail_auth import check_sent_emails
                                by_sender = {}
                                for i in unsent:
                                    by_sender.setdefault(links[i]["sender"], []).append(i)
                                tracker = load_send_counts()
                                newly_confirmed = 0
                                for sender_email, idxs in by_sender.items():
                                    recipients = [links[i]["email"] for i in idxs]
                                    found = check_sent_emails(recipients, hours_back=48, sender_email=sender_email)
                                    for i in idxs:
                                        if links[i]["email"].lower() in found:
                                            lnk = links[i]
                                            sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(lnk["lead_id"])).eq("sequence_name", lnk["sequence_name"]).eq("step", lnk["step"]).execute()
                                            log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                                            lead_row = df[df["id"] == str(lnk["lead_id"])]
                                            updates = {"last_touch": date.today().isoformat()}
                                            if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                                updates["stage"] = "Contacted"
                                            save_lead(lnk["lead_id"], updates)
                                            tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                                            st.session_state["seq_bulk_sent"].add(i)
                                            newly_confirmed += 1
                                save_send_counts(tracker)
                                st.session_state["_last_check_msg"] = f"✅ Found {newly_confirmed} sent emails via Gmail"
                                st.rerun()
                            except Exception as e:
                                st.error(f"Gmail check failed: {e}")
                    if st.session_state.get("_last_check_msg"):
                        st.success(st.session_state.pop("_last_check_msg"))

                    st.markdown(f"""<div style="background:#fff;border:1px solid #e2e4e9;border-radius:12px;padding:16px 20px;margin-bottom:16px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                            <span style="font-size:14px;font-weight:600;color:#1a1a2e;">{len(links)} sequence email tasks</span>
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
                            continue
                        else:
                            lc1, lc2, lc3 = st.columns([5, 2, 1])
                            # Streamlit button — clicking marks as sent + opens Gmail tab via JS popup
                            with lc1:
                                if st.button(f"✉ {lnk['business']} — {lnk['email']}", key=f"seq_open_{i}", use_container_width=True):
                                    # Mark sent in DB
                                    tracker = load_send_counts()
                                    sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(lnk["lead_id"])).eq("sequence_name", lnk["sequence_name"]).eq("step", lnk["step"]).execute()
                                    log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                                    lead_row = df[df["id"] == str(lnk["lead_id"])]
                                    updates = {"last_touch": date.today().isoformat()}
                                    if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                        updates["stage"] = "Contacted"
                                    save_lead(lnk["lead_id"], updates)
                                    tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                                    save_send_counts(tracker)
                                    st.session_state["seq_bulk_sent"].add(i)
                                    # Queue Gmail URL to auto-open on next rerun
                                    st.session_state["_seq_auto_open"] = lnk["link"]
                                    st.rerun()
                            if lc2.button("🚫", key=f"seq_skip_{i}", help="Skip this task"):
                                sb.table("sequence_queue").update({"status": "skipped"}).eq("lead_id", int(lnk["lead_id"])).eq("sequence_name", lnk["sequence_name"]).eq("step", lnk["step"]).execute()
                                st.rerun()

                    st.divider()
                    BATCH_SIZE = 10
                    opened_set = st.session_state.get("seq_bulk_opened", set())
                    unopened = [i for i in unsent if i not in opened_set]
                    if unopened:
                        sender_groups = {}
                        for idx in unopened:
                            lnk = links[idx]
                            sender_groups.setdefault(lnk["sender"], []).append(idx)
                        for sender_email, sender_idxs in sender_groups.items():
                            open_count = min(BATCH_SIZE, len(sender_idxs))
                            profile_dir = SENDER_PROFILE.get(sender_email, "Profile 5")
                            if st.button(f"Open next {open_count} for {sender_email} ({len(sender_idxs)} remaining)", type="primary", key=f"seq_b_open_{sender_email}"):
                                import subprocess, time
                                batch_to_open = sender_idxs[:BATCH_SIZE]
                                for idx in batch_to_open:
                                    subprocess.Popen([
                                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                                        f"--profile-directory={profile_dir}", links[idx]["link"]
                                    ])
                                    time.sleep(0.5)
                                st.session_state["seq_bulk_opened"].update(batch_to_open)
                                st.rerun()

                    cc1, cc2, cc3, cc4 = st.columns(4)
                    if cc4.button("🔍 Check Gmail sent", key="seq_b_check_sent"):
                        try:
                            from gmail_auth import check_sent_emails
                            all_unsent_emails = [links[i]["email"] for i in unsent]
                            found = check_sent_emails(all_unsent_emails, hours_back=24) if all_unsent_emails else set()
                            tracker = load_send_counts()
                            newly_confirmed = 0
                            for i in unsent:
                                if links[i]["email"].lower() in found:
                                    lnk = links[i]
                                    sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(lnk["lead_id"])).eq("sequence_name", lnk["sequence_name"]).eq("step", lnk["step"]).execute()
                                    log_activity(lnk["lead_id"], lnk["business"], "email", lnk["subject"], lnk["body"])
                                    lead_row = df[df["id"] == str(lnk["lead_id"])]
                                    updates = {"last_touch": date.today().isoformat()}
                                    if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                        updates["stage"] = "Contacted"
                                    save_lead(lnk["lead_id"], updates)
                                    tracker["counts"][lnk["sender"]] = tracker["counts"].get(lnk["sender"], 0) + 1
                                    st.session_state["seq_bulk_sent"].add(i)
                                    newly_confirmed += 1
                            save_send_counts(tracker)
                            st.success(f"Found {newly_confirmed} sent emails in Gmail.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Gmail check failed: {e}. Run `python3 gmail_auth.py` first.")
                    if cc1.button("🔄 Reset opened/sent", key="seq_b_reset"):
                        st.session_state["seq_bulk_opened"] = set()
                        st.session_state["seq_bulk_sent"] = set()
                        st.rerun()
                else:
                    st.info("No email tasks due.")

                # ─── Call tasks ───
                if call_tasks:
                    st.divider()
                    st.markdown("### 📞 Call tasks")
                    for t in call_tasks:
                        col1, col2, col3 = st.columns([3, 2, 1])
                        col1.markdown(f"**{t['business']}**")
                        if t["phone"]:
                            col2.markdown(f"<a href='tel:{t['phone']}'>{t['phone']}</a>", unsafe_allow_html=True)
                        if col3.button("Done", key=f"seq_call_done_{t['lead_id']}_{t['step']}"):
                            sb.table("sequence_queue").update({"status": "done"}).eq("lead_id", int(t["lead_id"])).eq("sequence_name", t["sequence_name"]).eq("step", t["step"]).execute()
                            log_activity(t["lead_id"], t["business"], "call", f"Sequence {t['sequence_name']} step {t['step']+1}", "")
                            updates = {"last_touch": date.today().isoformat()}
                            lead_row = df[df["id"] == str(t["lead_id"])]
                            if not lead_row.empty and lead_row.iloc[0]["stage"] == "New":
                                updates["stage"] = "Contacted"
                            save_lead(t["lead_id"], updates)
                            st.rerun()

    elif seq_sub == "Enroll leads":
        st.subheader("Enroll leads into sequence")
        # Show last enroll result if any
        if st.session_state.get("_last_enroll_msg"):
            st.success(st.session_state["_last_enroll_msg"])
            if st.button("Dismiss", key="dismiss_enroll_msg"):
                del st.session_state["_last_enroll_msg"]
                st.rerun()
        seq_name = st.selectbox("Sequence", list(sequences.keys()), key="seq_enroll_name")
        seq_def = sequences[seq_name]
        st.caption("Steps: " + " → ".join(
            [f"Day {s['day']}: {s['channel']}" + (f" ({s['template']})" if s.get('template') else "") for s in seq_def["steps"]]
        ))
        ec1, ec2, ec3 = st.columns(3)
        # Stage filter: empty default = ALL stages (was filtering out Cornwall leads not in "New")
        e_stage = ec1.multiselect("Stage filter (empty = all)", STAGES, default=[], key="seq_e_stage")
        e_region = ec2.text_input("Region filter", key="seq_e_region")
        import_options = ["All imports"] + sorted([s for s in df["source"].dropna().unique().tolist() if s])
        e_import = ec3.selectbox("Import (CSV) filter", import_options, key="seq_e_import")
        pool = df[df["stage"].isin(e_stage)] if e_stage else df.copy()
        if e_region:
            pool = pool[pool["region"].str.contains(e_region, case=False, na=False)]
        if e_import != "All imports":
            pool = pool[pool["source"] == e_import]
        pool = pool[pool["email"].str.contains("@", na=False)]
        # Force string comparison (sequence_queue stores int)
        already = set(str(x) for x in seq_q[seq_q["sequence_name"] == seq_name]["lead_id"].unique()) if not seq_q.empty else set()
        # Also exclude leads emailed in last 7 days via this sequence's templates
        _act_check = load_activity()
        _already_emailed_pool = set()
        if not _act_check.empty and "type" in _act_check.columns and "lead_id" in _act_check.columns:
            _email_acts = _act_check[_act_check["type"] == "email"].copy()
            if "date" in _email_acts.columns:
                _email_acts["date"] = pd.to_datetime(_email_acts["date"], errors="coerce")
                _cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
                _email_acts = _email_acts[_email_acts["date"] >= _cutoff]
            _already_emailed_pool = set(str(x) for x in _email_acts["lead_id"].unique())
        excluded = already | _already_emailed_pool
        before_excl = len(pool)
        pool = pool[~pool["id"].astype(str).isin(excluded)]
        excluded_count = before_excl - len(pool)
        st.caption(f"{len(pool)} eligible · {len(already)} already enrolled in '{seq_name}' · {len(_already_emailed_pool)} emailed in last 7 days · {excluded_count} excluded total")

        if len(pool) == 0:
            st.warning("No eligible leads match these filters.")
            # Show diagnostic — auto-expanded
            with st.expander("Why 0 leads?", expanded=True):
                _all_sources = sorted([s for s in df["source"].dropna().unique().tolist() if s])
                st.write(f"**All import sources in DB:**")
                st.write(_all_sources)
                if e_import != "All imports":
                    _src_df = df[df["source"] == e_import]
                    st.write(f"**Leads with source='{e_import}':** {len(_src_df)}")
                    if len(_src_df) > 0:
                        _stages_in_src = _src_df["stage"].value_counts().to_dict()
                        st.write(f"**Stages in this import:** {_stages_in_src}")
                        _with_email = len(_src_df[_src_df["email"].str.contains("@", na=False)])
                        st.write(f"**With valid email (@):** {_with_email}")
                        _not_enrolled = len(_src_df[~_src_df["id"].isin(already)])
                        st.write(f"**Not already enrolled:** {_not_enrolled}")
                        st.write(f"**Stage filter applied:** {e_stage if e_stage else '(none — all stages)'}")
                        # Sample 5 rows to see what's actually in the data
                        st.write("**Sample 5 leads from this import:**")
                        _sample_cols = [c for c in ["id", "business_name", "contact_name", "email", "phone", "region"] if c in _src_df.columns]
                        st.dataframe(_src_df[_sample_cols].head(5), use_container_width=True)
        else:
            e_limit = st.slider("Enroll how many", 1, max(min(len(pool), 500), 2),
                                min(50, len(pool)), key="seq_e_limit") if len(pool) > 1 else 1
            if len(pool) == 1:
                st.caption("Only 1 lead in pool — enroll all")

            # PREVIEW: show rendered emails for first lead
            with st.expander("📧 Preview emails (first lead)", expanded=True):
                _preview_lead = pool.iloc[0].to_dict()
                st.caption(f"Preview for: **{_preview_lead.get('business_name')}** ({_preview_lead.get('contact_name')} - {_preview_lead.get('email')})")
                for step_idx, step in enumerate(seq_def["steps"]):
                    st.markdown(f"**Step {step_idx+1} — Day {step['day']} — {step['channel'].upper()}**")
                    if step["channel"] == "email" and step.get("template"):
                        tmpl_name = step["template"]
                        if tmpl_name in templates:
                            _s, _b = render_template(templates[tmpl_name], _preview_lead)
                            st.markdown(f"*Subject:* `{_s}`")
                            st.code(_b, language=None)
                        else:
                            st.error(f"⚠️ Template '{tmpl_name}' not found in templates.json")
                    elif step["channel"] == "call":
                        st.caption(f"📞 Call task (no email)")
                    st.markdown("---")

            if st.button(f"Enroll {e_limit} leads into '{seq_name}'", type="primary", key="seq_e_go"):
                progress = st.progress(0, text=f"Enrolling {e_limit} leads...")
                seq = sequences[seq_name]
                lead_ids = pool.head(e_limit)[["id", "business_name"]].values.tolist()
                lead_id_ints = [int(lid) for lid, _ in lead_ids]

                # Batch delete via in_() — one call instead of N
                progress.progress(15, text="Clearing prior enrollments...")
                try:
                    sb.table("sequence_queue").delete().in_("lead_id", lead_id_ints).eq("sequence_name", seq_name).execute()
                except Exception as e:
                    st.session_state["_last_enroll_msg"] = f"❌ Delete failed: {e}"
                    st.rerun()

                progress.progress(40, text="Building queue...")
                queue_rows = []
                for lid, biz in lead_ids:
                    for i, step in enumerate(seq["steps"]):
                        due = (date.today() + pd.Timedelta(days=step["day"])).isoformat()
                        queue_rows.append({
                            "lead_id": int(lid),
                            "business_name": biz,
                            "sequence_name": seq_name,
                            "step": i,
                            "due_date": due,
                            "status": "pending",
                        })
                progress.progress(60, text=f"Inserting {len(queue_rows)} queue items...")
                try:
                    if queue_rows:
                        batch_size = 500
                        for i in range(0, len(queue_rows), batch_size):
                            sb.table("sequence_queue").insert(queue_rows[i:i+batch_size]).execute()
                    progress.progress(100, text="Done!")
                    st.session_state["_last_enroll_msg"] = f"✅ Enrolled **{len(lead_ids)} leads** into '{seq_name}' — {len(queue_rows)} tasks queued ({len(seq['steps'])} steps × {len(lead_ids)} leads)"
                except Exception as e:
                    st.session_state["_last_enroll_msg"] = f"❌ Insert failed: {e}"
                st.rerun()

    elif seq_sub == "Enrolled leads":
        st.subheader("Leads enrolled in sequences")
        if seq_q.empty:
            st.info("No leads enrolled in any sequence.")
        else:
            # Filters
            ef1, ef2 = st.columns([2, 2])
            seq_filter = ef1.selectbox("Sequence", ["All"] + list(seq_q["sequence_name"].unique()), key="enrolled_seq_f")
            status_filter = ef2.selectbox("Status", ["All", "pending", "done", "skipped"], key="enrolled_status_f")

            view = seq_q.copy()
            if seq_filter != "All":
                view = view[view["sequence_name"] == seq_filter]
            if status_filter != "All":
                view = view[view["status"] == status_filter]

            # Summary
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Total enrolled", view["lead_id"].nunique())
            sc2.metric("Pending steps", len(view[view["status"] == "pending"]))
            sc3.metric("Sent (done)", len(view[view["status"] == "done"]))
            sc4.metric("Skipped", len(view[view["status"] == "skipped"]))

            # Build display dataframe — one row per (lead, sequence) with step progress
            display_rows = []
            for (lid, sn), group in view.groupby(["lead_id", "sequence_name"]):
                total_steps = len(group)
                done_steps = len(group[group["status"] == "done"])
                pending = group[group["status"] == "pending"]
                next_due = pending["due_date"].min() if not pending.empty else "—"
                next_step = pending["step"].min() + 1 if not pending.empty else "—"
                biz = group["business_name"].iloc[0]
                lead_row = df[df["id"].astype(str) == str(lid)]
                email_addr = lead_row.iloc[0]["email"] if not lead_row.empty else ""
                stage = lead_row.iloc[0]["stage"] if not lead_row.empty else ""
                display_rows.append({
                    "Lead ID": str(lid),
                    "Business": biz,
                    "Email": email_addr,
                    "Stage": stage,
                    "Sequence": sn,
                    "Progress": f"{done_steps}/{total_steps}",
                    "Next step": next_step,
                    "Next due": next_due,
                })

            if display_rows:
                display_df = pd.DataFrame(display_rows)
                st.dataframe(display_df, use_container_width=True, hide_index=True, height=600)
                st.caption(f"Showing {len(display_df)} enrollments. Click column headers to sort.")
            else:
                st.info("No enrollments match filters.")

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
        deal_val = c1.number_input("Deal value (£)", value=0.0, min_value=0.0, step=50.0)
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
                    "created": date.today().isoformat(), "pipeline": pl, "deal_value": deal_val,
                }
                sb.table("leads").insert(new).execute()
                load_crm.clear()
                st.session_state.pop("_df_cache", None)
                st.success(f"Added {biz}")

# ─── Import ──────────────────────────────────────────────────────────────────
if _active_page == "import":
    # Show preset source section ONLY if file exists locally (skip on Streamlit Cloud)
    if LEADS_SRC.exists():
        with st.expander("Import from preset source CSV", expanded=False):
            st.write(f"Source: `{LEADS_SRC}`")
            src = pd.read_csv(LEADS_SRC, dtype=str).fillna("")
            regions = ["All"] + sorted(src["region"].dropna().unique().tolist())
            c1, c2 = st.columns(2)
            rf = c1.selectbox("Region", regions)
            lim = c2.number_input("Limit", min_value=1, max_value=5000, value=100, step=50)
            st.caption("Generic inbox emails (info@/hello@ etc) auto-filtered. Duplicates by business name skipped.")
            if st.button("Import", type="primary"):
                progress = st.progress(0, text="Importing leads...")
                progress.progress(10, text="Reading source file...")
                df, added, stats = import_leads(df, LEADS_SRC, rf, lim)
                progress.progress(100, text="Done!")
                st.session_state["_last_import_msg"] = f"✅ Imported {added} new leads — CSV had {stats['total']} rows: **{stats['inserted']} new** · {stats['skipped_existing']} already exist · {stats['skipped_generic_email']} generic emails · {stats['skipped_no_email']} no email · {stats['skipped_blocked']} bounced/blocked"
                st.rerun()
    # Show last import result if any
    if st.session_state.get("_last_import_msg"):
        st.success(st.session_state["_last_import_msg"])
        if st.button("Dismiss", key="dismiss_import_msg"):
            del st.session_state["_last_import_msg"]
            st.rerun()
    st.subheader("Upload custom CSV")
    st.caption("CSV needs `business_name` column. Optional: `email`, `phone`, `first_name`, `last_name`, `region`, `category`, `source`.")
    up = st.file_uploader("Choose CSV file", type="csv", key="csv_upload")
    if up:
        tmp_df = pd.read_csv(up, dtype=str).fillna("")
        st.write(f"**{len(tmp_df)} rows** found. Columns: {', '.join(tmp_df.columns.tolist())}")
        st.dataframe(tmp_df.head(10), use_container_width=True, height=200)
        default_name = up.name.rsplit('.', 1)[0]
        up_name = st.text_input("Import name (tags all rows so you can target them in sequences)",
                                value=default_name, key="up_name",
                                placeholder="e.g. Cornwall 2026-05-20")
        uc1, uc2 = st.columns(2)
        up_pipeline = uc1.selectbox("Import to pipeline", list(PIPELINES.keys()), key="up_pipeline")
        up_stage = uc2.selectbox("Default stage", PIPELINES[up_pipeline], key="up_stage")
        up_mode = st.radio("Mode", ["New leads only (skip existing)", "Update existing leads (merge missing fields)"],
                           key="up_mode", horizontal=True)
        if st.button("Import uploaded", type="primary"):
            if not up_name.strip():
                st.error("Import name required — needed to target these leads in sequences.")
            elif up_mode.startswith("Update"):
                # UPDATE mode — match on existing df in-memory, batch updates to Supabase
                up.seek(0)
                tmp_df_up = pd.read_csv(up, dtype=str).fillna("")
                progress = st.progress(0, text="Matching leads...")

                # Build lookup map: lowercase business_name → list of existing rows (id, email, phone, etc.)
                df_lookup = {}
                for _, row in df.iterrows():
                    key = str(row.get("business_name", "")).strip().lower()
                    if key:
                        df_lookup.setdefault(key, []).append({
                            "id": row["id"],
                            "email": row.get("email"),
                            "phone": row.get("phone"),
                            "contact_name": row.get("contact_name"),
                            "region": row.get("region"),
                            "category": row.get("category"),
                        })

                updates_to_apply = []  # list of (lead_id, updates_dict)
                inserts_to_apply = []
                nid_start = int(next_id(df)) if not df.empty else 1

                progress.progress(20, text="Processing CSV rows...")
                for _, r in tmp_df_up.iterrows():
                    biz = r.get("business_name", "").strip()
                    if not biz:
                        continue
                    contact = f"{r.get('first_name','')} {r.get('last_name','')}".strip()
                    new_email = r.get("email", "").strip()
                    new_phone = r.get("phone", "").strip()
                    new_region = r.get("region", "").strip()
                    new_cat = r.get("category", "").strip()

                    matches = df_lookup.get(biz.lower(), [])
                    if matches:
                        for row in matches:
                            u = {}
                            cur_email = row.get("email") or ""
                            if new_email and (not cur_email or "@" not in cur_email):
                                u["email"] = new_email
                            if new_phone and not row.get("phone"):
                                u["phone"] = new_phone
                            if contact and not row.get("contact_name"):
                                u["contact_name"] = contact
                            if new_region and not row.get("region"):
                                u["region"] = new_region
                            if new_cat and not row.get("category"):
                                u["category"] = new_cat
                            if u:
                                updates_to_apply.append((int(row["id"]), u))
                    else:
                        inserts_to_apply.append({
                            "business_name": biz,
                            "contact_name": contact or None,
                            "phone": new_phone or None,
                            "email": new_email or None,
                            "region": new_region or None,
                            "category": new_cat or None,
                            "stage": up_stage,
                            "source": up_name.strip(),
                            "created": date.today().isoformat(),
                            "pipeline": up_pipeline,
                            "deal_value": 0,
                        })

                # Apply updates
                total_ops = len(updates_to_apply) + len(inserts_to_apply)
                progress.progress(40, text=f"Applying {len(updates_to_apply)} updates, {len(inserts_to_apply)} inserts...")
                for i, (lid, u) in enumerate(updates_to_apply):
                    sb.table("leads").update(u).eq("id", lid).execute()
                    if i % 25 == 0:
                        progress.progress(min(95, 40 + int(i / max(total_ops, 1) * 50)), text=f"Updated {i}/{len(updates_to_apply)}...")

                # Apply inserts in batch
                if inserts_to_apply:
                    for i, ins in enumerate(inserts_to_apply):
                        ins["id"] = nid_start + i
                    batch_size = 500
                    for i in range(0, len(inserts_to_apply), batch_size):
                        sb.table("leads").insert(inserts_to_apply[i:i+batch_size]).execute()

                progress.progress(100, text="Done!")
                load_crm.clear()
                st.session_state.pop("_df_cache", None)
                _matched_no_change = sum(1 for biz in tmp_df_up["business_name"].str.strip().str.lower().unique() if biz in df_lookup) - len(set(lid for lid, _ in updates_to_apply))
                st.session_state["_last_import_msg"] = f"✅ Import complete — CSV had {len(tmp_df_up)} rows: **{len(updates_to_apply)} existing updated** · **{len(inserts_to_apply)} new inserted** · {max(_matched_no_change, 0)} matched but nothing to update"
                st.rerun()
            else:
                tmp = ROOT / "_upload.csv"
                tmp.write_bytes(up.getvalue())
                progress = st.progress(0, text="Importing leads...")
                progress.progress(10, text="Reading CSV...")
                df, added, stats = import_leads(df, tmp, None, None, pipeline=up_pipeline,
                                          default_stage=up_stage, import_name=up_name.strip())
                progress.progress(90, text=f"Imported {added} leads...")
                tmp.unlink()
                progress.progress(100, text="Done!")
                st.session_state["_last_import_msg"] = f"✅ Imported {added} new leads tagged '{up_name.strip()}' — CSV had {stats['total']} rows: **{stats['inserted']} new inserted** · {stats['skipped_existing']} already exist (skipped) · {stats['skipped_generic_email']} generic emails · {stats['skipped_no_email']} no email · {stats['skipped_blocked']} bounced/blocked"
                st.balloons()
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
    st.subheader("Pipelines & Stages")
    st.caption("Edit stage names for each pipeline. One stage per line.")

    _pipes = load_pipelines()
    _pipe_names = list(_pipes.keys())
    _edited_pipes = {}
    _changed = False

    for pname in _pipe_names:
        with st.expander(f"📋 {pname}", expanded=False):
            current_stages = "\n".join(_pipes[pname])
            new_stages_text = st.text_area(f"Stages for {pname}", value=current_stages, height=150, key=f"set_stages_{pname}")
            new_stages = [s.strip() for s in new_stages_text.strip().split("\n") if s.strip()]
            _edited_pipes[pname] = new_stages
            if new_stages != _pipes[pname]:
                _changed = True
            # Rename pipeline
            rc1, rc2 = st.columns([3, 1])
            new_pname = rc1.text_input("Rename pipeline", value=pname, key=f"set_rename_{pname}")
            if rc2.button("🗑️ Delete", key=f"set_del_{pname}"):
                del _pipes[pname]
                save_pipelines(_pipes)
                st.success(f"Deleted {pname}")
                st.rerun()
            if new_pname != pname and new_pname.strip():
                _edited_pipes[new_pname] = _edited_pipes.pop(pname)
                _changed = True

    # Add new pipeline
    with st.expander("➕ Add new pipeline"):
        new_pipe_name = st.text_input("Pipeline name", key="set_new_pipe_name")
        new_pipe_stages = st.text_area("Stages (one per line)", value="New\nIn Progress\nDone", key="set_new_pipe_stages", height=100)
        if st.button("Create pipeline", key="set_create_pipe"):
            if new_pipe_name and new_pipe_name not in _edited_pipes:
                _edited_pipes[new_pipe_name] = [s.strip() for s in new_pipe_stages.strip().split("\n") if s.strip()]
                save_pipelines(_edited_pipes)
                st.success(f"Created {new_pipe_name}")
                st.rerun()

    if st.button("💾 Save pipeline changes", type="primary", use_container_width=True):
        save_pipelines(_edited_pipes)
        st.success("Pipelines saved!")
        st.rerun()

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
