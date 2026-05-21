"""Upload gmail_token.json to Supabase app_config table.

One-time setup. After this, the cloud CRM reads the token from Supabase.
No Streamlit secrets fiddling needed.

Run from crm/ directory:
    python3 upload_gmail_token.py
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
if not url or not key:
    print("❌ SUPABASE_URL or SUPABASE_KEY missing in .env")
    raise SystemExit(1)

token_file = ROOT / "gmail_token.json"
if not token_file.exists():
    print(f"❌ Token file not found: {token_file}")
    print("Run `python3 gmail_auth.py` first to authenticate Gmail.")
    raise SystemExit(1)

token_data = json.loads(token_file.read_text())
sb = create_client(url, key)

# Upsert into app_config
sb.table("app_config").upsert({
    "key": "gmail_token",
    "value": json.dumps(token_data),
}).execute()
print("✅ Uploaded gmail_token to Supabase app_config")
print(f"   Token for: {token_data.get('client_id', '?')[:30]}...")
print("Cloud CRM will now load this token. Reload app and test.")
