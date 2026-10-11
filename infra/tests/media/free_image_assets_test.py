#!/usr/bin/env python3
"""Tests for the official-API free image client."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


UTILS = Path(__file__).resolve().parents[2] / "scripts" / "utils"
sys.path.insert(0, str(UTILS))

import free_image_assets as stock  # noqa: E402


OPENVERSE = {
    "results": [
        {
            "id": "abc123",
            "title": "Matcha bowl",
            "url": "https://cdn.example.com/matcha.jpg",
            "foreign_landing_url": "https://openverse.org/image/abc123",
            "creator": "Ada",
            "creator_url": "https://example.com/ada",
            "license": "by",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Matcha bowl by Ada / CC BY 4.0",
            "filetype": "jpg",
        }
    ]
}
PEXELS = {
    "photos": [
        {
            "id": 9,
            "alt": "Temple",
            "url": "https://www.pexels.com/photo/9/",
            "photographer": "Bo",
            "photographer_url": "https://www.pexels.com/@bo",
            "src": {"large": "https://images.pexels.com/temple.jpg"},
        }
    ]
}


class FreeImageAssetsTest(unittest.TestCase):
    def test_editor_pack_queries_are_defined(self) -> None:
        self.assertIn("watercolor paper texture", stock.PACKS["editor"])
        self.assertIn("botanical illustration", stock.PACKS["editor"])

    def test_run_pack_aggregates_queries(self) -> None:
        def fetch(_url: str, _headers: dict[str, str]) -> dict:
            return OPENVERSE

        payload = stock.run_pack(
            "editor",
            limit=1,
            apply=False,
            as_json=False,
            fetch_impl=fetch,
        )
        self.assertEqual(payload["pack"], "editor")
        self.assertEqual(len(payload["batches"]), 3)
        self.assertGreaterEqual(payload["count"], 3)
        self.assertFalse(payload["scraped_html"])

    def test_blocks_private_download_urls(self) -> None:
        self.assertFalse(stock.is_safe_http_url("http://127.0.0.1/secret.png"))
        self.assertFalse(stock.is_safe_http_url("file:///etc/passwd"))
        self.assertTrue(stock.is_safe_http_url("https://cdn.example.com/a.jpg"))

    def test_parse_openverse_keeps_license_fields(self) -> None:
        assets = stock.parse_openverse(OPENVERSE)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].creator, "Ada")
        self.assertIn("creativecommons.org", assets[0].license_url)
        self.assertEqual(assets[0].provider, "openverse")

    def test_parse_pexels(self) -> None:
        assets = stock.parse_pexels(PEXELS)
        self.assertEqual(assets[0].provider, "pexels")
        self.assertEqual(assets[0].creator, "Bo")

    def test_skips_unsafe_openverse_url(self) -> None:
        body = {"results": [{"id": "x", "url": "http://127.0.0.1/a.jpg", "title": "no"}]}
        self.assertEqual(stock.parse_openverse(body), [])

    def test_search_and_apply_write_sidecar_not_html_scrape(self) -> None:
        def fetch(url: str, _headers: dict[str, str]) -> dict:
            self.assertIn("api.openverse.org", url)
            self.assertNotIn(".html", url)
            return OPENVERSE

        def download(url: str, _headers: dict[str, str]) -> bytes:
            self.assertEqual(url, "https://cdn.example.com/matcha.jpg")
            return b"fake-image-bytes"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_stock = stock.STOCK_ROOT
            original_catalog = stock.CATALOG_PATH
            original_csv = stock.DESIGN_CSV
            stock.STOCK_ROOT = root / "free-stock"
            stock.CATALOG_PATH = stock.STOCK_ROOT / "catalog.jsonl"
            stock.DESIGN_CSV = root / "design-assets.csv"
            try:
                payload = stock.run(
                    "matcha",
                    provider="openverse",
                    limit=1,
                    apply=True,
                    as_json=True,
                    fetch_impl=fetch,
                    download_impl=download,
                )
                self.assertFalse(payload["scraped_html"])
                self.assertEqual(payload["saved"][0]["status"], "downloaded")
                self.assertEqual(payload["assets"][0]["license"], "by")
                sidecar_dirs = list((root / "free-stock" / "openverse").glob("*"))
                self.assertTrue(sidecar_dirs)
                metadata = json.loads((sidecar_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["creator"], "Ada")
                attribution = (sidecar_dirs[0] / "ATTRIBUTION.md").read_text(encoding="utf-8")
                self.assertIn("CC BY", attribution)
                catalog = (root / "free-stock" / "catalog.jsonl").read_text(encoding="utf-8")
                self.assertIn("openverse:abc123", catalog)
            finally:
                stock.STOCK_ROOT = original_stock
                stock.CATALOG_PATH = original_catalog
                stock.DESIGN_CSV = original_csv

    def test_list_catalog_reads_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_catalog = stock.CATALOG_PATH
            stock.CATALOG_PATH = root / "catalog.jsonl"
            stock.CATALOG_PATH.write_text(
                json.dumps({"idempotency_key": "openverse:1", "title": "A", "license": "by"})
                + "\n",
                encoding="utf-8",
            )
            try:
                payload = stock.list_catalog(limit=10)
            finally:
                stock.CATALOG_PATH = original_catalog
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["assets"][0]["title"], "A")
        self.assertFalse(payload["scraped_html"])

    def test_pexels_without_key_is_not_called(self) -> None:
        called = []

        def fetch(url: str, _headers: dict[str, str]) -> dict:
            called.append(url)
            return {}

        original = stock.get_env
        stock.get_env = lambda key, default="": default  # type: ignore[assignment]
        try:
            payload = stock.run(
                "temple",
                provider="pexels",
                limit=1,
                apply=False,
                as_json=True,
                fetch_impl=fetch,
            )
        finally:
            stock.get_env = original  # type: ignore[assignment]
        self.assertEqual(called, [])
        self.assertEqual(payload["provider_status"]["pexels"], "credentials_not_configured")


if __name__ == "__main__":
    unittest.main()
