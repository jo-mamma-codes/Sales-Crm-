"""Gmail OAuth setup and sent-mail checker.
Run once: python3 gmail_auth.py  (opens browser for OAuth consent)
Then the CRM app uses check_sent_emails() to verify sends.
"""
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
TOKEN_FILE = ROOT / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print(f"Missing {CREDS_FILE}")
                print("Download OAuth client JSON from Google Cloud Console and save as gmail_credentials.json")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=8090)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def check_sent_emails(recipient_emails, hours_back=24):
    """Check which recipient emails appear in sent mail recently.
    Returns set of email addresses that were found in sent folder.
    """
    service = get_gmail_service()
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


if __name__ == "__main__":
    print("Authenticating with Gmail...")
    service = get_gmail_service()
    if service:
        print("✅ Authenticated! Token saved to gmail_token.json")
        profile = service.users().getProfile(userId="me").execute()
        print(f"Logged in as: {profile['emailAddress']}")
    else:
        print("❌ Failed. Make sure gmail_credentials.json exists.")
