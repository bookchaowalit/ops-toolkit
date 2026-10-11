#!/usr/bin/env python3
"""Route bounded media workloads to API or an on-demand GPU worker.

This is a control-plane policy and queue metadata contract. It never starts a
RunPod Pod or stores input files, prompts, or model payloads. Only explicit
`status --live` reads RunPod management metadata. A high-volume job is only queueable when the RunPod adapter is
explicitly enabled, its GPU cost estimate is supplied, and the shared LLM/GPU
budget guard allows the reservation.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from llm_lab_budget import BudgetError, check_budget, load_settings

if os.name == "nt":
    import msvcrt
else:
    import fcntl


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
PROJECT_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "infra" / "platform" / "gpu-worker-catalog.json"
DEFAULT_QUEUE = PROJECT_ROOT / "data" / "runtime" / "gpu-workers" / "jobs.json"
CONTRACT = "solo-empire.gpu-worker-policy.v1"
CATALOG_SCHEMA = "solo-empire.gpu-worker-catalog.v1"
PRIVACY_CLASSES = {"public", "internal", "private", "restricted"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")


class WorkerPolicyError(ValueError):
    """Raised for invalid or unsafe worker policy input."""


from platform_output import configure_utf8_stdio  # noqa: E402


def _safe_id(value: str, field: str) -> str:
    candidate = str(value).strip()
    if not SAFE_ID.fullmatch(candidate):
        raise WorkerPolicyError(f"{field} must be a safe non-empty identifier")
    return candidate


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkerPolicyError(f"{field} must be a positive number") from exc
    if number <= 0 or number != number or number in {float("inf"), float("-inf")}:
        raise WorkerPolicyError(f"{field} must be a positive number")
    return number


def _boolean(value: str | bool | None, field: str, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise WorkerPolicyError(f"{field} must be a boolean")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkerPolicyError(f"worker catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkerPolicyError(f"worker catalog is invalid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA:
        raise WorkerPolicyError("worker catalog schema_version is invalid")
    workloads = payload.get("workloads")
    workers = payload.get("workers")
    pods = payload.get("pods")
    if not isinstance(workloads, dict) or not workloads:
        raise WorkerPolicyError("worker catalog must contain workloads")
    if not isinstance(workers, dict) or not workers:
        raise WorkerPolicyError("worker catalog must contain workers")
    if not isinstance(pods, dict) or not pods:
        raise WorkerPolicyError("worker catalog must contain pods")
    for name, definition in pods.items():
        _safe_id(name, "pod")
        if not isinstance(definition, dict):
            raise WorkerPolicyError(f"pod {name} must be an object")
        for field in ("purpose", "activation", "status", "hardware_guidance"):
            if not isinstance(definition.get(field), str) or not definition[field].strip():
                raise WorkerPolicyError(f"pod {name} is missing {field}")
        languages = definition.get("language_support")
        if not isinstance(languages, list) or not languages or not all(isinstance(item, str) and item.strip() for item in languages):
            raise WorkerPolicyError(f"pod {name} has invalid language_support")
        models = definition.get("models")
        if not isinstance(models, list) or not models:
            raise WorkerPolicyError(f"pod {name} must contain models")
        for index, model in enumerate(models):
            if not isinstance(model, dict):
                raise WorkerPolicyError(f"pod {name} model {index} must be an object")
            if not isinstance(model.get("id"), str) or not model["id"].strip():
                raise WorkerPolicyError(f"pod {name} model {index} is missing id")
    for name, definition in workloads.items():
        _safe_id(name, "workload")
        if not isinstance(definition, dict):
            raise WorkerPolicyError(f"workload {name} must be an object")
        for field in ("unit", "worker", "api_fallback"):
            if not isinstance(definition.get(field), str) or not definition[field].strip():
                raise WorkerPolicyError(f"workload {name} is missing {field}")
        trigger = definition.get("gpu_trigger_units")
        if not isinstance(trigger, (int, float)) or trigger <= 0:
            raise WorkerPolicyError(f"workload {name} has invalid gpu_trigger_units")
        if definition["worker"] not in workers:
            raise WorkerPolicyError(f"workload {name} references unknown worker {definition['worker']}")
        privacy = definition.get("privacy_classes")
        if not isinstance(privacy, list) or not privacy or not set(privacy) <= PRIVACY_CLASSES:
            raise WorkerPolicyError(f"workload {name} has invalid privacy_classes")
        languages = definition.get("language_support")
        if languages is not None and (
            not isinstance(languages, list)
            or not languages
            or not all(isinstance(item, str) and item.strip() for item in languages)
        ):
            raise WorkerPolicyError(f"workload {name} has invalid language_support")
    for name, definition in workers.items():
        _safe_id(name, "worker")
        if not isinstance(definition, dict):
            raise WorkerPolicyError(f"worker {name} must be an object")
        for field in ("pod", "backend", "activation", "status"):
            if not isinstance(definition.get(field), str) or not definition[field].strip():
                raise WorkerPolicyError(f"worker {name} is missing {field}")
        if definition["pod"] not in pods:
            raise WorkerPolicyError(f"worker {name} references unknown pod {definition['pod']}")
        candidates = definition.get("model_candidates")
        if not isinstance(candidates, list) or not candidates or not all(isinstance(item, str) and item.strip() for item in candidates):
            raise WorkerPolicyError(f"worker {name} must contain model_candidates")
        pod_model_ids = {model["id"] for model in pods[definition["pod"]]["models"]}
        if not set(candidates) <= pod_model_ids:
            raise WorkerPolicyError(f"worker {name} references a model not declared by pod {definition['pod']}")
        if not isinstance(definition.get("queue_required"), bool) or not definition["queue_required"]:
            raise WorkerPolicyError(f"worker {name} must require the queue")
    return payload


def build_preflight(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a read-only model/source/hardware readiness report.

    This deliberately does not resolve URLs or call a provider.  It only
    exposes the evidence and gates already recorded in the local catalog so a
    human can review them before enabling a worker.
    """
    catalog = catalog or load_catalog()
    rows: list[dict[str, Any]] = []
    for pod_id, pod in catalog["pods"].items():
        for model in pod["models"]:
            source_url = model.get("model_card_url") or model.get("source_url")
            status = str(model.get("status", "unverified"))
            blockers: list[str] = []
            if not source_url:
                blockers.append("model_source_missing")
            if "requires" in status or status.startswith("candidate_"):
                blockers.append("review_or_benchmark_required")
            if status.startswith("blocked_") or str(pod.get("status", "")).startswith("blocked_"):
                blockers.append("hardware_or_pod_gate_blocked")
            if model.get("license_gate"):
                blockers.append("license_gate_required")
            if model.get("access_gate"):
                blockers.append("access_gate_required")
            rows.append({
                "pod": pod_id,
                "pod_status": pod.get("status"),
                "model_id": model.get("id"),
                "model_id_status": model.get("model_id_status", "unverified"),
                "source_url": source_url,
                "status": status,
                "hardware_gate": model.get("hardware_gate"),
                "license": model.get("license"),
                "license_gate": model.get("license_gate"),
                "access_gate": model.get("access_gate"),
                "launch_ready": not blockers,
                "blockers": blockers,
            })
    return {
        "contract": "solo-empire.gpu-worker-preflight.v1",
        "provider_calls": False,
        "catalog_updated": catalog.get("updated"),
        "launch_policy": catalog.get("model_preflight", {}),
        "launch_ready": all(row["launch_ready"] for row in rows),
        "models": rows,
    }


def _runpod_enabled(env: dict[str, str]) -> bool:
    return _boolean(env.get("GPU_WORKER_RUNPOD_ENABLED"), "GPU_WORKER_RUNPOD_ENABLED", False)


def _budget_gate(estimate_usd: float, env: dict[str, str]) -> dict[str, Any]:
    settings = load_settings(env)
    result = check_budget(settings, estimate_usd=estimate_usd)
    return {
        "decision": result["decision"],
        "reason": result["reason"],
        "requested_estimate_usd": result["requested_estimate_usd"],
        "remaining_usd": result.get("remaining_usd"),
        "total_projected_usd": result.get("total_projected_usd"),
        "effective_budget_usd": result.get("effective_budget_usd"),
    }


def build_route(
    *,
    workload: str,
    units: float,
    privacy_class: str = "internal",
    estimated_gpu_hours: float | None = None,
    gpu_hourly_usd: float | None = None,
    runpod_enabled: bool | None = None,
    catalog: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic route without writing or calling a provider."""
    catalog = catalog or load_catalog()
    env = dict(os.environ if env is None else env)
    workload = _safe_id(workload, "workload")
    if workload not in catalog["workloads"]:
        raise WorkerPolicyError(f"unknown workload: {workload}")
    units = _positive_number(units, "units")
    if privacy_class not in PRIVACY_CLASSES:
        raise WorkerPolicyError(f"privacy_class must be one of: {', '.join(sorted(PRIVACY_CLASSES))}")
    definition = catalog["workloads"][workload]
    worker_id = definition["worker"]
    worker = catalog["workers"][worker_id]
    pod_id = worker["pod"]
    pod = catalog["pods"][pod_id]
    trigger = float(definition["gpu_trigger_units"])
    high_volume = units >= trigger
    base: dict[str, Any] = {
        "contract": CONTRACT,
        "workload": workload,
        "units": units,
        "unit": definition["unit"],
        "gpu_trigger_units": trigger,
        "privacy_class": privacy_class,
        "worker": worker_id,
        "pod": pod_id,
        "pod_status": pod["status"],
        "pod_launch_gate": pod.get("launch_gate"),
        "language_support": definition.get("language_support", pod["language_support"]),
        "model_candidates": worker["model_candidates"],
        "worker_status": worker["status"],
        "queue_required": bool(worker["queue_required"]),
        "estimated_gpu_hours": estimated_gpu_hours,
        "gpu_hourly_usd": gpu_hourly_usd,
        "external_calls": False,
        "write_attempted": False,
    }

    if not high_volume:
        fallback_ready = bool(definition.get("fallback_ready", False))
        base.update({
            "lane": definition["api_fallback"],
            "execution_status": "ready" if fallback_ready else "fallback_required",
            "reason": "below_gpu_volume_trigger" if fallback_ready else "fallback_adapter_not_configured",
            "queue_required": False,
            "cost_gate": {"decision": "not_applicable", "reason": "api_or_cpu_fallback"},
        })
        return base

    if privacy_class not in set(definition["privacy_classes"]):
        base.update({
            "lane": "local",
            "execution_status": "blocked",
            "reason": "privacy_class_requires_approved_private_worker",
            "cost_gate": {"decision": "not_run", "reason": "privacy_boundary"},
        })
        return base

    if str(pod.get("status", "")).startswith("blocked_"):
        base.update({
            "lane": catalog["runpod_lane"],
            "execution_status": "blocked",
            "reason": "pod_launch_gate_blocked",
            "cost_gate": {"decision": "not_run", "reason": "pod_hardware_or_compatibility_gate"},
        })
        return base

    enabled = _runpod_enabled(env) if runpod_enabled is None else bool(runpod_enabled)
    if not enabled:
        base.update({
            "lane": catalog["runpod_lane"],
            "execution_status": "blocked",
            "reason": "runpod_adapter_disabled",
            "cost_gate": {"decision": "not_run", "reason": "external_adapter_not_enabled"},
        })
        return base

    if estimated_gpu_hours is None or gpu_hourly_usd is None:
        base.update({
            "lane": catalog["runpod_lane"],
            "execution_status": "blocked",
            "reason": "gpu_cost_estimate_required",
            "cost_gate": {"decision": "not_run", "reason": "supply_hours_and_rate"},
        })
        return base

    hours = _positive_number(estimated_gpu_hours, "estimated_gpu_hours")
    rate = _positive_number(gpu_hourly_usd, "gpu_hourly_usd")
    estimate = round(hours * rate, 8)
    cost_gate = _budget_gate(estimate, env)
    base["estimated_gpu_hours"] = hours
    base["gpu_hourly_usd"] = rate
    base["estimated_cost_usd"] = estimate
    base["cost_gate"] = cost_gate
    base["lane"] = catalog["runpod_lane"]
    if cost_gate["decision"] != "allow":
        base.update({"execution_status": "blocked", "reason": f"gpu_budget_{cost_gate['reason']}"})
    else:
        base.update({"execution_status": "ready", "reason": "high_volume_gpu_worker"})
    return base


def _queue_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get("GPU_WORKER_QUEUE_PATH") or "").strip()
    path = Path(raw).expanduser() if raw else DEFAULT_QUEUE
    return path if path.is_absolute() else PROJECT_ROOT / path


@contextmanager
def _queue_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{path}.lock")
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            # msvcrt locks byte ranges, so make sure the first byte exists.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"contract": CONTRACT, "updated_at": _now(), "jobs": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot read worker queue: {path}") from exc
    if not isinstance(payload, dict) or payload.get("contract") != CONTRACT or not isinstance(payload.get("jobs"), dict):
        raise WorkerPolicyError("worker queue state is invalid")
    return payload


def _write_queue(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["contract"] = CONTRACT
    payload["updated_at"] = _now()
    fd, temp_name = tempfile.mkstemp(prefix="jobs.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def enqueue_job(
    *,
    job_id: str | None,
    idempotency_key: str,
    route: dict[str, Any],
    apply: bool,
    queue_path: Path,
) -> dict[str, Any]:
    """Preview or enqueue metadata after the route and cost gate pass."""
    key = _safe_id(idempotency_key, "idempotency_key")
    selected_id = _safe_id(job_id or f"gpu-{uuid.uuid4()}", "job_id")
    result = {"contract": CONTRACT, "queued": False, "write_attempted": False, "job_id": selected_id, "route": route}
    if route.get("lane") != "runpod" or route.get("execution_status") != "ready":
        result["reason"] = "route_not_queueable"
        return result
    if not apply:
        result["reason"] = "preview_only"
        return result
    result["write_attempted"] = True
    with _queue_lock(queue_path):
        payload = _read_queue(queue_path)
        jobs = payload["jobs"]
        for existing_id, existing in jobs.items():
            if existing.get("idempotency_key") == key:
                result.update({"queued": False, "reason": "already_queued", "job_id": existing_id})
                return result
        if selected_id in jobs:
            result["reason"] = "job_id_already_exists"
            return result
        jobs[selected_id] = {
            "job_id": selected_id,
            "idempotency_key": key,
            "workload": route["workload"],
            "units": route["units"],
            "unit": route["unit"],
            "privacy_class": route["privacy_class"],
            "worker": route["worker"],
            "pod": route.get("pod"),
            "status": "queued",
            "attempts": 0,
            "estimated_gpu_hours": route.get("estimated_gpu_hours"),
            "gpu_hourly_usd": route.get("gpu_hourly_usd"),
            "estimated_cost_usd": route.get("estimated_cost_usd"),
            "created_at": _now(),
        }
        _write_queue(queue_path, payload)
    result.update({"queued": True, "reason": "queued_metadata_only"})
    return result


def queue_status(queue_path: Path) -> dict[str, Any]:
    payload = _read_queue(queue_path)
    counts: dict[str, int] = {}
    for job in payload["jobs"].values():
        status = str(job.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return {"contract": CONTRACT, "queue_path": str(queue_path), "counts": counts, "jobs": list(payload["jobs"].values())}


def build_worker_status(
    queue_path: Path, *, live: bool = False,
    provider_collector: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = queue_status(queue_path)
    if live:
        from cloud_read_adapters import collect_provider

        result["cloud"] = (provider_collector or collect_provider)("runpod", live=True)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded specialized GPU worker policy")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog", help="show the worker catalog")
    catalog.add_argument("--json", action="store_true")
    preflight = sub.add_parser("preflight", help="show read-only model and hardware readiness")
    preflight.add_argument("--json", action="store_true")
    route = sub.add_parser("route", help="preview API/GPU routing")
    route.add_argument("--workload", required=True)
    route.add_argument("--units", required=True, type=float)
    route.add_argument("--privacy-class", choices=sorted(PRIVACY_CLASSES), default="internal")
    route.add_argument("--estimated-gpu-hours", type=float)
    route.add_argument("--gpu-hourly-usd", type=float)
    route.add_argument("--runpod-enabled", action="store_true")
    route.add_argument("--json", action="store_true")
    enqueue = sub.add_parser("enqueue", help="preview or enqueue GPU metadata")
    enqueue.add_argument("--job-id")
    enqueue.add_argument("--idempotency-key", required=True)
    enqueue.add_argument("--workload", required=True)
    enqueue.add_argument("--units", required=True, type=float)
    enqueue.add_argument("--privacy-class", choices=sorted(PRIVACY_CLASSES), default="internal")
    enqueue.add_argument("--estimated-gpu-hours", required=True, type=float)
    enqueue.add_argument("--gpu-hourly-usd", required=True, type=float)
    enqueue.add_argument("--runpod-enabled", action="store_true")
    enqueue.add_argument("--apply", action="store_true", help="write metadata to the local queue")
    enqueue.add_argument("--json", action="store_true")
    status = sub.add_parser("status", help="show local queue metadata")
    status.add_argument("--json", action="store_true")
    status_mode = status.add_mutually_exclusive_group()
    status_mode.add_argument("--live", action="store_true", help="also read RunPod metadata; never starts or stops pods")
    status_mode.add_argument("--offline", action="store_true", help="local queue only (default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    try:
        args = _parser().parse_args(argv)
        catalog = load_catalog()
        if args.command == "catalog":
            result = catalog
        elif args.command == "preflight":
            result = build_preflight(catalog)
        elif args.command == "route":
            result = build_route(
                workload=args.workload,
                units=args.units,
                privacy_class=args.privacy_class,
                estimated_gpu_hours=args.estimated_gpu_hours,
                gpu_hourly_usd=args.gpu_hourly_usd,
                runpod_enabled=args.runpod_enabled or None,
                catalog=catalog,
            )
        elif args.command == "enqueue":
            route = build_route(
                workload=args.workload,
                units=args.units,
                privacy_class=args.privacy_class,
                estimated_gpu_hours=args.estimated_gpu_hours,
                gpu_hourly_usd=args.gpu_hourly_usd,
                runpod_enabled=args.runpod_enabled or None,
                catalog=catalog,
            )
            result = enqueue_job(
                job_id=args.job_id,
                idempotency_key=args.idempotency_key,
                route=route,
                apply=args.apply,
                queue_path=_queue_path(),
            )
        else:
            result = build_worker_status(_queue_path(), live=args.live)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True))
        if args.command == "enqueue":
            route = result.get("route", {})
            if route.get("execution_status") == "blocked":
                return 3
            if not result.get("queued") and result.get("reason") not in {"already_queued"}:
                return 3
        if args.command in {"route", "enqueue"} and result.get("execution_status") == "blocked":
            return 3
        return 0
    except (WorkerPolicyError, BudgetError) as exc:
        print(json.dumps({"contract": CONTRACT, "decision": "deny", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
