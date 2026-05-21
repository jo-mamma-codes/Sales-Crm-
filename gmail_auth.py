"""Gmail OAuth setup and sent-mail checker — multi-account support.

Setup per sender:
    python3 gmail_auth.py joseph.allison@yetipay.me
    python3 gmail_auth.py dominic.ritchie@yetipay.me
    python3 gmail_auth.py insidesales@yetipay.me

Each saves its own token: gmail_token_<email>.json
The CRM picks the right token per sender automatically.
"""
import sys
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
CREDS_FILE = ROOT / "gmail_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Must match SENDERS config in app.py
SENDER_PROFILES = {
    "joseph.allison@yetipay.me": "Profile 5",
    "insidesales@yetipay.me": "Profile 4",
    "dominic.ritchie@yetipay.me": "Profile 1",
    "ashley@yetipay.me": "Profile 5",
}


def _token_file(email=None):
    if email:
        safe = email.replace("@", "_at_").replace(".", "_")
        return ROOT / f"gmail_token_{safe}.json"
    return ROOT / "gmail_token.json"


def get_gmail_service(sender_email=None):
    """Get Gmail API service for sender_email.

    Tries per-sender token first (gmail_token_<email>.json), falls back to master.
    """
    # Prefer per-sender token if it exists
    if sender_email:
        per_sender_path = _token_file(sender_email)
        if per_sender_path.exists():
            token_path = per_sender_path
        else:
            token_path = _token_file(None)
    else:
        token_path = _token_file(None)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        else:
            if not CREDS_FILE.exists():
                print(f"Missing {CREDS_FILE}")
                print("Download OAuth client JSON from Google Cloud Console and save as gmail_credentials.json")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            # login_hint pre-fills email, prompt=select_account forces picker
            extra = {"login_hint": sender_email, "prompt": "select_account"} if sender_email else {}
            # Open in sender's Chrome profile so correct Google session is used
            chrome_profile = SENDER_PROFILES.get(sender_email)
            if chrome_profile:
                import subprocess, webbrowser
                auth_url, _ = flow.authorization_url(**extra)
                print(f"\n🔗 Opening in {chrome_profile} Chrome profile...")
                print(f"   If browser doesn't open, paste this URL:\n   {auth_url}\n")
                subprocess.Popen([
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    f"--profile-directory={chrome_profile}",
                    auth_url,
                ])
                creds = flow.run_local_server(port=8090, open_browser=False, **extra)
            else:
                creds = flow.run_local_server(port=8090, **extra)
            # Save to per-sender token
            save_path = _token_file(sender_email) if sender_email else _token_file(None)
            save_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_authenticated_senders():
    """Return list of sender emails that have tokens set up."""
    senders = []
    for f in ROOT.glob("gmail_token_*.json"):
        name = f.stem.replace("gmail_token_", "").replace("_at_", "@").replace("_", ".")
        senders.append(name)
    # Legacy single token
    if (ROOT / "gmail_token.json").exists() and not senders:
        senders.append("joseph.allison@yetipay.me")  # original default
    return senders


def check_sent_emails(recipient_emails, hours_back=24, sender_email=None):
    """Check which recipient emails appear in sent mail recently."""
    service = get_gmail_service(sender_email)
    if not service:
        return set()

    after = datetime.now() - timedelta(hours=hours_back)
    after_str = after.strftime("%Y/%m/%d")
    found = set()

    for email in recipient_emails:
        query = f"to:{email} after:{after_str} in:sent"
        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=1
            ).execute()
            if results.get("messages"):
                found.add(email.lower())
        except Exception:
            continue

    return found


def check_bounces(recipient_emails, hours_back=72, sender_email=None):
    """Check which emails bounced (delivery failure notifications)."""
    service = get_gmail_service(sender_email)
    if not service:
        return set()

    after = datetime.now() - timedelta(hours=hours_back)
    after_str = after.strftime("%Y/%m/%d")
    bounced = set()

    for email in recipient_emails:
        query = f'("{email}" subject:("delivery" OR "undeliverable" OR "returned" OR "failure" OR "bounce" OR "not delivered")) after:{after_str}'
        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=1
            ).execute()
            if results.get("messages"):
                bounced.add(email.lower())
        except Exception:
            continue

    return bounced


def check_replies(recipient_emails, hours_back=168, sender_email=None):
    """Check which recipients have replied (email FROM them in inbox)."""
    service = get_gmail_service(sender_email)
    if not service:
        return set()

    after = datetime.now() - timedelta(hours=hours_back)
    after_str = after.strftime("%Y/%m/%d")
    replied = set()

    for email in recipient_emails:
        query = f"from:{email} after:{after_str} in:inbox"
        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=1
            ).execute()
            if results.get("messages"):
                replied.add(email.lower())
        except Exception:
            continue

    return replied


if __name__ == "__main__":
    email_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if email_arg:
        print(f"Authenticating {email_arg}...")
    else:
        print("Usage: python3 gmail_auth.py <sender@email.com>")
        print("Example: python3 gmail_auth.py joseph.allison@yetipay.me")
        print("\nNo email specified — using legacy default token.")

    service = get_gmail_service(email_arg)
    if service:
        token = _token_file(email_arg)
        profile = service.users().getProfile(userId="me").execute()
        print(f"✅ Authenticated as: {profile['emailAddress']}")
        print(f"   Token saved: {token.name}")
    else:
        print("❌ Failed. Make sure gmail_credentials.json exists.")
