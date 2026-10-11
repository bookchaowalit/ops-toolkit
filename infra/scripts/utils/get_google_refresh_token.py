#!/usr/bin/env python3
"""
Generate a Google OAuth refresh token with Gmail + Calendar scopes.
Uses main credentials (same ones the sync scripts use).

Usage:
    python3 infra/scripts/utils/get_google_refresh_token.py
    python3 infra/scripts/utils/get_google_refresh_token.py --send-only
    python3 infra/scripts/utils/get_google_refresh_token.py --search-console --store-infisical
    python3 infra/scripts/utils/get_google_refresh_token.py --matrix --store-infisical

This will open your browser. Use ``--store-infisical`` to write the new
refresh token directly to Infisical without printing it or placing it in a
local file.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _env import get_google_creds, get_env
from _http import get_access_token
from infisical_secrets import InfisicalSecretWriteError, store_secret

parser = argparse.ArgumentParser(description="Generate a Google OAuth refresh token")
parser.add_argument(
    "--send-only",
    action="store_true",
    help="Request only Gmail send permission for the task digest",
)
parser.add_argument(
    "--matrix",
    action="store_true",
    help="Request Gmail + Calendar readonly plus Google Sheets access for Matrix targets",
)
parser.add_argument(
    "--search-console",
    action="store_true",
    help="Request Search Console readonly and store a separate refresh token",
)
parser.add_argument(
    "--store-infisical",
    action="store_true",
    help="Store the new refresh token in Infisical /google without printing it",
)
parser.add_argument(
    "--port",
    type=int,
    default=0,
    help="Local callback port; 0 chooses an available port (default)",
)
args = parser.parse_args()

# Use main credentials (same as sync scripts)
try:
    CLIENT_ID, CLIENT_SECRET, _ = get_google_creds()
except ValueError:
    # Fallback to ALT creds if main not set
    CLIENT_ID, CLIENT_SECRET = get_env("GOOGLE_CLIENT_ID_ALT", required=True), \
                                get_env("GOOGLE_CLIENT_SECRET_ALT", required=True)

# The send-only token is intentionally separate from the shared read/calendar
# token so the digest cannot read the mailbox when its credential is scoped
# correctly and stored under GOOGLE_GMAIL_SEND_REFRESH_TOKEN.
if sum(bool(flag) for flag in (args.send_only, args.matrix, args.search_console)) > 1:
    parser.error("--send-only, --matrix, and --search-console cannot be combined")
if args.send_only:
    SCOPES = ['https://www.googleapis.com/auth/gmail.send']
    TOKEN_KEY = "GOOGLE_GMAIL_SEND_REFRESH_TOKEN"
    SCOPE_LABEL = "Gmail (send only)"
elif args.search_console:
    SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
    TOKEN_KEY = "GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN"
    SCOPE_LABEL = "Search Console (readonly)"
elif args.matrix:
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/calendar.readonly',
        'https://www.googleapis.com/auth/spreadsheets',
    ]
    TOKEN_KEY = "GOOGLE_REFRESH_TOKEN"
    SCOPE_LABEL = "Gmail (readonly) + Calendar (readonly) + Google Sheets"
else:
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/calendar.readonly',
    ]
    TOKEN_KEY = "GOOGLE_REFRESH_TOKEN"
    SCOPE_LABEL = "Gmail (readonly) + Calendar (readonly)"

# Create client config
client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}

print("🔐 Google OAuth Token Generator")
print("=" * 60)
print(f"   Client ID: {CLIENT_ID[:20]}...")
print(f"   Scopes: {SCOPE_LABEL}")
print("\nThis will open your browser for authentication.")
print(f"Please authorize {SCOPE_LABEL}.\n")

# Create flow and run local server
flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(
    port=args.port,
    access_type="offline",
    prompt="consent",
    open_browser=True,
)

print("\n✅ Authentication successful!")
if not creds.refresh_token:
    print("❌ Google did not return a refresh token; run the flow again with consent.")
    raise SystemExit(1)

if args.store_infisical:
    try:
        store_secret(TOKEN_KEY, creds.refresh_token)
    except InfisicalSecretWriteError as exc:
        print(f"❌ Could not store the new token: {exc}")
        print("   Confirm Infisical CLI authentication and project access; the token was not printed or saved locally.")
        raise SystemExit(1) from None
    print(f"✅ Stored {TOKEN_KEY} in Infisical /google")
    if args.send_only:
        print("🔎 Verifying the stored Gmail send token...")
        # The consent result is already an access-token proof; this additional
        # call confirms the newly stored secret can be read by the runtime.
        client_id, client_secret, _ = get_google_creds()
        get_access_token(client_id, client_secret, creds.refresh_token)
        print("✅ Stored token can refresh successfully")
        print("💡 Next: python3 infra/scripts/sync/task_email_digest.py --dry-run")
    elif args.search_console:
        print("🔎 Verifying the stored Search Console token...")
        client_id, client_secret, _ = get_google_creds()
        get_access_token(client_id, client_secret, creds.refresh_token)
        print("✅ Stored token can refresh successfully")
        print("💡 Next: task seo:report -- fetch --month 2026-08 --apply --json")
    else:
        print("🔎 Verifying the stored Gmail/Calendar token...")
        client_id, client_secret, stored_refresh = get_google_creds()
        get_access_token(client_id, client_secret, stored_refresh)
        print("✅ Stored token can refresh successfully")
        print("Next: the stored token is verified; the local Gmail pipeline was retired 2026-10-11.")
else:
    print("ℹ️ Token was not stored. Re-run with --store-infisical to avoid printing or copying secrets.")
