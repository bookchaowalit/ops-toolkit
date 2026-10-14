"""Fixed-origin, bounded provider clients for the existing tracker pilot."""
from __future__ import annotations

import base64
import email.utils
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class SyncError(Exception):
    """Only bounded operational codes cross the client boundary."""
    def __init__(self, code, *, status="failure", retry_after=0):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_delay(value):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = 900
    return max(1, min(delay, 86400))


class Http:
    def __init__(self, origin, authorization, *, opener=None):
        if origin not in ("https://api.trello.com", "https://api.linear.app", "https://bookchaowalit.atlassian.net"):
            raise ValueError("provider_origin_not_allowed")
        self.origin = origin
        self.authorization = authorization
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.deadline = None

    def request(self, method, path, body=None, *, read=False):
        if not path.startswith("/") or path.startswith("//") or urllib.parse.urlsplit(path).netloc:
            raise ValueError("provider_path_not_allowed")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(2 if read else 1):
            remaining = self.deadline - time.monotonic() if self.deadline is not None else 8
            if remaining <= 0: raise SyncError("tick_deadline")
            req = urllib.request.Request(self.origin + path, method=method, data=data,
                headers={"Authorization": self.authorization, "Content-Type": "application/json", "Accept": "application/json"})
            try:
                with self.opener.open(req, timeout=min(8, remaining)) as response:
                    raw = response.read(1024 * 1024 + 1)
                    if len(raw) > 1024 * 1024: raise SyncError("response_too_large")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as error:
                code, retry = error.code, error.headers.get("Retry-After")
                error.close()
                if code == 429 or retry:
                    raise SyncError("http_rate_limited", status="deferred", retry_after=retry_delay(retry)) from None
                if read and code in (500, 502, 503, 504) and attempt == 0:
                    continue
                raise SyncError("http_" + str(code), status="conflict" if code in (409, 412) else "failure") from None
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                if read and attempt == 0: continue
                raise SyncError("transport_unavailable") from None
            except (ValueError, UnicodeError):
                raise SyncError("invalid_provider_response") from None


class TasksAdapter:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.deadline = None

    def call(self, method, path, body=None, headers=None):
        from task_store import TaskStoreError
        for attempt in range(2 if method == "GET" else 1):
            remaining = self.deadline - time.monotonic() if self.deadline is not None else 8
            if remaining <= 0: raise SyncError("tick_deadline")
            self.store.timeout = min(8, remaining)
            try:
                return self.store._call(method, path, body=body, headers=headers)
            except TaskStoreError as error:
                retry = error.retry_after
                if error.status == 429 or retry and error.status in (500, 502, 503, 504):
                    raise SyncError("tasks_http_" + str(error.status), status="deferred", retry_after=retry_delay(retry) if retry else 900) from None
                if method == "GET" and error.status in (500, 502, 503, 504) and attempt == 0: continue
                raise SyncError("tasks_http_" + str(error.status), status="conflict" if error.status in (409, 412) else "failure") from None
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                if method == "GET" and attempt == 0: continue
                raise SyncError("tasks_transport_unavailable") from None

    def read(self):
        return self.call("GET", "/v1/tasks/" + self.config["task"]["id"])

    def read_external(self):
        return self.call("GET", "/v1/tasks/external/trello/" + self.config["trello"]["card_id"])

    def write(self, key, desired, current):
        self.call("PATCH", "/v1/tasks/" + self.config["task"]["id"], {"title": desired}, {"If-Match": str(current["version"])})


class TrelloAdapter:
    def __init__(self, config, http):
        self.config, self.http = config, http
        self.path = "/1/cards/" + config["trello"]["card_id"]

    def read(self):
        return self.http.request("GET", self.path + "?fields=id,idBoard,idList,name,desc,closed", read=True)

    def read_board(self):
        return self.http.request("GET", "/1/boards/" + self.config["trello"]["board_id"] + "?fields=id,idOrganization", read=True)

    def write(self, key, desired, current):
        self.http.request("PUT", self.path, {"idList": desired["list"], "desc": desired["description"]})


class JiraAdapter:
    def __init__(self, config, http):
        self.config, self.http = config, http
        self.path = "/rest/api/3/issue/" + config["jira"]["issue_key"]

    def read(self):
        return self.http.request("GET", self.path + "?fields=summary,status,description,updated,project", read=True)

    def write(self, key, desired, current, *, guard):
        choices = []
        if key == "jira_status":
            transitions = self.http.request("GET", self.path + "/transitions", read=True)
            choices = [t["id"] for t in transitions.get("transitions", []) if str(t.get("to", {}).get("id")) == desired]
            if len(choices) != 1: raise SyncError("jira_transition_unavailable", status="hold")
        # All discovery and target reads precede the final canonical authority guard.
        fresh = self.read()
        if fresh.get("id") != current.get("id") or any(fresh.get("fields", {}).get(k) != current.get("fields", {}).get(k) for k in ("summary", "status")):
            raise SyncError("jira_concurrent_drift", status="conflict")
        guard(fresh)
        if key == "jira_summary":
            self.http.request("PUT", self.path, {"fields": {"summary": desired}})
        else:
            self.http.request("POST", self.path + "/transitions", {"transition": {"id": choices[0]}})


class LinearAdapter:
    def __init__(self, config, http):
        self.config, self.http = config, http

    def read(self):
        result = self.http.request("POST", "/graphql", {"query": "query PilotIssue($id: String!) { issue(id: $id) { id identifier description team { id } } }", "variables": {"id": self.config["linear"]["issue_id"]}}, read=True)
        if result.get("errors"): raise SyncError("linear_query_failed")
        issue = result.get("data", {}).get("issue")
        if not isinstance(issue, dict): raise SyncError("linear_issue_unavailable")
        return issue

    def write(self, key, desired, current):
        result = self.http.request("POST", "/graphql", {"query": "mutation PilotEvidence($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }", "variables": {"id": self.config["linear"]["issue_id"], "input": {"description": desired}}})
        if result.get("errors") or result.get("data", {}).get("issueUpdate", {}).get("success") is not True:
            raise SyncError("linear_update_unverified")


def make_adapters(config, credentials, store, *, opener=None):
    # Tokens are header-only. Values never appear in reports, URLs or exception text.
    oauth = 'OAuth oauth_consumer_key="' + credentials["TRELLO_API_KEY"] + '", oauth_token="' + credentials["TRELLO_API_TOKEN"] + '"'
    jira = base64.b64encode((credentials["JIRA_EMAIL"] + ":" + credentials["JIRA_API_TOKEN"]).encode()).decode()
    for value in credentials.values():
        if any(char in value for char in ("\r", "\n", '"')): raise ValueError("invalid_credential_format")
    return {
        "tasks": TasksAdapter(config, store),
        "trello": TrelloAdapter(config, Http("https://api.trello.com", oauth, opener=opener)),
        "jira": JiraAdapter(config, Http(config["jira"]["origin"], "Basic " + jira, opener=opener)),
        "linear": LinearAdapter(config, Http("https://api.linear.app", credentials["LINEAR_API_KEY"], opener=opener))}
