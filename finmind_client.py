"""
Thin, rate-limit-aware client for FinMind's public data API
(https://api.finmindtrade.com/api/v4/data) -- used by institutional_data.py
to fetch 三大法人買賣超 (institutional investor net buy/sell) data.

Why FinMind instead of TWSE's own T86 report (2026-09-13, by explicit
request after a "the institutional fetch is too slow" report): see
institutional_data.py's module docstring for the full comparison. In
short, TWSE's T86 report returns EVERY listed stock for ONE calendar day
per call, so a multi-year backfill costs one request PER TRADING DAY
(~750 for a 3-year window) regardless of watchlist size. FinMind's
TaiwanStockInstitutionalInvestorsBuySell dataset is queried the other way
around -- one call per TICKER covers an entire date range -- so the same
backfill costs one request PER WATCHLIST TICKER instead, which for a
watchlist of a handful of tickers is dramatically fewer requests and
finishes in seconds rather than tens of minutes.

Trade-off, worth keeping in mind for a dissertation's methodology
section: FinMind is a third-party aggregator, not TWSE/TPEx directly.
It's a well-established, widely used Taiwan market data provider that
itself sources this data from TWSE/TPEx (its docs' own worked example
for this exact dataset uses 0050), not an independent estimate -- but
it is one hop further from the primary source than institutional_data.py
used to be. If a number here ever looks wrong, cross-checking a specific
date against TWSE's own T86 report directly (https://www.twse.com.tw/
zh/trading/fund/T86.html) is the way to verify it.

No API key is required for this dataset at FinMind's free/anonymous
tier (per their published limits: 300 requests/hour anonymous, 600/hour
with a free registered token) -- comfortably enough for a per-ticker
query pattern with any realistically sized watchlist. An optional free
token can be set via st.secrets["finmind"]["token"] (see
.streamlit/secrets.toml.example) if it's ever needed; sign up at
https://finmindtrade.com to get one. Read the same
lazy-import-streamlit-or-return-None way as market_data_sync.py's
_github_config(), so this stays a no-op (falls back to the anonymous
rate limit) when streamlit isn't configured or installed.

NOT YET VERIFIED AGAINST LIVE DATA: this client was built from FinMind's
published documentation (schema, parameter names, rate limits), not from
a live test call -- this environment has no network path to
api.finmindtrade.com to test against directly. The exact shape of an
error response (rate-limit-exceeded, invalid ticker, etc.) is inferred
from their docs and community reports, not observed firsthand. The
first real run is the actual test; if it behaves unexpectedly, the
`last_error` text printed on failure is FinMind's own message and is the
first thing to read.
"""

import time

import requests

DATA_URL = "https://api.finmindtrade.com/api/v4/data"

# FinMind's own limit is per-HOUR (300-600 requests), not per-second like
# TWSE's -- there's no equivalent need to space out individual calls the
# way twse_client.py's REQUEST_DELAY_SECONDS=1.5 does. This is just
# good manners between our own consecutive calls (one per watchlist
# ticker), not a measured "this is the exact safe rate" value.
REQUEST_DELAY_SECONDS = 0.3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def _token():
    """Optional FinMind token for the higher 600/hour registered rate
    limit -- see module docstring. Returns None (anonymous, 300/hour)
    whenever [finmind] isn't configured in secrets, or streamlit can't
    be imported at all (e.g. a plain `python main.py` run in an
    environment without streamlit installed); never raises."""
    try:
        import streamlit as st
        return st.secrets["finmind"]["token"]
    except Exception:
        return None


def get_json(params, max_retries=4, timeout=20):
    """GET https://api.finmindtrade.com/api/v4/data with `params` (plus
    the token, if configured) and return the parsed JSON envelope --
    expected shape {"status": 200, "msg": "success", "data": [...]} per
    FinMind's docs -- or None if it never succeeds after retries.

    Retries on network errors, non-200 HTTP responses, invalid JSON, AND
    a non-200 `status` field INSIDE an otherwise-200 HTTP response --
    FinMind reports some errors (e.g. exceeding your plan's rate limit
    or data-access level) that way rather than with an HTTP error code,
    per their docs and community reports. A `status` key that's simply
    absent from the payload is treated as success (not every FinMind
    response is confirmed to include one)."""
    token = _token()
    if token:
        params = {**params, "token": token}

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = _session.get(DATA_URL, params=params, timeout=timeout)
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError as e:
                    last_error = f"invalid JSON: {e}"
                else:
                    inner_status = payload.get("status")
                    if inner_status in (None, 200):
                        time.sleep(REQUEST_DELAY_SECONDS)
                        return payload
                    last_error = f"FinMind status {inner_status}: {payload.get('msg')}"
            else:
                last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_error = str(e)

        # +1 on top of the usual doubling: FinMind's limit is hourly, so
        # a fast retry loop wouldn't actually help recover from a
        # rate-limit error the way it can for a transient network blip --
        # this just gives a little more room than twse_client.py's
        # backoff without pretending a few extra seconds fixes an
        # hourly quota.
        wait = REQUEST_DELAY_SECONDS * (2 ** (attempt - 1)) + 1
        print(f"  [warn] FinMind request failed ({last_error}); retrying in {wait:.1f}s "
              f"(attempt {attempt}/{max_retries}) -- params={params}")
        time.sleep(wait)

    print(f"  [error] giving up on FinMind after {max_retries} attempts: {last_error} -- params={params}")
    return None
