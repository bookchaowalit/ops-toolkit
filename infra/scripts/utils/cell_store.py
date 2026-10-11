#!/usr/bin/env python3
"""One client for every cell made with solo-cell-kit (goals, projects, roles-skills, ...).

Each kit cell serves the same routes for every resource, so one client covers
them all::

    store = open_cell_store("goals", "portfolio-reader")
    goals = store.list("goals", status=["active", "planning"])
    task = store.create("goal-tasks", {"goal_id": 12, "title": "Ship"})
    store.update("goals", 12, {"progress_percent": 40})

The API URL comes from ``EMPIRE_<CELL>_API`` (environment, or the owner-only
client switch ``~/.config/empire-platform/client.env``); the token is the
caller's automation token. https only (http only on loopback); redirects are
never followed. Ids of imported rows are their old SQLite ids, so references
between cells keep working.
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

PAGE = 1000


class CellStoreError(RuntimeError):
    def __init__(self, status: int, reason: str, cell: str = "cell") -> None:
        super().__init__(f"{cell} API {status}: {reason}")
        self.status = status
        self.reason = reason


def api_key(cell: str) -> str:
    return f"EMPIRE_{cell.upper().replace('-', '_')}_API"


def cell_api_url(cell: str, environ: Any = None) -> str:
    environ = os.environ if environ is None else environ
    key = api_key(cell)
    value = (environ.get(key) or "").strip()
    if not value:
        value = read_client_file(client_config_path(environ)).get(key, "")
    if not value:
        raise ClientConfigError(f"the {cell} API is not configured: set {key} (env or client.env)")
    try:
        return validate_api_url(value)
    except ClientConfigError:
        raise ClientConfigError(f"{key} must be https (http only for 127.0.0.1, ::1 or localhost)") from None


class ApiCellStore:
    """Stdlib HTTP client for one kit cell."""

    def __init__(self, cell: str, base_url: str, token: str, *, channel: str = "workflow",
                 timeout: float = 30.0) -> None:
        self.cell = cell
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.channel = channel
        self.correlation_id = uuid.uuid4().hex
        self.timeout = timeout  # the cell may be waking from sleep
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"ApiCellStore({self.cell!r}, {self.base_url!r}, channel={self.channel!r})"

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
            raise CellStoreError(error.code, str(reason), self.cell) from None
        except (urllib.error.URLError, OSError) as error:
            raise CellStoreError(503, f"unavailable:{type(error).__name__}", self.cell) from None

    @staticmethod
    def _params(filters: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for name, value in filters.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = str(value).lower()
            elif isinstance(value, (list, tuple, set)):
                value = [str(v).lower() if isinstance(v, bool) else v for v in value]
            out[name] = value
        return out

    def list(self, plural: str, *, order: str = "asc", limit: int | None = None, **filters: Any
             ) -> list[dict[str, Any]]:
        """Every matching record (all pages), in the resource's order; ``null=[field]`` matches empty fields."""
        out: list[dict[str, Any]] = []
        params = {**self._params(filters), "order": order}
        while True:
            size = PAGE if limit is None else min(PAGE, limit - len(out))
            page = self._call("GET", f"/v1/{plural}", params={**params, "limit": size, "offset": len(out)})["items"]
            out.extend(page)
            if len(page) < size or (limit is not None and len(out) >= limit):
                return out

    def get(self, plural: str, record_id: int) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"/v1/{plural}/{int(record_id)}")
        except CellStoreError as exc:
            if exc.status == 404:
                return None
            raise

    def create(self, plural: str, fields: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("POST", f"/v1/{plural}", fields, headers=headers)

    def update(self, plural: str, record_id: int, fields: dict[str, Any], *,
               expected_version: int | None = None) -> dict[str, Any]:
        body = {**fields, **({"expected_version": expected_version} if expected_version else {})}
        return self._call("PATCH", f"/v1/{plural}/{int(record_id)}", body)

    def delete(self, plural: str, record_id: int) -> bool:
        try:
            self._call("DELETE", f"/v1/{plural}/{int(record_id)}")
            return True
        except CellStoreError as exc:
            if exc.status == 404:
                return False
            raise

    def stats(self, plural: str, group_by: str, **filters: Any) -> dict[str, int]:
        return self._call("GET", f"/v1/{plural}/stats", params={**self._params(filters), "group_by": group_by})["groups"]


def open_cell_store(cell: str, automation: str, *, channel: str = "workflow") -> ApiCellStore:
    """The API store of kit cell ``cell`` for ``automation`` (an ID from the automations registry)."""
    url = cell_api_url(cell)
    try:
        token = automation_token(automation)
    except RuntimeError as exc:  # a missing token is a configuration problem, like a missing URL
        raise ClientConfigError(str(exc)) from None
    return ApiCellStore(cell, url, token, channel=channel)
