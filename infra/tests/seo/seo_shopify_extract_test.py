#!/usr/bin/env python3
"""Tests for the read-only Shopify extractor used by the SEO monthly report."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
UTILS = REPO_ROOT / "scripts" / "utils"
sys.path.insert(0, str(UTILS))

import seo_shopify_extract as shop  # noqa: E402


def fake_api(*, fail_query: str | None = None, http_error: int | None = None):
    calls: list[dict] = []

    def api_request(url, data=None, headers=None, method=None, timeout=None, **_):
        body = json.loads(data.decode())
        calls.append({"url": url, "body": body, "headers": headers, "method": method})
        if http_error:
            raise urllib.error.HTTPError(url, http_error, "err", {}, None)
        query = body["query"]
        variables = body.get("variables") or {}
        if "mutation" in query:
            raise AssertionError("extractor must never send mutations")
        if "shopifyqlQuery" in query:
            q = variables["q"]
            if fail_query and fail_query in q:
                return {"data": {"shopifyqlQuery": {"tableData": None, "parseErrors": ["bad column"]}}}
            return {
                "data": {
                    "shopifyqlQuery": {
                        "tableData": {
                            "columns": [{"name": "month"}, {"name": "sessions"}],
                            "rows": [["2026-09-01", "25769"], {"month": "2026-08-01", "sessions": "30935"}],
                        },
                        "parseErrors": [],
                    }
                }
            }
        if "shopLocales" in query:
            return {"data": {"shopLocales": [{"locale": "th", "primary": True, "published": True}]}}
        if "translatableResources" in query:
            after = variables.get("after")
            if variables["type"] == "PRODUCT" and after is None:
                return {
                    "data": {
                        "translatableResources": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [
                                {
                                    "resourceId": "gid://shopify/Product/1",
                                    "translatableContent": [
                                        {"key": "title", "value": "Rolex"},
                                        {"key": "handle", "value": "rolex"},
                                        {"key": "body_html", "value": "<p>ไทย</p>"},
                                        {"key": "meta_title", "value": ""},
                                    ],
                                    "en": [
                                        {"key": "title", "value": "Rolex", "outdated": False},
                                        {"key": "body_html", "value": "<p>ยังไทย</p>", "outdated": True},
                                    ],
                                    "zh": [{"key": "title", "value": "Rolex", "outdated": False}],
                                }
                            ],
                        }
                    }
                }
            return {"data": {"translatableResources": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}
        raise AssertionError(f"unexpected query: {query[:60]}")

    return api_request, calls


class MonthWindowTest(unittest.TestCase):
    def test_partial_month_ends_yesterday_and_spans_three_months(self):
        window = shop.month_window("2026-09", today=date(2026, 9, 30))
        self.assertEqual(window["since"], "2026-07-01")
        self.assertEqual(window["month_start"], "2026-09-01")
        self.assertEqual(window["until"], "2026-09-29")
        self.assertTrue(window["partial"])

    def test_closed_month_and_year_wrap(self):
        window = shop.month_window("2026-02", today=date(2026, 3, 5))
        self.assertEqual(window["since"], "2025-12-01")
        self.assertEqual(window["until"], "2026-02-28")
        self.assertFalse(window["partial"])

    def test_rejects_future_and_bad_month(self):
        with self.assertRaises(ValueError):
            shop.month_window("2026-10", today=date(2026, 10, 1))
        with self.assertRaises(ValueError):
            shop.month_window("2026-9", today=date(2026, 10, 1))


class ExtractTest(unittest.TestCase):
    def test_missing_credentials_returns_status_without_calls(self):
        api, calls = fake_api()
        payload = shop.extract_shopify(
            month="2026-09", domain="shop.example", token="", api_request=api, today=date(2026, 9, 30)
        )
        self.assertEqual(payload["status"], "credentials_not_configured")
        self.assertIn("SHOPIFY_ADMIN_TOKEN", payload["needed"])
        self.assertEqual(calls, [])

    def test_extract_normalizes_rows_and_reports_locales(self):
        api, calls = fake_api()
        payload = shop.extract_shopify(
            month="2026-09", domain="shop.example", token="tok", api_request=api, today=date(2026, 9, 30)
        )
        self.assertEqual(payload["status"], "ok")
        rows = payload["analytics"]["sessions_monthly"]["rows"]
        self.assertEqual(rows[0], {"month": "2026-09-01", "sessions": "25769"})
        self.assertEqual(rows[1]["sessions"], "30935")
        self.assertEqual(payload["shop_locales"]["rows"][0]["locale"], "th")
        self.assertTrue(all(call["method"] == "POST" for call in calls))
        self.assertIn("/admin/api/", calls[0]["url"])
        self.assertNotIn("tok", json.dumps(payload))
        self.assertTrue(any("in progress" in note for note in payload["notes"]))

    def test_parse_error_marks_partial_without_inventing_rows(self):
        api, _ = fake_api(fail_query="FROM sales")
        payload = shop.extract_shopify(
            month="2026-09", domain="shop.example", token="tok", api_request=api, today=date(2026, 9, 30)
        )
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["failed_queries"], ["online_orders_monthly"])
        self.assertEqual(payload["analytics"]["online_orders_monthly"]["rows"], [])

    def test_forbidden_is_reported_per_query(self):
        api, _ = fake_api(http_error=403)
        payload = shop.extract_shopify(
            month="2026-09", domain="shop.example", token="tok", api_request=api, today=date(2026, 9, 30)
        )
        self.assertEqual(payload["analytics"]["sessions_monthly"]["status"], "forbidden")
        self.assertEqual(payload["shop_locales"]["status"], "error")

    def test_coverage_counts_missing_outdated_and_thai_and_skips_handle(self):
        api, _ = fake_api()
        payload = shop.extract_shopify(
            month="2026-09", domain="shop.example", token="tok", api_request=api,
            coverage=True, today=date(2026, 9, 30),
        )
        product = payload["translation_coverage"]["by_type"]["PRODUCT"]
        self.assertEqual(product["resources"], 1)
        self.assertEqual(product["pages"], 2)
        self.assertEqual(product["fields"], 2)  # handle and empty meta_title excluded
        self.assertEqual(product["en"], {"missing": 0, "outdated": 1, "thai_left": 1})
        self.assertEqual(product["zh-TW"], {"missing": 1, "outdated": 0, "thai_left": 0})

    def test_apply_writes_raw_file_only_when_data_exists(self):
        api, _ = fake_api()
        with tempfile.TemporaryDirectory() as tmp:
            payload = shop.run_extract(
                month="2026-09", apply=True, dest_root=Path(tmp),
                domain="shop.example", token="tok", api_request=api, today=date(2026, 9, 30),
            )
            target = Path(tmp) / "evidence" / "2026-09" / "raw" / "shopify.json"
            self.assertEqual(payload["written"], [str(target)])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["contract"], shop.CONTRACT)

            empty = shop.run_extract(
                month="2026-09", apply=True, dest_root=Path(tmp) / "none",
                domain="shop.example", token="", api_request=api, today=date(2026, 9, 30),
            )
            self.assertEqual(empty["written"], [])
            self.assertFalse((Path(tmp) / "none").exists())


if __name__ == "__main__":
    unittest.main()
