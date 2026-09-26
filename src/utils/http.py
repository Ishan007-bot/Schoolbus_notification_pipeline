"""HTTP GET with retries and backoff. Every network call in the pipeline goes through here."""
import logging
import time

import requests

log = logging.getLogger("http")

# Worth retrying: rate limiting and server-side errors. Anything else (404, 400) won't fix itself.
RETRY_STATUS = {429, 500, 502, 503, 504}


class HTTPFailure(Exception):
    """The request failed and retrying didn't help."""


def get(url, params=None, *, http_cfg, session=requests, sleep=time.sleep):
    backoffs = list(http_cfg["backoff_seconds"])[: http_cfg["retries"]]
    attempts = len(backoffs) + 1
    error = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=http_cfg["timeout_seconds"])
        except (requests.ConnectionError, requests.Timeout) as e:
            error = f"{type(e).__name__}: {e}"
        else:
            if response.status_code < 400:
                return response
            error = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUS:
                raise HTTPFailure(f"{url} -> {error} (not retried)")

        if attempt < attempts:
            wait = backoffs[attempt - 1]
            log.warning("attempt %d/%d failed (%s); retrying in %ss", attempt, attempts, error, wait)
            sleep(wait)

    raise HTTPFailure(f"{url} -> {error} after {attempts} attempts")
