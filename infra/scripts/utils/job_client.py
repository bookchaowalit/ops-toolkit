#!/usr/bin/env python3
"""Read-only client for the separate Track B ``job.v1`` contract.

The client never opens scraper CSV files. It calls book-job-data on :8109 and,
when explicitly enabled, falls back to the same Bronze Parquet through DuckDB.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = "job.v1"
DEFAULT_BASE_URL = os.environ.get("BOOK_JOB_DATA_URL", "http://127.0.0.1:8109")
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("BOOK_JOB_DATA_TIMEOUT_SECONDS", "10"))
DEFAULT_API_TOKEN = os.environ.get("BOOK_JOB_DATA_API_TOKEN", "").strip()
DEFAULT_LAKE_URI = os.environ.get("SOLO_EMPIRE_DATA_LAKE_URI", os.environ.get("DATA_LAKE_URI", ""))
JOB_DATASETS = {
    "job_postings",
    "job_postings_history",
    "job_matches",
    "job_matches_history",
    "job_leads",
    "job_leads_history",
}


@dataclass
class JobLoadResult:
    source: str = "none"
    endpoint: str = "/v1/records"
    data_status: str = "unavailable"
    items: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""
    retrieved_at: str = ""

    @property
    def ok(self) -> bool:
        return self.data_status in {"ok", "stale"} and bool(self.items)


def _request_json(url: str, *, timeout: float, api_token: str = "") -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    token = api_token or DEFAULT_API_TOKEN
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_job_envelope(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and body.get("schema_version") == SCHEMA_VERSION
        and body.get("source") == "book-job-data"
        and isinstance(body.get("items"), list)
        and "data_status" in body
        and "next_cursor" in body
    )


def _lake_rows(*, dataset: str, history: bool, data_lake_uri: Optional[str]) -> list[dict[str, Any]]:
    if dataset not in JOB_DATASETS:
        return []
    root = Path(__file__).resolve().parents[3]
    scripts = root / "infra" / "scripts"
    import sys

    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from data_lake.product_adapter import LakeProductContract, parse_payload_json, read_bronze_rows, select_latest_bronze_rows  # type: ignore

    contract = LakeProductContract(
        source="book-job-data",
        domain="jobs",
        product_schema_version=SCHEMA_VERSION,
        privacy_class="internal",
        retention_class="operational",
        bronze_schema_version="1",
        project_root=root,
        data_lake_uri=data_lake_uri or DEFAULT_LAKE_URI,
        datasets=tuple(sorted(JOB_DATASETS)),
    )
    rows = read_bronze_rows(contract, dataset, data_lake_uri=data_lake_uri or DEFAULT_LAKE_URI)
    if not history:
        rows = select_latest_bronze_rows(rows)
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        payload = parse_payload_json(row)
        item = dict(payload)
        item.update(
            {
                "record_id": str(payload.get("id") or row.get("source_record_id") or row.get("event_id") or "") + (f"#h{idx}" if history else ""),
                "event_time": str(row.get("event_time") or payload.get("event_time") or ""),
                "source_record_id": str(row.get("source_record_id") or ""),
                "ingest_run_id": str(row.get("ingest_run_id") or ""),
                "raw_object_key": str(row.get("raw_object_key") or ""),
            }
        )
        items.append(item)
    return items


def fetch_jobs(
    *,
    base_url: str = DEFAULT_BASE_URL,
    dataset: str = "job_postings",
    history: bool = False,
    limit: int = 500,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_lake_fallback: bool = True,
    data_lake_uri: Optional[str] = None,
    api_token: str = "",
    fetch_impl=None,
) -> JobLoadResult:
    endpoint = "/v1/history" if history else "/v1/records"
    params = {"limit": str(max(1, min(int(limit), 500))), "dataset": dataset}
    url = f"{base_url.rstrip('/')}{endpoint}?{urllib.parse.urlencode(params)}"
    result = JobLoadResult(endpoint=endpoint)
    try:
        body = fetch_impl(url) if fetch_impl is not None else _request_json(url, timeout=timeout, api_token=api_token)
        if not is_job_envelope(body):
            raise ValueError("invalid job.v1 envelope")
        result.source = "api"
        result.data_status = str(body.get("data_status") or "empty")
        result.items = [item for item in body.get("items", []) if isinstance(item, dict)]
        result.retrieved_at = str(body.get("retrieved_at") or "")
        if not result.items and result.data_status == "ok":
            result.data_status = "empty"
        return result
    except Exception as exc:  # noqa: BLE001 - consumers need a safe status object
        result.error_message = f"job API unavailable: {type(exc).__name__}"

    if not use_lake_fallback:
        return result
    try:
        items = _lake_rows(dataset=dataset, history=history, data_lake_uri=data_lake_uri)
        result.source = "bronze_duckdb"
        result.items = items
        result.data_status = "ok" if items else "empty"
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_message = f"{result.error_message}; lake fallback unavailable: {type(exc).__name__}"
        return result
