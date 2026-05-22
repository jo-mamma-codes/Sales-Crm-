"""Migrate bounced_emails.json → leads.do_not_email column.

Run once after applying SUPABASE_SETUP_PHASE1.sql.
Marks every lead whose email is in the bounced or never_contact list as do_not_email=true.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

dnc_file = ROOT / "bounced_emails.json"
if not dnc_file.exists():
    print("No bounced_emails.json — nothing to migrate")
    raise SystemExit(0)

data = json.loads(dnc_file.read_text())
bounced = [e.lower().strip() for e in data.get("bounced", []) if e]
never = [e.lower().strip() for e in data.get("never_contact", []) if e]
all_dnc = list(set(bounced + never))

print(f"Migrating {len(bounced)} bounced + {len(never)} never_contact = {len(all_dnc)} unique emails")

updated_total = 0
batch_size = 100
for i in range(0, len(all_dnc), batch_size):
    batch = all_dnc[i:i+batch_size]
    # Find leads with these emails (case-insensitive via ilike on each)
    # Supabase doesn't have IN with case-insensitive; do batched lookups
    r = sb.table("leads").select("id, email").in_("email", batch).execute()
    ids = [row["id"] for row in r.data]
    if ids:
        flag = bool(set(batch) & set(bounced))
        sb.table("leads").update({
            "do_not_email": True,
            "bounced": flag,
        }).in_("id", ids).execute()
        updated_total += len(ids)
        print(f"  batch {i//batch_size + 1}: matched {len(ids)} of {len(batch)}")

print(f"\n✅ Flagged {updated_total} leads as do_not_email")
print("Note: emails in the file with no matching lead in DB are skipped (no lead exists for them yet)")
