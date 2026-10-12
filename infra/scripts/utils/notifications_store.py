#!/usr/bin/env python3
"""Notifications live in the notifications cell (notifications); this is its client.

Every reader and writer of notification rules and the notification log goes
through ``open_notifications_store`` instead of the retired SQLite tables. The
API URL comes from ``EMPIRE_NOTIFICATIONS_API`` (environment, or the owner-only
client switch ``~/.config/empire-platform/client.env``); the token is the
caller's automation token. https only (http only on loopback); redirects are
never followed.

Shapes: ids are integers, timestamps ISO 8601 strings (UTC), ``metadata`` a
dict, rule ``config``/``channels``/``status_filter`` a dict or lists.

Queueing is idempotent with a ``dedupe_key`` (``queue`` returns
``("existing", entry)`` for a known key, so callers decide whether to retry
delivery); ``dedupe_key(*parts)`` builds a short stable key from any values.
Delivery results go through ``record_delivery``, which the service applies
atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from task_store import ClientConfigError, _NoRedirect, automation_token, client_config_path, read_client_file, \
    validate_api_url

API_KEY = "EMPIRE_NOTIFICATIONS_API"
PAGE = 1000


class NotificationsStoreError(RuntimeError):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"notifications API {status}: {reason}")
        self.status = status
        self.reason = reason


def notifications_api_url(environ: Any = None) -> str:
    environ = os.environ if environ is None else environ
    value = (environ.get(API_KEY) or "").strip()
    if not value:
        value = read_client_file(client_config_path(environ)).get(API_KEY, "")
    if not value:
        raise ClientConfigError(f"the notifications API is not configured: set {API_KEY} (env or client.env)")
    try:
        return validate_api_url(value)
    except ClientConfigError:
        raise ClientConfigError(f"{API_KEY} must be https (http only for 127.0.0.1, ::1 or localhost)") from None


def dedupe_key(*parts: Any) -> str:
    """A stable key (<= 64 chars) for the given values, e.g. a task snapshot and a local date."""
    return "k-" + hashlib.sha256(json.dumps(parts, default=str, ensure_ascii=False).encode()).hexdigest()[:60]


class ApiNotificationsStore:
    """Stdlib HTTP client for the notifications API."""

    def __init__(self, base_url: str, token: str, *, channel: str = "workflow", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.channel = channel
        self.correlation_id = uuid.uuid4().hex
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"ApiNotificationsStore({self.base_url!r}, channel={self.channel!r})"

    def _call(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = f"?{urllib.parse.urlencode(clean, doseq=True)}" if clean else ""
        request = urllib.request.Request(
            f"{self.base_url}{path}{query}", method=method,
            data=json.dumps(body, default=str).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "X-Empire-Channel": self.channel,
                     "X-Correlation-Id": self.correlation_id, "Content-Type": "application/json"})
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
            raise NotificationsStoreError(error.code, str(reason)) from None
        except (urllib.error.URLError, OSError) as error:
            raise NotificationsStoreError(503, f"unavailable:{type(error).__name__}") from None

    # --- rules ---------------------------------------------------------------------------------

    def rules(self, *, active: bool | None = None, rule_type: str | None = None) -> list[dict[str, Any]]:
        params = {"active": None if active is None else str(bool(active)).lower(), "rule_type": rule_type}
        return self._call("GET", "/v1/notification-rules", params=params)["items"]

    def rule(self, rule_id: int) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/v1/notification-rules/{int(rule_id)}")
        except NotificationsStoreError as exc:
            if exc.status == 404:
                return None
            raise

    def create_rule(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/v1/notification-rules", fields)

    def update_rule(self, rule_id: int, fields: dict[str, Any], *, expected_version: int | None = None
                    ) -> dict[str, Any]:
        body = {**fields, **({"expected_version": expected_version} if expected_version else {})}
        return self._call("PATCH", f"/v1/notification-rules/{int(rule_id)}", body)

    # --- log -----------------------------------------------------------------------------------

    def notifications(self, *, notification_type: str | list[str] | None = None, since: str | None = None,
                      until: str | None = None, task_id: int | None = None, rule_id: int | None = None,
                      undelivered: bool | None = None, max_attempts: int | None = None,
                      dedupe_key: str | None = None, order: str = "asc",
                      limit: int | None = None) -> list[dict[str, Any]]:
        """Matching entries ordered by id (``since`` inclusive, ``until`` exclusive); every page unless ``limit``."""
        types = [notification_type] if isinstance(notification_type, str) else notification_type
        params = {"notification_type": types, "since": since, "until": until, "task_id": task_id,
                  "rule_id": rule_id, "max_attempts": max_attempts, "dedupe_key": dedupe_key, "order": order,
                  "undelivered": None if undelivered is None else str(bool(undelivered)).lower()}
        out: list[dict[str, Any]] = []
        while True:
            size = min(PAGE, limit - len(out)) if limit else PAGE
            page = self._call("GET", "/v1/notifications", params={**params, "limit": size, "offset": len(out)})["items"]
            out.extend(page)
            if len(page) < size or (limit and len(out) >= limit):
                return out

    def notification(self, notification_id: int) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/v1/notifications/{int(notification_id)}")
        except NotificationsStoreError as exc:
            if exc.status == 404:
                return None
            raise

    def stats(self, group_by: str = "notification_type", since: str | None = None) -> dict[str, int]:
        return self._call("GET", "/v1/notifications/stats", params={"group_by": group_by, "since": since})["groups"]

    def queue(self, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """(created|existing, entry); with ``dedupe_key`` a repeat returns the first entry unchanged."""
        result = self._call("POST", "/v1/notifications", fields)
        return result["outcome"], result["notification"]

    def record_delivery(self, notification_id: int, outcome: str, *, channel: str | None = None,
                        error: str | None = None) -> dict[str, Any]:
        """outcome: delivered (needs a non-console channel), failed (error), or console."""
        return self._call("POST", f"/v1/notifications/{int(notification_id)}/delivery",
                          {"outcome": outcome, "channel": channel, "error": error})


def open_notifications_store(automation: str, *, channel: str = "workflow") -> ApiNotificationsStore:
    """The notifications API store for ``automation`` (an ID from the automations registry)."""
    url = notifications_api_url()
    try:
        token = automation_token(automation)
    except RuntimeError as exc:  # a missing token is a configuration problem, like a missing URL
        raise ClientConfigError(str(exc)) from None
    return ApiNotificationsStore(url, token, channel=channel)
