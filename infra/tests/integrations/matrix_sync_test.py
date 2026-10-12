#!/usr/bin/env python3
"""Offline tests for the CSV-first Matrix sync adapter."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists  # noqa: E402
exit_unless_exists(ARCHITECTURE_ROOT / "templates", "architecture-docs checkout (SOLO_ARCHITECTURE_ROOT)")
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "sync"))

from matrix_sync import MatrixSyncError, load_yaml, run_sync, validate_rows  # noqa: E402


SCHEMA = ARCHITECTURE_ROOT / "templates" / "matrices" / "matrix-schema.yaml"
MAPPING = ARCHITECTURE_ROOT / "templates" / "matrices" / "sync-mappings.yaml"
TEMPLATE = ARCHITECTURE_ROOT / "templates" / "matrices" / "matrix-template.csv"


def write_fixture(path: Path, *, duplicate: bool = False) -> None:
    with TEMPLATE.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    row = {field: "" for field in header}
    row.update({
        "matrix_id": "demo-matrix",
        "row_id": "R-001",
        "matrix_type": "uat",
        "domain": "demo",
        "subject": "Reward flow",
        "item": "Redeem coupon",
        "criterion": "Coupon is redeemed once",
        "expected": "Success screen is shown",
        "owner": "qa",
        "status": "not-started",
        "priority": "P0",
        "custom_fields_json": json.dumps({"device": "android"}),
        "updated_at": "2026-08-21T00:00:00Z",
    })
    rows = [row]
    if duplicate:
        rows.append(dict(row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_populated_csv_validates():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "matrix.csv"
        write_fixture(source)
        header, rows = validate_rows(source, load_yaml(SCHEMA))
        assert len(header) == 25
        assert len(rows) == 1
        assert rows[0]["row_id"] == "R-001"


def test_duplicate_identity_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "matrix.csv"
        write_fixture(source, duplicate=True)
        try:
            validate_rows(source, load_yaml(SCHEMA))
        except MatrixSyncError as exc:
            assert str(exc).startswith("duplicate_identity:")
        else:
            raise AssertionError("duplicate identity was accepted")


def test_markdown_apply_is_local_and_idempotent_shape():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "matrix.csv"
        output = Path(directory) / "matrix.md"
        write_fixture(source)
        payload = run_sync(source, MAPPING, ["markdown"], output, True, None)
        assert payload["status"] == "ok"
        assert payload["targets"][0]["status"] == "written"
        text = output.read_text(encoding="utf-8")
        assert "Redeem coupon" in text
        assert "Matrix demo-matrix" in text


def test_external_apply_is_disabled_by_default():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "matrix.csv"
        write_fixture(source)
        try:
            run_sync(source, MAPPING, ["google-sheets"], None, True, None)
        except MatrixSyncError as exc:
            assert str(exc) == "target_disabled:google-sheets"
        else:
            raise AssertionError("disabled external target was applied")


if __name__ == "__main__":
    for test in (
        test_populated_csv_validates,
        test_duplicate_identity_is_rejected,
        test_markdown_apply_is_local_and_idempotent_shape,
        test_external_apply_is_disabled_by_default,
    ):
        test()
        print(f"✅ {test.__name__}")
