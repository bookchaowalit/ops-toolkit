#!/usr/bin/env python3
"""Real localhost HTTP tests of the bounded tracker synchronization worker."""
import copy
import contextlib
import io
import importlib
import json
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "infra/scripts/sync"), str(ROOT / "infra/scripts/utils")]

CONFIG = {
    "schema_version": 1, "pilot": "task-177",
    "task": {"id": "01a13b0d-976a-7a17-94f8-2b95bb175612", "number": 177},
    "trello": {
        "card_id": "69b6b2394811358126a19b4d", "board_id": "697de98c7caf3602f76b0248",
        "workspace_id": "605e3dd7400b1620126cb080",
        "lists": {"Backlog": "697de9b56ba3512a2565a1f8", "WIP": "697de9b2e8235fc702551dd3", "Review": "697de9ba62ad743534beebb0", "Done": "697de9bd27a92285f988fcd7"},
        "source_marker": "trello:69b6b2394811358126a19b4d"},
    "jira": {
        "issue_id": "10000", "issue_key": "KAN-1", "project_id": "10000",
        "origin": "https://bookchaowalit.atlassian.net",
        "status_ids": {"Backlog": "10000", "WIP": "10001", "Review": "10003", "Done": "10002"},
        "source_marker": "trello:69b6b2394811358126a19b4d"},
    "linear": {
        "issue_id": "a2456197-4e66-40ed-ba42-a1510fce65ad", "identifier": "BOO-27",
        "team_id": "784ebf2d-d9f7-4545-a892-c96365331a90", "source_marker": "trello:69b6b2394811358126a19b4d"},
    "managed_block": {"start": "<!-- tracker-sync:task-177:start -->", "end": "<!-- tracker-sync:task-177:end -->"}}


class LocalOpener:
    """Route fixed production origins to this test's actual local HTTP server."""
    def __init__(self, origin):
        self.origin = origin
        self.real = urllib.request.build_opener()

    def open(self, request, timeout):
        from urllib.parse import urlsplit
        url = urlsplit(request.full_url)
        provider = {"api.trello.com": "trello", "api.linear.app": "linear", "bookchaowalit.atlassian.net": "jira"}[url.hostname]
        target = self.origin + "/" + provider + url.path + ("?" + url.query if url.query else "")
        rewritten = urllib.request.Request(target, data=request.data, headers=dict(request.header_items()), method=request.method)
        return self.real.open(rewritten, timeout=timeout)


class FakeServices:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.failure = None
        self.read_failure = None
        self.read_failure_count = 0
        self.redirect = False
        self.slow_drip = False
        self.lost = None
        self.task = {"id": CONFIG["task"]["id"], "number": 177, "version": 3, "title": "Create rudimentary UI for the website", "status": "todo",
                     "external_source": "trello", "external_id": CONFIG["trello"]["card_id"],
                     "notes": json.dumps({"source_key": CONFIG["trello"]["source_marker"], "linear": {"uuid": CONFIG["linear"]["issue_id"]}, "jira": {"id": "10000", "key": "KAN-1"}})}
        desc = "Acceptance criteria stay unchanged.\n" + CONFIG["trello"]["source_marker"] + "\n" + CONFIG["task"]["id"]
        self.card = {"id": CONFIG["trello"]["card_id"], "idBoard": CONFIG["trello"]["board_id"], "idList": CONFIG["trello"]["lists"]["Backlog"], "name": self.task["title"], "desc": desc, "closed": False, "dueComplete": False, "complete": False, "labels": [{"id": "keep"}]}
        self.issue = {"id": "10000", "key": "KAN-1", "fields": {"summary": self.task["title"], "status": {"id": "10000"}, "project": {"id": "10000"}, "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": desc}]}]}}}
        self.linear = {"id": CONFIG["linear"]["issue_id"], "identifier": "BOO-27", "team": {"id": CONFIG["linear"]["team_id"]}, "description": desc, "title": "Evidence parent keep title", "state": {"id": "backlog"}}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self): self.route()
            def do_PATCH(self): self.route()
            def do_PUT(self): self.route()
            def do_POST(self): self.route()
            def route(self):
                path = self.path.split("?")[0]
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))) if self.headers.get("Content-Length") else None
                owner.calls.append((self.command, self.path, body, dict(self.headers)))
                if owner.hook: owner.hook(self.command, path, body)
                if owner.redirect:
                    self.send_response(302); self.send_header("Location", owner.origin + "/trap"); self.end_headers(); return
                if owner.read_failure and self.command == "GET" and owner.read_failure_count > 0:
                    owner.read_failure_count -= 1
                    status, retry = owner.read_failure
                    self.send_response(status)
                    if retry: self.send_header("Retry-After", retry)
                    self.end_headers(); return
                if owner.failure and owner.failure[0] in path and self.command != "GET" and not (path.startswith("/linear") and "mutation" not in body["query"]):
                    _, status, retry = owner.failure
                    owner.failure = None
                    self.send_response(status)
                    if retry: self.send_header("Retry-After", retry)
                    self.end_headers()
                    self.wfile.write(b'{"error":"secret-token-private-description"}')
                    return
                value = None
                if path.startswith("/v1/tasks/"):
                    if self.command == "PATCH":
                        if self.headers.get("If-Match") != str(owner.task["version"]):
                            self.send_response(412); self.end_headers(); return
                        owner.task.update(body); owner.task["version"] += 1
                    value = owner.task
                elif "/trello/1/boards/" in path:
                    value = {"id": CONFIG["trello"]["board_id"], "idOrganization": CONFIG["trello"]["workspace_id"]}
                elif "/trello/1/cards/" in path:
                    if self.command == "PUT": owner.card.update(body)
                    value = owner.card
                elif path.endswith("/transitions"):
                    if self.command == "POST":
                        states = {"11": "10000", "21": "10001", "31": "10003", "41": "10002"}
                        owner.issue["fields"]["status"]["id"] = states[body["transition"]["id"]]
                        value = {}
                    else: value = {"transitions": [{"id": k, "to": {"id": v}} for k, v in {"11": "10000", "21": "10001", "31": "10003", "41": "10002"}.items()]}
                elif "/jira/rest/api/3/issue/" in path:
                    if self.command == "PUT": owner.issue["fields"].update(body["fields"])
                    value = owner.issue
                elif path == "/linear/graphql":
                    if "mutation" in body["query"]:
                        owner.linear.update(body["variables"]["input"])
                        value = {"data": {"issueUpdate": {"success": True}}}
                    else: value = {"data": {"issue": owner.linear}}
                else:
                    self.send_response(404); self.end_headers(); return
                if owner.lost and owner.lost in path and self.command != "GET" and (not path.startswith("/linear") or "mutation" in body["query"]):
                    owner.lost = None
                    self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                encoded = json.dumps(value).encode()
                try:
                    if owner.slow_drip and path == "/linear/graphql" and "mutation" not in body["query"]:
                        owner.slow_drip = False
                        until, offset = time.monotonic() + 1.2, 0
                        while time.monotonic() < until and offset < len(encoded):
                            self.wfile.write(encoded[offset:offset + 1]); self.wfile.flush()
                            offset += 1
                            time.sleep(0.02)
                        self.wfile.write(encoded[offset:])
                    else:
                        self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def writes(self):
        return [c for c in self.calls if c[0] in ("PUT", "PATCH") or c[0] == "POST" and ("/transitions" in c[1] or "mutation" in c[2].get("query", ""))]


class TrackerSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # _env normally loads the owner's .env on import. Offline tests must not.
        exists = Path.exists
        with patch.object(Path, "exists", lambda path: False if path == ROOT / "infra/.env" else exists(path)):
            importlib.import_module("_env")
        try:
            cls.sync = importlib.import_module("tracker_sync")
            cls.http = importlib.import_module("tracker_sync_http")
        except ModuleNotFoundError:
            cls.sync = cls.http = None

    def setUp(self):
        self.assertIsNotNone(self.sync, "bounded tracker synchronization worker is missing")
        self.fake = FakeServices()
        self.addCleanup(self.fake.close)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        from task_store import ApiTaskStore
        self.adapters = self.http.make_adapters(CONFIG, {
            "LINEAR_API_KEY": "synthetic-linear", "TRELLO_API_KEY": "synthetic-key", "TRELLO_API_TOKEN": "synthetic-token", "JIRA_EMAIL": "test@example.invalid", "JIRA_API_TOKEN": "synthetic-jira"},
            ApiTaskStore(self.fake.origin, "synthetic-tasks", channel="cli"), opener=LocalOpener(self.fake.origin))
        self.worker = self.sync.SyncWorker(copy.deepcopy(CONFIG), Path(self.directory.name), self.adapters)

    def bootstrap(self):
        report = self.worker.tick(apply=True)
        self.assertEqual(report["status"], "changed", report)
        self.fake.calls.clear()

    def test_dry_run_has_no_provider_writes(self):
        result = self.worker.tick()
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(self.fake.writes(), [])
        self.assertFalse((Path(self.directory.name) / "checkpoint.json").exists())

    def test_noop_and_parent_preservation(self):
        before = copy.deepcopy(self.fake.linear)
        self.bootstrap()
        self.assertEqual(self.worker.tick(apply=True)["status"], "unchanged")
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.linear["title"], before["title"])
        self.assertEqual(self.fake.linear["state"], before["state"])
        self.assertIn(before["description"], self.fake.linear["description"])

    def test_source_title_uses_task_version_and_only_owned_fields(self):
        self.bootstrap()
        self.fake.card["name"] = "Source title changed"
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "changed", result)
        self.assertEqual(self.fake.task["title"], "Source title changed")
        self.assertEqual(self.fake.issue["fields"]["summary"], "Source title changed")
        patches = [c for c in self.fake.writes() if c[0] == "PATCH"]
        self.assertEqual(patches[0][2], {"title": "Source title changed"})
        self.assertEqual(patches[0][3]["If-Match"], "3")

    def test_wip_review_done_use_discovered_transitions(self):
        self.bootstrap()
        for status, phase, expected in [("in_progress", None, "WIP"), ("in_progress", "Review", "Review"), ("done", None, "Done")]:
            self.fake.task["status"] = status
            notes = json.loads(self.fake.task["notes"])
            notes["tracker_sync"] = {} if phase is None else {"phase": phase}
            self.fake.task["notes"] = json.dumps(notes)
            report = self.worker.tick(apply=True)
            self.assertEqual(report["status"], "changed", report)
            self.assertEqual(self.fake.card["idList"], CONFIG["trello"]["lists"][expected])
            self.assertEqual(self.fake.issue["fields"]["status"]["id"], CONFIG["jira"]["status_ids"][expected])
        self.assertFalse(self.fake.card["complete"])
        self.assertFalse(self.fake.card["dueComplete"])
        for method, path, body, headers in self.fake.writes():
            if "/trello" in path: self.assertEqual(set(body), {"idList", "desc"})
            if "/linear" in path: self.assertEqual(set(body["variables"]["input"]), {"description"})
            self.assertNotIn("key=", path); self.assertNotIn("token=", path)
        self.assertTrue(any("OAuth " in c[3].get("Authorization", "") for c in self.fake.calls if "/trello/" in c[1]))

    def test_unsupported_states_hold(self):
        self.bootstrap()
        for status in ("blocked", "cancelled"):
            self.fake.task["status"] = status
            self.assertEqual(self.worker.tick(apply=True)["status"], "hold")
        self.assertEqual(self.fake.writes(), [])

    def test_incompatible_explicit_phase_holds(self):
        self.fake.task["notes"] = json.dumps({**json.loads(self.fake.task["notes"]), "tracker_sync": {"phase": "Done"}})
        self.assertEqual(self.worker.tick(apply=True)["status"], "hold")
        self.assertEqual(self.fake.writes(), [])

    def test_bootstrap_rejects_mismatched_mirrors_before_any_write(self):
        self.fake.issue["fields"]["status"]["id"] = "10002"
        self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
        self.assertEqual(self.fake.writes(), [])
        self.assertFalse((Path(self.directory.name) / "checkpoint.json").exists())

    def test_scope_identity_and_source_marker_rejection(self):
        for target, field, value in [(self.fake.card, "idBoard", "wrong-board"), (self.fake.card, "closed", True), (self.fake.linear, "identifier", "BOO-99"), (self.fake.card, "desc", "no source markers")]:
            old = target[field]; target[field] = value
            self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
            self.assertEqual(self.fake.writes(), [])
            target[field] = old
        invalid = copy.deepcopy(CONFIG); invalid["task"]["number"] = 180
        with self.assertRaises(ValueError): self.sync.SyncWorker(invalid, Path(self.directory.name), self.adapters)

    def test_task_link_notes_and_external_lookup_identity_rejection(self):
        self.fake.task["notes"] = "{}"
        self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
        self.assertEqual(self.fake.writes(), [])

    def test_manual_mirror_drift_holds(self):
        self.bootstrap()
        self.fake.issue["fields"]["summary"] = "Manual mirror edit"
        self.fake.task["status"] = "in_progress"
        self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
        self.assertEqual(self.fake.writes(), [])

    def test_reread_detects_concurrent_drift(self):
        self.bootstrap()
        self.fake.card["name"] = "New source title"
        seen = [0]
        def hook(method, path, body):
            if method == "GET" and "/jira/" in path and not path.endswith("transitions"):
                seen[0] += 1
                if seen[0] == 2: self.fake.issue["fields"]["summary"] = "Concurrent manual title"
        self.fake.hook = hook
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(self.fake.issue["fields"]["summary"], "Concurrent manual title")
        self.assertFalse(any("/jira/" in c[1] for c in self.fake.writes()))

    def test_stale_task_version_refuses_write(self):
        self.bootstrap()
        self.fake.card["name"] = "New source title"
        def hook(method, path, body):
            if method == "PATCH": self.fake.task["version"] += 1
        self.fake.hook = hook
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertNotEqual(self.fake.task["title"], "New source title")

    def test_intent_is_durable_before_mutation_and_readback(self):
        checks = []
        def hook(method, path, body):
            if method == "PUT" or method == "POST" and "mutation" in (body or {}).get("query", ""):
                state = json.loads((Path(self.directory.name) / "checkpoint.json").read_text())
                checks.append(bool(state["pending"]))
        self.fake.hook = hook
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertTrue(checks); self.assertTrue(all(checks))
        state = json.loads((Path(self.directory.name) / "checkpoint.json").read_text())
        self.assertEqual(state["pending"], {})
        self.assertNotIn(self.fake.task["title"], json.dumps(state))
        for index, (method, path, body, _) in enumerate(self.fake.calls):
            if (method, path, body) not in [(c[0], c[1], c[2]) for c in self.fake.writes()]: continue
            following = self.fake.calls[index + 1:]
            if "/linear/" in path:
                self.assertTrue(any(c[0] == "POST" and "query PilotIssue" in c[2].get("query", "") for c in following))
            else:
                self.assertTrue(any(c[0] == "GET" and c[1].split("?")[0] == path.split("?")[0] for c in following))

    def test_lost_ack_recovers_by_readback_without_replay(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        self.fake.lost = "/transitions"
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(self.fake.issue["fields"]["status"]["id"], "10001")
        self.fake.calls.clear()
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "changed", result)
        self.assertFalse(any(c[0] == "POST" and "/transitions" in c[1] for c in self.fake.writes()))

    def test_partial_failure_recovers_other_providers(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        self.fake.failure = ("/linear", 503, None)
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(self.fake.card["idList"], CONFIG["trello"]["lists"]["WIP"])
        self.fake.calls.clear()
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertEqual(len(self.fake.writes()), 1)
        self.assertIn("/linear", self.fake.writes()[0][1])

    def test_rate_limit_defers_without_sleep_and_redacts_error(self):
        self.fake.failure = ("/trello", 429, "120")
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "deferred", result)
        self.assertNotIn("secret-token", json.dumps(result))
        count = len(self.fake.calls)
        self.assertEqual(self.worker.tick(apply=True)["status"], "deferred")
        self.assertEqual(len(self.fake.calls), count)

    def test_fresh_unrelated_descriptions_preserved(self):
        self.bootstrap()
        self.fake.card["desc"] = "New unrelated note\n" + self.fake.card["desc"]
        self.fake.linear["description"] += "\nNew evidence outside managed block"
        self.fake.task["status"] = "in_progress"
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertTrue(self.fake.card["desc"].startswith("New unrelated note\n"))
        self.assertTrue(self.fake.linear["description"].endswith("\nNew evidence outside managed block"))

    def test_nonblocking_lock_and_corrupt_journal_hold(self):
        import fcntl
        lock = open(Path(self.directory.name) / "worker.lock", "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.worker.tick(apply=True)["status"], "locked")
        self.assertEqual(self.fake.calls, [])
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        (Path(self.directory.name) / "checkpoint.json").write_text('{"unexpected":"bad"}')
        self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
        self.assertEqual(self.fake.writes(), [])

    def test_mid_tick_task_version_change_holds_remaining_writes(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        changed = [False]
        def hook(method, path, body):
            if method == "GET" and "/v1/tasks/" in path and not changed[0]:
                changed[0] = True
            elif method == "GET" and "/v1/tasks/" in path and changed[0]:
                self.fake.task["version"] += 1
        self.fake.hook = hook
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(self.fake.writes(), [])

    def test_mid_tick_blocked_authority_holds_remaining_writes(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        seen = [0]
        def hook(method, path, body):
            if method == "GET" and "/v1/tasks/" in path:
                seen[0] += 1
                if seen[0] == 3: self.fake.task["status"] = "blocked"
        self.fake.hook = hook
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "hold", result)
        self.assertEqual(self.fake.writes(), [])

    def test_read_rate_limit_before_bootstrap_persists_deferral(self):
        self.fake.read_failure = (429, "1800")
        self.fake.read_failure_count = 1
        self.assertEqual(self.worker.tick(apply=True)["status"], "deferred")
        count = len(self.fake.calls)
        self.assertEqual(self.worker.tick(apply=True)["status"], "deferred")
        self.assertEqual(len(self.fake.calls), count)

    def test_tasks_retry_after_preserves_server_delay_before_bootstrap(self):
        for status in (429, 503):
            with self.subTest(status=status):
                checkpoint = Path(self.directory.name) / "checkpoint.json"
                if checkpoint.exists(): checkpoint.unlink()
                self.fake.calls.clear()
                self.fake.read_failure = (status, "1800")
                self.fake.read_failure_count = 1
                started = time.time()
                result = self.worker.tick(apply=True)
                self.assertEqual(result["status"], "deferred", result)
                state = json.loads(checkpoint.read_text())
                self.assertGreaterEqual(state["deferred_until"], started + 1800)
                self.assertLess(state["deferred_until"], started + 1802)
                self.assertEqual(len(self.fake.calls), 1)
                self.assertEqual(self.worker.tick(apply=True)["status"], "deferred")
                self.assertEqual(len(self.fake.calls), 1)

    def test_jira_discovery_rechecks_authority_before_transition(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        self.fake.task["version"] += 1
        for change in ("blocked", "version", "source_title"):
            with self.subTest(change=change):
                task_before = copy.deepcopy(self.fake.task)
                title_before = self.fake.card["name"]
                self.fake.calls.clear()
                def hook(method, path, body):
                    if method == "GET" and path.endswith("/transitions"):
                        if change == "blocked": self.fake.task["status"] = "blocked"
                        elif change == "version": self.fake.task["version"] += 1
                        else: self.fake.card["name"] = "Source title changed during discovery"
                self.fake.hook = hook
                result = self.worker.tick(apply=True)
                self.assertEqual(result["status"], "hold" if change == "blocked" else "conflict", result)
                self.assertEqual(self.fake.writes(), [])
                self.fake.hook = None
                self.fake.task = task_before
                self.fake.card["name"] = title_before
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertEqual(self.fake.issue["fields"]["status"]["id"], "10001")

    def test_jira_discovery_revalidates_scope_and_source_markers(self):
        self.bootstrap()
        self.fake.task["status"] = "in_progress"
        for change in ("key", "project", "marker"):
            with self.subTest(change=change):
                original = copy.deepcopy(self.fake.issue)
                self.fake.calls.clear()
                def hook(method, path, body):
                    if method == "GET" and path.endswith("/transitions"):
                        if change == "key": self.fake.issue["key"] = "KAN-99"
                        elif change == "project": self.fake.issue["fields"]["project"]["id"] = "other-project"
                        else: self.fake.issue["fields"]["description"] = "markers removed during discovery"
                self.fake.hook = hook
                result = self.worker.tick(apply=True)
                self.assertEqual(result["status"], "conflict", result)
                self.assertEqual(self.fake.writes(), [])
                self.fake.hook = None
                self.fake.issue = original

    def test_reads_retry_only_once_and_writes_never_retry(self):
        self.fake.read_failure = (503, None)
        self.fake.read_failure_count = 1
        self.assertEqual(self.worker.tick()["status"], "dry_run")
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/v1/tasks/" + CONFIG["task"]["id"]]), 2)
        self.fake.calls.clear()
        self.fake.failure = ("/trello", 503, None)
        self.assertEqual(self.worker.tick(apply=True)["status"], "failure")
        self.assertEqual(len(self.fake.writes()), 1)

    def test_provider_redirect_is_not_followed(self):
        self.fake.redirect = True
        opener = LocalOpener(self.fake.origin)
        opener.real = urllib.request.build_opener(self.http.NoRedirect())
        transport = self.http.Http("https://api.trello.com", "synthetic-header", opener=opener)
        with self.assertRaises(self.http.SyncError) as error:
            transport.request("GET", "/1/cards/allowed", read=True)
        self.assertEqual(error.exception.code, "http_302")
        self.assertEqual(len(self.fake.calls), 1)

    def test_preflight_reports_missing_names_without_provider_reads(self):
        config = Path(self.directory.name) / "config.json"
        config.write_text(json.dumps(CONFIG))
        output = io.StringIO()
        with patch("_env.get_env", return_value=None), patch("tracker_sync.make_adapters", side_effect=AssertionError("unexpected provider client")), contextlib.redirect_stdout(output):
            code = self.sync.main(["--config", str(config), "--preflight", "--json"])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["missing"], ["LINEAR_API_KEY", "TRELLO_API_KEY", "TRELLO_API_TOKEN", "JIRA_EMAIL", "JIRA_API_TOKEN"])
        self.assertEqual(self.fake.calls, [])

    def test_preflight_rejects_missing_delegated_tasks_client(self):
        config = Path(self.directory.name) / "config.json"
        config.write_text(json.dumps(CONFIG))
        output = io.StringIO()
        with patch("_env.get_env", return_value="synthetic"), patch("task_store.open_task_store", side_effect=RuntimeError("private-client-token")), contextlib.redirect_stdout(output):
            code = self.sync.main(["--config", str(config), "--preflight", "--json"])
        self.assertEqual(code, 1)
        self.assertNotIn("private-client-token", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "failure")

    def test_quiet_noop_cli_produces_no_output(self):
        self.bootstrap()
        config = Path(self.directory.name) / "config.json"
        config.write_text(json.dumps(CONFIG))
        output = io.StringIO()
        with patch("_env.get_env", return_value="synthetic"), patch("task_store.open_task_store", return_value=self.adapters["tasks"].store), patch("tracker_sync.make_adapters", return_value=self.adapters), contextlib.redirect_stdout(output):
            code = self.sync.main(["--config", str(config), "--state-dir", self.directory.name, "--apply", "--quiet", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "")

    def test_tick_budget_stops_before_provider_calls(self):
        with patch("tracker_sync.time") as clock:
            clock.monotonic.side_effect = [0, 61]
            clock.time.return_value = time.time()
            result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["codes"], ["tick_deadline"])
        self.assertEqual(self.fake.calls, [])

    def test_absolute_deadline_interrupts_slow_drip_and_keeps_recoverable_intent(self):
        self.worker.budget_seconds = 0.35
        previous_handler = signal.getsignal(signal.SIGALRM)
        def hook(method, path, body):
            if path == "/linear/graphql" and "mutation" in body["query"]:
                self.fake.slow_drip = True
        self.fake.hook = hook
        started = time.monotonic()
        result = self.worker.tick(apply=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.8, "socket inactivity timeout allowed a slow response to overrun the absolute budget")
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["codes"], ["tick_deadline"])
        self.assertIn("trello", result["changed"], "deadline reporting discarded an already verified provider change")
        state = json.loads((Path(self.directory.name) / "checkpoint.json").read_text())
        self.assertIn("linear", state["pending"])
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.fake.hook = None
        self.worker.budget_seconds = 60
        self.fake.calls.clear()
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertEqual(self.fake.writes(), [])

    def test_deadline_during_final_checkpoint_preserves_verified_changes(self):
        self.worker.budget_seconds = 0.35
        previous_handler = signal.getsignal(signal.SIGALRM)
        save = self.worker.save
        def slow_final_save(state):
            if state["last_status"] == "changed":
                time.sleep(1)
            save(state)
        with patch.object(self.worker, "save", side_effect=slow_final_save):
            result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["codes"], ["tick_deadline"])
        self.assertEqual(result["changed"], ["trello", "linear"])
        self.assertEqual(result["planned"], ["trello", "linear"])
        state = json.loads((Path(self.directory.name) / "checkpoint.json").read_text())
        self.assertEqual(state["pending"], {})
        self.assertEqual(set(state["baseline"]), {"task_title", "jira_summary", "jira_status", "trello", "linear"})
        self.assertEqual(len(self.fake.writes()), 2)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.worker.budget_seconds = 60
        self.fake.calls.clear()
        recovered = self.worker.tick(apply=True)
        self.assertEqual(recovered["status"], "unchanged", recovered)
        self.assertEqual(recovered["changed"], [])
        self.assertEqual(self.fake.writes(), [])

    def test_final_success_budget_check_preserves_verified_changes(self):
        expired = [False]
        save = self.worker.save
        def final_save(state):
            save(state)
            if state["last_status"] == "changed":
                expired[0] = True
        with patch.object(self.worker, "save", side_effect=final_save), patch("tracker_sync.time", wraps=time) as clock:
            clock.monotonic.side_effect = lambda: time.monotonic() + (120 if expired[0] else 0)
            result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["codes"], ["tick_deadline"])
        self.assertEqual(result["changed"], ["trello", "linear"])
        self.assertEqual(result["planned"], ["trello", "linear"])
        self.fake.calls.clear()
        recovered = self.worker.tick(apply=True)
        self.assertEqual(recovered["status"], "unchanged", recovered)
        self.assertEqual(recovered["changed"], [])
        self.assertEqual(self.fake.writes(), [])

    def test_deadline_crossed_during_final_readback_cannot_report_success(self):
        expired = [False]
        def hook(method, path, body):
            if path == "/linear/graphql" and "mutation" not in body["query"] and any(c[0] == "POST" and "mutation" in c[2].get("query", "") for c in self.fake.calls if "/linear/" in c[1]):
                expired[0] = True
        self.fake.hook = hook
        with patch("tracker_sync.time", wraps=time) as clock:
            clock.monotonic.side_effect = lambda: time.monotonic() + (120 if expired[0] else 0)
            result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "failure", result)
        self.assertEqual(result["codes"], ["tick_deadline"])
        state = json.loads((Path(self.directory.name) / "checkpoint.json").read_text())
        self.assertIn("linear", state["pending"])
        self.fake.hook = None
        self.fake.calls.clear()
        self.assertEqual(self.worker.tick(apply=True)["status"], "changed")
        self.assertEqual(self.fake.writes(), [])

    def test_absolute_deadline_refuses_non_main_thread_before_io(self):
        results = []
        thread = threading.Thread(target=lambda: results.append(self.worker.tick(apply=True)))
        thread.start(); thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "failure", results)
        self.assertEqual(results[0]["codes"], ["deadline_context_unsupported"])
        self.assertEqual(self.fake.calls, [])

    def test_absolute_deadline_preserves_an_existing_timer(self):
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        handler = lambda signum, frame: None
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, 5)
        try:
            result = self.worker.tick(apply=True)
            self.assertEqual(result["status"], "failure", result)
            self.assertEqual(result["codes"], ["deadline_timer_in_use"])
            self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 4)
            self.assertEqual(self.fake.calls, [])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)

    def test_jira_reread_checks_all_managed_fields_before_mutation(self):
        self.bootstrap()
        self.fake.card["name"] = "New source title"
        seen = [0]
        def hook(method, path, body):
            if method == "GET" and "/jira/" in path and not path.endswith("transitions"):
                seen[0] += 1
                if seen[0] == 2: self.fake.issue["fields"]["status"]["id"] = "10002"
        self.fake.hook = hook
        result = self.worker.tick(apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertFalse(any("/jira/" in c[1] for c in self.fake.writes()))

    def test_duplicate_managed_markers_hold(self):
        self.bootstrap()
        self.fake.linear["description"] += "\n" + CONFIG["managed_block"]["start"]
        self.assertEqual(self.worker.tick(apply=True)["status"], "conflict")
        self.assertEqual(self.fake.writes(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
