#!/usr/bin/env python3
"""Weekly Life Framework check-in: ask five questions, store them in the habits cell.

The answers go to the habits cell (solo-habits), not to local files:

* four weekly habits hold the counts (exercise, rest days, intentional contacts,
  client offers); each check-in upserts one log on the check-in date with
  ``count`` = the weekly number and ``completed`` = count reached the target;
* the ``life-weekly-checkin`` routine holds the weekly mood (``mood_after``,
  1-5) and the average sleep hours in its notes.

A summary row is appended to the validation log so the 30-day trial stays
readable in the repository. Every value is self-reported; a question left
blank is stored as unknown (no log), never guessed.

Usage:
  python3 life-checkin.py                       # interactive
  python3 life-checkin.py --mood 3 --exercise 2 --rest-days 1 --contacts 4 --offers 1 --sleep 8
  python3 life-checkin.py --date 2026-10-18 ... --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

from habits_store import HabitsStoreError, open_habits_store  # noqa: E402
from task_store import ClientConfigError  # noqa: E402
from _env import KNOWLEDGE_ROOT  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
# Owned by bookchaowalit/solo-knowledge (plans/decision-context/); override SOLO_KNOWLEDGE_ROOT.
VALIDATION_LOG = KNOWLEDGE_ROOT / "plans" / "decision-context" / "LIFE-VALIDATION-LOG-2026-Q4.md"
LOG_HEADER = "## Weekly check-ins"
TABLE_HEAD = ("| วันที่ | mood (1-5) | sleep (ชม.) | exercise | rest days | contacts | offers | note |\n"
              "|---|---|---|---|---|---|---|---|\n")

# Weekly habits backing the framework metrics; slugs are stable keys in the cell.
HABITS = [
    {"key": "exercise", "slug": "life-exercise", "name": "Exercise (weekly count)", "category": "health",
     "target_count": 3, "metric": "exercise_sessions", "question": "ออกกำลังกายกี่ครั้งสัปดาห์นี้"},
    {"key": "rest_days", "slug": "life-rest-day", "name": "Full rest day (weekly count)", "category": "mindfulness",
     "target_count": 1, "metric": "rest_days", "question": "หยุดงานเต็มวันกี่วัน"},
    {"key": "contacts", "slug": "life-intentional-contact", "name": "Intentional contact (weekly count)",
     "category": "social", "target_count": 1, "metric": "intentional_contacts",
     "question": "ติดต่อคนใกล้ตัวอย่างตั้งใจกี่ครั้ง"},
    {"key": "offers", "slug": "life-client-offer", "name": "Client offer or proposal (weekly count)",
     "category": "business", "target_count": 1, "metric": "paying_clients (leading)",
     "question": "ส่ง offer หรือ proposal ให้ลูกค้าใหม่กี่ครั้ง"},
]
ROUTINE = {"slug": "life-weekly-checkin", "name": "Life Framework weekly check-in", "routine_type": "weekly_review",
           "description": "Weekly self-report for the Universal Life Framework trial; mood_after = weekly mood."}
NOTE_PREFIX = "life-checkin"


def ensure_records(store, *, dry_run: bool) -> tuple[dict[str, dict], dict | None]:
    """Find (or create) the four habits and the routine by slug; idempotent."""
    habits = {}
    for spec in HABITS:
        habit = store.habit_by_slug(spec["slug"])
        if habit is None and not dry_run:
            habit = store.create_habit({"name": spec["name"], "slug": spec["slug"], "category": spec["category"],
                                        "frequency": "weekly", "target_count": spec["target_count"],
                                        "status": "active", "notes": f"Life Framework metric: {spec['metric']}"},
                                       idempotency_key=f"life-checkin-{spec['slug']}")
        habits[spec["key"]] = habit
    routine = store.routine_by_slug(ROUTINE["slug"])
    if routine is None and not dry_run:
        routine = store.create_routine({**ROUTINE, "status": "active"},
                                       idempotency_key=f"life-checkin-{ROUTINE['slug']}")
    return habits, routine


def parse_count(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    value = int(str(raw).strip())
    if value < 0:
        raise ValueError("count must be 0 or more")
    return value


def parse_mood(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    value = int(str(raw).strip())
    if not 1 <= value <= 5:
        raise ValueError("mood must be 1-5")
    return value


def parse_sleep(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    value = float(str(raw).strip())
    if not 0 <= value <= 24:
        raise ValueError("sleep must be 0-24 hours")
    return value


def collect(args) -> dict:
    """Answers from flags; ask interactively for anything not given (blank = unknown)."""
    interactive = sys.stdin.isatty() and not args.no_input
    answers = {"mood": args.mood, "sleep": args.sleep, "note": args.note}
    for spec in HABITS:
        answers[spec["key"]] = getattr(args, spec["key"])
    prompts = [("mood", "mood เฉลี่ยสัปดาห์นี้ (1-5)", parse_mood),
               ("sleep", "นอนเฉลี่ยคืนละกี่ชั่วโมง", parse_sleep),
               *[(s["key"], s["question"], parse_count) for s in HABITS]]
    for key, question, parser in prompts:
        if answers[key] is None and interactive:
            while True:
                try:
                    answers[key] = parser(input(f"  {question} (เว้นว่าง = ไม่ทราบ): "))
                    break
                except ValueError as exc:
                    print(f"  ⚠️  {exc}")
        else:
            answers[key] = parser(answers[key]) if answers[key] is not None else None
    if answers["note"] is None and interactive:
        answers["note"] = input("  โน้ตสั้น ๆ (ไม่บังคับ): ").strip() or None
    return answers


def store_answers(store, habits: dict, routine: dict | None, day: str, answers: dict) -> list[str]:
    """Upsert the logs for ``day``; unknown answers write nothing. Returns outcome lines."""
    lines = []
    for spec in HABITS:
        count = answers[spec["key"]]
        if count is None:
            lines.append(f"{spec['slug']}: unknown (not stored)")
            continue
        outcome, _ = store.log_habit(habits[spec["key"]]["id"], day, {
            "completed": count >= spec["target_count"], "count": count,
            "notes": f"{NOTE_PREFIX}: self_reported weekly count"})
        lines.append(f"{spec['slug']}: {count} ({outcome})")
    if answers["mood"] is None and answers["sleep"] is None:
        lines.append(f"{ROUTINE['slug']}: unknown (not stored)")
    else:
        notes = f"{NOTE_PREFIX}: self_reported; sleep_hours_avg=" + (
            "unknown" if answers["sleep"] is None else f"{answers['sleep']:g}")
        if answers["note"]:
            notes += f"; note={answers['note']}"
        fields = {"notes": notes, **({"mood_after": answers["mood"]} if answers["mood"] is not None else {})}
        outcome, _ = store.log_routine(routine["id"], day, fields)
        lines.append(f"{ROUTINE['slug']}: mood={answers['mood'] if answers['mood'] is not None else 'unknown'}"
                     f" ({outcome})")
    return lines


def table_row(day: str, answers: dict) -> str:
    def cell(value):
        if value is None:
            return "unknown"
        return f"{value:g}" if isinstance(value, float) else str(value)
    note = (answers.get("note") or "").replace("|", "/")
    return (f"| {day} | {cell(answers['mood'])} | {cell(answers['sleep'])} | {cell(answers['exercise'])} | "
            f"{cell(answers['rest_days'])} | {cell(answers['contacts'])} | {cell(answers['offers'])} | {note} |\n")


def append_to_log(path: Path, day: str, answers: dict) -> str:
    """Add or replace the row for ``day`` in the Weekly check-ins table; returns added|replaced."""
    text = path.read_text(encoding="utf-8")
    row = table_row(day, answers)
    if LOG_HEADER not in text:
        marker = "## ลำดับถัดไป"
        section = (f"{LOG_HEADER}\n\nบันทึกโดย `task life:checkin` ค่าทุกตัวเป็น self_reported "
                   f"และเก็บจริงใน habits cell\n\n{TABLE_HEAD}{row}\n")
        text = text.replace(marker, section + marker, 1) if marker in text else text.rstrip() + "\n\n" + section
        path.write_text(text, encoding="utf-8")
        return "added"
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(LOG_HEADER))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    for i in range(start, end):
        if lines[i].startswith(f"| {day} |"):
            lines[i] = row
            path.write_text("".join(lines), encoding="utf-8")
            return "replaced"
    last = max(i for i in range(start, end) if lines[i].startswith("|"))
    lines.insert(last + 1, row)
    path.write_text("".join(lines), encoding="utf-8")
    return "added"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly Life Framework check-in (habits cell)")
    parser.add_argument("--date", default=date.today().isoformat(), help="check-in date YYYY-MM-DD (default today)")
    parser.add_argument("--mood")
    parser.add_argument("--sleep")
    parser.add_argument("--exercise")
    parser.add_argument("--rest-days", dest="rest_days")
    parser.add_argument("--contacts")
    parser.add_argument("--offers")
    parser.add_argument("--note")
    parser.add_argument("--no-input", action="store_true", help="never prompt; missing answers stay unknown")
    parser.add_argument("--dry-run", action="store_true", help="show what would be stored; write nothing")
    parser.add_argument("--no-log", action="store_true", help="do not touch the validation log file")
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date).isoformat()

    print(f"\n  Life check-in {day}\n")
    try:
        answers = collect(args)
    except ValueError as exc:
        print(f"  ❌ {exc}")
        return 2
    if args.dry_run:
        print("\n  dry-run: nothing stored")
        print("  " + table_row(day, answers).strip())
        return 0
    try:
        store = open_habits_store("owner-cli", channel="cli")
        habits, routine = ensure_records(store, dry_run=False)
        lines = store_answers(store, habits, routine, day, answers)
    except (ClientConfigError, HabitsStoreError) as exc:
        print(f"  ❌ habits cell: {exc}")
        return 1
    print("\n  stored in the habits cell:")
    for line in lines:
        print(f"   - {line}")
    if not args.no_log:
        print(f"  validation log: row {append_to_log(VALIDATION_LOG, day, answers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
