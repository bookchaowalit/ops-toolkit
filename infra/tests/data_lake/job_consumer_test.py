from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from siblings import ARCHITECTURE_ROOT, exit_unless_exists, requires  # noqa: E402
JOB_CONTRACT = ARCHITECTURE_ROOT / "systems" / "architecture" / "job-data-contract-v1.yaml"

import data_product_contract_guard as guard  # noqa: E402
import job_client  # noqa: E402


SAMPLE = {
    "schema_version": "job.v1",
    "source": "book-job-data",
    "retrieved_at": "2026-08-08T00:00:00Z",
    "data_status": "ok",
    "items": [{"id": "job:1", "title": "Data Engineer", "source": "JobsDB-TH"}],
    "next_cursor": None,
}


class JobClientTests(unittest.TestCase):
    def test_envelope_and_api_first(self):
        self.assertTrue(job_client.is_job_envelope(SAMPLE))
        result = job_client.fetch_jobs(fetch_impl=lambda _url: SAMPLE, use_lake_fallback=False)
        self.assertEqual(result.source, "api")
        self.assertEqual(result.data_status, "ok")
        self.assertEqual(result.items[0]["title"], "Data Engineer")

    def test_malformed_envelope_is_not_consumed(self):
        result = job_client.fetch_jobs(fetch_impl=lambda _url: {"schema_version": "unknown.v1"}, use_lake_fallback=False)
        self.assertEqual(result.source, "none")
        self.assertEqual(result.data_status, "unavailable")

    @requires(JOB_CONTRACT, "architecture-docs job data contract")
    def test_track_b_files_have_no_capture_csv_dependency(self):
        errors: list[str] = []
        guard.check_job_contract(errors)
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_csv_guard_uses_basename_boundaries(self):
        self.assertTrue(guard._contains_csv_reference("data/leads.csv", "leads.csv"))
        self.assertFalse(
            guard._contains_csv_reference(
                "data/property_social_leads.csv", "leads.csv"
            )
        )


if __name__ == "__main__":
    unittest.main()
