"""
Fetch historical daily share price (OHLC + volume) for each ticker in the
watchlist from TWSE and keep a running CSV per ticker under
data/prices/<code>.csv.

TWSE's legacy STOCK_DAY report only returns one calendar month at a time,
so a full history is built up by looping over months. Re-running this
script is cheap: months already saved to disk are skipped, except the
most recent month on file, which is always re-fetched (it may have been
partial when last saved).
"""

import csv
import os
from datetime import date

from config import WATCHLIST, PRICE_HISTORY_MONTHS, DATA_DIR
from twse_client import get_json

STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"

FIELDNAMES = ["Date", "Open", "High", "Low", "Close", "Change", "Volume", "TradeValue", "Transactions"]


def _roc_to_iso(roc_date):
    """Convert an ROC date string like '114/08/01' to '2025-08-01'."""
    y, m, d = roc_date.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def _num(s):
    """Parse a TWSE numeric field ('1,234.56', '--', '') into a float or None."""
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s in ("", "--", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _month_starts(n_months):
    """Yield (year, month) tuples for the last n_months, oldest first."""
    today = date.today()
    y, m = today.year, today.month
    months = []
    for _ in range(n_months):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(months))


def _csv_path(code):
    return os.path.join(DATA_DIR, "prices", f"{code}.csv")


def _load_existing(code):
    """Return {iso_date: row_dict} of what's already saved for this ticker."""
    path = _csv_path(code)
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows[row["Date"]] = row
    return rows


def _months_present(existing_dates):
    """Return the set of (year, month) already represented in existing_dates."""
    present = set()
    for iso in existing_dates:
        y, m = int(iso[:4]), int(iso[5:7])
        present.add((y, m))
    return present


def fetch_ticker_prices(code, name_label):
    print(f"[price] {code} {name_label}")
    existing = _load_existing(code)
    present_months = _months_present(existing.keys())
    target_months = _month_starts(PRICE_HISTORY_MONTHS)

    # Always re-fetch the most recent month on file (it may be partial),
    # plus any month not yet fetched at all.
    latest_present = max(present_months) if present_months else None

    for (y, m) in target_months:
        if (y, m) in present_months and (y, m) != latest_present:
            continue  # already have this month in full
        date_param = f"{y:04d}{m:02d}01"
        payload = get_json(STOCK_DAY_URL, params={"date": date_param, "stockNo": code, "response": "json"})
        if not payload or payload.get("stat") != "OK":
            # Common and expected for months before the company was listed.
            continue
        for row in payload.get("data", []):
            # fields: 日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數
            try:
                iso = _roc_to_iso(row[0])
            except (ValueError, IndexError):
                continue
            existing[iso] = {
                "Date": iso,
                "Open": _num(row[3]),
                "High": _num(row[4]),
                "Low": _num(row[5]),
                "Close": _num(row[6]),
                "Change": _num(row[7]),
                "Volume": _num(row[1]),
                "TradeValue": _num(row[2]),
                "Transactions": _num(row[8]) if len(row) > 8 else None,
            }

    _write_csv(code, existing)
    print(f"  -> {len(existing)} trading days saved")


def _write_csv(code, rows_by_date):
    path = _csv_path(code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for iso in sorted(rows_by_date.keys()):
            writer.writerow(rows_by_date[iso])


def run():
    os.makedirs(os.path.join(DATA_DIR, "prices"), exist_ok=True)
    for stock in WATCHLIST:
        fetch_ticker_prices(stock["code"], f'{stock["name"]} ({stock["name_en"]})')


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise. Added 2026-09-11 so a
    # standalone `python price_data.py` run also protects its result
    # from Streamlit Cloud wiping data/ on a container restart, not only
    # runs through the app's own buttons.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("prices")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
