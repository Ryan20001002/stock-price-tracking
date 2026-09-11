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

Skip-if-already-fetched (2026-09-11, by explicit request): a ticker is
fetched from yfinance AT MOST ONCE, ever -- once a ticker has been
attempted (successfully or not), run() leaves its saved rows untouched
on every later call and only fetches tickers new to the watchlist. This
is a deliberate trade-off, not an accident: yfinance's dividend history
call always returns the WHOLE history in one shot (there's no "just tell
me what's new" query to make this incremental the way price_data.py's
month-by-month or institutional_data.py's day-by-day skipping is), so
"skip already-covered data" here means skip the ticker entirely rather
than skip a date range. The real cost is that a genuinely NEW dividend
payment for an already-fetched ticker will NOT show up on its own --
pass force=True (or check the "強制重新抓取" box in the app sidebar) to
re-fetch specific or all tickers when you want a refresh.

"Already fetched" is tracked in data/dividends/_fetched_codes.json (a
plain list of ticker codes) rather than inferred from whether a ticker
has any rows in dividends.csv -- inferring it from row presence would
mean a genuinely zero-dividend ticker (e.g. a brand new ETF) gets
re-fetched forever, since it never accumulates a row to mark it "done".
"""

import csv
import json
import os
import time

import yfinance as yf

from config import WATCHLIST, DATA_DIR, REQUEST_DELAY_SECONDS

FIELDNAMES = ["code", "name", "name_en", "symbol", "ex_dividend_date", "dividend_per_share"]
FETCHED_CODES_FILE = os.path.join(DATA_DIR, "dividends", "_fetched_codes.json")

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


def _load_existing_rows():
    """Return {code: [row dict, ...]} from the existing dividends.csv, or
    {} if it doesn't exist yet."""
    out_path = os.path.join(DATA_DIR, "dividends", "dividends.csv")
    by_code = {}
    if os.path.exists(out_path):
        with open(out_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                by_code.setdefault(row["code"], []).append(row)
    return by_code


def _load_fetched_codes():
    if os.path.exists(FETCHED_CODES_FILE):
        try:
            with open(FETCHED_CODES_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_fetched_codes(codes):
    os.makedirs(os.path.dirname(FETCHED_CODES_FILE), exist_ok=True)
    with open(FETCHED_CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(codes), f)


def run(force=False, codes_to_force=None):
    """force=True re-fetches EVERY watchlist ticker regardless of what's
    already on file (the old, pre-2026-09-11 behavior). codes_to_force
    (an iterable of ticker codes) re-fetches only those specific tickers
    -- use this for "I know 2330 just declared a new dividend" without
    paying for every other ticker too. force=True takes priority over
    codes_to_force if both are given."""
    existing = _load_existing_rows()
    fetched_codes = _load_fetched_codes()
    force_codes = set(codes_to_force or ())

    all_rows = []
    n_skipped = 0
    n_fetched = 0
    for stock in WATCHLIST:
        code = stock["code"]
        already_done = code in fetched_codes and not force and code not in force_codes
        if already_done:
            all_rows.extend(existing.get(code, []))
            n_skipped += 1
            continue

        symbol, payments = fetch_ticker_dividends(code, stock["name"], stock["name_en"])
        fetched_codes.add(code)
        n_fetched += 1
        for date_str, amount in payments:
            all_rows.append({
                "code": code,
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
    _save_fetched_codes(fetched_codes)

    print(f"  -> saved {len(all_rows)} dividend payments to {out_path} "
          f"({n_fetched} ticker(s) fetched, {n_skipped} already on file and skipped)")


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
