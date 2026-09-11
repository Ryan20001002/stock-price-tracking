"""
Fetch outstanding-share/unit-count data for each ticker and join it with
the price history already pulled by price_data.py to compute an
approximate daily market value (price x shares outstanding) per ticker.

For an ordinary stock, "market value" here is the standard market
capitalization. For an ETF, it's a proxy for assets under management
(AUM) -- unit price x units outstanding -- NOT a company's market cap in
the usual sense.

Data source, and why
---------------------
Primary source: TWSE's own OpenAPI dataset t187ap47_L ("基金基本資料彙總表"
-- fund basic information summary), field "發行單位數/轉換數" (units
outstanding). This covers ETFs/funds directly from the source that
actually tracks them, which turned out to matter: `yfinance` was tried
first and returned nothing at all for any of 0050/00878/006208 (no
get_shares_full() history, no .info snapshot either) -- ETF unit counts
apparently aren't something Yahoo tracks well for TWSE-listed funds.
TWSE's own dataset does have it, confirmed live for all 3 watchlist
tickers (as of 2026-09-07): 0050 = 22,067,500,000 units, 00878 =
18,830,290,000 units, 006208 = 1,860,540,000 units.

Fallback: yfinance (get_shares_full(), then .info['sharesOutstanding']),
for any ticker not found in the TWSE fund dataset -- e.g. an ordinary
stock, which wouldn't appear in a *fund* dataset.

Since TWSE's dataset only ever gives "the current figure as of today,"
there's no retroactive history available from it. Instead, this script
accumulates its own history over time: each run adds today's figure to
data/shares/<code>.csv (keyed by date, one row per day you've run it),
so running it regularly (e.g. daily, alongside the other fetchers)
builds up a real day-by-day series going forward.

Usage:
    python market_value_data.py
Requires data/prices/<code>.csv to already exist (run price_data.py, or
`main.py --prices`, first). Also runnable via `python main.py
--market-value`.

CAVEATS
-------
- No retroactive history: the very first time you run this, you get
  exactly one data point (today's date). data/market_value/<code>.csv
  only gets a row for dates on or after your first collected snapshot --
  shares_as_of() forward-fills the most recent known share count onto
  each trading day, but has nothing to fill in with for dates before
  that first snapshot, so those earlier trading days are simply skipped
  (not backfilled with an approximation). The file fills in and gets
  more complete/accurate the longer you keep running this regularly.
- Even with a real day-by-day series, ETF units outstanding can change
  intraday/between report dates as authorized participants create/redeem
  units -- daily granularity is a reasonable approximation, not a precise
  intraday figure.
"""

import csv
import os
import time
from datetime import date

import yfinance as yf

from config import WATCHLIST, DATA_DIR, REQUEST_DELAY_SECONDS
from twse_client import get_json

TWSE_FUND_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
SUFFIXES_TO_TRY = [".TW", ".TWO"]
SHARES_HISTORY_START = "2010-01-01"  # only used for the yfinance fallback


def _roc_to_iso(roc_str):
    """Convert TWSE's ROC report date ('1150903') to an ISO date string."""
    roc_str = roc_str.strip()
    if len(roc_str) == 7:
        y, m, d = int(roc_str[:3]), int(roc_str[3:5]), int(roc_str[5:7])
    elif len(roc_str) == 6:
        y, m, d = int(roc_str[:2]), int(roc_str[2:4]), int(roc_str[4:6])
    else:
        raise ValueError(f"unexpected ROC date format: {roc_str!r}")
    return date(y + 1911, m, d).isoformat()


def fetch_twse_fund_units():
    """One call covering every TWSE fund/ETF. Returns dict:
    code -> (iso_date_str, units_int). Empty dict on failure."""
    payload = get_json(TWSE_FUND_URL)
    if not payload:
        return {}
    result = {}
    for row in payload:
        code = row.get("基金代號")
        units_str = row.get("發行單位數/轉換數")
        report_date_str = row.get("出表日期")
        if not code or not units_str or not report_date_str:
            continue
        try:
            units = int(units_str)
            iso_date = _roc_to_iso(report_date_str)
        except (ValueError, TypeError):
            continue
        result[code] = (iso_date, units)
    return result


def _fetch_shares_outstanding_yfinance(symbol):
    """yfinance fallback for tickers not in the TWSE fund dataset (e.g.
    ordinary stocks). Returns (rows, mode); rows is a sorted list of
    (date, shares_int); mode is 'history', 'snapshot_only', or
    'unavailable'."""
    ticker = yf.Ticker(symbol)
    try:
        series = ticker.get_shares_full(start=SHARES_HISTORY_START)
    except Exception as e:
        print(f"    [warn] get_shares_full failed for {symbol}: {e}")
        series = None

    if series is not None and len(series) > 0:
        rows = []
        for idx, val in series.items():
            d = idx.date() if hasattr(idx, "date") else idx
            rows.append((d, int(val)))
        rows.sort()
        return rows, "history"

    try:
        info = ticker.info or {}
    except Exception as e:
        print(f"    [warn] .info failed for {symbol}: {e}")
        info = {}
    snapshot = info.get("sharesOutstanding")
    if snapshot:
        return [(date.today(), int(snapshot))], "snapshot_only"
    return [], "unavailable"


def fetch_ticker_shares_yfinance(code):
    for suffix in SUFFIXES_TO_TRY:
        symbol = f"{code}{suffix}"
        rows, mode = _fetch_shares_outstanding_yfinance(symbol)
        time.sleep(REQUEST_DELAY_SECONDS)
        if rows:
            return symbol, rows, mode
    return f"{code}{SUFFIXES_TO_TRY[0]}", [], "unavailable"


def load_existing_shares(code):
    """Returns dict: iso_date_str -> (shares_int, source_str)."""
    path = os.path.join(DATA_DIR, "shares", f"{code}.csv")
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows[row["date"]] = (int(row["shares_outstanding"]), row["source"])
    return rows


def save_shares(code, rows_by_date):
    path = os.path.join(DATA_DIR, "shares", f"{code}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "shares_outstanding", "source"])
        for d in sorted(rows_by_date):
            shares, source = rows_by_date[d]
            writer.writerow([d, shares, source])


def load_price_series(code):
    path = os.path.join(DATA_DIR, "prices", f"{code}.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("Close"):
                continue
            y, m, d = row["Date"].split("-")
            rows.append((date(int(y), int(m), int(d)), float(row["Close"])))
    rows.sort()
    return rows


def shares_as_of(shares_history, target_date):
    """shares_history: sorted list of (date, shares). Most recent figure
    known as of target_date (forward-fill)."""
    best = None
    for d, shares in shares_history:
        if d > target_date:
            break
        best = shares
    return best


def run():
    print("[shares] fetching TWSE fund dataset (covers ETFs/funds in one call)...")
    twse_units = fetch_twse_fund_units()
    print(f"  -> {len(twse_units)} funds found in the TWSE dataset\n")

    for stock in WATCHLIST:
        code = stock["code"]
        print(f"[shares] {code} {stock['name']}")
        existing = load_existing_shares(code)

        if code in twse_units:
            report_date, units = twse_units[code]
            existing[report_date] = (units, "twse_fund_dataset")
            save_shares(code, existing)
            print(f"  -> TWSE fund dataset: {units:,} units as of {report_date} "
                  f"(source=twse_fund_dataset); {len(existing)} date(s) on file now")
        else:
            symbol, rows, mode = fetch_ticker_shares_yfinance(code)
            if not rows:
                print(f"  [warn] not in TWSE's fund dataset, and no yfinance data via {symbol} either "
                      f"-- skipping {code} (see this script's CAVEATS)")
                print()
                continue
            for d, shares in rows:
                existing[d.isoformat()] = (shares, mode)
            save_shares(code, existing)
            print(f"  -> yfinance {symbol} (source={mode}): {len(rows)} record(s); "
                  f"{len(existing)} date(s) on file now")

        shares_history = sorted((date.fromisoformat(d), v[0]) for d, v in existing.items())

        prices = load_price_series(code)
        if not prices:
            print(f"  [warn] no price data found for {code} (run price_data.py first) -- skipping market value join")
            print()
            continue

        out_path = os.path.join(DATA_DIR, "market_value", f"{code}.csv")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        n_written = 0
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Close", "SharesOutstanding", "MarketValue"])
            for d, close in prices:
                shares = shares_as_of(shares_history, d)
                if shares is None:
                    continue
                writer.writerow([d.isoformat(), close, shares, round(close * shares, 2)])
                n_written += 1
        print(f"  -> {n_written} daily market-value row(s) saved to {out_path}")
        print()


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise. Added 2026-09-11, see
    # price_data.py's __main__ block for why.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("market_value")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
