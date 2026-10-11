#!/usr/bin/env python3
"""Bounded budget and session ledger for the Solo Empire LLM lab.

The lab may use several models and providers, so a provider dashboard alone is
not a sufficient stop condition. This script combines fixed human subscription
and storage commitments, API cost telemetry from SQLite, and an append-only GPU
session ledger. It never starts or stops a provider by itself; callers can
use the decision as a preflight and the idle guard can invoke an explicitly
configured provider hook.

All records are operational metadata only. Prompts, responses, credentials,
and provider payloads are intentionally not accepted or written.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import re
import shlex
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
PROJECT_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "infra" / "platform" / "llm-lab-models.json"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "generated" / "llm-lab" / "budget-events.jsonl"
CONTRACT_VERSION = "solo-empire.llm-lab-budget.v1"
SAFE_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:/-")
PILOT_PREFLIGHT_CONTRACT = "solo-empire.llm-lab-pilot-preflight.v1"
MAX_ON_DEMAND_SESSION_HOURS = 12.0
RUNPOD_POD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
PILOT_SCRIPT_PATHS = {
    "budget_guard": SCRIPT_DIR / "llm_lab_budget.py",
    "vllm_smoke": SCRIPT_DIR / "runpod_vllm_smoke.sh",
    "vllm_idle_guard": SCRIPT_DIR / "runpod_vllm_idle_guard.sh",
    "runpod_stop_hook": SCRIPT_DIR / "runpod_stop_pod_hook.py",
}


from platform_output import configure_utf8_stdio  # noqa: E402


class BudgetError(ValueError):
    """Raised when a budget or ledger contract is invalid."""


@dataclass(frozen=True)
class Settings:
    monthly_budget_usd: float | None
    hard_stop_ratio: float
    allow_unmetered_api: bool
    ledger_path: Path
    catalog_path: Path
    subscription_monthly_usd: float = 0.0
    storage_monthly_usd: float = 0.0
    max_session_hours: float = 12.0


def _safe_id(value: str, field: str) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > 160 or any(char not in SAFE_ID for char in candidate):
        raise BudgetError(f"{field} must be a safe non-empty identifier")
    return candidate


def _finite_non_negative(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise BudgetError(f"{field} must be a finite non-negative number")
    return number


def _positive(value: Any, field: str) -> float:
    number = _finite_non_negative(value, field)
    if number <= 0:
        raise BudgetError(f"{field} must be greater than zero")
    return number


def _bool_env(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise BudgetError("LLM_LAB_ALLOW_UNMETERED_API must be a boolean")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    if env is None:
        env = os.environ
    raw_budget = (env.get("LLM_LAB_MONTHLY_BUDGET_USD") or env.get("AI_GATEWAY_MONTHLY_BUDGET_USD") or "").strip()
    budget = None if not raw_budget else _finite_non_negative(raw_budget, "LLM_LAB_MONTHLY_BUDGET_USD")
    raw_subscription = (env.get("AI_SUBSCRIPTION_MONTHLY_USD") or env.get("LLM_LAB_SUBSCRIPTION_MONTHLY_USD") or "0").strip()
    subscription = _finite_non_negative(raw_subscription, "AI_SUBSCRIPTION_MONTHLY_USD")
    raw_storage = (env.get("AI_STORAGE_MONTHLY_USD") or env.get("LLM_LAB_STORAGE_MONTHLY_USD") or "0").strip()
    storage = _finite_non_negative(raw_storage, "AI_STORAGE_MONTHLY_USD")
    max_session_hours = _positive((env.get("LLM_LAB_MAX_SESSION_HOURS") or "12").strip(), "LLM_LAB_MAX_SESSION_HOURS")
    if max_session_hours > MAX_ON_DEMAND_SESSION_HOURS:
        raise BudgetError(
            f"LLM_LAB_MAX_SESSION_HOURS must be at most {MAX_ON_DEMAND_SESSION_HOURS:g} for the on-demand GPU lane"
        )
    raw_ratio = (env.get("LLM_LAB_HARD_STOP_RATIO") or "1").strip()
    ratio = _finite_non_negative(raw_ratio, "LLM_LAB_HARD_STOP_RATIO")
    if ratio <= 0 or ratio > 1:
        raise BudgetError("LLM_LAB_HARD_STOP_RATIO must be greater than zero and at most one")
    def configured_path(raw: str | None, default: Path) -> Path:
        path = Path(raw).expanduser() if raw else default
        return path if path.is_absolute() else PROJECT_ROOT / path

    return Settings(
        monthly_budget_usd=budget,
        hard_stop_ratio=ratio,
        allow_unmetered_api=_bool_env(env.get("LLM_LAB_ALLOW_UNMETERED_API")),
        ledger_path=configured_path(env.get("LLM_LAB_LEDGER_PATH"), DEFAULT_LEDGER_PATH),
        catalog_path=configured_path(env.get("LLM_LAB_CATALOG_PATH"), DEFAULT_CATALOG_PATH),
        subscription_monthly_usd=subscription,
        storage_monthly_usd=storage,
        max_session_hours=max_session_hours,
    )


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BudgetError(f"model catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BudgetError(f"model catalog is invalid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "solo-empire.llm-lab-models.v1":
        raise BudgetError("model catalog schema_version is invalid")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise BudgetError("model catalog must contain profiles")
    for name, profile in profiles.items():
        _safe_id(str(name), "profile")
        if not isinstance(profile, dict):
            raise BudgetError(f"model profile {name} must be an object")
        for field in ("model_id", "served_model_name", "backend", "deployment_mode", "cost_class"):
            if not isinstance(profile.get(field), str) or not profile[field].strip():
                raise BudgetError(f"model profile {name} is missing {field}")
        activation = profile.get("activation", "on_demand")
        if activation not in {"on_demand", "burst_only"}:
            raise BudgetError(f"model profile {name} must use on_demand or burst_only activation")
    return payload


def profile_environment(profile_name: str, catalog: dict[str, Any]) -> dict[str, str]:
    profiles = catalog["profiles"]
    if profile_name not in profiles:
        raise BudgetError(f"unknown model profile: {profile_name}")
    profile = profiles[profile_name]
    served = str(profile["served_model_name"])
    gateway_profiles = profile.get("gateway_profiles", [])
    if not isinstance(gateway_profiles, list):
        raise BudgetError(f"model profile {profile_name} gateway_profiles must be a list")
    result = {
        "VLLM_MODEL_ID": str(profile["model_id"]),
        "VLLM_MODEL_DIR": f"/workspace/llm-lab/models/{profile_name}",
        "VLLM_SERVED_MODEL_NAME": served,
        "VLLM_MAX_MODEL_LEN": str(profile.get("context_tokens", 32768)),
    }
    # The gateway requires every slot to have a model mapping. Profiles still
    # declare their intended slots for routing documentation, while the safe
    # fallback for an unlisted slot is the same served model.
    for slot in ("fast", "balanced", "quality"):
        result[f"AI_GATEWAY_MODEL_{slot.upper()}"] = served
    return result


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BudgetError("ledger timestamp is invalid") from exc
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{path}.lock")
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
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


def read_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [type(exc).__name__]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("event") not in {"session_started", "session_stopped"}:
                raise BudgetError("unknown ledger event")
            events.append(event)
        except (json.JSONDecodeError, BudgetError):
            errors.append(f"line_{line_number}")
    return events, errors


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _month_window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
    return start, next_month


def _overlap_seconds(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> float:
    left = max(start, window_start)
    right = min(end, window_end)
    return max(0.0, (right - left).total_seconds())


def _sessions(events: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    active: dict[str, dict[str, Any]] = {}
    closed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for event in events:
        session_id = str(event.get("session_id", ""))
        try:
            _safe_id(session_id, "session_id")
        except BudgetError:
            errors.append("unsafe_session_id")
            continue
        if event.get("event") == "session_started":
            if session_id in active or session_id in closed:
                errors.append("duplicate_session_start")
                continue
            try:
                event["started"] = _parse_time(str(event["started_at"]))
                event["rate"] = _positive(event["gpu_hourly_usd"], "gpu_hourly_usd")
                event["gpu_count"] = int(event["gpu_count"])
                event["max_hours"] = _positive(event["max_hours"], "max_hours")
                if event["gpu_count"] < 1 or event["gpu_count"] > 64:
                    raise BudgetError("gpu_count is out of bounds")
            except (KeyError, TypeError, ValueError, BudgetError):
                errors.append("invalid_session_start")
                continue
            active[session_id] = event
        else:
            if session_id in closed:
                errors.append("duplicate_session_stop")
                continue
            if session_id not in active:
                errors.append("orphan_session_stop")
                continue
            try:
                event["stopped"] = _parse_time(str(event["stopped_at"]))
                event["actual_cost"] = _finite_non_negative(event["actual_cost_usd"], "actual_cost_usd")
            except (KeyError, BudgetError):
                errors.append("invalid_session_stop")
                continue
            started = active.pop(session_id)
            if event["stopped"] < started["started"]:
                errors.append("session_stop_before_start")
                continue
            closed[session_id] = {"start": started, "stop": event}
    return active, closed, errors


def _api_cost() -> dict[str, Any]:
    """Hosted API spend is owned by solo-ai-gateway; this ledger covers GPU, subscription, and storage only."""
    return {
        "status": "external",
        "cost_usd": 0.0,
        "unknown_cost_events": 0,
        "note": "API spend lives in solo-ai-gateway",
    }


def build_status(settings: Settings, now: datetime | None = None, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start, window_end = _month_window(now)
    loaded_events, ledger_errors = read_events(settings.ledger_path) if events is None else (events, [])
    active, closed, session_errors = _sessions(loaded_events)
    gpu_cost = 0.0
    reserved_cost = 0.0
    active_rows: list[dict[str, Any]] = []
    for session_id, entry in active.items():
        started = entry["started"]
        rate = entry["rate"]
        count = entry["gpu_count"]
        max_seconds = entry["max_hours"] * 3600
        elapsed_all = max(0.0, (now - started).total_seconds())
        overlap = _overlap_seconds(started, now, window_start, window_end)
        accrued = overlap * rate * count / 3600
        remaining = max(0.0, max_seconds - elapsed_all) * rate * count / 3600
        gpu_cost += accrued
        reserved_cost += remaining
        active_rows.append({
            "session_id": session_id,
            "profile": entry.get("profile"),
            "started_at": entry.get("started_at"),
            "gpu_count": count,
            "accrued_cost_usd": round(accrued, 8),
            "remaining_reservation_usd": round(remaining, 8),
            "max_hours": entry["max_hours"],
        })
    for entry in closed.values():
        started = entry["start"]["started"]
        stopped = entry["stop"]["stopped"]
        duration = max(0.0, (stopped - started).total_seconds())
        overlap = _overlap_seconds(started, stopped, window_start, window_end)
        actual = entry["stop"]["actual_cost"]
        gpu_cost += (actual if window_start <= stopped < window_end else 0) if duration == 0 else actual * overlap / duration
    gpu_cost = round(gpu_cost, 8)
    reserved_cost = round(reserved_cost, 8)
    api = _api_cost()
    api_cost = float(api["cost_usd"])
    total_spent = round(gpu_cost + api_cost, 8)
    projected = round(total_spent + reserved_cost, 8)
    effective_budget = None if settings.monthly_budget_usd is None else round(settings.monthly_budget_usd * settings.hard_stop_ratio, 8)
    subscription_committed = round(settings.subscription_monthly_usd, 8)
    storage_committed = round(settings.storage_monthly_usd, 8)
    committed = round(subscription_committed + storage_committed, 8)
    total_projected = round(projected + committed, 8)
    reasons: list[str] = []
    if any(now >= entry['started'] + timedelta(hours=entry['max_hours']) for entry in active.values()):
        reasons.append('gpu_session_overdue')
    if effective_budget is None:
        decision = "deny"
        reasons.append("monthly_budget_not_configured")
    if ledger_errors or session_errors:
        decision = "deny"
        reasons.append("gpu_ledger_invalid")
    if effective_budget is not None and committed > effective_budget + 1e-9:
        decision = "deny"
        if subscription_committed > effective_budget + 1e-9:
            reasons.append("subscription_exceeds_monthly_budget")
        elif storage_committed > effective_budget + 1e-9:
            reasons.append("storage_exceeds_monthly_budget")
        else:
            reasons.append("fixed_commitments_exceed_monthly_budget")
    if effective_budget is not None and total_projected > effective_budget + 1e-9:
        decision = "deny"
        reasons.append("monthly_budget_exceeded")
    decision = "deny" if reasons else "allow"
    return {
        "contract": CONTRACT_VERSION,
        "period": {"month": window_start.strftime("%Y-%m"), "starts_at": _iso(window_start), "ends_at": _iso(window_end)},
        "decision": decision,
        "reason": reasons[0] if reasons else "within_budget",
        "reasons": reasons,
        "budget_usd": settings.monthly_budget_usd,
        "hard_stop_ratio": settings.hard_stop_ratio,
        "effective_budget_usd": effective_budget,
        "spent_usd": total_spent,
        "reserved_usd": reserved_cost,
        "projected_usd": projected,
        "committed_usd": committed,
        "subscription_monthly_usd": subscription_committed,
        "storage_monthly_usd": storage_committed,
        "total_projected_usd": total_projected,
        "remaining_usd": None if effective_budget is None else round(effective_budget - total_projected, 8),
        "api": api,
        "gpu": {"closed_cost_usd": gpu_cost, "active_sessions": active_rows},
        "ledger": {"path_configured": True, "errors": len(ledger_errors) + len(session_errors)},
    }


def check_budget(settings: Settings, estimate_usd: float = 0.0, now: datetime | None = None) -> dict[str, Any]:
    estimate = _finite_non_negative(estimate_usd, "estimate_usd")
    result = build_status(settings, now=now)
    result["requested_estimate_usd"] = round(estimate, 8)
    if result["decision"] == "allow" and result["effective_budget_usd"] is not None:
        if result["total_projected_usd"] + estimate > result["effective_budget_usd"] + 1e-9:
            result["decision"] = "deny"
            result["reason"] = "request_would_exceed_monthly_budget"
            result["reasons"] = [result["reason"]]
    return result


def _env_estimate(env: dict[str, str], name: str, default: float = 0.0) -> tuple[float, bool]:
    """Read an optional non-negative estimate without exposing environment values."""
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default, False
    return _finite_non_negative(raw, name), True


def _check(name: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": status, "reason": reason}
    result.update(details)
    return result


def _pilot_stop_hook_check(env: dict[str, str], *, required: bool) -> dict[str, Any]:
    """Check the local stop-hook contract without calling the provider."""
    hook_raw = env.get("VLLM_IDLE_STOP_HOOK", "").strip()
    if not hook_raw:
        return _check(
            "provider_stop_hook",
            "fail" if required else "manual",
            "provider_stop_hook_not_configured",
            required=required,
            configured=False,
        )
    hook = Path(hook_raw)
    path_valid = hook.is_absolute() and hook.is_file() and os.access(hook, os.X_OK)
    enabled = env.get("RUNPOD_AUTO_STOP_ENABLED", "").strip().lower() == "true"
    pod_id_valid = bool(RUNPOD_POD_ID_RE.fullmatch(env.get("RUNPOD_POD_ID", "").strip()))
    key_present = bool(env.get("RUNPOD_API_KEY", "").strip())
    valid = path_valid and enabled and pod_id_valid and key_present
    return _check(
        "provider_stop_hook",
        "pass" if valid else "fail",
        "provider_stop_hook_contract_ready" if valid else "provider_stop_hook_configuration_incomplete",
        required=required,
        configured=True,
        executable=path_valid,
        auto_stop_enabled=enabled,
        pod_id_present=bool(env.get("RUNPOD_POD_ID", "").strip()),
        pod_id_valid=pod_id_valid,
        api_key_present=key_present,
    )


def _pilot_launcher_check(env: dict[str, str]) -> dict[str, Any]:
    launcher_raw = env.get("VLLM_LAUNCHER", "").strip()
    if not launcher_raw:
        return _check("local_process_stop", "manual", "vllm_launcher_not_configured", configured=False)
    launcher = Path(launcher_raw)
    valid = launcher.is_file() and os.access(launcher, os.X_OK)
    return _check(
        "local_process_stop",
        "pass" if valid else "fail",
        "vllm_launcher_ready" if valid else "vllm_launcher_invalid",
        configured=True,
        executable=valid,
    )


def _pilot_idle_guard_check(env: dict[str, str], duration_minutes: float) -> dict[str, Any]:
    """Validate guard timings and keep the hard stop inside the reservation."""
    idle_raw = env.get("VLLM_IDLE_TIMEOUT_SEC", "").strip()
    max_raw = env.get("VLLM_MAX_RUNTIME_SEC", "").strip()
    poll_raw = env.get("VLLM_IDLE_POLL_SEC", "").strip()
    if not idle_raw and not max_raw and not poll_raw:
        return _check("idle_guard", "manual", "idle_guard_not_configured", configured=False)
    if not idle_raw or not max_raw or not poll_raw:
        return _check("idle_guard", "fail", "idle_guard_configuration_incomplete", configured=True)
    if not all(value.isdigit() for value in (idle_raw, max_raw, poll_raw)):
        return _check("idle_guard", "fail", "idle_guard_values_must_be_integers", configured=True)
    idle_timeout = int(idle_raw)
    max_runtime = int(max_raw)
    poll = int(poll_raw)
    if idle_timeout <= 0 or max_runtime <= 0 or poll <= 0:
        return _check("idle_guard", "fail", "idle_guard_values_must_be_positive", configured=True)
    reservation_seconds = duration_minutes * 60
    if max_runtime > reservation_seconds + 1e-9:
        return _check(
            "idle_guard",
            "fail",
            "idle_guard_exceeds_session_reservation",
            configured=True,
            idle_timeout_sec=idle_timeout,
            max_runtime_sec=max_runtime,
            poll_sec=poll,
        )
    return _check(
        "idle_guard",
        "pass",
        "idle_guard_bounded",
        configured=True,
        idle_timeout_sec=idle_timeout,
        max_runtime_sec=max_runtime,
        poll_sec=poll,
    )


def build_pilot_preflight(
    *,
    profile_name: str | None = None,
    env: dict[str, str] | None = None,
    monthly_budget_usd: float | None = None,
    gpu_hourly_usd: float | None = None,
    gpu_count: int = 1,
    duration_minutes: float = 30.0,
    api_estimate_usd: float | None = None,
    storage_estimate_usd: float | None = None,
    require_provider_stop_hook: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a read-only, bounded pilot launch report.

    This function reads the catalog, ledger, and local
    script contracts. It never reserves a session, contacts a provider, or
    writes an evidence file. The operator must still run ``session-start``
    after reviewing this report so the reservation is serialized with other
    GPU CLI sessions.
    """
    source_env = dict(os.environ if env is None else env)
    settings = load_settings(source_env)
    if monthly_budget_usd is not None:
        settings = Settings(
            monthly_budget_usd=_finite_non_negative(monthly_budget_usd, "monthly_budget_usd"),
            hard_stop_ratio=settings.hard_stop_ratio,
            allow_unmetered_api=settings.allow_unmetered_api,
            ledger_path=settings.ledger_path,
            catalog_path=settings.catalog_path,
            subscription_monthly_usd=settings.subscription_monthly_usd,
            storage_monthly_usd=settings.storage_monthly_usd,
            max_session_hours=settings.max_session_hours,
        )
    catalog = load_catalog(settings.catalog_path)
    selected_profile = profile_name or source_env.get("LLM_LAB_DEFAULT_PROFILE") or catalog.get("default_profile") or "compact"
    if selected_profile not in catalog["profiles"]:
        raise BudgetError(f"unknown model profile: {selected_profile}")
    definition = catalog["profiles"][selected_profile]

    if gpu_hourly_usd is None:
        raw_rate = source_env.get("LLM_LAB_GPU_HOURLY_USD")
        rate = None if raw_rate is None or raw_rate.strip() == "" else _positive(raw_rate, "gpu_hourly_usd")
    else:
        rate = _positive(gpu_hourly_usd, "gpu_hourly_usd")
    if not isinstance(gpu_count, int) or gpu_count < 1 or gpu_count > 64:
        raise BudgetError("gpu_count must be between 1 and 64")
    duration = _positive(duration_minutes, "duration_minutes")
    if duration > 744 * 60:
        raise BudgetError("duration_minutes must be at most 44640")
    if api_estimate_usd is None:
        api_estimate, api_explicit = _env_estimate(source_env, "LLM_LAB_PILOT_API_ESTIMATE_USD")
    else:
        api_estimate, api_explicit = _finite_non_negative(api_estimate_usd, "api_estimate_usd"), True
    if storage_estimate_usd is None:
        storage_estimate, storage_explicit = _env_estimate(source_env, "LLM_LAB_PILOT_STORAGE_ESTIMATE_USD")
    else:
        storage_estimate, storage_explicit = _finite_non_negative(storage_estimate_usd, "storage_estimate_usd"), True

    budget_status = build_status(settings, now=now)
    gpu_cap = None if rate is None else round(rate * gpu_count * duration / 60, 8)
    pilot_cap = None if gpu_cap is None else round(gpu_cap + api_estimate + storage_estimate, 8)
    current_projected = budget_status.get("total_projected_usd")
    effective_budget = budget_status.get("effective_budget_usd")
    projected_after = None
    headroom_after = None
    if isinstance(current_projected, (int, float)) and pilot_cap is not None:
        projected_after = round(float(current_projected) + pilot_cap, 8)
    if isinstance(effective_budget, (int, float)) and projected_after is not None:
        headroom_after = round(float(effective_budget) - projected_after, 8)

    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "monthly_budget",
        "pass" if settings.monthly_budget_usd is not None else "fail",
        "monthly_budget_configured" if settings.monthly_budget_usd is not None else "monthly_budget_not_configured",
    ))
    checks.append(_check(
        "budget_signal",
        "pass" if budget_status["decision"] == "allow" else "fail",
        "budget_status_allow" if budget_status["decision"] == "allow" else str(budget_status.get("reason", "budget_unavailable")),
    ))
    checks.append(_check(
        "provider_rate_card",
        "pass" if rate is not None else "fail",
        "gpu_rate_supplied" if rate is not None else "gpu_hourly_price_required",
    ))
    checks.append(_check(
        "session_duration_cap",
        "pass" if duration / 60 <= settings.max_session_hours + 1e-9 else "fail",
        "session_duration_within_cap" if duration / 60 <= settings.max_session_hours + 1e-9 else "session_duration_exceeds_cap",
        max_session_hours=settings.max_session_hours,
    ))
    cap_ok = projected_after is not None and effective_budget is not None and projected_after <= float(effective_budget) + 1e-9
    checks.append(_check(
        "pilot_cost_cap",
        "pass" if cap_ok else "fail",
        "pilot_cap_within_effective_budget" if cap_ok else "pilot_cap_exceeds_or_cannot_be_compared",
    ))
    missing_scripts = [name for name, path in PILOT_SCRIPT_PATHS.items() if not path.is_file() or not os.access(path, os.X_OK)]
    checks.append(_check(
        "local_script_contracts",
        "pass" if not missing_scripts else "fail",
        "scripts_present_and_executable" if not missing_scripts else "script_contract_missing_or_not_executable",
        missing=missing_scripts,
    ))
    checks.append(_pilot_launcher_check(source_env))
    checks.append(_pilot_idle_guard_check(source_env, duration))
    checks.append(_pilot_stop_hook_check(source_env, required=require_provider_stop_hook))
    checks.append(_check(
        "hardware_fit",
        "manual",
        "verify_checkpoint_quantization_vram_and_gpu_count_on_provider",
    ))
    checks.append(_check(
        "storage_rate",
        "pass" if storage_explicit else "manual",
        "storage_estimate_supplied" if storage_explicit else "confirm_provider_storage_fee_before_launch",
    ))
    checks.append(_check(
        "api_rate_card",
        "pass" if api_explicit else "manual",
        "api_estimate_supplied" if api_explicit else "confirm_external_api_cost_or_keep_local_vllm",
    ))

    blockers = [item["reason"] for item in checks if item["status"] == "fail"]
    review_required = any(item["status"] == "manual" for item in checks)
    report_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if blockers:
        next_steps = ["แก้รายการที่ status=fail ก่อนเปิด Pod หรือจอง GPU session"]
    else:
        next_steps = [
            "ตรวจรายการ status=manual และราคา GPU/Storage ปัจจุบันใน provider",
            "รัน session-start ด้วย profile, gpu count, rate และ max-hours ชุดเดียวกับรายงานนี้",
            "เริ่ม vLLM แล้วรัน synthetic coding smoke จากเครื่อง loopback",
            "หลังยืนยัน Pod หยุดจริง ให้รัน session-stop พร้อมต้นทุนจริง",
        ]
    return {
        "contract": PILOT_PREFLIGHT_CONTRACT,
        "checked_at": _iso(report_now),
        "read_only": True,
        "provider_call_attempted": False,
        "write_attempted": False,
        "decision": "deny" if blockers else "allow",
        "review_required": review_required,
        "blockers": blockers,
        "profile": {
            "name": selected_profile,
            "model_id": definition["model_id"],
            "served_model_name": definition["served_model_name"],
            "backend": definition["backend"],
            "deployment_mode": definition["deployment_mode"],
            "service_lane": definition.get("service_lane", "runpod"),
            "activation": definition.get("activation", "on_demand"),
            "workloads": definition.get("workloads", []),
            "context_tokens": definition.get("context_tokens"),
            "model_card_url": definition.get("model_card_url"),
        },
        "pilot": {
            "duration_minutes": duration,
            "gpu_count": gpu_count,
            "gpu_hourly_usd": rate,
            "max_hours": None if rate is None else round(duration / 60, 8),
            "max_session_hours": settings.max_session_hours,
        },
        "cost": {
            "gpu_max_usd": gpu_cap,
            "api_estimate_usd": api_estimate,
            "storage_estimate_usd": storage_estimate,
            "pilot_cap_usd": pilot_cap,
            "current_projected_usd": current_projected,
            "projected_after_pilot_usd": projected_after,
            "subscription_monthly_usd": budget_status.get("subscription_monthly_usd", 0.0),
            "storage_monthly_usd": budget_status.get("storage_monthly_usd", 0.0),
            "fixed_commitments_usd": budget_status.get("committed_usd", 0.0),
            "effective_budget_usd": effective_budget,
            "headroom_after_pilot_usd": headroom_after,
        },
        "budget_status": budget_status,
        "checks": checks,
        "next_steps": next_steps,
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _profile_payload(settings: Settings, profile_name: str) -> dict[str, Any]:
    catalog = load_catalog(settings.catalog_path)
    profiles = catalog["profiles"]
    if profile_name not in profiles:
        raise BudgetError(f"unknown model profile: {profile_name}")
    return {
        "contract": CONTRACT_VERSION,
        "catalog": catalog["schema_version"],
        "profile": profile_name,
        "definition": profiles[profile_name],
        "environment": profile_environment(profile_name, catalog),
    }


def _start_session(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    profile_name = args.profile or os.environ.get("LLM_LAB_DEFAULT_PROFILE") or "compact"
    profile = _profile_payload(settings, profile_name)
    rate_raw = args.gpu_hourly_usd if args.gpu_hourly_usd is not None else os.environ.get("LLM_LAB_GPU_HOURLY_USD")
    if rate_raw is None:
        raise BudgetError("gpu hourly price is required; pass --gpu-hourly-usd or LLM_LAB_GPU_HOURLY_USD")
    rate = _positive(rate_raw, "gpu_hourly_usd")
    max_hours = _positive(args.max_hours, "max_hours")
    if max_hours > 744:
        raise BudgetError("max_hours must be at most 744")
    if max_hours > settings.max_session_hours + 1e-9:
        raise BudgetError(f"max_hours must be at most {settings.max_session_hours:g} for the on-demand GPU lane")
    gpu_count = int(args.gpu_count)
    if gpu_count < 1 or gpu_count > 64:
        raise BudgetError("gpu_count must be between 1 and 64")
    session_id = _safe_id(args.session_id or f"llm-{uuid.uuid4()}", "session_id")
    reservation = round(rate * gpu_count * max_hours, 8)
    with ledger_lock(settings.ledger_path):
        events, errors = read_events(settings.ledger_path)
        if any(event.get('session_id') == session_id for event in events):
            raise BudgetError('session_id already exists')
        status = build_status(settings, events=events)
        if errors:
            status["decision"] = "deny"
            status["reason"] = "gpu_ledger_invalid"
        if status["decision"] != "allow" or status["effective_budget_usd"] is None:
            status.update({"session_id": session_id, "reservation_usd": reservation})
            return status
        if status["total_projected_usd"] + reservation > status["effective_budget_usd"] + 1e-9:
            status.update({"decision": "deny", "reason": "session_reservation_exceeds_monthly_budget", "reasons": ["session_reservation_exceeds_monthly_budget"], "session_id": session_id, "reservation_usd": reservation})
            return status
        event = {
            "contract": CONTRACT_VERSION,
            "event": "session_started",
            "session_id": session_id,
            "started_at": _iso(datetime.now(timezone.utc)),
            "profile": profile_name,
            "model_id": profile["definition"]["model_id"],
            "provider": _safe_id(args.provider, "provider"),
            "gpu_count": gpu_count,
            "gpu_hourly_usd": rate,
            "max_hours": max_hours,
            "reserved_cost_usd": reservation,
        }
        append_event(settings.ledger_path, event)
    return {"contract": CONTRACT_VERSION, "decision": "allow", "reason": "session_reserved", "session_id": session_id, "reservation_usd": reservation, "profile": profile_name, "model_id": profile["definition"]["model_id"]}


def _stop_session(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    session_id = _safe_id(args.session_id, "session_id")
    with ledger_lock(settings.ledger_path):
        events, errors = read_events(settings.ledger_path)
        if errors:
            raise BudgetError("gpu ledger is invalid")
        active, closed, session_errors = _sessions(events)
        if session_id in closed:
            return {"contract": CONTRACT_VERSION, "decision": "already_stopped", "session_id": session_id}
        if session_id not in active or session_errors:
            raise BudgetError(f"active session not found: {session_id}")
        started = active[session_id]
        stopped_at = datetime.now(timezone.utc)
        duration_seconds = max(0.0, (stopped_at - started["started"]).total_seconds())
        actual = _finite_non_negative(args.actual_cost_usd, "actual_cost_usd") if args.actual_cost_usd is not None else duration_seconds * started["rate"] * started["gpu_count"] / 3600
        event = {
            "contract": CONTRACT_VERSION,
            "event": "session_stopped",
            "session_id": session_id,
            "stopped_at": _iso(stopped_at),
            "duration_seconds": round(duration_seconds, 3),
            "actual_cost_usd": round(actual, 8),
        }
        append_event(settings.ledger_path, event)
    return {"contract": CONTRACT_VERSION, "decision": "stopped", "session_id": session_id, "actual_cost_usd": round(actual, 8), "duration_seconds": round(duration_seconds, 3)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM lab model profiles and hard budget guard")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="show month-to-date spend and reservations")
    status.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    check = sub.add_parser("check", help="check whether a request estimate fits the budget")
    check.add_argument("--estimate-usd", type=float, default=0)
    check.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    pilot = sub.add_parser("pilot-preflight", help="build a read-only bounded pilot launch report")
    pilot.add_argument("--profile", default=None)
    pilot.add_argument("--monthly-budget-usd", type=float, default=None)
    pilot.add_argument("--gpu-hourly-usd", type=float, default=None)
    pilot.add_argument("--gpu-count", type=int, default=1)
    pilot.add_argument("--duration-minutes", type=float, default=30.0)
    pilot.add_argument("--api-estimate-usd", type=float, default=None)
    pilot.add_argument("--storage-estimate-usd", type=float, default=None)
    pilot.add_argument("--require-provider-stop-hook", action="store_true")
    pilot.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    profiles = sub.add_parser("profiles", help="show the versioned model catalog")
    profiles.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    profile = sub.add_parser("profile-env", help="show safe environment values for one model profile")
    profile.add_argument("--profile", default=None)
    profile.add_argument("--format", choices=("json", "shell"), default="json")
    profile.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    start = sub.add_parser("session-start", help="reserve a bounded GPU session")
    start.add_argument("--profile", default=None)
    start.add_argument("--provider", default="runpod-vllm")
    start.add_argument("--gpu-hourly-usd", type=float)
    start.add_argument("--gpu-count", type=int, default=1)
    start.add_argument("--max-hours", type=float, required=True)
    start.add_argument("--session-id")
    start.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    stop = sub.add_parser("session-stop", help="close a GPU session and record actual cost")
    stop.add_argument("--session-id", required=True)
    stop.add_argument("--actual-cost-usd", type=float)
    stop.add_argument("--json", action="store_true", help="keep the machine-readable output contract")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    try:
        args = _parser().parse_args(argv)
        settings = load_settings()
        if args.command == "status":
            _emit(build_status(settings))
        elif args.command == "check":
            result = check_budget(settings, args.estimate_usd)
            _emit(result)
            return 0 if result['decision'] == 'allow' else 3
        elif args.command == "pilot-preflight":
            result = build_pilot_preflight(
                profile_name=args.profile,
                monthly_budget_usd=args.monthly_budget_usd,
                gpu_hourly_usd=args.gpu_hourly_usd,
                gpu_count=args.gpu_count,
                duration_minutes=args.duration_minutes,
                api_estimate_usd=args.api_estimate_usd,
                storage_estimate_usd=args.storage_estimate_usd,
                require_provider_stop_hook=args.require_provider_stop_hook,
            )
            _emit(result)
            return 0 if result["decision"] == "allow" else 3
        elif args.command == "profiles":
            _emit(load_catalog(settings.catalog_path))
        elif args.command == "profile-env":
            name = args.profile or os.environ.get("LLM_LAB_DEFAULT_PROFILE") or "compact"
            payload = _profile_payload(settings, name)
            if args.format == "shell":
                for key, value in payload["environment"].items():
                    if value:
                        print(f"export {key}={shlex.quote(value)}")
            else:
                _emit(payload)
        elif args.command == "session-start":
            result = _start_session(settings, args)
            _emit(result)
            return 0 if result.get("decision") == "allow" else 3
        elif args.command == "session-stop":
            _emit(_stop_session(settings, args))
        else:
            raise BudgetError("unknown command")
        return 0
    except BudgetError as exc:
        _emit({"contract": CONTRACT_VERSION, "decision": "deny", "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
