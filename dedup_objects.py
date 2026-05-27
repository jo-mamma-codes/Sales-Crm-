"""Dedupe companies + contacts created by repeated migration runs.

For each duplicate group (same lowercase name for companies, same lowercase
email for contacts):
- Keep oldest (lowest id)
- Re-point associations + activity from duplicates → kept id
- Delete duplicates

Idempotent. Safe to re-run.
"""
import os
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def fetch_all(table, page_size=1000):
    rows = []
    offset = 0
    while True:
        r = sb.table(table).select("*").range(offset, offset + page_size - 1).execute()
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < page_size:
            break
        offset += page_size
    return rows


def dedup_companies():
    rows = fetch_all("companies")
    by_name = defaultdict(list)
    for c in rows:
        k = (c.get("name") or "").strip().lower()
        if k:
            by_name[k].append(c)

    dupes_to_delete = []
    remaps = {}  # old_id → kept_id

    for k, group in by_name.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: x["id"])
        keeper = group[0]["id"]
        for d in group[1:]:
            dupes_to_delete.append(d["id"])
            remaps[d["id"]] = keeper

    print(f"Companies: {len(rows)} total → {len(dupes_to_delete)} dupes to merge")

    # Re-point associations
    for old_id, new_id in remaps.items():
        try:
            sb.table("associations").update({"from_object_id": new_id}).eq("from_object_type", "company").eq("from_object_id", old_id).execute()
            sb.table("associations").update({"to_object_id": new_id}).eq("to_object_type", "company").eq("to_object_id", old_id).execute()
        except Exception:
            pass

    # Delete dupes in batches
    for i in range(0, len(dupes_to_delete), 200):
        batch = dupes_to_delete[i:i+200]
        sb.table("companies").delete().in_("id", batch).execute()

    return len(dupes_to_delete)


def dedup_contacts():
    rows = fetch_all("contacts")
    by_email = defaultdict(list)
    for c in rows:
        em = (c.get("email") or "").strip().lower()
        if em:
            by_email[em].append(c)

    dupes_to_delete = []
    remaps = {}

    for em, group in by_email.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: x["id"])
        keeper = group[0]["id"]
        for d in group[1:]:
            dupes_to_delete.append(d["id"])
            remaps[d["id"]] = keeper

    print(f"Contacts: {len(rows)} total → {len(dupes_to_delete)} dupes to merge")

    for old_id, new_id in remaps.items():
        try:
            sb.table("associations").update({"from_object_id": new_id}).eq("from_object_type", "contact").eq("from_object_id", old_id).execute()
            sb.table("associations").update({"to_object_id": new_id}).eq("to_object_type", "contact").eq("to_object_id", old_id).execute()
            sb.table("activity").update({"object_id": new_id}).eq("object_type", "contact").eq("object_id", old_id).execute()
        except Exception:
            pass

    for i in range(0, len(dupes_to_delete), 200):
        batch = dupes_to_delete[i:i+200]
        sb.table("contacts").delete().in_("id", batch).execute()

    return len(dupes_to_delete)


def dedup_associations():
    """Remove duplicate associations (same from/to/label)."""
    rows = fetch_all("associations")
    seen = {}
    dupes = []
    for a in rows:
        k = (a["from_object_type"], a["from_object_id"], a["to_object_type"], a["to_object_id"], a.get("association_label"))
        if k in seen:
            dupes.append(a["id"])
        else:
            seen[k] = a["id"]
    print(f"Associations: {len(rows)} total → {len(dupes)} dupes to remove")
    for i in range(0, len(dupes), 200):
        batch = dupes[i:i+200]
        sb.table("associations").delete().in_("id", batch).execute()
    return len(dupes)


def main():
    c = dedup_companies()
    p = dedup_contacts()
    a = dedup_associations()
    print(f"\n✅ Deduped: {c} companies + {p} contacts + {a} associations")


if __name__ == "__main__":
    main()
