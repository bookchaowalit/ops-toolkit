"""Offline tests for role practice evidence input validation."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
from _env import PROJECT_ROOT  # noqa: E402

from role_practice_evidence import parse_records, safe_evidence_path  # noqa: E402


def test_parse_records_supports_role_and_optional_task():
    assert parse_records(["data-engineer", "backend-engineer:2418"]) == [
        ("data-engineer", None),
        ("backend-engineer", 2418),
    ]


def test_parse_records_rejects_non_numeric_task():
    try:
        parse_records(["backend-engineer:task"])
    except ValueError as exc:
        assert "numeric" in str(exc)
    else:
        raise AssertionError("expected invalid task id to be rejected")


def test_safe_evidence_path_requires_existing_repository_file():
    path, relative = safe_evidence_path("infra/package.json")
    assert path.is_file()
    assert relative == "infra/package.json"


if __name__ == "__main__":
    for name in dir():
        if name.startswith("test_"):
            globals()[name]()
    print("Role practice evidence tests passed")
