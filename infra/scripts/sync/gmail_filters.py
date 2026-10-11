#!/usr/bin/env python3
"""
Gmail Server-Side Filters — Create Gmail filters via API for automatic
categorization at delivery time (no need to wait for hourly cron).

Maps existing CATEGORY_RULES from gmail_categorizer.py to Gmail filter criteria.
Skips filters that already exist (by query match).

Usage:
    python3 gmail_filters.py              # Create/update filters
    python3 gmail_filters.py --json       # JSON output
    python3 gmail_filters.py --dry-run    # Show what would be created
    python3 gmail_filters.py --list       # List existing filters
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_google_creds
from _http import api_request, get_access_token as _get_access_token

CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()

# ── Filter definitions ──────────────────────────────────────
# Each filter: (query, add_label_name, [optional actions])
# Query uses Gmail search syntax
FILTERS = [
    # Dev / Tech alerts
    {
        "query": "from:(github.com) OR from:(gitlab.com) OR from:(bitbucket.org) OR from:(vercel.com) OR from:(netlify.com) OR from:(cloudflare.com) OR from:(neon.tech) OR from:(supabase.com) OR from:(railway.app) OR from:(todoist.com) OR from:(email.openai.com) OR from:(ifttt.com) OR from:(godaddy.com) OR from:(gitguardian.com)",
        "label": "03 Tech/Dev Alerts",
        "actions": ["archive"],
    },
    {
        "query": "subject:(deploy) OR subject:(\"pull request\") OR subject:(merge) OR subject:(\"security alert\") OR subject:(\"run failed\") OR subject:(\"ci -\") OR subject:(\"incident detected\")",
        "label": "03 Tech/Dev Alerts",
        "actions": ["archive"],
    },
    # Google Search Console
    {
        "query": "from:(google.com) subject:(search console OR indexing OR sitemap)",
        "label": "03 Tech/Dev Alerts",
        "actions": ["archive"],
    },
    # Job Applications
    {
        "query": "from:(linkedin.com) OR from:(indeed.com) OR from:(glassdoor.com) OR from:(lever.co) OR from:(greenhouse.io) OR from:(workday.com) OR from:(upwork.com) OR from:(freelancer.com)",
        "label": "04 Work/Job Apps",
        "actions": ["star"],
    },
    {
        "query": "subject:(application) OR subject:(interview) OR subject:(\"job offer\") OR subject:(hiring) OR subject:(onboarding)",
        "label": "04 Work/Job Apps",
        "actions": ["star"],
    },
    # Work / Freelance
    {
        "query": "from:(slack.com) OR from:(notion.so) OR from:(asana.com) OR from:(jira.com) OR from:(atlassian.net) OR from:(figma.com) OR from:(trello.com) OR from:(clickup.com) OR from:(linear.app) OR from:(fastwork.co)",
        "label": "04 Work/Freelance",
        "actions": ["star"],
    },
    # Finance
    {
        "query": "from:(paypal.com) OR from:(stripe.com) OR from:(wise.com) OR from:(binance.com) OR from:(binance.th) OR from:(kraken.com) OR from:(scb.com) OR from:(kbank.com) OR from:(bangkokbank.com) OR from:(dime.co.th) OR from:(myfxbook.com)",
        "label": "01 Money/Banking Cards",
        "actions": ["archive"],
    },
    # Receipts — Grab
    {
        "query": "from:(grab.com)",
        "label": "02 Receipts/Grab Food",
        "actions": ["archive"],
    },
    # Receipts — Epic Games
    {
        "query": "from:(epicgames.com) OR from:(unrealengine.com)",
        "label": "02 Receipts/Epic Games",
        "actions": ["archive"],
    },
    # Receipts — Google Play
    {
        "query": "from:(google.com) subject:(google play OR play store)",
        "label": "02 Receipts/Google Play",
        "actions": ["archive"],
    },
    # Receipts — Other
    {
        "query": "from:(iameztax.com) OR subject:(invoice) OR subject:(receipt) OR subject:(billing) OR subject:(ใบเสร็จ) OR subject:(ใบกำกับภาษี)",
        "label": "02 Receipts/Other",
        "actions": ["archive"],
    },
    # Newsletters
    {
        "query": "from:(substack.com) OR from:(medium.com) OR from:(mailchimp.com) OR from:(convertkit.com) OR from:(beehiiv.com) OR from:(ghost.io) OR from:(email.meetup.com)",
        "label": "07 Newsletters",
        "actions": ["archive"],
    },
    # Promotions
    {
        "query": "from:(creativefabrica.com) OR from:(th.email.samsung.com) OR subject:(sale) OR subject:(discount) OR subject:(\"special offer\") OR subject:(\"free picks\")",
        "label": "99 Noise/Promotions",
        "actions": ["archive"],
    },
    # Travel
    {
        "query": "from:(booking.com) OR from:(airbnb.com) OR from:(expedia.com) OR from:(agoda.com) OR from:(hotels.com) OR from:(skyscanner.com) OR subject:(flight) OR subject:(hotel) OR subject:(reservation)",
        "label": "06 Travel",
        "actions": [],
    },
]


# ── HTTP helpers (delegated to shared _http module) ─────────
def get_access_token():
    """Get Google access token using configured credentials."""
    return _get_access_token(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)


def get_existing_filters(token):
    """Get all existing Gmail filters."""
    url = "https://www.googleapis.com/gmail/v1/users/me/settings/filters"
    resp = api_request(url, headers={"Authorization": f"Bearer {token}"})
    return resp.get("filter", [])


def get_labels(token):
    """Get all labels as {name: id}."""
    url = "https://www.googleapis.com/gmail/v1/users/me/labels"
    resp = api_request(url, headers={"Authorization": f"Bearer {token}"})
    return {l["name"]: l["id"] for l in resp.get("labels", [])}


def create_filter(token, criteria, action):
    """Create a Gmail filter."""
    url = "https://www.googleapis.com/gmail/v1/users/me/settings/filters"
    body = {"criteria": criteria, "action": action}
    data = json.dumps(body).encode()
    return api_request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )


def filter_exists(existing_filters, query):
    """Check if a filter with this query already exists."""
    for f in existing_filters:
        if f.get("criteria", {}).get("query") == query:
            return True
    return False


def main():
    json_mode = "--json" in sys.argv
    dry_run = "--dry-run" in sys.argv
    list_only = "--list" in sys.argv

    try:
        token = get_access_token()

        # List existing filters
        existing = get_existing_filters(token)
        labels = get_labels(token)

        if list_only:
            if json_mode:
                print(json.dumps({"filters": existing}, indent=2))
            else:
                print(f"📧 Existing Gmail Filters ({len(existing)})")
                for f in existing:
                    criteria = f.get("criteria", {})
                    action = f.get("action", {})
                    query = criteria.get("query", "N/A")
                    add_labels = action.get("addLabelIds", [])
                    label_names = []
                    for lid in add_labels:
                        for name, id_ in labels.items():
                            if id_ == lid:
                                label_names.append(name)
                                break
                        else:
                            label_names.append(lid)
                    print(f"   🔍 {query[:60]}")
                    if label_names:
                        print(f"      → {', '.join(label_names)}")
                    if action.get("removeLabelIds"):
                        print(f"      → remove: {action['removeLabelIds']}")
            return

        # Create missing filters
        created = 0
        skipped = 0
        errors = 0

        for f in FILTERS:
            query = f["query"]
            label_name = f["label"]
            actions = f.get("actions", [])

            # Check if filter already exists
            if filter_exists(existing, query):
                skipped += 1
                continue

            # Resolve label ID
            label_id = labels.get(label_name)
            if not label_id:
                if not json_mode:
                    print(f"   ⚠️  Label '{label_name}' not found, skipping")
                errors += 1
                continue

            # Build action
            action = {"addLabelIds": [label_id]}
            if "archive" in actions:
                action["removeLabelIds"] = ["INBOX"]
            if "star" in actions:
                action["addLabelIds"].append("STARRED")

            criteria = {"query": query}

            if dry_run:
                if not json_mode:
                    print(f"   🔍 Would create: {query[:60]}")
                    print(f"      → {label_name} {actions}")
                created += 1
                continue

            try:
                create_filter(token, criteria, action)
                created += 1
                if not json_mode:
                    print(f"   ✅ {label_name}: {query[:50]}")
                time.sleep(0.2)  # rate limit
            except Exception as e:
                errors += 1
                if not json_mode:
                    print(f"   ❌ Failed: {query[:40]} — {e}")

        # Output
        result = {
            "status": "success",
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "total_existing": len(existing),
            "dry_run": dry_run,
        }

        if json_mode:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n📧 Gmail Server-Side Filters")
            prefix = "🔍 DRY RUN — " if dry_run else ""
            print(f"   ✅ Created {created} filters")
            print(f"   ⏭️  Skipped {skipped} (already exist)")
            if errors:
                print(f"   ❌ Errors: {errors}")
            print(f"   📊 Total existing: {len(existing)}")

    except Exception as e:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(e)}))
        else:
            print(f"❌ Gmail filters failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
