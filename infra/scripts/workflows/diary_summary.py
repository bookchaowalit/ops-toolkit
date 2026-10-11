#!/usr/bin/env python3
"""Build a privacy-safe, deterministic weekly or monthly diary summary.

This aggregates the same bounded metadata as the daily activity diary
(``daily_diary.py``) over a Monday-Sunday week or a calendar month: task
counts/breakdowns, metadata-only AI conversation counts, merged
agent-authored conversation summaries, and root-repository git commits.
Raw AI prompts, responses, and
journal content are never read or copied into the summary.

Usage:
    python3 diary_summary.py --cadence weekly --date 2026-08-11
    python3 diary_summary.py --cadence monthly --date 2026-08-11 --write
    python3 diary_summary.py --cadence weekly --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKFLOWS_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOWS_DIR.parent / "utils"
for import_path in (WORKFLOWS_DIR, UTILS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from _env import PROJECT_ROOT  # noqa: E402
from daily_diary import _clean, load_conversation_summaries  # noqa: E402
from task_store import open_task_store  # noqa: E402


CONTRACT = "solo-empire.diary-summary.v1"
DEFAULT_ENTRIES_ROOT = PROJECT_ROOT / "diary" / "entries"
DEFAULT_OUTPUT_DIR = DEFAULT_ENTRIES_ROOT / "summaries"
MAX_HIGHLIGHTS = 15
CADENCES = ("weekly", "monthly")


def _date_arg(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _week_window(as_of: date) -> tuple[date, date]:
    start = as_of - timedelta(days=as_of.weekday())
    return start, start + timedelta(days=6)


def _month_window(as_of: date) -> tuple[date, date]:
    start = as_of.replace(day=1)
    next_month = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, next_month - timedelta(days=1)


def compute_window(cadence: str, as_of: str | date | None) -> tuple[date, date]:
    if cadence not in CADENCES:
        raise ValueError("cadence must be weekly or monthly")
    day = as_of if isinstance(as_of, date) else date.fromisoformat(as_of or date.today().isoformat())
    return _week_window(day) if cadence == "weekly" else _month_window(day)


def _period_label(cadence: str, start: date) -> str:
    if cadence == "weekly":
        iso_year, iso_week, _ = start.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return f"{start.year:04d}-{start.month:02d}"


def _breakdown(rows: list[dict[str, Any]], key: str, default: str = "ไม่ระบุ") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = _clean(row.get(key)) or default
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _git_commits_window(
    repo_root: Path, start: date, end: date
) -> tuple[list[dict[str, str]], int, set[str]]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                f"--since={start.isoformat()} 00:00:00",
                f"--until={(end + timedelta(days=1)).isoformat()} 00:00:00",
                "--date=short",
                "--pretty=format:%ad%x09%h%x09%s",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], 0, set()
    if result.returncode != 0:
        return [], 0, set()

    all_commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        commit_date, _, rest = line.partition("\t")
        sha, _, subject = rest.partition("\t")
        if sha and subject:
            all_commits.append(
                {
                    "date": _clean(commit_date, 10),
                    "sha": _clean(sha, 16),
                    "subject": _clean(subject),
                }
            )
    distinct_days = {row["date"] for row in all_commits if row["date"]}
    return all_commits[:MAX_HIGHLIGHTS], len(all_commits), distinct_days


def _conversation_summaries_window(
    entries_root: str | Path, start: date, end: date
) -> tuple[list[dict[str, str]], set[str]]:
    merged: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    active_dates: set[str] = set()
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        day_summaries = load_conversation_summaries(entries_root, day)
        if day_summaries:
            active_dates.add(day)
        for item in day_summaries:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                merged.append({**item, "date": day})
    return merged, active_dates


def _local_day(stamp: Any) -> str | None:
    """The local calendar day of a stored timestamp (a naive stamp is UTC, as the task API stores it)."""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().date().isoformat()


def _active_days_from_db(start: str, end: str) -> set[str]:
    store = open_task_store(None, "reporting-reader")
    completed_stamps = [
        task["completed_at"]
        for task in store.list(status=["done"], completed_between=(start, end), include_archived=True)
    ]
    return {day for day in (_local_day(stamp) for stamp in completed_stamps) if day}


def build_diary_summary(
    *,
    cadence: str,
    as_of: str | date | None = None,
    repo_root: str | Path = PROJECT_ROOT,
    entries_root: str | Path = DEFAULT_ENTRIES_ROOT,
) -> dict[str, Any]:
    """Build the bounded weekly/monthly payload without writing or mutating state."""
    if cadence not in CADENCES:
        raise ValueError("cadence must be weekly or monthly")
    start, end = compute_window(cadence, as_of)
    start_s, end_s = start.isoformat(), end.isoformat()
    label = _period_label(cadence, start)

    store = open_task_store(None, "reporting-reader")
    completed_tasks = [
        {**{k: t[k] for k in ("title", "project", "category", "priority")}, "completed_at": t["completed_at"]}
        for t in sorted(
            store.list(status=["done"], completed_between=(start_s, end_s), include_archived=True),
            key=lambda t: (t["completed_at"] or "", t["id"]),
        )
    ]
    created_tasks = [
        {**{k: t[k] for k in ("title", "project", "category", "priority")}, "created_at": t["created_at"]}
        for t in sorted(
            store.list(created_between=(start_s, end_s), include_archived=True),
            key=lambda t: (t["created_at"] or "", t["id"]),
        )
    ]
    blocked_tasks_current = store.stats(status=["blocked"])["total"]
    overdue_tasks_current = sum(
        1 for t in store.list(due_before=end_s) if t["status"] not in ("done", "cancelled")
    )

    git_commits, git_commit_total, git_days = _git_commits_window(Path(repo_root), start, end)
    conversation_summaries, summary_days = _conversation_summaries_window(entries_root, start, end)
    db_active_days = _active_days_from_db(start_s, end_s)
    active_days = len(db_active_days | git_days | summary_days)

    overview = {
        "completed_tasks": len(completed_tasks),
        "created_tasks": len(created_tasks),
        "conversation_summaries": len(conversation_summaries),
        "git_commits": git_commit_total,
        "blocked_tasks_current": blocked_tasks_current,
        "overdue_tasks_current": overdue_tasks_current,
        "active_days": active_days,
        "days_in_period": (end - start).days + 1,
    }

    return {
        "contract": CONTRACT,
        "cadence": cadence,
        "label": label,
        "privacy_class": "private",
        "window": {"start": start_s, "end": end_s},
        "overview": overview,
        "breakdowns": {
            "completed_tasks_by_project": _breakdown(completed_tasks, "project", "ไม่มี project"),
            "completed_tasks_by_category": _breakdown(completed_tasks, "category"),
            "created_tasks_by_project": _breakdown(created_tasks, "project", "ไม่มี project"),
        },
        "highlights": {
            "completed_tasks": completed_tasks[:MAX_HIGHLIGHTS],
            "conversation_summaries": conversation_summaries[:MAX_HIGHLIGHTS],
            "git_commits": git_commits,
        },
        "policy": {
            "read_only_sources": [
                "tasks-api:tasks",
                "local:conversation_summaries.metadata_only",
                "git:root_repository_history",
            ],
            "raw_ai_prompt_included": False,
            "raw_ai_response_included": False,
            "raw_conversation_included": False,
            "raw_journal_content_included": False,
            "external_messages": False,
            "idempotent_file_write": True,
        },
    }


def _fmt_mapping(values: dict[str, int]) -> str:
    if not values:
        return "ไม่มีข้อมูล"
    return ", ".join(f"{key} ({value})" for key, value in values.items())


def _fmt_list(rows: list[dict[str, Any]], render_row, total: int | None = None) -> list[str]:
    if not rows:
        return ["- ไม่มีข้อมูล"]
    lines = [f"- {render_row(row)}" for row in rows]
    if total is not None and total > len(rows):
        lines.append(f"- … และอีก {total - len(rows)} รายการ")
    return lines


def render_diary_summary(payload: dict[str, Any]) -> str:
    """Render only bounded fields; never render prompt/response/content."""
    cadence = payload["cadence"]
    label = payload["label"]
    window = payload["window"]
    overview = payload["overview"]
    breakdowns = payload["breakdowns"]
    highlights = payload["highlights"]
    title_th = "รายสัปดาห์" if cadence == "weekly" else "รายเดือน"

    lines = [
        "---",
        f"title: Auto {cadence.title()} Diary Summary",
        f"cadence: {cadence}",
        f"label: {label}",
        "entry_type: system_activity_summary",
        "privacy_class: private",
        "generated: true",
        f"contract: {payload['contract']}",
        "---",
        "",
        f"# สรุปกิจกรรม{title_th} {label}",
        "",
        f"> ช่วง: {window['start']} ถึง {window['end']} ({overview['days_in_period']} วัน)",
        "> ไฟล์นี้สร้างจาก metadata ของระบบแบบอัตโนมัติ ไม่ใช่ personal reflection",
        "> และไม่คัดลอก prompt/response ของ AI หรือเนื้อหา journal ดิบ",
        "",
        "## ภาพรวม",
        f"- วันที่มี activity: {overview['active_days']} / {overview['days_in_period']} วัน",
        f"- งานที่เสร็จ: {overview['completed_tasks']} รายการ",
        f"- งานที่สร้าง: {overview['created_tasks']} รายการ",
        f"- บทสนทนาที่ agent สรุปจากรอบคุย: {overview['conversation_summaries']} รายการ",
        f"- root git commits: {overview['git_commits']} รายการ",
        f"- งาน blocked ณ ปัจจุบัน: {overview['blocked_tasks_current']} รายการ",
        f"- งาน overdue ณ ปัจจุบัน: {overview['overdue_tasks_current']} รายการ",
        "",
        "## งานที่เสร็จ แยกตามโปรเจกต์",
        f"- {_fmt_mapping(breakdowns['completed_tasks_by_project'])}",
        "",
        "## งานที่เสร็จ แยกตามหมวด",
        f"- {_fmt_mapping(breakdowns['completed_tasks_by_category'])}",
        "",
        "## ตัวอย่างงานที่เสร็จ",
    ]
    lines.extend(
        _fmt_list(
            highlights["completed_tasks"],
            lambda row: f"{_clean(row.get('title')) or '(ไม่มีชื่อ)'} ({_clean(row.get('project')) or 'ไม่มี project'})",
            total=overview["completed_tasks"],
        )
    )
    lines.extend(["", "## บทสนทนาที่สรุปจากรอบคุย"])
    lines.extend(
        _fmt_list(
            highlights["conversation_summaries"],
            lambda row: f"{row.get('date')} — {row.get('topic')}: {row.get('question_summary')} → {row.get('outcome_summary')}",
            total=overview["conversation_summaries"],
        )
    )
    lines.extend(["", "## Git activity ของ root repository"])
    lines.extend(
        _fmt_list(
            highlights["git_commits"],
            lambda row: f"{row.get('date')} `{_clean(row.get('sha'), 16)}` {_clean(row.get('subject'))}",
            total=overview["git_commits"],
        )
    )
    lines.extend(
        [
            "",
            "## ขอบเขตข้อมูล",
            "- แหล่งข้อมูลเป็น Tasks API (เมื่อเข้าถึงได้), agent-authored conversation summaries และ root git history เท่านั้น",
            "- ไม่ได้สแกน nested repositories และไม่รวม uncommitted files",
            "- สามารถรันซ้ำเพื่ออัปเดตไฟล์ของช่วงเดิมได้โดยไม่สร้างรายการซ้ำ",
            "",
        ]
    )
    return "\n".join(lines)


def _output_paths(output_dir: Path, cadence: str, label: str) -> tuple[Path, Path]:
    target_dir = output_dir / cadence
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{label}.md", target_dir / f"{label}.json"


def write_diary_summary(payload: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, str]:
    """Write the date-partitioned private Markdown + JSON summary files."""
    markdown_path, json_path = _output_paths(Path(output_dir), payload["cadence"], payload["label"])
    markdown_path.write_text(render_diary_summary(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"markdown": str(markdown_path), "json": str(json_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a privacy-safe weekly or monthly diary summary")
    parser.add_argument("--cadence", choices=CADENCES, required=True)
    parser.add_argument(
        "--date",
        type=_date_arg,
        default=date.today().isoformat(),
        help="Any date inside the target week/month",
    )
    parser.add_argument("--entries-root", type=Path, default=DEFAULT_ENTRIES_ROOT)
    parser.add_argument("--repo-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="Write diary/entries/summaries/<cadence>/<label>.md")
    parser.add_argument("--json", action="store_true", help="Print the bounded payload as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_diary_summary(
        cadence=args.cadence,
        as_of=args.date,
        repo_root=args.repo_root,
        entries_root=args.entries_root,
    )

    output_paths = write_diary_summary(payload, args.output_dir) if args.write else {}
    if args.json:
        result = dict(payload)
        if output_paths:
            result["output_paths"] = output_paths
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_diary_summary(payload))
        if output_paths:
            print(f"\n✅ {args.cadence.title()} diary summary written: {output_paths['markdown']}")
        else:
            print("\n(dry-run — use --write to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
