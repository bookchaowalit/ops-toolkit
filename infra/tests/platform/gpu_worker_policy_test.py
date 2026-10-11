#!/usr/bin/env python3
"""Contract tests for specialized GPU routing and queue metadata."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLATFORM_SCRIPTS = ROOT / "scripts" / "platform"
sys.path.insert(0, str(PLATFORM_SCRIPTS))
MODULE_PATH = PLATFORM_SCRIPTS / "gpu_worker_policy.py"
SPEC = importlib.util.spec_from_file_location("gpu_worker_policy", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
policy = importlib.util.module_from_spec(SPEC)
sys.modules["gpu_worker_policy"] = policy
SPEC.loader.exec_module(policy)


class GpuWorkerPolicyTest(unittest.TestCase):
    def test_catalog_has_specialized_workers_and_queue_contract(self) -> None:
        catalog = policy.load_catalog(ROOT / "platform" / "gpu-worker-catalog.json")
        self.assertEqual(catalog["schema_version"], "solo-empire.gpu-worker-catalog.v1")
        self.assertEqual(catalog["thresholds_status"], "planning_defaults_require_job_benchmark")
        self.assertEqual(catalog["queue"]["raw_payloads"], "forbidden")
        self.assertEqual(set(catalog["pods"]), {"pod-1", "pod-2", "pod-3"})
        self.assertEqual(catalog["pods"]["pod-2"]["language_support"], ["th", "en"])
        self.assertEqual(catalog["workers"]["doc-ocr"]["pod"], "pod-2")
        self.assertEqual(catalog["workers"]["media-video"]["pod"], "pod-3")
        self.assertEqual(catalog["workloads"]["ocr"]["worker"], "doc-ocr")
        self.assertEqual(catalog["workers"]["qwen-text"]["pod"], "pod-1")
        self.assertEqual(catalog["workers"]["qwen-text"]["model_candidates"], ["nvidia/Qwen3.8-27B-NVFP4"])
        self.assertEqual(catalog["pods"]["pod-2"]["models"][3]["id"], "Qwen/Qwen3-ASR-1.7B-hf")
        self.assertEqual(catalog["pods"]["pod-1"]["models"][0]["model_id_status"], "verified_official_checkpoint_blackwell_target")

    def test_model_preflight_is_read_only_and_blocks_unverified_launches(self) -> None:
        catalog = policy.load_catalog(ROOT / "platform" / "gpu-worker-catalog.json")
        result = policy.build_preflight(catalog)
        self.assertEqual(result["contract"], "solo-empire.gpu-worker-preflight.v1")
        self.assertFalse(result["provider_calls"])
        self.assertFalse(result["launch_ready"])
        qwen = next(row for row in result["models"] if row["model_id"] == "nvidia/Qwen3.8-27B-NVFP4")
        self.assertIn("hardware_or_pod_gate_blocked", qwen["blockers"])
        self.assertEqual(qwen["source_url"], "https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4")

    def test_small_ocr_stays_on_api_fallback(self) -> None:
        result = policy.build_route(workload="ocr", units=20, runpod_enabled=True)
        self.assertEqual(result["lane"], "api_or_cpu")
        self.assertEqual(result["execution_status"], "ready")
        self.assertEqual(result["reason"], "below_gpu_volume_trigger")
        self.assertFalse(result["queue_required"])
        self.assertEqual(result["pod"], "pod-2")
        self.assertEqual(result["language_support"], ["th", "en"])

    def test_small_image_requires_a_managed_media_fallback(self) -> None:
        result = policy.build_route(workload="image", units=1, runpod_enabled=True)
        self.assertEqual(result["lane"], "managed_media_api")
        self.assertEqual(result["execution_status"], "fallback_required")
        self.assertEqual(result["pod"], "pod-3")

    def test_large_image_is_blocked_until_runpod_is_enabled(self) -> None:
        result = policy.build_route(workload="image", units=20, runpod_enabled=False)
        self.assertEqual(result["lane"], "runpod")
        self.assertEqual(result["execution_status"], "blocked")
        self.assertEqual(result["reason"], "runpod_adapter_disabled")

    def test_new_specialist_and_media_workloads_keep_pod_boundaries(self) -> None:
        tts = policy.build_route(workload="tts", units=1, runpod_enabled=True)
        self.assertEqual(tts["pod"], "pod-2")
        self.assertEqual(tts["model_candidates"], ["fishaudio/s2-pro"])
        music = policy.build_route(workload="music", units=20, runpod_enabled=False)
        self.assertEqual(music["pod"], "pod-3")
        self.assertEqual(music["model_candidates"], ["ACE-Step/Ace-Step1.5"])
        self.assertEqual(music["execution_status"], "blocked")

    def test_coding_worker_is_blocked_until_pod_hardware_gate_is_cleared(self) -> None:
        result = policy.build_route(
            workload="batch_text",
            units=1000,
            estimated_gpu_hours=1,
            gpu_hourly_usd=0.53,
            runpod_enabled=True,
            env={
                **os.environ,
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(Path(tempfile.gettempdir()) / "gpu-worker-test-events.jsonl"),
            },
        )
        self.assertEqual(result["pod"], "pod-1")
        self.assertEqual(result["execution_status"], "blocked")
        self.assertEqual(result["reason"], "pod_launch_gate_blocked")

    def test_private_batch_is_blocked_at_hosted_boundary(self) -> None:
        result = policy.build_route(workload="stt", units=20, privacy_class="private", runpod_enabled=True)
        self.assertEqual(result["lane"], "local")
        self.assertEqual(result["execution_status"], "blocked")
        self.assertEqual(result["reason"], "privacy_class_requires_approved_private_worker")

    def test_large_image_passes_shared_budget_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {
                **os.environ,
                "LLM_LAB_MONTHLY_BUDGET_USD": "100",
                "LLM_LAB_LEDGER_PATH": str(root / "events.jsonl"),
                "GPU_WORKER_RUNPOD_ENABLED": "true",
            }
            result = policy.build_route(
                workload="image", units=20, estimated_gpu_hours=1, gpu_hourly_usd=0.53,
                env=env,
            )
            self.assertEqual(result["execution_status"], "ready")
            self.assertEqual(result["cost_gate"]["decision"], "allow")
            self.assertEqual(result["estimated_cost_usd"], 0.53)

    def test_enqueue_is_preview_only_without_apply_and_idempotent_with_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = root / "jobs.json"
            route = {
                "lane": "runpod", "execution_status": "ready", "workload": "image",
                "units": 20, "unit": "assets", "privacy_class": "internal", "worker": "media-image",
                "pod": "pod-3",
                "estimated_gpu_hours": 1.0, "gpu_hourly_usd": 0.53, "estimated_cost_usd": 0.53,
            }
            preview = policy.enqueue_job(
                job_id="job-1", idempotency_key="image-batch-1", route=route, apply=False, queue_path=queue,
            )
            self.assertFalse(preview["queued"])
            self.assertFalse(queue.exists())
            first = policy.enqueue_job(
                job_id="job-1", idempotency_key="image-batch-1", route=route, apply=True, queue_path=queue,
            )
            second = policy.enqueue_job(
                job_id="job-2", idempotency_key="image-batch-1", route=route, apply=True, queue_path=queue,
            )
            self.assertTrue(first["queued"])
            self.assertEqual(second["reason"], "already_queued")
            payload = json.loads(queue.read_text(encoding="utf-8"))
            self.assertEqual(list(payload["jobs"]), ["job-1"])
            self.assertNotIn("prompt", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
