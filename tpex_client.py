"""
Thin, rate-limit-aware client for TPEx's public "after-trading" report
endpoints (www.tpex.org.tw/web/stock/aftertrading/...) -- same idea and
same retry/backoff shape as twse_client.py, but kept as a SEPARATE module
rather than reused directly. This matches this project's established
convention of not cross-importing between different data sources' client
code (see market_index_data.py's own _roc_to_iso()/_num(), duplicated
locally rather than imported from price_data.py, for the same reasoning):
the two exchanges' endpoints have already diverged in ways (headers, date
format, response envelope) that make sharing a real constraint, not just
duplicated convenience code.

Added 2026-09-15, for TPEx's own free whole-market daily turnover report
(st41_result.php, "日成交量值指數" -- TPEx's equivalent of TWSE's FMTQIK).
Found via web research (WebFetch/WebSearch could not reach tpex.org.tw
directly from this sandboxed environment -- every path under /web/ 403s,
confirmed repeatedly; only the bare domain root loads), so the endpoint
and its exact response shape were CONFIRMED LIVE the same day by the user
opening the URL directly in their own browser (normal, unsandboxed
internet access) and pasting back the real JSON. See
market_index_data.py's _fetch_tpex_turnover() for the confirmed field
layout and units.

UNTESTED FROM THIS ENVIRONMENT: the retry/backoff logic, headers, and
error handling below follow the same standard as twse_client.py, but have
never actually executed a real request that reached tpex.org.tw (this
sandbox can't -- see above). The user's own machine has normal internet
access, so this should just work there -- but if the first live run gets
repeated failures specifically against this endpoint (not a general
network problem), double-check whether TPEx expects a different
Referer/User-Agent than what's set below before assuming the endpoint
itself changed or moved.
"""

import time
import requests

from config import REQUEST_DELAY_SECONDS

HEADERS = {
    # A plain "python-requests" UA is sometimes rejected; pretend to be a
    # normal browser -- same reasoning as twse_client.py's own HEADERS.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    # A same-site Referer, matching what a real browser loading the report
    # page (st41.php) would send -- twse_client.py needed the equivalent
    # for TWSE's T86 report; harmless to send even if TPEx turns out not
    # to require it.
    "Referer": "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41.php?l=zh-tw",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def get_json(url, params=None, max_retries=4, timeout=20):
    """GET a URL and return parsed JSON, or None if it never succeeds.

    Identical retry/backoff strategy to twse_client.get_json -- retries on
    network errors, non-200 responses, and invalid JSON with exponential
    backoff, and always sleeps REQUEST_DELAY_SECONDS after a successful
    call so we don't hammer TPEx's server any harder than this project
    already treats TWSE's.
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
