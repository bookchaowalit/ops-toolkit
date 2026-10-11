#!/usr/bin/env python3
"""
Daily Email Digest — Summarize yesterday's email activity.

Generates a digest with:
- Total emails labeled
- Starred priority emails
- Archived emails
- Job responses detected
- Uncategorized emails

Logs to DB and optionally outputs JSON.
"""

# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
from cell_store import open_cell_store
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))


def main():
    json_mode = "--json" in sys.argv

    try:
        # Get yesterday's date range
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        # Read automation-log entries for Gmail from yesterday (automation cell)
        logged = open_cell_store("automation", "reporting-reader").list("automation-log", action_type="cron")
        entries = [
            r for r in logged
            if (r.get("description") or "").startswith("Gmail: ")
            and yesterday <= (r.get("executed_at") or "") < today
        ]
        entries.sort(key=lambda r: r["executed_at"], reverse=True)
        rows = [(r["description"], r["executed_at"], r.get("affected_count")) for r in entries]

        if not rows:
            result = {
                "status": "success",
                "date": yesterday,
                "message": "No Gmail activity yesterday",
                "total_labeled": 0,
                "total_starred": 0,
                "total_archived": 0,
                "total_job_responses": 0,
                "runs": 0,
            }
            if json_mode:
                import json
                print(json.dumps(result))
            else:
                print(f"📧 Daily Email Digest — {yesterday}")
                print(f"   No Gmail activity recorded")
            return

        # Parse and aggregate
        total_labeled = 0
        total_starred = 0
        total_archived = 0
        total_job_responses = 0
        runs = len(rows)

        for desc, executed_at, affected_count in rows:
            # Parse: "Gmail: 50 labeled, 0 starred, 50 archived"
            # Optional: ", 2 job responses"
            parts = desc.replace("Gmail: ", "").split(", ")
            for part in parts:
                if "labeled" in part:
                    total_labeled += int(part.split()[0])
                elif "starred" in part:
                    total_starred += int(part.split()[0])
                elif "archived" in part:
                    total_archived += int(part.split()[0])
                elif "job responses" in part:
                    total_job_responses += int(part.split()[0])

        # Output
        result = {
            "status": "success",
            "date": yesterday,
            "total_labeled": total_labeled,
            "total_starred": total_starred,
            "total_archived": total_archived,
            "total_job_responses": total_job_responses,
            "runs": runs,
        }

        if json_mode:
            import json
            print(json.dumps(result, indent=2))
        else:
            print(f"📧 Daily Email Digest — {yesterday}")
            print(f"   🏷️  Labeled: {total_labeled} emails")
            print(f"   ⭐ Starred: {total_starred} priority emails")
            print(f"   📦 Archived: {total_archived} emails")
            if total_job_responses > 0:
                print(f"   💼 Job responses: {total_job_responses}")
            print(f"   🔄 Runs: {runs}")

    except Exception as e:
        if json_mode:
            import json
            print(json.dumps({"status": "error", "error": str(e)}))
        else:
            print(f"❌ Daily digest failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
