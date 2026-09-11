"""
Fetch historical dividend/distribution payments for each ticker in the
watchlist and save them to data/dividends/dividends.csv.

This uses yfinance rather than TWSE's own OpenAPI, because TWSE's public
dividend dataset (t187ap45_L, "上市公司股利分派情形") only covers
individual listed *companies'* board-resolved distributions -- it does
not include ETFs at all. yfinance's per-ticker dividend history works
uniformly for both ordinary stocks and ETFs, and gives actual paid
amounts with ex-dividend dates rather than resolution-stage records,
which is also the more directly useful shape for the later prediction
modeling step.

Each run overwrites the CSV with a fresh full pull (yfinance returns the
whole history each time; there's no meaningful "incremental" fetch here).
"""

import csv
import os
import time

import yfinance as yf

from config import WATCHLIST, DATA_DIR, REQUEST_DELAY_SECONDS

FIELDNAMES = ["code", "name", "name_en", "symbol", "ex_dividend_date", "dividend_per_share"]

# Try TWSE (.TW) first; fall back to TPEx/OTC (.TWO) if a ticker has no
# data under .TW. All of TWSE's main board (including most ETFs) uses
# .TW, so this only matters for the occasional OTC-listed ticker.
SUFFIXES_TO_TRY = [".TW", ".TWO"]


def _fetch_dividends_for_symbol(symbol):
    """Return a list of (date_str, amount) or None if the symbol has no data at all."""
    ticker = yf.Ticker(symbol)
    try:
        series = ticker.dividends
    except Exception as e:  # yfinance can raise a variety of network/parsing errors
        print(f"  [warn] yfinance error for {symbol}: {e}")
        return None

    if series is None or len(series) == 0:
        return []
    return [(idx.strftime("%Y-%m-%d"), float(val)) for idx, val in series.items()]


def fetch_ticker_dividends(code, name, name_en):
    for suffix in SUFFIXES_TO_TRY:
        symbol = f"{code}{suffix}"
        rows = _fetch_dividends_for_symbol(symbol)
        time.sleep(REQUEST_DELAY_SECONDS)
        if rows:  # got at least one dividend payment -- good, stop here
            print(f"[dividend] {code} {name}: {len(rows)} payments found via {symbol}")
            return symbol, rows
        if rows == []:
            # Symbol resolved fine but genuinely has no dividend history yet
            # (e.g. a very new ETF); no need to try the other suffix.
            print(f"[dividend] {code} {name}: 0 payments found via {symbol}")
            return symbol, []
    print(f"[dividend] {code} {name}: could not fetch data under any suffix")
    return f"{code}{SUFFIXES_TO_TRY[0]}", []


def run():
    all_rows = []
    for stock in WATCHLIST:
        symbol, payments = fetch_ticker_dividends(stock["code"], stock["name"], stock["name_en"])
        for date_str, amount in payments:
            all_rows.append({
                "code": stock["code"],
                "name": stock["name"],
                "name_en": stock["name_en"],
                "symbol": symbol,
                "ex_dividend_date": date_str,
                "dividend_per_share": amount,
            })

    all_rows.sort(key=lambda r: (r["code"], r["ex_dividend_date"]))

    out_path = os.path.join(DATA_DIR, "dividends", "dividends.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  -> saved {len(all_rows)} dividend payments to {out_path}")


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise. Added 2026-09-11, see
    # price_data.py's __main__ block for why.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("dividends")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
