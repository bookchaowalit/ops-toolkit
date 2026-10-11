#!/usr/bin/env python3
"""
Gmail Email Categorizer — Fetches recent unread emails and applies
category labels automatically based on sender, subject, and content.

Uses existing label system (numeric prefix order):
  - 00 Action/             (Read Later, Reply, Today, Waiting)
  - 01 Money/Banking Cards (banks, payments)
  - 01 Money/Investing Crypto (crypto exchanges, DCA)
  - 02 Receipts/Epic Games (game purchases)
  - 02 Receipts/Google Play (app/store purchases)
  - 02 Receipts/Grab Food  (ride-hailing, food delivery)
  - 02 Receipts/Other      (invoices, receipts, eTax)
  - 03 Tech/AI Dev News    (AI newsletters, dev blogs)
  - 03 Tech/CI/CD          (CI pipeline, deployment)
  - 03 Tech/Database       (database alerts)
  - 03 Tech/Dev Alerts     (GitHub, GitLab, dev tools)
  - 03 Tech/Operations     (runtime, backup, platform incidents)
  - 03 Tech/SEO-Monitoring  (site audits, crawl failures)
  - 03 Tech/Security       (security alerts, vulnerability)
  - 04 Work/Freelance      (job-related, companies, recruiters)
  - 04 Work/Job Apps       (job applications, LinkedIn, Indeed)
  - 05 Home/Condo          (condo management, maintenance)
  - 05 Home/Important Docs (important documents)
  - 06 Travel              (flights, hotels, booking)
  - 07 Newsletters         (mailing lists, digests)
  - 99 Noise/Promotions    (sales, discounts, marketing)
  - 99 Noise/Unsubscribe Candidates
  - 99 Noise/Unsubscribed

Usage:
    python3 gmail_categorizer.py              # Normal run
    python3 gmail_categorizer.py --json        # JSON output
    python3 gmail_categorizer.py --dry-run     # Show what would be done
    python3 gmail_categorizer.py --days 1      # Process emails from last N days
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import open_cell_store
from task_store import open_task_store
import json
import sys
import urllib.parse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_google_creds
from _http import api_request, get_access_token as _get_access_token

CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN = get_google_creds()

# ── Category rules mapped to existing labels ────────────────
# Order matters: first match wins. More specific rules first.
CATEGORY_RULES = [
    # ── 00 Action ───────────────────────────────────────────
    {
        "category": "Action",
        "label_name": "00 Action/Today",
        "sender_domains": [],
        "subject_keywords": [
            "action required", "action needed", "please respond",
            "reply needed", "urgent action",
        ],
    },
    # ── 01 Money ────────────────────────────────────────────
    {
        "category": "Investing Crypto",
        "label_name": "01 Money/Investing Crypto",
        "sender_domains": [
            "binance.com", "binance.th", "kraken.com", "coinbase.com",
            "myfxbook.com",
        ],
        "subject_keywords": [
            "dca transaction", "crypto", "trading",
            "สรุปข้อมูลการลงทุน", "weekly economic calendar",
        ],
    },
    {
        "category": "Finance",
        "label_name": "01 Money/Banking Cards",
        "sender_domains": [
            "paypal.com", "stripe.com", "wise.com", "revolut.com",
            "hsbc.com", "scb.com", "kbank.com", "bangkokbank.com",
            "kasikornbank.com", "chase.com", "wellsfargo.com",
            "citibank.com", "dime.co.th",
        ],
        "subject_keywords": [
            "payment", "transfer", "bank", "transaction", "balance",
            "monthly statement",
        ],
    },
    # ── 02 Receipts ─────────────────────────────────────────
    {
        "category": "Receipts Epic Games",
        "label_name": "02 Receipts/Epic Games",
        "sender_domains": [
            "epicgames.com", "unrealengine.com",
        ],
        "subject_keywords": [
            "epic games", "unreal engine", "fortnite",
        ],
    },
    {
        "category": "Receipts Google Play",
        "label_name": "02 Receipts/Google Play",
        "sender_domains": [
            "google.com",  # Google Play receipts come from google.com
        ],
        "subject_keywords": [
            "google play", "play store", "android purchase",
        ],
    },
    {
        "category": "Receipts Grab",
        "label_name": "02 Receipts/Grab Food",
        "sender_domains": [
            "grab.com",
        ],
        "subject_keywords": [
            "grab", "grabfood", "grab ride",
        ],
    },
    {
        "category": "Receipts",
        "label_name": "02 Receipts/Other",
        "sender_domains": [
            "iameztax.com",  # eTax invoices
        ],
        "subject_keywords": [
            "invoice", "receipt", "billing", "subscription charge",
            "ใบเสร็จ", "ใบกำกับภาษี", "etax",
            "postpaid bill", "bill is due",
            "สรุปยอดขาย", "คำสั่งซื้อ",
        ],
    },
    # ── 03 Tech ─────────────────────────────────────────────
    {
        "category": "AI Dev News",
        "label_name": "03 Tech/AI Dev News",
        "sender_domains": [
            "email.openai.com", "anthropic.com", "huggingface.co",
            "paperswithcode.com", "ai.googleblog.com",
        ],
        "subject_keywords": [
            "ai news", "llm", "machine learning", "deep learning",
            "model release", "gpt-", "claude",
        ],
    },
    {
        "category": "Operations",
        "label_name": "03 Tech/Operations",
        "sender_domains": [],
        "subject_keywords": [
            "server failure", "daily db backup", "backup workflow",
            "workflow run", "run failed", "build failed", "node.js 20",
            "node.js 24", "database expired", "outage", "incident",
        ],
    },
    {
        "category": "SEO Monitoring",
        "label_name": "03 Tech/SEO-Monitoring",
        "sender_domains": [
            "ahrefs.com",
        ],
        "subject_keywords": [
            "site audit", "crawl failed", "crawl error",
        ],
    },
    {
        "category": "CI/CD",
        "label_name": "03 Tech/CI/CD",
        "sender_domains": [
            "vercel.com", "netlify.com", "railway.app",
        ],
        "subject_keywords": [
            "deploy", "deployment", "ci -", "run failed",
            "build failed", "pipeline",
        ],
    },
    {
        "category": "Database",
        "label_name": "03 Tech/Database",
        "sender_domains": [
            "neon.tech", "supabase.com", "planetscale.com",
        ],
        "subject_keywords": [
            "database", "migration", "query",
        ],
    },
    {
        "category": "Security",
        "label_name": "03 Tech/Security",
        "sender_domains": [
            "gitguardian.com",
        ],
        "subject_keywords": [
            "security alert", "credential", "exposed", "vulnerability",
            "incident detected", "api key", "new device", "new ip",
            "login attempted", "verify account",
        ],
    },
    {
        "category": "Dev",
        "label_name": "03 Tech/Dev Alerts",
        "sender_domains": [
            "github.com", "gitlab.com", "bitbucket.org",
            "cloudflare.com", "todoist.com",
            "ifttt.com", "godaddy.com",
            "google.com",  # Search Console, indexing issues
        ],
        "subject_keywords": [
            "pull request", "merge", "code review",
            "github pages", "daily:", "task reminder",
            "scheduled tasks", "prevent pages from being indexed", "sitemap",
        ],
    },
    # ── 04 Work ─────────────────────────────────────────────
    {
        "category": "Job Apps",
        "label_name": "04 Work/Job Apps",
        "sender_domains": [
            "linkedin.com", "indeed.com", "glassdoor.com", "lever.co",
            "greenhouse.io", "workday.com", "taleo.net", "icims.com",
            "smartrecruiters.com", "jobvite.com", "bamboohr.com",
            "myworkday.com", "jobs.com", "reed.co.uk", "cv-library.co.uk",
            "upwork.com", "freelancer.com",
        ],
        "subject_keywords": [
            "application", "interview", "job offer", "position",
            "your application", "hiring", "candidate", "onboarding",
        ],
    },
    {
        "category": "Work",
        "label_name": "04 Work/Freelance",
        "sender_domains": [
            "slack.com", "notion.so", "asana.com", "jira.com",
            "atlassian.net", "figma.com", "zoom.us", "teams.microsoft.com",
            "trello.com", "monday.com", "clickup.com", "linear.app",
            "fastwork.co",  # Freelance platform notifications
        ],
        "subject_keywords": [
            "meeting", "sprint", "task assigned", "project update", "standup",
            # Thai keywords for freelance work
            "ผู้จ้างอนุมัติ", "อนุมัติงาน", "ให้เรตติ้ง", "รีวิว",
        ],
    },
    # ── 05 Home ─────────────────────────────────────────────
    {
        "category": "Home Condo",
        "label_name": "05 Home/Condo",
        "sender_domains": [],
        "subject_keywords": [
            "condo", "นิติบุคคล", "ส่วนกลาง", "ค่าส่วนกลาง",
            "maintenance fee", "juristic person",
        ],
    },
    {
        "category": "Home Important Docs",
        "label_name": "05 Home/Important Docs",
        "sender_domains": [],
        "subject_keywords": [
            "เอกสารสำคัญ", "important document",
            "สัญญา", "contract", "agreement",
        ],
    },
    # ── 06 Travel ───────────────────────────────────────────
    {
        "category": "Travel",
        "label_name": "06 Travel",
        "sender_domains": [
            "booking.com", "airbnb.com", "expedia.com", "agoda.com",
            "hotels.com", "skyscanner.com", "kayak.com", "tripadvisor.com",
            "uber.com", "airlines.com", "thaiairways.com",
        ],
        "subject_keywords": [
            "flight", "hotel", "booking", "reservation", "check-in",
            "itinerary", "departure", "arrival",
        ],
    },
    # ── 07 Newsletters ──────────────────────────────────────
    {
        "category": "Newsletters",
        "label_name": "07 Newsletters",
        "sender_domains": [
            "substack.com", "medium.com", "mailchimp.com",
            "convertkit.com", "beehiiv.com", "ghost.io",
            "email.meetup.com",  # Meetup group notifications
            "crm.aia.co.th",  # AIA event notifications
        ],
        "subject_keywords": [
            "newsletter", "weekly digest", "daily roundup", "this week in",
            "edition", "issue #", "vol.",
        ],
    },
    # ── 99 Noise ────────────────────────────────────────────
    {
        "category": "Promotions",
        "label_name": "99 Noise/Promotions",
        "sender_domains": [
            "creativefabrica.com",  # Free design picks
            "th.email.samsung.com",  # Samsung TH promotions
        ],
        "subject_keywords": [
            "sale", "discount", "offer", "coupon", "deal", "promo",
            "limited time", "flash sale", "clearance", "buy now",
            "special offer", "free shipping",
            "free picks", "today's free",
        ],
    },
]


# ── HTTP helpers (delegated to shared _http module) ─────────
def get_access_token():
    """Get Google access token using configured credentials."""
    return _get_access_token(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)


# ── Gmail API helpers ───────────────────────────────────────
def fetch_recent_emails(token, days=1, max_results=50):
    """Fetch recent unread emails without custom labels."""
    after_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    # Exclude emails that already have our category labels
    exclude_labels = " ".join([f"-label:\"{r['label_name']}\"" for r in CATEGORY_RULES])
    params = urllib.parse.urlencode({
        "q": f"is:unread after:{after_date} {exclude_labels}",
        "maxResults": max_results,
    })
    url = f"https://www.googleapis.com/gmail/v1/users/me/messages?{params}"
    resp = api_request(url, headers={"Authorization": f"Bearer {token}"})
    message_ids = [m["id"] for m in resp.get("messages", [])]

    emails = []
    for msg_id in message_ids:
        msg_url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full"
        try:
            msg = api_request(msg_url, headers={"Authorization": f"Bearer {token}"})
        except Exception:
            continue

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        emails.append({
            "id": msg_id,
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })
    return emails


def get_existing_labels(token):
    """Get all existing Gmail labels as {name: id}."""
    url = "https://www.googleapis.com/gmail/v1/users/me/labels"
    resp = api_request(url, headers={"Authorization": f"Bearer {token}"})
    return {l["name"]: l["id"] for l in resp.get("labels", [])}


def apply_label(token, message_id, label_id):
    """Apply a label to a message."""
    url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify"
    data = json.dumps({"addLabelIds": [label_id]}).encode()
    api_request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )


def modify_labels(token, message_id, add_labels=None, remove_labels=None):
    """Add and/or remove labels from a message."""
    url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify"
    payload = {}
    if add_labels:
        payload["addLabelIds"] = add_labels
    if remove_labels:
        payload["removeLabelIds"] = remove_labels
    data = json.dumps(payload).encode()
    api_request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )


# ── Priority categories (will be starred) ───────────────────
PRIORITY_CATEGORIES = {"Job Apps", "Work", "Operations", "SEO Monitoring", "Security"}

# ── Auto-archive categories (remove from inbox after labeling) ──
AUTO_ARCHIVE_CATEGORIES = {"Dev", "Receipts", "Newsletters", "Promotions", "Finance"}

# ── Job response detection keywords ─────────────────────────
JOB_RESPONSE_KEYWORDS = [
    "thank you for applying", "your application", "we received your",
    "interview", "next steps", "application status",
    "congratulations", "offer letter", "welcome aboard",
]


# ── Categorization logic ────────────────────────────────────
def extract_domain(from_header):
    """Extract domain from From header."""
    try:
        if "<" in from_header:
            email = from_header.split("<")[1].split(">")[0]
        else:
            email = from_header.strip()
        return email.split("@")[-1].lower()
    except Exception:
        return ""


def categorize_email(email):
    """Determine category for an email based on rules."""
    domain = extract_domain(email["from"])
    subject_lower = email["subject"].lower()
    snippet_lower = email["snippet"].lower()
    combined_text = f"{subject_lower} {snippet_lower}"

    for rule in CATEGORY_RULES:
        # Check sender domain
        for sd in rule["sender_domains"]:
            if sd in domain:
                return rule["category"]
        # Check subject keywords
        for kw in rule["subject_keywords"]:
            if kw in combined_text:
                return rule["category"]

    return None  # No match


# ── Main ────────────────────────────────────────────────────
def main():
    json_mode = "--json" in sys.argv
    dry_run = "--dry-run" in sys.argv
    days = 1
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])

    try:
        token = get_access_token()

        # Fetch uncategorized unread emails
        emails = fetch_recent_emails(token, days=days, max_results=50)

        if not emails:
            result = {"status": "success", "categorized": 0, "total": 0, "dry_run": dry_run}
            if json_mode:
                print(json.dumps(result))
            else:
                print("📧 No uncategorized emails found.")
            return

        # Get existing labels and build category -> label_id map
        existing_labels = get_existing_labels(token)
        label_map = {}  # category -> label_id

        for rule in CATEGORY_RULES:
            cat = rule["category"]
            label_name = rule["label_name"]
            if label_name in existing_labels:
                label_map[cat] = existing_labels[label_name]
            else:
                if not json_mode:
                    print(f"   ⚠️  Label '{label_name}' not found, skipping {cat}")

        # Categorize and label emails
        categorized = {rule["category"]: [] for rule in CATEGORY_RULES}
        categorized["Uncategorized"] = []
        job_responses = []  # Track potential job application responses

        for email in emails:
            cat = categorize_email(email)
            if cat and cat in label_map:
                categorized[cat].append(email)
                if not dry_run:
                    try:
                        # Apply category label
                        apply_label(token, email["id"], label_map[cat])
                        time.sleep(0.1)  # rate limit

                        # Star priority emails (Job Apps, Work)
                        if cat in PRIORITY_CATEGORIES:
                            modify_labels(token, email["id"], add_labels=["STARRED"])
                            time.sleep(0.1)

                        # Auto-archive certain categories (remove from inbox)
                        if cat in AUTO_ARCHIVE_CATEGORIES:
                            modify_labels(token, email["id"], remove_labels=["INBOX"])
                            time.sleep(0.1)

                        # Detect job application responses
                        subject_lower = email["subject"].lower()
                        snippet_lower = email["snippet"].lower()
                        combined = f"{subject_lower} {snippet_lower}"
                        if any(kw in combined for kw in JOB_RESPONSE_KEYWORDS):
                            job_responses.append(email)

                    except Exception as e:
                        if not json_mode:
                            print(f"   ⚠️  Failed to label '{email['subject'][:40]}': {e}")
            else:
                categorized["Uncategorized"].append(email)

        # Build summary
        total = len(emails)
        cat_counts = {cat: len(msgs) for cat, msgs in categorized.items() if msgs}
        starred_count = sum(len(categorized.get(c, [])) for c in PRIORITY_CATEGORIES)
        archived_count = sum(len(categorized.get(c, [])) for c in AUTO_ARCHIVE_CATEGORIES)

        if json_mode:
            print(json.dumps({
                "status": "success",
                "categorized": sum(v for k, v in cat_counts.items() if k != "Uncategorized"),
                "uncategorized": cat_counts.get("Uncategorized", 0),
                "total": total,
                "by_category": cat_counts,
                "starred": starred_count,
                "archived": archived_count,
                "job_responses": len(job_responses),
                "dry_run": dry_run,
            }, indent=2))
        else:
            print(f"📧 Gmail Categorizer — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            print(f"   Found {total} uncategorized emails")
            if dry_run:
                print(f"   🔍 DRY RUN — no labels applied")
            print()
            icons = {"Job Apps": "💼", "Work": "💻", "Dev": "🔧", "Finance": "💰",
                     "Receipts": "🧾", "Receipts Epic Games": "🎮",
                     "Receipts Google Play": "📱", "Receipts Grab": "🛵",
                     "Newsletters": "📰", "Promotions": "🏷️",
                     "Travel": "✈️", "Action": "🔴",
                     "Investing Crypto": "📈", "AI Dev News": "🤖",
                     "CI/CD": "🚀", "Database": "🗄️", "Security": "🔒",
                     "Home Condo": "🏠", "Home Important Docs": "📄",
                     "Uncategorized": "📎"}
            for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
                icon = icons.get(cat, "📎")
                print(f"   {icon} {cat:15} {count:3} emails")
            print()
            labeled = sum(v for k, v in cat_counts.items() if k != "Uncategorized")
            print(f"   ✅ Labeled {labeled} emails ({cat_counts.get('Uncategorized', 0)} uncategorized)")
            if not dry_run:
                print(f"   ⭐ Starred {starred_count} priority emails")
                print(f"   📦 Archived {archived_count} emails")
                if job_responses:
                    print(f"   💼 Job responses detected: {len(job_responses)}")
                    for jr in job_responses[:3]:  # Show top 3
                        print(f"      - {jr['subject'][:50]}")

        # Log the run to the automation cell and open review tasks
        if not dry_run:
            try:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                labeled_count = sum(v for k, v in cat_counts.items() if k != "Uncategorized")
                desc = f"Gmail: {labeled_count} labeled, {starred_count} starred, {archived_count} archived"
                if job_responses:
                    desc += f", {len(job_responses)} job responses"
                try:
                    open_cell_store("automation", "email-pipeline").create("automation-log", {
                        "action_type": "cron", "description": desc, "affected_count": labeled_count,
                        "success": True, "executed_at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    pass  # the run log is best-effort; the review tasks below still go ahead

                # Create notification tasks for job responses
                store = open_task_store(None, "email-pipeline")
                for jr in job_responses:
                    # Keyed by the Gmail message id so a retry cannot duplicate the task,
                    # and an existing task keeps whatever the owner changed on it.
                    if store.find_external("gmail-job-response", jr["id"]) is not None:
                        continue
                    store.upsert_external("gmail-job-response", jr["id"], {
                        "title": f"Job response: {jr['subject'][:80]}",
                        "description": f"From: {jr['from']}\nDate: {jr['date']}\n\n{jr['snippet']}",
                        "priority": 1,
                        "project": "Job Applications",
                        "category": "income",
                    }, legacy={"created_at": now, "updated_at": now})

            except Exception:
                pass  # non-critical

    except Exception as e:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(e)}))
        else:
            print(f"❌ Gmail categorizer failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
