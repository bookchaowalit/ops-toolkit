"""
Shared HTTP helpers for Solo Empire Python scripts.
Provides retry-capable HTTP requests and Google OAuth token refresh.

Usage in scripts:
    from _http import api_request, get_access_token
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error

# ── Defaults ────────────────────────────────────────────────
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 2
DEFAULT_TIMEOUT = 30

# Transient HTTP status codes that should be retried
RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


class GoogleOAuthError(RuntimeError):
    """Safe, machine-readable Google OAuth failure.

    The provider response can contain details that should never reach logs.
    Keep only a bounded public error code for cron, alerts, and operators.
    """

    def __init__(self, code: str):
        self.code = code if code in {
            "invalid_grant", "invalid_client", "invalid_request",
            "unauthorized_client", "http_400", "http_401", "http_403",
            "http_429", "http_500", "http_502", "http_503",
        } else "unknown"
        super().__init__(f"google_oauth_{self.code}")


def api_request(url, data=None, headers=None, method=None,
                retries=DEFAULT_RETRIES, retry_delay=DEFAULT_RETRY_DELAY,
                timeout=DEFAULT_TIMEOUT):
    """HTTP request with retry on transient failures.

    Args:
        url: Target URL.
        data: Request body (bytes or None).
        headers: Dict of HTTP headers.
        method: HTTP method string (GET, POST, etc.).
        retries: Max number of retries on transient failures.
        retry_delay: Base delay between retries (multiplied by attempt+1).
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response (dict/list).

    Raises:
        urllib.error.HTTPError: On non-retryable HTTP errors or exhausted retries.
        urllib.error.URLError: On network errors after retries exhausted.
        RuntimeError: With last error message if all retries fail.
    """
    last_err = None
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            last_err = f"HTTP {e.code}"
            # Preserve only a safe provider error code for callers.  The body
            # is intentionally not re-raised or logged because token endpoint
            # responses are external/authentication material.
            try:
                provider_error = json.loads(body).get("error")
            except (TypeError, json.JSONDecodeError):
                provider_error = None
            if provider_error in {
                "invalid_grant", "invalid_client", "invalid_request",
                "unauthorized_client",
            }:
                setattr(e, "safe_provider_error", provider_error)
            if e.code in RETRYABLE_STATUS_CODES and attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise
    raise RuntimeError(last_err)


def get_access_token(client_id, client_secret, refresh_token):
    """Refresh and return a Google OAuth2 access token.

    Args:
        client_id: Google OAuth client ID.
        client_secret: Google OAuth client secret.
        refresh_token: Google OAuth refresh token.

    Returns:
        Access token string.
    """
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    try:
        resp = api_request("https://oauth2.googleapis.com/token", data=data)
    except urllib.error.HTTPError as exc:
        safe_code = getattr(exc, "safe_provider_error", None) or f"http_{exc.code}"
        raise GoogleOAuthError(safe_code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise GoogleOAuthError("unknown") from None
    if not isinstance(resp, dict) or not resp.get("access_token"):
        raise GoogleOAuthError("invalid_request")
    return resp["access_token"]
