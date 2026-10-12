#!/usr/bin/env python3
"""Offline contract checks for the RunPod vLLM operational scripts."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "support"))
from siblings import ARCHITECTURE_ROOT  # noqa: E402

# The launcher runbook asset is owned by architecture-docs (SOLO_ARCHITECTURE_ROOT).
LAUNCHER = ARCHITECTURE_ROOT / "operations" / "runbooks" / "assets" / "runpod-vllm" / "llm-coder.sh"
TUNNEL = ROOT / "scripts" / "platform" / "runpod_vllm_tunnel.sh"
SMOKE = ROOT / "scripts" / "platform" / "runpod_vllm_smoke.sh"
IDLE_GUARD = ROOT / "scripts" / "platform" / "runpod_vllm_idle_guard.sh"
STOP_HOOK = ROOT / "scripts" / "platform" / "runpod_stop_pod_hook.py"


def bash_executable() -> str:
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        git_bash = program_files / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return shutil.which("bash") or "bash"


def bash_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt":
        return resolved
    drive, tail = os.path.splitdrive(resolved)
    if not drive:
        raise ValueError(f"expected a drive-qualified Windows path: {resolved}")
    return f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"


def make_executable(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)
        return
    result = subprocess.run(
        [bash_executable(), "-c", 'chmod 700 -- "$1"', "bash", bash_path(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"Could not mark {path} executable: {result.stderr}")


class RunpodVllmContractTest(unittest.TestCase):
    def test_failed_or_unverified_provider_stop_never_closes_ledger(self):
        for hook_exit in (0, 1):
            with self.subTest(hook_exit=hook_exit), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                hook = root / 'hook'
                hook.write_text(f'#!/bin/sh\necho called > "{bash_path(root / "called")}"\nexit {hook_exit}\n')
                make_executable(hook)
                ledger = root / 'ledger'
                ledger.write_text('reservation-must-remain\n')
                env = {**os.environ, 'VLLM_IDLE_STOP_HOOK': bash_path(hook), 'VLLM_LAUNCHER': '',
                       'VLLM_IDLE_TIMEOUT_SEC': '10', 'VLLM_MAX_RUNTIME_SEC': '1', 'VLLM_IDLE_POLL_SEC': '1',
                       'VLLM_IDLE_METRICS_URL': 'http://127.0.0.1:1/metrics',
                       'LLM_LAB_SESSION_ID': 'gpu', 'LLM_LAB_LEDGER_PATH': str(ledger)}
                result = subprocess.run([bash_executable(), str(IDLE_GUARD)], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, hook_exit, result.stderr)
                self.assertTrue((root / 'called').exists())
                self.assertEqual(ledger.read_text(), 'reservation-must-remain\n')
                self.assertIn('provider_verification_required', result.stdout)

    def test_busy_second_engine_does_not_trigger_idle_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            curl = root / 'curl'
            curl.write_text('#!/bin/sh\ncat <<EOF\nvllm:num_requests_running{engine="0"} 0\nvllm:num_requests_running{engine="1"} 1\nvllm:num_requests_waiting{engine="0"} 0\nEOF\n')
            make_executable(curl)
            env = {**os.environ, 'PATH': f'{root}{os.pathsep}{os.environ["PATH"]}', 'VLLM_LAUNCHER': '/bin/true',
                   'VLLM_IDLE_STOP_HOOK': '', 'LLM_LAB_SESSION_ID': '',
                   'VLLM_IDLE_TIMEOUT_SEC': '1', 'VLLM_MAX_RUNTIME_SEC': '2', 'VLLM_IDLE_POLL_SEC': '1'}
            result = subprocess.run([bash_executable(), str(IDLE_GUARD)], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('max_runtime_reached', result.stdout)
            self.assertNotIn('reason=idle_timeout', result.stdout)

    def test_scripts_have_valid_bash_syntax(self):
        for script in (TUNNEL, SMOKE, IDLE_GUARD, *([LAUNCHER] if LAUNCHER.is_file() else [])):
            result = subprocess.run([bash_executable(), "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")

    def test_provider_stop_hook_has_valid_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(STOP_HOOK)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"{STOP_HOOK}: {result.stderr}")

    @unittest.skipUnless(LAUNCHER.is_file(), "architecture-docs checkout not present (SOLO_ARCHITECTURE_ROOT)")
    def test_launcher_is_loopback_and_restart_safe(self):
        content = LAUNCHER.read_text(encoding="utf-8")
        for required in (
            'HOST="${VLLM_HOST:-127.0.0.1}"',
            "VLLM_HOST must remain loopback",
            "flock -n 9",
            "STARTUP_TIMEOUT_SEC",
            "MODEL_REVISION",
            ".runpod-model-revision",
            "model.safetensors.index.json",
            "weight_map",
            "--max-num-seqs",
            "--gpu-memory-utilization",
            "VLLM_REQUIRE_BLACKWELL",
            "modelopt_fp4",
            "compute_cap",
        ):
            self.assertIn(required, content)
        self.assertNotIn("--host 0.0.0.0", content)

    def test_tunnel_requires_forward_failure_detection_and_loopback_target(self):
        content = TUNNEL.read_text(encoding="utf-8")
        for required in (
            "ExitOnForwardFailure=yes",
            '127.0.0.1:$REMOTE_PORT',
            "RUNPOD_LOCAL_HOST must remain loopback",
            'args+=( -i "$SSH_KEY" )',
            "ServerAliveInterval=30",
        ):
            self.assertIn(required, content)
        self.assertNotIn("-R ", content)

    def test_smoke_uses_synthetic_prompt_and_optional_gateway_gate(self):
        content = SMOKE.read_text(encoding="utf-8")
        self.assertIn('RUNPOD_VLLM_EXPECTED_MODEL:-${VLLM_SERVED_MODEL_NAME:-qwen3.8-27b-nvfp4}', content)
        self.assertIn("Write a Python function named add", content)
        self.assertIn('"def add" in content', content)
        self.assertIn("RUNPOD_SMOKE_REQUIRE_GATEWAY", content)
        self.assertIn("solo-empire.ai-gateway.v1", content)

    def test_idle_guard_requires_bounded_timeout_and_explicit_provider_hook(self):
        content = IDLE_GUARD.read_text(encoding="utf-8")
        for required in (
            "VLLM_IDLE_TIMEOUT_SEC",
            "VLLM_MAX_RUNTIME_SEC",
            "VLLM_IDLE_STOP_HOOK",
            "provider_stop_hook=not_configured",
            "metrics",
        ):
            self.assertIn(required, content)

    def test_runpod_stop_hook_is_fixed_https_and_never_logs_provider_payload(self):
        content = STOP_HOOK.read_text(encoding="utf-8")
        for required in (
            'RUNPOD_STOP_URL = "https://rest.runpod.io/v1/pods/{pod_id}/stop"',
            "RUNPOD_AUTO_STOP_ENABLED",
            "RUNPOD_POD_ID",
            "RUNPOD_API_KEY",
            "ProxyHandler({})",
            "NoRedirect",
            "response.read(1024 * 1024 + 1)",
        ):
            self.assertIn(required, content)
        self.assertNotIn("RUNPOD_STOP_URL = os.environ", content)
        self.assertNotIn("print(token", content)


if __name__ == "__main__":
    unittest.main()
