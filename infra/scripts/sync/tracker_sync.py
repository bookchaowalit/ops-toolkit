#!/usr/bin/env python3
"""One guarded tick for the task #177 tracker pilot; dry-run unless --apply."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from tracker_sync_http import SyncError, make_adapters

PILOT = {
    "schema_version": 1, "pilot": "task-177",
    "task": {"id": "01a13b0d-976a-7a17-94f8-2b95bb175612", "number": 177},
    "trello": {
        "card_id": "69b6b2394811358126a19b4d", "board_id": "697de98c7caf3602f76b0248",
        "workspace_id": "605e3dd7400b1620126cb080",
        "lists": {"Backlog": "697de9b56ba3512a2565a1f8", "WIP": "697de9b2e8235fc702551dd3", "Review": "697de9ba62ad743534beebb0", "Done": "697de9bd27a92285f988fcd7"},
        "source_marker": "trello:69b6b2394811358126a19b4d"},
    "jira": {
        "issue_id": "10000", "issue_key": "KAN-1", "project_id": "10000", "origin": "https://bookchaowalit.atlassian.net",
        "status_ids": {"Backlog": "10000", "WIP": "10001", "Review": "10003", "Done": "10002"},
        "source_marker": "trello:69b6b2394811358126a19b4d"},
    "linear": {
        "issue_id": "a2456197-4e66-40ed-ba42-a1510fce65ad", "identifier": "BOO-27", "team_id": "784ebf2d-d9f7-4545-a892-c96365331a90", "source_marker": "trello:69b6b2394811358126a19b4d"},
    "managed_block": {"start": "<!-- tracker-sync:task-177:start -->", "end": "<!-- tracker-sync:task-177:end -->"}}
CREDENTIALS = ("LINEAR_API_KEY", "TRELLO_API_KEY", "TRELLO_API_TOKEN", "JIRA_EMAIL", "JIRA_API_TOKEN")
KEYS = ("task_title", "jira_summary", "jira_status", "trello", "linear")
PROVIDERS = {"task_title": "tasks", "jira_summary": "jira", "jira_status": "jira", "trello": "trello", "linear": "linear"}
LIMITATION = "Jira/Trello rereads reduce the race window; provider writes have no atomic compare-and-swap or cross-provider transaction."


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def notes_of(task):
    try:
        notes = json.loads(task["notes"]) if isinstance(task.get("notes"), str) else task.get("notes")
    except (ValueError, TypeError):
        raise SyncError("task_notes_invalid", status="conflict") from None
    if not isinstance(notes, dict): raise SyncError("task_notes_invalid", status="conflict")
    return notes


def phase_of(task):
    notes = notes_of(task)
    sync = notes.get("tracker_sync", {})
    if not isinstance(sync, dict): raise SyncError("phase_invalid", status="hold")
    explicit = sync.get("phase")
    allowed = {"todo": ("Backlog",), "in_progress": ("WIP", "Review"), "done": ("Done",)}
    phases = allowed.get(task.get("status"))
    if not phases or explicit is not None and explicit not in phases:
        raise SyncError("unsupported_execution_state", status="hold")
    return explicit or phases[0]


def description_text(value):
    if isinstance(value, str): return value
    if isinstance(value, dict):
        # Jira ADF identity inspection only; its document is never rewritten.
        return "\n".join([str(value.get("text", ""))] + [description_text(v) for v in value.get("content", [])])
    raise SyncError("description_invalid", status="conflict")


def block_span(text, markers):
    start, end = markers["start"], markers["end"]
    if not isinstance(text, str): raise SyncError("description_invalid", status="conflict")
    if start not in text and end not in text: return None
    if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
        raise SyncError("managed_block_invalid", status="conflict")
    return text.index(start), text.index(end) + len(end)


def managed_block(text, markers):
    span = block_span(text, markers)
    return text[span[0]:span[1]] if span else None


def replace_block(text, block, markers):
    span = block_span(text, markers)
    return text[:span[0]] + block + text[span[1]:] if span else text + ("\n\n" if text else "") + block


def report(status, codes=(), changed=(), planned=()):
    return {"schema": "tracker-sync.report.v1", "task_number": 177, "status": status,
        "codes": list(codes)[:10], "changed": list(changed)[:5], "planned": list(planned)[:5], "limitation": LIMITATION}


@contextmanager
def absolute_deadline(deadline):
    """Interrupt blocking I/O on Linux; never replace another caller's active alarm."""
    if sys.platform != "linux" or threading.current_thread() is not threading.main_thread():
        raise SyncError("deadline_context_unsupported")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_handler is None: raise SyncError("deadline_context_unsupported")
    if any(previous_timer): raise SyncError("deadline_timer_in_use")
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise SyncError("tick_deadline")
    def expired(signum, frame):
        raise SyncError("tick_deadline")
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous_handler)
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)


class SyncWorker:
    def __init__(self, config, state_dir, adapters, *, budget_seconds=60):
        if digest(config) != digest(PILOT): raise ValueError("config_outside_pilot")
        if not isinstance(budget_seconds, (int, float)) or isinstance(budget_seconds, bool) or not 0 < budget_seconds <= 60:
            raise ValueError("invalid_tick_budget")
        self.config = copy.deepcopy(config)
        self.config_hash = digest(config)
        self.directory = Path(state_dir)
        self.adapters = adapters
        self.budget_seconds = budget_seconds
        self.deadline = None

    def check_budget(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SyncError("tick_deadline")

    def initial_state(self):
        return {"schema": "tracker-sync.checkpoint.v1", "config_hash": self.config_hash,
            "baseline": {}, "pending": {}, "deferred_until": 0, "last_status": "new", "last_codes": []}

    def load_state(self):
        path = self.directory / "checkpoint.json"
        if not path.exists(): return self.initial_state()
        try:
            if path.stat().st_size > 16384: raise ValueError()
            state = json.loads(path.read_text())
            if set(state) != set(self.initial_state()) or state["schema"] != "tracker-sync.checkpoint.v1" or state["config_hash"] != self.config_hash:
                raise ValueError()
            if set(state["baseline"]) not in (set(), set(KEYS)) or not set(state["pending"]) <= set(KEYS): raise ValueError()
            if not state["baseline"] and state["pending"]: raise ValueError()
            def valid_hash(value): return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
            if not all(valid_hash(v) for v in state["baseline"].values()): raise ValueError()
            if not all(set(v) == {"before", "after"} and valid_hash(v["before"]) and valid_hash(v["after"]) for v in state["pending"].values()): raise ValueError()
            if not isinstance(state["deferred_until"], (int, float)) or not 0 <= state["deferred_until"] < 10**12: raise ValueError()
            if state["last_status"] not in ("new", "changed", "unchanged", "conflict", "failure", "hold", "deferred"): raise ValueError()
            if not isinstance(state["last_codes"], list) or len(state["last_codes"]) > 10 or any(not isinstance(v, str) or len(v) > 64 for v in state["last_codes"]): raise ValueError()
            return state
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise SyncError("checkpoint_invalid", status="conflict") from None

    def save(self, state):
        fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(state, handle, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.directory / "checkpoint.json")
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def validate(self, provider, obj):
        c = self.config
        if provider == "tasks":
            n = notes_of(obj)
            valid = (obj.get("id") == c["task"]["id"] and obj.get("number") == 177
                and isinstance(obj.get("version"), int) and not isinstance(obj["version"], bool) and obj["version"] >= 1
                and obj.get("external_source") == "trello" and obj.get("external_id") == c["trello"]["card_id"]
                and n.get("source_key") == c["trello"]["source_marker"]
                and n.get("linear", {}).get("uuid") == c["linear"]["issue_id"]
                and n.get("jira", {}).get("id") == c["jira"]["issue_id"] and n.get("jira", {}).get("key") == c["jira"]["issue_key"])
        elif provider == "trello":
            valid = (obj.get("id") == c["trello"]["card_id"] and obj.get("idBoard") == c["trello"]["board_id"]
                and obj.get("idList") in c["trello"]["lists"].values() and obj.get("closed") is False
                and isinstance(obj.get("name"), str) and bool(obj["name"].strip()))
        elif provider == "jira":
            valid = (obj.get("id") == c["jira"]["issue_id"] and obj.get("key") == c["jira"]["issue_key"]
                and obj.get("fields", {}).get("project", {}).get("id") == c["jira"]["project_id"])
        else:
            valid = (obj.get("id") == c["linear"]["issue_id"] and obj.get("identifier") == c["linear"]["identifier"]
                and obj.get("team", {}).get("id") == c["linear"]["team_id"])
        if not valid: raise SyncError(provider + "_identity_invalid", status="conflict")
        if provider != "tasks":
            value = obj.get("desc") if provider == "trello" else obj.get("fields", {}).get("description") if provider == "jira" else obj.get("description")
            text = description_text(value)
            if c[provider]["source_marker"] not in text or c["task"]["id"] not in text:
                raise SyncError(provider + "_source_marker_invalid", status="conflict")

    def read(self, provider):
        self.check_budget()
        obj = self.adapters[provider].read()
        self.check_budget()
        if not isinstance(obj, dict): raise SyncError(provider + "_response_invalid", status="conflict")
        self.validate(provider, obj)
        return obj

    def value(self, key, obj):
        if key == "task_title": return obj["title"]
        if key == "jira_summary": return obj["fields"]["summary"]
        if key == "jira_status": return obj["fields"]["status"]["id"]
        if key == "trello": return {"list": obj["idList"], "block": managed_block(obj["desc"], self.config["managed_block"])}
        return managed_block(obj["description"], self.config["managed_block"])

    def authority(self, task, card):
        return digest({"status": task["status"], "phase": phase_of(task), "version": task["version"], "source_title": card["name"]})

    def desired(self, objects):
        phase = phase_of(objects["tasks"])
        block = self.config["managed_block"]["start"] + "\nTask #177 execution: " + phase + "\n" + self.config["managed_block"]["end"]
        return {"task_title": objects["trello"]["name"], "jira_summary": objects["trello"]["name"],
            "jira_status": self.config["jira"]["status_ids"][phase], "trello": {"list": self.config["trello"]["lists"][phase], "block": block}, "linear": block}

    def guarded(self, state, key, actual, wanted):
        current, target = digest(actual), digest(wanted)
        pending = state["pending"].get(key)
        if pending:
            if current == pending["after"]:
                state["baseline"][key] = current
                del state["pending"][key]
                return True
            if current != pending["before"]:
                raise SyncError(key + "_uncertain_drift", status="conflict")
        elif current not in (state["baseline"][key], target):
            raise SyncError(key + "_manual_drift", status="conflict")
        return False

    def tick(self, *, apply=False):
        progress = {"changed": [], "planned": []}
        try:
            self.deadline = time.monotonic() + self.budget_seconds
            with absolute_deadline(self.deadline):
                result = self._tick(progress, apply=apply)
                # Preserve verified partial-change evidence in an error report.
                # Success still requires the final absolute-budget check.
                if result["status"] in ("changed", "unchanged", "dry_run"):
                    self.check_budget()
                return result
        except SyncError as error:
            return report(error.status, [error.code], **progress)

    def _tick(self, progress, *, apply=False):
        changed, planned, state = progress["changed"], progress["planned"], None
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(self.directory / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return report("locked", ["another_tick_running"])
            try:
                for adapter in self.adapters.values():
                    if hasattr(adapter, "http"): adapter.http.deadline = self.deadline
                    if hasattr(adapter, "deadline"): adapter.deadline = self.deadline
                state = self.load_state()
                if state["deferred_until"] > time.time(): return report("deferred", ["retry_after_active"])
                objects = {p: self.read(p) for p in ("tasks", "trello", "jira", "linear")}
                self.check_budget()
                linked = self.adapters["tasks"].read_external()
                self.check_budget()
                self.validate("tasks", linked)
                self.check_budget()
                board = self.adapters["trello"].read_board()
                self.check_budget()
                if board.get("id") != self.config["trello"]["board_id"] or board.get("idOrganization") != self.config["trello"]["workspace_id"]:
                    raise SyncError("trello_workspace_invalid", status="conflict")
                wanted = self.desired(objects)
                authority = self.authority(objects["tasks"], objects["trello"])
                if not state["baseline"]:
                    # Bootstrap is an explicit validation of live managed fields, not adoption.
                    for key in KEYS:
                        actual = self.value(key, objects[PROVIDERS[key]])
                        expected = wanted[key]
                        if key == "trello": expected = {**expected, "block": actual["block"]}
                        if key == "linear": expected = actual
                        if actual != expected or key in ("trello", "linear") and (actual["block"] if key == "trello" else actual) not in (None, wanted["trello"]["block"]):
                            raise SyncError("bootstrap_mirror_mismatch", status="conflict")
                    state["baseline"] = {k: digest(self.value(k, objects[PROVIDERS[k]])) for k in KEYS}
                for key in KEYS:
                    if self.guarded(state, key, self.value(key, objects[PROVIDERS[key]]), wanted[key]): changed.append(key)
                planned[:] = [k for k in KEYS if self.value(k, objects[PROVIDERS[k]]) != wanted[k]]
                if not apply: return report("dry_run" if planned or changed else "unchanged", planned=planned)
                state["deferred_until"] = 0
                self.save(state)
                for key in KEYS:
                    provider = PROVIDERS[key]
                    fresh = self.read(provider)
                    for related in KEYS:
                        if PROVIDERS[related] == provider:
                            self.guarded(state, related, self.value(related, fresh), wanted[related])
                    if self.value(key, fresh) == wanted[key]:
                        state["baseline"][key] = digest(wanted[key]); state["pending"].pop(key, None)
                        self.save(state)
                        continue
                    # Hold if the execution authority or Trello source title changed mid-tick.
                    task_now = fresh if provider == "tasks" else self.read("tasks")
                    card_now = fresh if provider == "trello" else self.read("trello")
                    if self.authority(task_now, card_now) != authority:
                        raise SyncError("execution_authority_changed", status="conflict")
                    before, after = digest(self.value(key, fresh)), digest(wanted[key])
                    state["pending"][key] = {"before": before, "after": after}
                    self.save(state)
                    output = wanted[key]
                    if key == "trello": output = {**output, "description": replace_block(fresh["desc"], output["block"], self.config["managed_block"])}
                    if key == "linear": output = replace_block(fresh["description"], output, self.config["managed_block"])
                    self.check_budget()
                    if provider == "jira":
                        def final_guard(jira_now):
                            self.validate("jira", jira_now)
                            for related in ("jira_summary", "jira_status"):
                                self.guarded(state, related, self.value(related, jira_now), wanted[related])
                            if self.authority(self.read("tasks"), self.read("trello")) != authority:
                                raise SyncError("execution_authority_changed", status="conflict")
                            self.check_budget()
                        self.adapters[provider].write(key, output, fresh, guard=final_guard)
                    else:
                        self.adapters[provider].write(key, output, fresh)
                    verified = self.read(provider)
                    if digest(self.value(key, verified)) != after:
                        raise SyncError(key + "_readback_mismatch", status="conflict")
                    if key == "task_title":
                        if verified["version"] != fresh["version"] + 1 or verified["status"] != fresh["status"] or phase_of(verified) != phase_of(fresh):
                            raise SyncError("execution_authority_changed", status="conflict")
                        authority = self.authority(verified, card_now)
                    if key not in changed: changed.append(key)
                    state["baseline"][key] = after; del state["pending"][key]
                    self.save(state)
                self.check_budget()
                result = report("changed" if changed else "unchanged", changed=changed)
            except SyncError as error:
                result = report(error.status, [error.code], changed, planned)
                if state is not None and error.retry_after:
                    state["deferred_until"] = time.time() + error.retry_after
            except (KeyError, TypeError, AttributeError, ValueError):
                result = report("conflict", ["malformed_provider_or_checkpoint"], changed, planned)
            except OSError:
                result = report("failure", ["local_state_unavailable"], changed, planned)
            if apply and state is not None and (state["baseline"] or state["deferred_until"]):
                state["last_status"], state["last_codes"] = result["status"], result["codes"]
                try: self.save(state)
                except OSError: return report("failure", ["checkpoint_write_failed"], changed, planned)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2] / "platform/tracker-sync-pilot.json")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text())
        if digest(config) != digest(PILOT): raise ValueError()
        from _env import get_env
        credentials = {name: get_env(name) for name in CREDENTIALS}
        missing = [name for name in CREDENTIALS if not credentials[name]]
        if missing:
            result = report("not_configured", ["worker_credentials_missing"])
            result["missing"] = missing
        else:
            from task_store import open_task_store
            store = open_task_store(None, "task-sync", channel="cli")
            store.correlation_id = "tracker-sync-180-" + uuid.uuid4().hex
            store.timeout = 8
            if args.preflight:
                result = report("ready", ["credentials_present_auth_unverified"])
                result["auth_verified"] = False
            else:
                adapters = make_adapters(config, credentials, store)
                directory = args.state_dir or Path.home() / ".local/state/solo-empire/tracker-sync" / digest(config)[:16]
                result = SyncWorker(config, directory, adapters).tick(apply=args.apply)
    except (OSError, ValueError, RuntimeError):
        result = report("failure", ["configuration_or_credentials_unavailable"])
    if not args.quiet or result["status"] != "unchanged":
        print(json.dumps(result, sort_keys=True) if args.json else "tracker-sync: " + result["status"] + (" (" + ", ".join(result["codes"]) + ")" if result["codes"] else "") + ("; missing: " + ", ".join(result["missing"]) if result.get("missing") else ""))
    return 0 if result["status"] in ("ready", "changed", "unchanged", "dry_run", "locked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
