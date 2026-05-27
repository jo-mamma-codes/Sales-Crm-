"""Migrate existing `leads` rows into companies + contacts + deals + associations.

One lead row → one company + one contact + (optionally) one deal.
Deduplicates companies by business_name.

Run from crm/ after applying SUPABASE_SETUP_PHASE4.sql:
    python3 migrate_leads_to_objects.py [--dry-run]
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

DRY = "--dry-run" in sys.argv


def fetch_all_leads():
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        r = sb.table("leads").select("*").range(offset, offset + page_size - 1).execute()
        if not r.data:
            break
        all_rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
    return all_rows


def main():
    leads = fetch_all_leads()
    print(f"Loaded {len(leads)} leads")

    # Existing companies dedupe
    existing_companies = {}
    r = sb.table("companies").select("id, name").execute()
    for c in r.data or []:
        existing_companies[(c["name"] or "").strip().lower()] = c["id"]
    print(f"{len(existing_companies)} companies already in DB")

    existing_contacts_by_email = {}
    r = sb.table("contacts").select("id, email").execute()
    for c in r.data or []:
        if c.get("email"):
            existing_contacts_by_email[c["email"].lower()] = c["id"]
    print(f"{len(existing_contacts_by_email)} contacts already in DB")

    companies_created = 0
    contacts_created = 0
    deals_created = 0
    assocs_created = 0
    activities_linked = 0

    for lead in leads:
        biz = (lead.get("business_name") or "").strip()
        if not biz:
            continue
        key = biz.lower()

        # ─── Company ───
        if key in existing_companies:
            company_id = existing_companies[key]
        else:
            company_payload = {
                "name": biz,
                "region": lead.get("region"),
                "industry": lead.get("category"),
                "source": lead.get("source"),
                "phone": lead.get("phone"),
                "lifecycle_stage": "lead",
            }
            if DRY:
                print(f"[DRY] create company '{biz}'")
                company_id = -1
            else:
                cr = sb.table("companies").insert(company_payload).execute()
                company_id = cr.data[0]["id"]
            existing_companies[key] = company_id
            companies_created += 1

        # ─── Contact ───
        contact_id = None
        email = (lead.get("email") or "").strip().lower()
        contact_name = (lead.get("contact_name") or "").strip()
        if contact_name or email:
            if email and email in existing_contacts_by_email:
                contact_id = existing_contacts_by_email[email]
            else:
                first, last = "", ""
                if contact_name:
                    parts = contact_name.split(maxsplit=1)
                    first = parts[0]
                    last = parts[1] if len(parts) > 1 else ""
                contact_payload = {
                    "first_name": first or None,
                    "last_name": last or None,
                    "email": email or None,
                    "phone": lead.get("phone"),
                    "do_not_email": bool(lead.get("do_not_email")),
                    "bounced": bool(lead.get("bounced")),
                }
                if DRY:
                    print(f"[DRY] create contact '{contact_name}' <{email}>")
                    contact_id = -1
                else:
                    cr = sb.table("contacts").insert(contact_payload).execute()
                    contact_id = cr.data[0]["id"]
                    if email:
                        existing_contacts_by_email[email] = contact_id
                contacts_created += 1

        # ─── Deal (only if stage indicates active opportunity) ───
        deal_id = None
        stage = (lead.get("stage") or "").strip()
        if stage and stage not in ("New",):
            deal_payload = {
                "name": f"{biz} — {stage}",
                "pipeline": lead.get("pipeline") or "Sales",
                "stage": stage,
                "amount": float(lead.get("deal_value") or 0) or None,
            }
            if DRY:
                print(f"[DRY] create deal '{biz} — {stage}'")
                deal_id = -1
            else:
                dr = sb.table("deals").insert(deal_payload).execute()
                deal_id = dr.data[0]["id"]
            deals_created += 1

        # ─── Associations ───
        assocs = []
        if contact_id and company_id and not DRY:
            assocs.append({
                "from_object_type": "contact", "from_object_id": contact_id,
                "to_object_type": "company", "to_object_id": company_id,
                "association_label": "works_at",
            })
            assocs.append({
                "from_object_type": "company", "from_object_id": company_id,
                "to_object_type": "contact", "to_object_id": contact_id,
                "association_label": "employs",
            })
        if deal_id and company_id and not DRY:
            assocs.append({
                "from_object_type": "deal", "from_object_id": deal_id,
                "to_object_type": "company", "to_object_id": company_id,
                "association_label": "primary_company",
            })
        if deal_id and contact_id and not DRY:
            assocs.append({
                "from_object_type": "deal", "from_object_id": deal_id,
                "to_object_type": "contact", "to_object_id": contact_id,
                "association_label": "primary_contact",
            })
        if assocs:
            try:
                sb.table("associations").upsert(assocs, on_conflict="from_object_type,from_object_id,to_object_type,to_object_id,association_label").execute()
                assocs_created += len(assocs)
            except Exception as e:
                print(f"  assoc failed: {e}")

        # ─── Link activity entries to new contact ───
        if contact_id and not DRY:
            try:
                sb.table("activity").update({
                    "object_type": "contact", "object_id": contact_id,
                }).eq("lead_id", str(lead["id"])).is_("object_type", "null").execute()
                activities_linked += 1
            except Exception:
                pass

    print(f"\n=== Migration summary ===")
    print(f"Companies created: {companies_created}")
    print(f"Contacts created: {contacts_created}")
    print(f"Deals created: {deals_created}")
    print(f"Associations created: {assocs_created}")
    print(f"Activity rows linked: {activities_linked}")
    if DRY:
        print("\n(DRY RUN — no changes written. Re-run without --dry-run to apply.)")


if __name__ == "__main__":
    main()
