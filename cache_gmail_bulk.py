"""Cache Gmail thread data for CRM contacts.
Processes raw Gmail API thread data and writes per-email cache files.
Usage: pipe JSON threads array into stdin, or call cache_threads() directly.
"""
import json
from pathlib import Path
from datetime import datetime

GMAIL_CACHE = Path(__file__).resolve().parent / "gmail_cache"


def cache_threads(threads):
    """Take a list of Gmail thread dicts and cache per-recipient."""
    GMAIL_CACHE.mkdir(exist_ok=True)
    per_email = {}

    for t in threads:
        for msg in t.get("messages", []):
            recipients = msg.get("toRecipients", [])
            sender = msg.get("sender", "")
            # Group by the non-yetipay email address
            contact_email = None
            if "yetipay" in sender.lower():
                for r in recipients:
                    if "yetipay" not in r.lower():
                        contact_email = r.lower()
                        break
            else:
                contact_email = sender.lower()

            if not contact_email:
                continue

            if contact_email not in per_email:
                per_email[contact_email] = []

            # Check if thread already added
            thread_ids = [tt["id"] for tt in per_email[contact_email]]
            if t["id"] not in thread_ids:
                per_email[contact_email].append(t)

    cached = 0
    for email, email_threads in per_email.items():
        safe = email.replace("@", "_at_").replace(".", "_")
        cache_file = GMAIL_CACHE / f"{safe}.json"

        # Merge with existing cache
        existing_threads = []
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                existing_threads = data.get("threads", [])
            except Exception:
                pass

        existing_ids = {t["id"] for t in existing_threads}
        for t in email_threads:
            if t["id"] not in existing_ids:
                existing_threads.append(t)

        cache_file.write_text(json.dumps({
            "fetched": datetime.now().isoformat(timespec="seconds"),
            "email": email,
            "threads": existing_threads,
        }, indent=2))
        cached += 1

    return cached, len(per_email)
