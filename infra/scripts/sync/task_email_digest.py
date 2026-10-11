#!/usr/bin/env python3
"""Build and optionally send a categorized Solo Empire task digest.

The database remains the task source of truth.  This script is a delivery
channel only: it reads open tasks, groups them by Generalist path/category, and
prints a dry-run digest unless ``--send`` is explicitly supplied.

Examples:
    python3 task_email_digest.py --dry-run
    python3 task_email_digest.py --dry-run --project '🌐 Generalist Paths'
    python3 task_email_digest.py --send --to owner@example.com
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import smtplib
import ssl
import sys
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import get_env, get_google_creds  # noqa: E402
from _http import api_request, get_access_token  # noqa: E402
from smtp_delivery import send_smtp_message  # noqa: E402
from task_store import open_task_store  # noqa: E402


PATH_TAG_PATTERN = re.compile(r"^path:(.+)$", re.IGNORECASE)
GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _parse_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return [str(value)]
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def task_group(task: dict[str, Any]) -> str:
    """Return a stable display group, preferring explicit path tags."""
    for tag in _parse_tags(task.get("tags")):
        match = PATH_TAG_PATTERN.match(tag.strip())
        if match:
            return match.group(1).lower()
    if task.get("project") == "🌐 Generalist Paths":
        return "generalist"
    return str(task.get("category") or "uncategorized").lower()


def _task_sort_key(task: dict[str, Any]) -> tuple[int, int, str, int]:
    status_rank = {"blocked": 0, "in_progress": 1, "todo": 2}
    due = str(task.get("due_date") or "9999-12-31")
    return (
        status_rank.get(str(task.get("status")), 3),
        int(task.get("priority") or 3),
        due,
        int(task.get("id") or 0),
    )


def fetch_open_tasks(conn, project: str | None = None) -> list[dict[str, Any]]:
    store = open_task_store(conn, "reporting-reader")
    rows = [
        t for t in store.list(project=project or None)
        if t["status"] not in ("done", "cancelled")
    ]
    # ORDER BY priority ASC, due_date ASC, id ASC (NULLs first as SQLite sorts them)
    rows.sort(key=lambda t: (t["priority"] is not None, t["priority"] or 0,
                             t["due_date"] is not None, t["due_date"] or "", t["id"]))
    columns = ("id", "title", "status", "priority", "project", "category", "tags", "due_date")
    return [{column: t[column] for column in columns} for t in rows]


def build_digest(
    tasks: Iterable[dict[str, Any]],
    *,
    display_limit: int = 80,
    generated_at: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build plain-text digest content and safe summary metadata."""
    all_tasks = list(tasks)
    ordered = sorted(all_tasks, key=_task_sort_key)
    shown = ordered[: max(display_limit, 1)]
    groups = Counter(task_group(task) for task in all_tasks)
    stamp = generated_at or datetime.now(timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M %Z"
    )

    lines = [
        f"Solo Empire Task Digest — {stamp}",
        f"Open tasks: {len(all_tasks)} | Showing: {len(shown)}",
        "",
        "By path/category:",
    ]
    if groups:
        lines.extend(f"- {name}: {count}" for name, count in sorted(groups.items()))
    else:
        lines.append("- none")

    lines.extend(["", "Tasks:"])
    if not shown:
        lines.append("- No open tasks.")
    else:
        for task in shown:
            due = f" | due {task['due_date']}" if task.get("due_date") else ""
            category = str(task.get("category") or "uncategorized")
            lines.append(
                f"- [{task['status']}] P{task['priority']} "
                f"#{task['id']} [{task_group(task)} / {category}] "
                f"{task['title']}{due}"
            )
    if len(ordered) > len(shown):
        lines.extend(["", f"... {len(ordered) - len(shown)} more open tasks remain in SQLite."])

    metadata = {
        "status": "ok",
        "open_tasks": len(all_tasks),
        "shown_tasks": len(shown),
        "groups": dict(sorted(groups.items())),
    }
    return "\n".join(lines) + "\n", metadata


def _google_send_credentials() -> tuple[str, str, str]:
    """Resolve a least-privilege Gmail send token, with shared-token fallback."""
    send_refresh = get_env("GOOGLE_GMAIL_SEND_REFRESH_TOKEN")
    send_client_id = get_env("GOOGLE_GMAIL_SEND_CLIENT_ID")
    send_client_secret = get_env("GOOGLE_GMAIL_SEND_CLIENT_SECRET")
    if bool(send_client_id) != bool(send_client_secret):
        raise ValueError(
            "GOOGLE_GMAIL_SEND_CLIENT_ID and GOOGLE_GMAIL_SEND_CLIENT_SECRET must be configured together"
        )
    if (send_client_id or send_client_secret) and not send_refresh:
        raise ValueError(
            "GOOGLE_GMAIL_SEND_REFRESH_TOKEN must be configured when using Gmail API delivery"
        )
    if send_refresh:
        if send_client_id and send_client_secret:
            return send_client_id, send_client_secret, send_refresh
        client_id, client_secret, _ = get_google_creds()
        return client_id, client_secret, send_refresh
    return get_google_creds()


def send_digest_gmail(body: str, *, recipient: str, subject: str) -> str:
    """Send through Gmail API using OAuth, without storing an SMTP password."""
    client_id, client_secret, refresh_token = _google_send_credentials()
    access_token = get_access_token(client_id, client_secret, refresh_token)
    from_address = (
        get_env("GMAIL_FROM")
        or get_env("EMAIL_FROM")
        or get_env("SMTP_USER")
        or get_env("EMAIL_USER")
    )

    message = EmailMessage()
    if from_address:
        message["From"] = from_address
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    try:
        result = api_request(
            GMAIL_SEND_ENDPOINT,
            data=json.dumps({"raw": raw}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError(
                "Gmail OAuth lacks gmail.send permission; re-authorize the send-only token"
            ) from None
        raise RuntimeError(f"Gmail API send failed with HTTP {exc.code}") from None
    message_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    return message_id


def send_digest_smtp(body: str, *, recipient: str, subject: str) -> None:
    """Send through SMTP as an explicit compatibility fallback."""
    username = get_env("EMAIL_USER") or get_env("SMTP_USER")
    password = get_env("EMAIL_PASS") or get_env("SMTP_PASS")
    if not username:
        raise ValueError("Set EMAIL_USER or SMTP_USER before sending")
    if not password:
        raise ValueError("Set EMAIL_PASS or SMTP_PASS before sending")
    host = str(get_env("SMTP_HOST", default="smtp.gmail.com"))
    port = int(get_env("SMTP_PORT", default="587"))

    send_smtp_message(
        body, recipient=recipient, subject=subject, host=host, port=port,
        username=str(username), password=str(password),
    )


def resolve_recipient(explicit: str | None) -> str:
    """Resolve and protect the configured digest recipient allowlist."""
    configured = get_env("TASK_DIGEST_TO") or get_env("EMAIL_TO")
    if explicit and configured and explicit.lower() != str(configured).lower():
        raise ValueError("Recipient differs from the configured TASK_DIGEST_TO/EMAIL_TO allowlist")
    recipient = explicit or configured
    if not recipient:
        raise ValueError("Set --to, TASK_DIGEST_TO, or EMAIL_TO before sending")
    return str(recipient)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Limit the digest to one task project")
    parser.add_argument("--limit", type=int, default=80, help="Maximum task lines in the email")
    parser.add_argument("--to", help="Recipient; defaults to TASK_DIGEST_TO or EMAIL_TO")
    parser.add_argument("--subject", default="Solo Empire Task Digest")
    parser.add_argument(
        "--transport",
        choices=("gmail", "smtp"),
        default="gmail",
        help="Delivery backend; Gmail OAuth is the default, SMTP is an explicit fallback",
    )
    parser.add_argument("--send", action="store_true", help="Send; otherwise dry-run")
    parser.add_argument("--dry-run", action="store_true", help="Print the digest without sending")
    parser.add_argument("--json", action="store_true", help="Print summary JSON instead of the digest")
    args = parser.parse_args(argv)

    tasks = fetch_open_tasks(None, project=args.project)

    body, metadata = build_digest(tasks, display_limit=args.limit)
    if args.send:
        recipient = resolve_recipient(args.to)
        if args.transport == "gmail":
            message_id = send_digest_gmail(body, recipient=recipient, subject=args.subject)
            metadata.update({"mode": "sent", "transport": "gmail", "message_id": message_id})
        else:
            send_digest_smtp(body, recipient=recipient, subject=args.subject)
            metadata.update({"mode": "sent", "transport": "smtp"})
    else:
        metadata["mode"] = "dry-run"

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    else:
        print(body, end="")
        print(f"Mode: {metadata['mode']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Task digest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
