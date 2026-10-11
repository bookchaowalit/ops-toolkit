#!/usr/bin/env python3
"""Contract tests for the model catalog and LLM lab budget guard."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "platform" / "llm_lab_budget.py"
SPEC = importlib.util.spec_from_file_location("llm_lab_budget", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
llm = importlib.util.module_from_spec(SPEC)
sys.modules["llm_lab_budget"] = llm
SPEC.loader.exec_module(llm)


class LlmLabBudgetTest(unittest.TestCase):
    def event(self, started='2026-09-12T00:00:00Z', hours=1):
        return dict(event='session_started', session_id='gpu', started_at=started,
                    gpu_hourly_usd=2, gpu_count=1, max_hours=hours, reserved_cost_usd=hours * 2)

    def test_overdue_keeps_accruing_and_blocks_new_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp))
            result = llm.build_status(settings, now=datetime(2026, 9, 12, 3, tzinfo=timezone.utc), events=[self.event()])
            self.assertEqual(result['spent_usd'], 6)
            self.assertEqual(result['decision'], 'deny')
            self.assertIn('gpu_session_overdue', result['reasons'])

    def test_closed_session_prorates_across_month_and_keeps_zero_duration_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp))
            start = self.event('2026-08-31T23:00:00Z', 2)
            stop = dict(event='session_stopped', session_id='gpu', stopped_at='2026-09-01T01:00:00Z', actual_cost_usd=4)
            result = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc), events=[start, stop])
            self.assertEqual(result['spent_usd'], 2)
            start['started_at'] = stop['stopped_at']
            result = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc), events=[start, stop])
            self.assertEqual(result['spent_usd'], 4)

    def test_concurrent_cli_sessions_cannot_reserve_same_remaining_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp), budget=3)
            env = {**os.environ, 'LLM_LAB_MONTHLY_BUDGET_USD': '3', 'LLM_LAB_LEDGER_PATH': str(settings.ledger_path)}
            def start(i):
                return subprocess.run([sys.executable, str(MODULE_PATH), 'session-start', '--gpu-hourly-usd', '2', '--max-hours', '1', '--session-id', f'gpu-{i}'], env=env, capture_output=True).returncode
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(start, range(2))), [0, 3])

    def test_duplicate_session_and_denied_cli_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp))
            args = Namespace(profile='compact', provider='test', gpu_hourly_usd=1, gpu_count=1, max_hours=1, session_id='dup')
            llm._start_session(settings, args)
            before = settings.ledger_path.read_bytes()
            with self.assertRaises(llm.BudgetError):
                llm._start_session(settings, args)
            self.assertEqual(before, settings.ledger_path.read_bytes())
            env = {**os.environ, 'LLM_LAB_MONTHLY_BUDGET_USD': '0', 'LLM_LAB_LEDGER_PATH': str(settings.ledger_path)}
            result = subprocess.run([sys.executable, str(MODULE_PATH), 'check', '--estimate-usd', '1'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 3)

    def _settings(self, root: Path, budget: float | None = 100.0, *, subscription: float = 0.0, storage: float = 0.0) -> llm.Settings:
        return llm.Settings(
            monthly_budget_usd=budget,
            hard_stop_ratio=1.0,
            allow_unmetered_api=False,
            ledger_path=root / "events.jsonl",
            catalog_path=ROOT / "platform" / "llm-lab-models.json",
            subscription_monthly_usd=subscription,
            storage_monthly_usd=storage,
        )

    def test_subscription_commitment_is_reserved_before_variable_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp), budget=100, subscription=80)
            status = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(status["committed_usd"], 80)
            self.assertEqual(status["total_projected_usd"], 80)
            self.assertEqual(status["remaining_usd"], 20)
            self.assertEqual(status["decision"], "allow")
            denied = llm.check_budget(settings, estimate_usd=21, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(denied["decision"], "deny")
            self.assertEqual(denied["reason"], "request_would_exceed_monthly_budget")

    def test_subscription_commitment_cannot_exceed_shared_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp), budget=100, subscription=101)
            status = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(status["decision"], "deny")
            self.assertEqual(status["reason"], "subscription_exceeds_monthly_budget")

    def test_storage_commitment_is_reserved_before_variable_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp), budget=100, subscription=20, storage=10)
            status = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(status["committed_usd"], 30)
            self.assertEqual(status["subscription_monthly_usd"], 20)
            self.assertEqual(status["storage_monthly_usd"], 10)
            self.assertEqual(status["remaining_usd"], 70)
            denied = llm.check_budget(settings, estimate_usd=71, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(denied["decision"], "deny")

    def test_catalog_exposes_switchable_profiles_without_provider_payloads(self):
        catalog = llm.load_catalog(ROOT / "platform" / "llm-lab-models.json")
        self.assertEqual(set(catalog["profiles"]), {"compact", "daily", "frontier"})
        self.assertEqual(catalog["profiles"]["compact"]["service_lane"], "runpod")
        self.assertEqual(catalog["profiles"]["compact"]["activation"], "on_demand")
        self.assertEqual(catalog["profiles"]["compact"]["pod_id"], "pod-1")
        self.assertEqual(catalog["profiles"]["compact"]["model_id"], "nvidia/Qwen3.8-27B-NVFP4")
        self.assertEqual(
            catalog["profiles"]["compact"]["model_id_status"],
            "verified_official_checkpoint_blackwell_target",
        )
        self.assertEqual(catalog["profiles"]["compact"]["quantization"], "modelopt_fp4")
        self.assertEqual(catalog["profiles"]["compact"]["initial_concurrent_agents"], 4)
        env = llm.profile_environment("daily", catalog)
        self.assertEqual(env["VLLM_MODEL_ID"], "Qwen/Qwen3-Coder-Next")
        self.assertEqual(env["AI_GATEWAY_MODEL_QUALITY"], "qwen3-coder-next")
        self.assertNotIn("api_key", json_text(catalog))

    def test_missing_budget_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp), budget=None)
            result = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(result["decision"], "deny")
            self.assertEqual(result["reason"], "monthly_budget_not_configured")

    def test_api_spend_is_external_and_never_blocks_the_gpu_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self._settings(Path(temp))
            result = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(result["decision"], "allow")
            self.assertEqual(result["api"]["status"], "external")
            self.assertEqual(result["api"]["cost_usd"], 0.0)

    def test_session_reservation_and_stop_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root)
            args = Namespace(
                profile="compact",
                provider="runpod-vllm",
                gpu_hourly_usd=5.0,
                gpu_count=1,
                max_hours=2.0,
                session_id="session-1",
            )
            started = llm._start_session(settings, args)
            self.assertEqual(started["decision"], "allow")
            status = llm.build_status(settings, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
            self.assertEqual(status["reserved_usd"], 10.0)
            stopped = llm._stop_session(settings, Namespace(session_id="session-1", actual_cost_usd=1.25))
            self.assertEqual(stopped["decision"], "stopped")
            again = llm._stop_session(settings, Namespace(session_id="session-1", actual_cost_usd=None))
            self.assertEqual(again["decision"], "already_stopped")

    def test_corrupt_ledger_blocks_new_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root)
            settings.ledger_path.write_text('{"event":"unknown"}\n', encoding="utf-8")
            args = Namespace(
                profile="compact",
                provider="runpod-vllm",
                gpu_hourly_usd=1.0,
                gpu_count=1,
                max_hours=1.0,
                session_id="session-2",
            )
            result = llm._start_session(settings, args)
            self.assertEqual(result["decision"], "deny")
            self.assertEqual(result["reason"], "gpu_ledger_invalid")

    def test_pilot_preflight_is_read_only_and_computes_bounded_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root, budget=100)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
            }
            result = llm.build_pilot_preflight(
                profile_name="compact",
                env=env,
                gpu_hourly_usd=2,
                gpu_count=1,
                duration_minutes=30,
                api_estimate_usd=0.25,
                storage_estimate_usd=0.10,
                now=datetime(2026, 9, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(result["contract"], "solo-empire.llm-lab-pilot-preflight.v1")
            self.assertEqual(result["decision"], "allow")
            self.assertEqual(result["cost"]["gpu_max_usd"], 1.0)
            self.assertEqual(result["cost"]["pilot_cap_usd"], 1.35)
            self.assertTrue(result["read_only"])
            self.assertFalse(result["provider_call_attempted"])
            self.assertFalse(result["write_attempted"])
            self.assertFalse(settings.ledger_path.exists())
            self.assertNotIn("api_key", json_text(result).lower())

    def test_pilot_preflight_includes_subscription_commitment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(Path(temp), budget=100, subscription=90)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "AI_SUBSCRIPTION_MONTHLY_USD": "90",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
            }
            result = llm.build_pilot_preflight(
                profile_name="compact", env=env, gpu_hourly_usd=1, duration_minutes=30,
                api_estimate_usd=0, storage_estimate_usd=0,
                now=datetime(2026, 9, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(result["decision"], "allow")
            self.assertEqual(result["cost"]["subscription_monthly_usd"], 90)
            self.assertEqual(result["cost"]["current_projected_usd"], 90)
            self.assertEqual(result["cost"]["headroom_after_pilot_usd"], 9.5)

    def test_pilot_preflight_denies_cap_and_required_stop_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root, budget=1)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "1",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
            }
            result = llm.build_pilot_preflight(
                profile_name="compact",
                env=env,
                gpu_hourly_usd=3,
                duration_minutes=30,
                require_provider_stop_hook=True,
            )
            self.assertEqual(result["decision"], "deny")
            self.assertIn("pilot_cap_exceeds_or_cannot_be_compared", result["blockers"])
            self.assertIn("provider_stop_hook_not_configured", result["blockers"])

    def test_pilot_preflight_rejects_always_on_duration_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root, budget=100)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
                "LLM_LAB_MAX_SESSION_HOURS": "12",
            }
            result = llm.build_pilot_preflight(
                profile_name="compact", env=env, gpu_hourly_usd=1, duration_minutes=13 * 60,
                api_estimate_usd=0, storage_estimate_usd=0,
                now=datetime(2026, 9, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(result["decision"], "deny")
            self.assertIn("session_duration_exceeds_cap", result["blockers"])

    def test_settings_reject_an_always_on_session_cap(self):
        with self.assertRaises(llm.BudgetError):
            llm.load_settings({"LLM_LAB_MAX_SESSION_HOURS": "13"})

    def test_pilot_preflight_accepts_complete_stop_hook_without_calling_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root, budget=100)
            hook = root / "stop-hook"
            hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            hook.chmod(0o700)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
                "VLLM_IDLE_STOP_HOOK": str(hook),
                "RUNPOD_AUTO_STOP_ENABLED": "true",
                "RUNPOD_POD_ID": "pod-abcdef",
                "RUNPOD_API_KEY": "secret-value-never-returned",
            }
            result = llm.build_pilot_preflight(
                profile_name="compact",
                env=env,
                gpu_hourly_usd=1,
                duration_minutes=30,
                api_estimate_usd=0,
                storage_estimate_usd=0,
                require_provider_stop_hook=True,
            )
            hook_check = next(item for item in result["checks"] if item["name"] == "provider_stop_hook")
            self.assertEqual(hook_check["status"], "pass")
            self.assertEqual(result["decision"], "allow")
            self.assertNotIn("secret-value-never-returned", json_text(result))

    def test_pilot_preflight_rejects_idle_guard_that_outlives_reserved_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = self._settings(root, budget=100)
            env = {
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(settings.ledger_path),
                "VLLM_IDLE_TIMEOUT_SEC": "60",
                "VLLM_MAX_RUNTIME_SEC": "3601",
                "VLLM_IDLE_POLL_SEC": "30",
            }
            result = llm.build_pilot_preflight(
                profile_name="compact",
                env=env,
                gpu_hourly_usd=1,
                duration_minutes=60,
                api_estimate_usd=0,
                storage_estimate_usd=0,
            )
            guard_check = next(item for item in result["checks"] if item["name"] == "idle_guard")
            self.assertEqual(guard_check["reason"], "idle_guard_exceeds_session_reservation")
            self.assertEqual(result["decision"], "deny")


def json_text(value: object) -> str:
    """Serialize only for a small secret-field assertion."""
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
