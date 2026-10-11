#!/usr/bin/env python3
"""Tests for the Pixiu Watch SEO monthly-report CLI."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
UTILS = REPO_ROOT / "scripts" / "utils"
CLI = REPO_ROOT / "scripts" / "cli"
sys.path.insert(0, str(UTILS))
sys.path.insert(0, str(CLI))

import seo_monthly_report as seo  # noqa: E402
import seo_live_fetch as live  # noqa: E402


def write_gsc_zip(path: Path) -> Path:
    devices = io.StringIO()
    writer = csv.writer(devices)
    writer.writerow(
        ["Device", "Clicks", "Clicks", "Impressions", "Impressions", "CTR", "CTR", "Position", "Position"]
    )
    writer.writerow(
        [
            "",
            "Last 28 days",
            "Previous 28 days",
            "Last 28 days",
            "Previous 28 days",
            "Last 28 days",
            "Previous 28 days",
            "Last 28 days",
            "Previous 28 days",
        ]
    )
    writer.writerow(["MOBILE", "2653", "2243", "113363", "107000", "2.34%", "2.10%", "6.56", "6.40"])
    writer.writerow(["DESKTOP", "753", "700", "45928", "45000", "1.64%", "1.56%", "10.53", "10.20"])
    writer.writerow(["TABLET", "77", "70", "3075", "4183", "2.50%", "1.67%", "5.92", "6.10"])

    queries = io.StringIO()
    qwriter = csv.writer(queries)
    qwriter.writerow(["Top queries", "Clicks", "Clicks", "Impressions", "CTR", "Position"])
    qwriter.writerow(["", "Last 28 days", "Previous 28 days", "Last 28 days", "Last 28 days", "Last 28 days"])
    qwriter.writerow(["นาฬิกามือสอง", "90", "80", "1205", "7.47%", "4.02"])
    qwriter.writerow(["rolex มือสอง", "21", "27", "473", "4.44%", "6.12"])

    filters = io.StringIO()
    fwriter = csv.writer(filters)
    fwriter.writerow(["Filter", "Value"])
    fwriter.writerow(["Search type", "Web"])
    fwriter.writerow(["Date range", "Last 28 days"])

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Devices.csv", devices.getvalue())
        archive.writestr("Queries.csv", queries.getvalue())
        archive.writestr("Filters.csv", filters.getvalue())
    return path


class SeoMonthlyReportTest(unittest.TestCase):
    def test_fixture_july_kpis_match_shipped_pack(self) -> None:
        pack = seo.fixture_pack("july-2026")
        gsc = pack["gsc"]
        self.assertEqual(gsc["clicks"], 3483)
        self.assertEqual(gsc["impressions"], 162366)
        self.assertAlmostEqual(gsc["ctr"], 2.145, places=2)
        self.assertAlmostEqual(gsc["position"], 7.67, places=2)
        self.assertEqual(pack["ahrefs"]["health_score"], 99)
        self.assertIsNone(pack["ga4"])
        self.assertFalse(pack["fixes_applied"])
        self.assertFalse(pack["sent"])
        self.assertTrue(any("GA4" in note for note in pack["limitations"]))
        self.assertTrue(any("Last 28 days" in note for note in pack["limitations"]))

    def test_parse_gsc_zip_comparison_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = write_gsc_zip(Path(tmp) / "gsc.zip")
            gsc = seo.parse_gsc_zip(zip_path)
        self.assertEqual(gsc["clicks"], 3483)
        self.assertEqual(gsc["clicks_previous"], 3013)
        self.assertEqual(gsc["impressions"], 162366)
        self.assertEqual(gsc["window"], "last_28_days")
        self.assertEqual(gsc["devices"][0]["key"], "Mobile")
        queries = {row["key"]: row for row in gsc["queries"]}
        self.assertEqual(queries["นาฬิกามือสอง"]["clicks_change"], 10)
        self.assertEqual(queries["rolex มือสอง"]["clicks_change"], -6)

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            payload = seo.run(command="report", fixture="july-2026", apply=False, dest_root=dest)
            self.assertFalse(payload["apply"])
            self.assertEqual(payload["written"], [])
            self.assertFalse(list(dest.iterdir()))
            self.assertEqual(payload["kpis"]["clicks"], 3483)
            self.assertFalse(payload["kpis"]["ga4"])

    def test_apply_writes_pack_markdown_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            payload = seo.run(command="report", fixture="july-2026", apply=True, dest_root=dest)
            self.assertTrue(payload["apply"])
            pack_path = dest / "evidence" / "2026-07" / "pack.json"
            draft = dest / "2026-07-seo-report-draft.md"
            html_path = dest / "2026-07-seo-report-client.html"
            self.assertTrue(pack_path.exists())
            self.assertTrue(draft.exists())
            self.assertTrue(html_path.exists())
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["contract"], seo.CONTRACT)
            self.assertIsNone(pack["ga4"])
            markdown = draft.read_text(encoding="utf-8")
            html_text = html_path.read_text(encoding="utf-8")
            self.assertIn("3,483", markdown)
            self.assertIn("162,366", markdown)
            self.assertIn("Pending GA4 export", markdown)
            self.assertIn("กรกฎาคม 2026", html_text)
            self.assertIn("ไม่ใส่เป็นศูนย์", html_text)
            self.assertIn("อ้างอิงข้อมูลที่ตรวจสอบได้", html_text)
            self.assertNotIn("ยังไม่ได้ส่ง", html_text)
            self.assertNotIn("GA4 users / sessions | **0**", markdown)

    def test_missing_gsc_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            seo.run(command="report", client="pixiu-watch", month="2026-08")

    def test_status_lists_existing_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            seo.run(command="report", fixture="july-2026", apply=True, dest_root=dest)
            payload = seo.run(command="status", client="pixiu-watch", dest_root=dest)
        self.assertEqual(payload["command"], "status")
        self.assertEqual(payload["months"][0]["month"], "2026-07")
        self.assertTrue(payload["months"][0]["pack_json"])
        self.assertFalse(payload["live_site_access"])
        self.assertIn("call Search Console / Ahrefs / GA4 APIs", payload["does_not"])

    def test_unknown_client_fails(self) -> None:
        with self.assertRaises(ValueError):
            seo.status_payload("not-a-client")

    def test_gmail_compact_and_performance_email(self) -> None:
        self.assertEqual(live.parse_compact_number("3.82K"), 3820)
        self.assertEqual(live.parse_compact_number("179K"), 179000)
        parsed = live.parse_gsc_performance_email(
            "Your July Search performance for https://pixiuwatch.com/",
            "Your July 2026 performance on Google Search https://pixiuwatch.com/ 3.82K Clicks (web) 179K Impressions (web) 338 Pages with first impressions (estimated)",
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["month"], "2026-07")
        self.assertEqual(parsed["clicks"], 3820)
        self.assertEqual(parsed["impressions"], 179000)
        self.assertEqual(parsed["pages_first_impressions"], 338)
        self.assertIsNone(parsed["ctr"])

    def test_ahrefs_health_and_failed_crawl(self) -> None:
        ok = live.parse_ahrefs_audit_email(
            "(Pixiuwatch) Image file size too large [New]: 169 URLs",
            "3,913 internal URLs were analyzed. Health Score 99 (+98) · Errors 219 (−2,457) · Warnings 3,908 (+1,208) · Notices 3,858 (+3,796) links to redirect New 3,578 View Noindex page in sitemap New 1 View 4XX page New 5 View",
            "Fri, 31 Jul 2026 02:36:08 +0000 (UTC)",
        )
        self.assertEqual(ok["health_score"], 99)
        self.assertEqual(ok["errors"], 219)
        self.assertEqual(ok["errors_change"], -2457)
        names = {item["name"]: item["count"] for item in ok["issues"]}
        self.assertEqual(names["Pages have links to redirects"], 3578)
        failed = live.parse_ahrefs_audit_email(
            "(Pixiuwatch) Site Audit crawl failed",
            "Your crawl failed to start because of an error.",
            "Thu, 20 Aug 2026 09:23:57 +0000 (UTC)",
        )
        self.assertEqual(failed["status"], "crawl_failed")
        self.assertIsNone(failed.get("health_score"))
        self.assertEqual(live._ahrefs_month_guess("Thu, 20 Aug 2026 09:23:57 +0000 (UTC)"), "2026-08")

    def test_failed_current_ahrefs_uses_dated_backlog_without_reusing_health(self) -> None:
        failed = {
            "source": "gmail",
            "status": "crawl_failed",
            "health_score": None,
            "issues": [],
            "last_success": {
                **seo.JULY_2026_AHREFS,
                "received_at": "Fri, 31 Jul 2026 02:36:08 +0000 (UTC)",
            },
        }
        pack = seo.build_pack(
            client="pixiu-watch",
            month="2026-08",
            gsc=seo.JULY_2026_GSC,
            ahrefs=failed,
            ga4=None,
        )

        markdown = seo.render_markdown(pack)
        html_text = seo.render_html(pack)

        self.assertIn("last verified crawl (2026-07-31)", markdown)
        self.assertIn("3,578", markdown)
        self.assertIn("169", markdown)
        self.assertIn("Crawl ไม่สำเร็จ", html_text)
        self.assertIn("ข้อมูลยืนยันล่าสุด 2026-07-31", html_text)
        self.assertIn("3,578", html_text)
        self.assertNotIn("ประมาณ — รายการ", html_text)
        self.assertNotIn("<b>99 / 100</b>", html_text)

    def test_opportunities_use_positive_evidence_and_keep_recovery_separate(self) -> None:
        gsc = {
            **seo.JULY_2026_GSC,
            "queries": [
                {"key": "pixiu watch", "clicks": 137, "clicks_change": 47, "ctr": 26.25, "position": 1.10},
                {"key": "นาฬิกามือสอง", "clicks": 103, "clicks_change": 10, "ctr": 10.77, "position": 3.38},
                {"key": "นาฬิกามือสองของแท้", "clicks": 15, "clicks_change": 7, "ctr": 7.28, "position": 3.28},
                {"key": "grand seiko มือสอง", "clicks": 16, "clicks_change": 7, "ctr": 8.16, "position": 4.38},
            ],
            "pages": [
                {"key": "https://pixiuwatch.com/", "clicks": 571, "clicks_change": 56, "ctr": 4.52, "position": 5.91},
                {"key": "https://pixiuwatch.com/blogs/knowledges/how-to-clean-your-watch-by-yourself", "clicks": 102, "clicks_change": 15, "ctr": 2.19, "position": 5.84},
                {"key": "https://pixiuwatch.com/collections/cartier-%E0%B8%A1%E0%B8%B7%E0%B8%AD%E0%B8%AA%E0%B8%AD%E0%B8%87", "clicks": 60, "clicks_change": 12, "ctr": 2.48, "position": 11.12},
            ],
            "search_appearance": [
                {"key": "PRODUCT_SNIPPETS", "clicks": 45, "clicks_previous": 89, "clicks_change": -44, "ctr": 2.87, "position": 13.93}
            ],
        }

        opportunities = seo.seo_opportunities(gsc)
        titles = [item["title"] for item in opportunities]

        self.assertEqual(len(opportunities), 5)
        self.assertIn("ขยาย Brand Demand ที่กำลังโต", titles)
        self.assertIn("เปลี่ยนบทความที่โตให้เป็นเส้นทางสู่ Conversion", titles)
        self.assertIn("กู้ Product snippets และ Rich Results", titles)
        self.assertIn("137 clicks (+47)", opportunities[0]["evidence"])

        good_news = seo.seo_good_news(gsc)
        self.assertEqual(len(good_news), 4)
        self.assertIn("Brand Demand", good_news[0])
        self.assertIn("Homepage มี Clicks เพิ่มขึ้น 56 ครั้ง", good_news[0])
        self.assertIn("นาฬิกามือสองของแท้", good_news[1])
        self.assertIn("Mobile ยังคงเป็นช่องทางหลัก", good_news[3])

        pack = seo.build_pack(
            client="pixiu-watch",
            month="2026-08",
            gsc=gsc,
            ahrefs=seo.JULY_2026_AHREFS,
            ga4=None,
        )
        html_text = seo.render_html(pack)
        self.assertIn("SEO Opportunities — โอกาสที่ทำต่อได้", html_text)
        self.assertIn("สิ่งที่ทำได้ดี", html_text)
        self.assertIn("สิ่งที่ปรับปรุงได้", html_text)
        self.assertIn("ทีม SEO จะตรวจ Product schema", html_text)
        self.assertIn("Organic Clicks", html_text)
        self.assertIn("หลักฐาน:", html_text)
        self.assertIn("สิ่งที่ทำต่อ:", html_text)

    def test_gsc_api_row_conversion_and_site_choice(self) -> None:
        rows = live.gsc_rows_to_items(
            [{"keys": ["MOBILE"], "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 6.5}],
            device=True,
        )
        self.assertEqual(rows[0]["key"], "Mobile")
        self.assertEqual(rows[0]["ctr"], 10.0)
        windows = live.month_windows("2026-08", today=date(2026, 8, 26))
        self.assertEqual(windows["start"], "2026-08-01")
        self.assertEqual(windows["end"], "2026-08-25")
        self.assertTrue(windows["incomplete"])
        self.assertEqual(
            live.select_gsc_site(["https://other.com/", "https://pixiuwatch.com/"], "https://pixiuwatch.com/"),
            "https://pixiuwatch.com/",
        )

    def test_ahrefs_api_probe_maps_http_errors(self) -> None:
        def boom(_url, **_kwargs):
            raise urllib.error.HTTPError(_url, 401, "unauthorized", hdrs=None, fp=io.BytesIO(b""))

        payload = live.probe_ahrefs_api("not-a-real-key", boom)
        self.assertEqual(payload["status"], "forbidden")
        self.assertEqual(payload["http_status"], 401)

    def test_pending_gsc_pack_does_not_invent_clicks(self) -> None:
        pack = seo.build_pack(
            client="pixiu-watch",
            month="2026-08",
            gsc=None,
            ahrefs={"source": "gmail", "status": "crawl_failed", "health_score": None, "issues": []},
            ga4=None,
            allow_pending_gsc=True,
            last_known_gsc={"month": "2026-07", "clicks_compact": "3.82K", "impressions_compact": "179K"},
        )
        self.assertTrue(pack["gsc_pending"])
        self.assertIsNone(pack["gsc"])
        self.assertTrue(any("pending" in note.lower() for note in pack["limitations"]))
        markdown = seo.render_markdown(pack)
        self.assertIn("pending", markdown.lower())
        self.assertNotIn("| Search Console clicks | **0**", markdown)
        self.assertIn("3.82K", markdown)


if __name__ == "__main__":
    unittest.main()
