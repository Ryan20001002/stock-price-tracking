"""
Thin, rate-limit-aware client for TWSE's public data endpoints.

Two families of endpoint are used elsewhere in this project:

1. The newer TWSE OpenAPI (https://openapi.twse.com.tw/v1/...) -- clean
   JSON, no API key, used for dividend data and today's all-stock prices.
2. The legacy "afterTrading" report endpoints
   (https://www.twse.com.tw/rwd/zh/...) -- used for per-stock monthly
   historical price data, which the OpenAPI doesn't provide.

Both are free and require no authentication, but TWSE will temporarily
block an IP that requests too quickly, so every call here is throttled
and retried with backoff.

Uses a single shared requests.Session() (module-level, below) rather than
a fresh connection per call -- TCP/TLS handshakes are reused across the
many sequential calls price_data.py makes to the same TWSE host, which
shaves a small but real amount of time off a full price-history backfill
without changing the request rate TWSE actually sees (REQUEST_DELAY_SECONDS
still applies exactly as before). This is safe under the concurrent
fetching main.py/app.py can now do too (see their docstrings) since
price_data.py is the only caller of this module that ever runs as part of
a concurrent job group -- there's no concurrent use of this session from
multiple threads at once.
"""

import time
import requests

from config import REQUEST_DELAY_SECONDS

HEADERS = {
    # A plain "python-requests" UA is sometimes rejected; pretend to be a
    # normal browser.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def get_json(url, params=None, max_retries=4, timeout=20):
    """GET a URL and return parsed JSON, or None if it never succeeds.

    Retries on network errors, non-200 responses, and invalid JSON with
    exponential backoff. Always sleeps REQUEST_DELAY_SECONDS after a
    successful call so we don't hammer TWSE.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as e:
                    last_error = f"invalid JSON: {e}"
                else:
                    time.sleep(REQUEST_DELAY_SECONDS)
                    return data
            else:
                last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_error = str(e)

        wait = REQUEST_DELAY_SECONDS * (2 ** (attempt - 1))
        print(f"  [warn] request failed ({last_error}); retrying in {wait:.1f}s "
              f"(attempt {attempt}/{max_retries}) -- {url}")
        time.sleep(wait)

    print(f"  [error] giving up on {url} after {max_retries} attempts: {last_error}")
    return None
