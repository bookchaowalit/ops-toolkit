#!/usr/bin/env python3
"""Explicit RunPod Stop Pod hook for the vLLM idle guard.

This is a provider mutation boundary. It is intentionally disabled unless an
operator sets RUNPOD_AUTO_STOP_ENABLED=true and supplies the Pod ID and API key
through the runtime environment. It never prints the key or the provider body.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request


RUNPOD_STOP_URL = "https://rest.runpod.io/v1/pods/{pod_id}/stop"
POD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")


class StopHookError(RuntimeError):
    """Raised when the explicit provider stop contract is not met."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):  # noqa: D401
        raise StopHookError("redirect_refused")


def stop_pod(env: dict[str, str] | None = None, *, opener_factory=urllib.request.build_opener) -> dict[str, str]:
    env = os.environ if env is None else env
    if env.get("RUNPOD_AUTO_STOP_ENABLED", "").strip().lower() != "true":
        raise StopHookError("runpod_autostop_not_enabled")
    pod_id = env.get("RUNPOD_POD_ID", "").strip()
    if not POD_ID.fullmatch(pod_id):
        raise StopHookError("runpod_pod_id_invalid")
    token = env.get("RUNPOD_API_KEY", "").strip()
    if not token:
        raise StopHookError("runpod_api_key_unavailable")
    if len(token) > 512 or any(char in token for char in "\r\n"):
        raise StopHookError("runpod_api_key_invalid")
    request = urllib.request.Request(
        RUNPOD_STOP_URL.format(pod_id=pod_id),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    opener = opener_factory(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise StopHookError(f"runpod_stop_http_{response.status}")
            response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise StopHookError(f"runpod_stop_http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise StopHookError(f"runpod_stop_network_{type(exc.reason).__name__}") from exc
    except TimeoutError as exc:
        raise StopHookError("runpod_stop_timeout") from exc
    except OSError as exc:
        raise StopHookError(f"runpod_stop_network_{type(exc).__name__}") from exc
    return {"status": "stopped", "pod_id": pod_id}


def main() -> int:
    try:
        result = stop_pod()
    except StopHookError as exc:
        print(f"runpod-stop-hook: {exc}", file=sys.stderr)
        return 1
    print(f"runpod_pod_stop={result['status']} pod_id={result['pod_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
