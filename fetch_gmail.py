"""Fetch Gmail threads for CRM contacts and cache locally.
Run: python3 fetch_gmail.py <email> OR python3 fetch_gmail.py --all
This is called by the CRM app via subprocess or manually.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GMAIL_CACHE = ROOT / "gmail_cache"
DATA = ROOT / "crm_data.csv"


def fetch_for_email(email_addr):
    """Use mcp-cli or direct API - placeholder for manual cache population."""
    GMAIL_CACHE.mkdir(exist_ok=True)
    safe = email_addr.replace("@", "_at_").replace(".", "_")
    cache_file = GMAIL_CACHE / f"{safe}.json"
    print(f"Cache file: {cache_file}")
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
        print(f"  Cached {len(data.get('threads', []))} threads (fetched {data.get('fetched', '?')})")
    else:
        print(f"  No cache. Use 'Refresh from Gmail' button in CRM or populate manually.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_gmail.py <email> OR --all")
        sys.exit(1)
    if sys.argv[1] == "--all":
        import pandas as pd
        df = pd.read_csv(DATA, dtype=str).fillna("")
        contacted = df[df["stage"].isin(["Contacted", "Demo Booked", "Proposal", "Won"])]
        for _, r in contacted.iterrows():
            if r["email"]:
                fetch_for_email(r["email"])
    else:
        fetch_for_email(sys.argv[1])
