"""
Fetch daily OHLC history for Taiwan's two headline market indices and
keep a running CSV per index under data/market_index/<code>.csv:

    TAIEX  -- the TWSE main-board weighted index (台股加權指數)
    TPEX   -- the Taipei Exchange (TPEx, formerly OTC/GreTai) composite
              index (櫃買指數) -- Taiwan's "second board", covering
              stocks listed on the over-the-counter market rather than
              the TWSE-listed market price_data.py/the rest of this
              project already tracks.

Added 2026-09-15, by explicit request ("add a page to store TAIEX and
TSEA"). "TSEA" isn't a real ticker/index name -- confirmed via a
clarifying question that the user meant the TPEx/OTC index above, so
that's what's implemented here; TSEA is kept only as a comment/alias so
this is easy to find later if that name comes up again.

Unlike every other fetch script in this project (price_data.py,
dividend_data.py, institutional_data.py, market_value_data.py), these
two are market-WIDE index values, not per-ticker data for something in
config.WATCHLIST -- there's no "WATCHLIST loop" here, and no TWSE
STOCK_DAY-style report for an index itself, so this uses yfinance
instead (the same library already used elsewhere in this project, for
splits.py's split data and market_value_data.py's shares-outstanding
fallback).

CAVEAT, consistent with every other yfinance-based addition in this
project (see splits.py's and institutional_data.py's/finmind_client.py's
docstrings for the same situation): the sandboxed environment this was
built in has no network path to Yahoo Finance to test against live
(outbound HTTPS there is allowlisted to package registries only), so
this was built and unit-tested against a MOCKED yfinance response, not
an observed real one. `^TWII` (TAIEX) is the same ticker family this
project's other yfinance calls already use (an ordinary equity/index
symbol with a caret prefix), so it's lower-risk; `^TWOII` (TPEx) was
only confirmed by cross-checking Yahoo Finance's own Taiwan-region site
(tw.stock.yahoo.com), not by an actual yfinance pull. FIRST REAL RUN is
the real test for both -- if either comes back empty, double check the
symbol is still current on Yahoo Finance before assuming the code itself
is wrong.
"""

import os
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from config import DATA_DIR, PRICE_HISTORY_MONTHS

INDEXES = [
    {"code": "TAIEX", "yf_symbol": "^TWII", "name": "台股加權指數", "name_en": "TAIEX"},
    {"code": "TPEX", "yf_symbol": "^TWOII", "name": "櫃買指數", "name_en": "TPEx Index"},
]

FIELDNAMES = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _csv_path(code):
    return os.path.join(DATA_DIR, "market_index", f"{code}.csv")


def _load_existing(code):
    """Return {iso_date: row_dict} of what's already saved for this index."""
    path = _csv_path(code)
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            import csv
            for row in csv.DictReader(f):
                rows[row["Date"]] = row
    return rows


def _write_csv(code, rows_by_date):
    import csv
    path = _csv_path(code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for iso in sorted(rows_by_date.keys()):
            writer.writerow(rows_by_date[iso])


def _rows_from_history(hist):
    """hist: a yfinance .history() DataFrame (DatetimeIndex, Open/High/
    Low/Close/Volume columns, possibly also Dividends/Stock Splits which
    are ignored here -- irrelevant for an index). Returns {iso_date:
    row_dict}, skipping any row with no Close (yfinance can return a
    partial/NaN bar for the still-in-progress current trading day)."""
    rows = {}
    if hist is None or hist.empty:
        return rows
    for idx, row in hist.iterrows():
        close = row.get("Close")
        if pd.isna(close):
            continue
        iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        rows[iso] = {
            "Date": iso,
            "Open": round(float(row["Open"]), 4) if pd.notna(row.get("Open")) else "",
            "High": round(float(row["High"]), 4) if pd.notna(row.get("High")) else "",
            "Low": round(float(row["Low"]), 4) if pd.notna(row.get("Low")) else "",
            "Close": round(float(close), 4),
            "Volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else "",
        }
    return rows


def fetch_index(code, yf_symbol, name_label):
    print(f"[market_index] {code} {name_label} ({yf_symbol})")
    existing = _load_existing(code)

    if existing:
        # Incremental: re-fetch starting from the latest date already on
        # file (inclusive, to reconfirm a possibly-partial last day)
        # through today -- same "always reconfirm the most recent point"
        # idea price_data.py uses for its most recent month.
        start = date.fromisoformat(max(existing.keys()))
    else:
        start = date.today() - timedelta(days=PRICE_HISTORY_MONTHS * 31)

    try:
        hist = yf.Ticker(yf_symbol).history(start=start.isoformat(), interval="1d")
    except Exception as e:
        print(f"  [!] fetch failed: {e}")
        return

    new_rows = _rows_from_history(hist)
    if not new_rows:
        print("  -> no data returned")
        return

    existing.update(new_rows)
    _write_csv(code, existing)
    print(f"  -> {len(existing)} trading days saved")


def run():
    for idx in INDEXES:
        fetch_index(idx["code"], idx["yf_symbol"], f'{idx["name"]} ({idx["name_en"]})')


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise, same pattern every other
    # fetch script in this project follows.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("market_index")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
