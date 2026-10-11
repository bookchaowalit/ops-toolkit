#!/usr/bin/env python3
"""Run and validate quizzes whose source material lives under learning/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_file, repo_rel  # noqa: E402

QUIZ_DIR = KNOWLEDGE_ROOT / "quiz"
LEARNING_DIR = KNOWLEDGE_ROOT / "learning"


def discover_quizzes() -> list[Path]:
    return sorted(path for path in QUIZ_DIR.glob("*.json") if path.is_file())


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def load_quiz(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not _inside(resolved, QUIZ_DIR) or resolved.suffix.lower() != ".json":
        raise ValueError(f"quiz file must be a JSON file inside {QUIZ_DIR}")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {resolved}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{resolved.name}: root must be an object")
    return data


def validate_quiz(data: dict[str, Any], path: Path) -> list[str]:
    errors: list[str] = []
    label = path.name

    for field in ("id", "title", "track"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            errors.append(f"{label}: {field} must be a non-empty string")

    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append(f"{label}: questions must be a non-empty list")
        return errors

    question_ids: set[str] = set()
    for index, question in enumerate(questions, 1):
        prefix = f"{label} question {index}"
        if not isinstance(question, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            errors.append(f"{prefix}: id must be a non-empty string")
        elif question_id in question_ids:
            errors.append(f"{prefix}: duplicate id {question_id!r}")
        else:
            question_ids.add(question_id)

        if not isinstance(question.get("prompt"), str) or not question["prompt"].strip():
            errors.append(f"{prefix}: prompt must be a non-empty string")

        choices = question.get("choices")
        if not isinstance(choices, dict) or len(choices) < 2:
            errors.append(f"{prefix}: choices must contain at least two options")
            choices = {}
        elif any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in choices.items()
        ):
            errors.append(f"{prefix}: choices must map non-empty labels to text")

        answer = question.get("answer")
        if not isinstance(answer, str) or answer not in choices:
            errors.append(f"{prefix}: answer must match one of the choice labels")

        source = question.get("source")
        if not isinstance(source, str) or not source.startswith("learning/"):
            errors.append(f"{prefix}: source must be a path under learning/")
        else:
            source_path = repo_file(source.split("#", 1)[0]).resolve()
            if not _inside(source_path, LEARNING_DIR) or not source_path.is_file():
                errors.append(f"{prefix}: source does not point to a file under learning/: {source}")

        if not isinstance(question.get("explanation"), str) or not question["explanation"].strip():
            errors.append(f"{prefix}: explanation must be a non-empty string")

    return errors


def check_all() -> int:
    paths = discover_quizzes()
    if not paths:
        print(f"FAIL: no quiz JSON files found in {QUIZ_DIR}")
        return 1

    errors: list[str] = []
    for path in paths:
        try:
            data = load_quiz(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        errors.extend(validate_quiz(data, path))

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print(f"OK: validated {len(paths)} quiz files and their learning sources")
    return 0


def list_quizzes() -> int:
    paths = discover_quizzes()
    if not paths:
        print("No quizzes found.")
        return 1
    for path in paths:
        try:
            data = load_quiz(path)
        except ValueError as exc:
            print(f"FAIL: {exc}")
            return 1
        print(f"{data.get('id', path.stem)}\t{data.get('title', 'Untitled')}\t{len(data.get('questions', []))} questions")
    return 0


def normalize_answer(raw: str, choices: dict[str, str]) -> str | None:
    value = raw.strip().upper()
    labels = list(choices)
    if value.isdigit() and 1 <= int(value) <= len(labels):
        return labels[int(value) - 1]
    return value if value in choices else None


def run_quiz(data: dict[str, Any], path: Path) -> int:
    questions = data["questions"]
    score = 0
    print(f"\n{data['title']}")
    print(f"Source track: {data['track']} | Questions: {len(questions)}")
    print("Answer with a choice label (A, B, C...) or its number.\n")

    for index, question in enumerate(questions, 1):
        print(f"{index}. {question['prompt']}")
        labels = list(question["choices"])
        for label in labels:
            print(f"   {label}. {question['choices'][label]}")

        while True:
            try:
                raw = input("Your answer: ")
            except EOFError:
                print("\nQuiz stopped: input ended before all questions were answered.")
                return 2
            answer = normalize_answer(raw, question["choices"])
            if answer is not None:
                break
            print(f"Please choose one of: {', '.join(labels)}")

        correct = answer == question["answer"]
        if correct:
            score += 1
            print("Correct.")
        else:
            print(f"Not quite. Correct answer: {question['answer']}.")
        print(f"Why: {question['explanation']}")
        print(f"Review: {question['source']}\n")

    percentage = score / len(questions) * 100
    print(f"Score: {score}/{len(questions)} ({percentage:.0f}%)")
    print(f"Quiz file: {repo_rel(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list available quizzes")
    mode.add_argument("--check", action="store_true", help="validate quiz files and learning links")
    parser.add_argument("--track", default="ai-agents", help="quiz id to run (default: ai-agents)")
    args = parser.parse_args(argv)

    if args.list:
        return list_quizzes()
    if args.check:
        return check_all()

    path = QUIZ_DIR / f"{args.track}.json"
    if not path.is_file():
        print(f"Quiz not found: {args.track}", file=sys.stderr)
        print("Use --list to see available quizzes.", file=sys.stderr)
        return 1
    try:
        data = load_quiz(path)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    errors = validate_quiz(data, path)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return run_quiz(data, path)


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("quiz.py")
    raise SystemExit(main())
