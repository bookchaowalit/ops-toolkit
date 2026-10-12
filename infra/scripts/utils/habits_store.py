#!/usr/bin/env python3
"""Habits live in the habits cell (habits); this is its client.

Every reader and writer of habits, habit logs, routines and routine logs goes
through ``open_habits_store`` instead of the retired SQLite tables. The API URL
comes from ``EMPIRE_HABITS_API`` (environment, or the owner-only client switch
``~/.config/empire-platform/client.env``); the token is the caller's automation
token. https only (http only on loopback); redirects are never followed.

Shapes: ids are integers, dates ``YYYY-MM-DD`` strings, ``completed`` a bool,
``schedule_days``/``steps``/``completed_steps``/``skipped_steps`` lists (not
JSON strings). The service owns ``current_streak``, ``best_streak`` and
``last_completed_at``: logging or un-logging a day recomputes them, so writers
never compute streaks themselves.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from task_store import ClientConfigError, _NoRedirect, automation_token, client_config_path, read_client_file, \
    validate_api_url

API_KEY = "EMPIRE_HABITS_API"
PAGE = 500


class HabitsStoreError(RuntimeError):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"habits API {status}: {reason}")
        self.status = status
        self.reason = reason


def habits_api_url(environ: Any = None) -> str:
    environ = os.environ if environ is None else environ
    value = (environ.get(API_KEY) or "").strip()
    if not value:
        value = read_client_file(client_config_path(environ)).get(API_KEY, "")
    if not value:
        raise ClientConfigError(f"the habits API is not configured: set {API_KEY} (env or client.env)")
    try:
        return validate_api_url(value)
    except ClientConfigError:
        raise ClientConfigError(f"{API_KEY} must be https (http only for 127.0.0.1, ::1 or localhost)") from None


class ApiHabitsStore:
    """Stdlib HTTP client for the habits API."""

    def __init__(self, base_url: str, token: str, *, channel: str = "workflow", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.channel = channel
        self.correlation_id = uuid.uuid4().hex
        self.timeout = timeout  # the cell may be waking from sleep
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"ApiHabitsStore({self.base_url!r}, channel={self.channel!r})"

    def _call(self, method: str, path: str, body: Any = None, params: dict | None = None,
              headers: dict | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = f"?{urllib.parse.urlencode(clean, doseq=True)}" if clean else ""
        request = urllib.request.Request(
            f"{self.base_url}{path}{query}", method=method,
            data=json.dumps(body, default=str).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "X-Empire-Channel": self.channel,
                     "X-Correlation-Id": self.correlation_id, "Content-Type": "application/json", **(headers or {})})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            reason = (detail.get("reason") or detail.get("error")) if isinstance(detail, dict) else "error"
            raise HabitsStoreError(error.code, str(reason)) from None
        except (urllib.error.URLError, OSError) as error:
            raise HabitsStoreError(503, f"unavailable:{type(error).__name__}") from None

    def _pages(self, path: str, params: dict) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            page = self._call("GET", path, params={**params, "limit": PAGE, "offset": len(out)})["items"]
            out.extend(page)
            if len(page) < PAGE:
                return out

    def _one(self, path: str) -> dict[str, Any] | None:
        try:
            return self._call("GET", path)
        except HabitsStoreError as exc:
            if exc.status == 404:
                return None
            raise

    # --- habits --------------------------------------------------------------------------

    def habits(self, *, status: str | list[str] | None = None, category: str | None = None,
               frequency: str | None = None) -> list[dict[str, Any]]:
        """Every habit matching the filters, ordered by id."""
        statuses = [status] if isinstance(status, str) else status
        return self._pages("/v1/habits", {"status": statuses, "category": category, "frequency": frequency})

    def habit(self, habit_id: int) -> dict[str, Any] | None:
        return self._one(f"/v1/habits/{int(habit_id)}")

    def habit_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self._one(f"/v1/habits/slug/{urllib.parse.quote(slug, safe='')}")

    def create_habit(self, fields: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("POST", "/v1/habits", fields, headers=headers)

    def update_habit(self, habit_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                     ) -> dict[str, Any]:
        body = {**fields, **({"expected_version": expected_version} if expected_version else {})}
        return self._call("PATCH", f"/v1/habits/{int(habit_id)}", body)

    def delete_habit(self, habit_id: int) -> None:
        self._call("DELETE", f"/v1/habits/{int(habit_id)}")

    def habit_logs(self, *, habit_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
                   completed: bool | None = None, notes_prefix: str | None = None,
                   order: str = "asc") -> list[dict[str, Any]]:
        """Every matching log (inclusive date range), ordered by log_date then id."""
        params = {"habit_id": habit_id, "from": date_from, "to": date_to, "notes_prefix": notes_prefix,
                  "order": order, "completed": None if completed is None else str(bool(completed)).lower()}
        return self._pages("/v1/habit-logs", params)

    def log_habit(self, habit_id: int, log_date: str, fields: dict[str, Any] | None = None
                  ) -> tuple[str, dict[str, Any]]:
        """Upsert one day's log (``completed`` defaults to true); returns (created|updated|unchanged, log)."""
        result = self._call("PUT", f"/v1/habits/{int(habit_id)}/logs/{log_date}", fields or {})
        return result["outcome"], result["log"]

    def unlog_habit(self, habit_id: int, log_date: str) -> bool:
        try:
            self._call("DELETE", f"/v1/habits/{int(habit_id)}/logs/{log_date}")
            return True
        except HabitsStoreError as exc:
            if exc.status == 404:
                return False
            raise

    # --- routines --------------------------------------------------------------------------

    def routines(self, *, status: str | list[str] | None = None, routine_type: str | None = None
                 ) -> list[dict[str, Any]]:
        statuses = [status] if isinstance(status, str) else status
        return self._pages("/v1/routines", {"status": statuses, "routine_type": routine_type})

    def routine(self, routine_id: int) -> dict[str, Any] | None:
        return self._one(f"/v1/routines/{int(routine_id)}")

    def routine_by_slug(self, slug: str) -> dict[str, Any] | None:
        return self._one(f"/v1/routines/slug/{urllib.parse.quote(slug, safe='')}")

    def create_routine(self, fields: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("POST", "/v1/routines", fields, headers=headers)

    def update_routine(self, routine_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                       ) -> dict[str, Any]:
        body = {**fields, **({"expected_version": expected_version} if expected_version else {})}
        return self._call("PATCH", f"/v1/routines/{int(routine_id)}", body)

    def routine_logs(self, *, routine_id: int | None = None, date_from: str | None = None,
                     date_to: str | None = None, order: str = "asc") -> list[dict[str, Any]]:
        return self._pages("/v1/routine-logs", {"routine_id": routine_id, "from": date_from, "to": date_to,
                                                "order": order})

    def log_routine(self, routine_id: int, log_date: str, fields: dict[str, Any] | None = None
                    ) -> tuple[str, dict[str, Any]]:
        result = self._call("PUT", f"/v1/routines/{int(routine_id)}/logs/{log_date}", fields or {})
        return result["outcome"], result["log"]

    # --- counts ------------------------------------------------------------------------------

    def stats(self, kind: str = "habits", group_by: str = "status") -> dict[str, int]:
        return self._call("GET", f"/v1/{kind}/stats", params={"group_by": group_by})["groups"]


def open_habits_store(automation: str, *, channel: str = "workflow") -> ApiHabitsStore:
    """The habits API store for ``automation`` (an ID from the automations registry)."""
    url = habits_api_url()
    try:
        token = automation_token(automation)
    except RuntimeError as exc:  # a missing token is a configuration problem, like a missing URL
        raise ClientConfigError(str(exc)) from None
    return ApiHabitsStore(url, token, channel=channel)


# --- small helpers readers share (pure functions over the lists above) ---------------------------

def completed_today(logs: list[dict[str, Any]], day: str) -> set[int]:
    """Habit ids with a completed log on ``day``."""
    return {log["habit_id"] for log in logs if log["log_date"] == day and log["completed"]}


def scheduled_on(habit: dict[str, Any], weekday: str) -> bool:
    """Whether ``habit`` is scheduled on ``weekday`` ('mon'..'sun'); no schedule means every day."""
    days = habit.get("schedule_days") or []
    return not days or weekday.lower() in {d.lower() for d in days}
