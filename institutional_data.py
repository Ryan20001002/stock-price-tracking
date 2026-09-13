"""
Fetch daily net buy/sell volume (買賣超) by the three major institutional
investor groups -- 外資 (foreign investors), 投信 (investment trusts), and
自營商 (securities dealers) -- for each ticker in the watchlist, and keep a
running CSV per ticker under data/institutional/<code>.csv.

Backfill window: kept IDENTICAL to price_data.py's, i.e. the same
PRICE_HISTORY_MONTHS calendar months back from today (per explicit user
request, 2026-09-09, to keep the two histories aligned) -- see
_history_start_date() below, which reconstructs the exact same "N months
back" starting point price_data.py's _month_starts() uses.

Data source (2026-09-13, by explicit request after "the institutional
fetch is too slow"): FinMind's TaiwanStockInstitutionalInvestorsBuySell
dataset, via finmind_client.py -- see that module's docstring for the
full rationale and the accuracy trade-off (FinMind is a third-party
aggregator, not TWSE directly). This REPLACES this script's original
approach, which called TWSE's own T86 report ("三大法人買賣超日報")
directly. That's not being deleted out of caution -- it's gone -- because
the two produce the same CSV output shape and the whole point of the
switch is to stop paying T86's cost structure:

    TWSE's T86 report returns EVERY listed stock for ONE calendar day per
    call, so a full historical backfill cost roughly one network request
    PER TRADING DAY (not per ticker) -- at PRICE_HISTORY_MONTHS=36 (3
    years), that was 700-780 requests, throttled at
    REQUEST_DELAY_SECONDS=1.5s each (TWSE blocks IPs that go faster) --
    a floor of 20-30+ minutes for a first backfill, matching what was
    reported live ("broken when we let it run for an hour").

    FinMind's dataset is queried the other way around: one call per
    TICKER covers an entire date range in one shot. For this project's
    watchlist (a handful of tickers, not hundreds), that turns a 3-year
    backfill into a handful of requests total -- seconds, not tens of
    minutes -- and every incremental re-run after the first is now also
    just one small request per ticker (covering only the new days since
    last time), rather than a full day-by-day scan to find out which
    ones are missing.

FinMind's response is "long" format: one row per (date, stock_id,
investor-category, buy, sell) rather than one row per date with named
net columns, and categories vary by era (see CATEGORY constants below --
自營商's self-trading/hedging split only exists from 2014-12-01, foreign
investors' dealer-arm split only from 2018-01-15; before those dates a
single merged category is used instead). This module aggregates that
long format into the same wide per-day shape the rest of the app already
expects (Date, ForeignNet, InvestmentTrustNet, DealerNet,
ThreeInstitutionsNet), matching the same definitions the old TWSE-based
version used:

    外資 (foreign investors) net = Foreign_Investor (excl. dealer arm)
                                  + Foreign_Dealer_Self (from 2018-01-15)
    投信 (investment trust) net  = Investment_Trust
    自營商 (dealer) net          = Dealer_self + Dealer_Hedging (from
                                    2014-12-01) OR the single merged
                                    Dealer category before that date
    三大法人合計 (all three combined) = the sum of the three above
                                       (FinMind doesn't publish this as
                                       its own field the way TWSE's T86
                                       did, so it's derived here --
                                       exactly matching the definition,
                                       not an approximation)

Every value is a NET number of shares for that day (positive = net
bought, negative = net sold), not a running total -- unchanged from
before.

NOT YET VERIFIED AGAINST LIVE DATA -- see finmind_client.py's docstring.
The first real run against your actual watchlist is the real test;
worth spot-checking a recent date for one ticker against TWSE's own
T86 report directly (https://www.twse.com.tw/zh/trading/fund/T86.html)
to confirm the numbers line up before trusting a full backfill.
"""

import csv
import os
from collections import defaultdict
from datetime import date

from config import WATCHLIST, DATA_DIR, PRICE_HISTORY_MONTHS
import finmind_client

FINMIND_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"

FIELDNAMES = ["Date", "ForeignNet", "InvestmentTrustNet", "DealerNet", "ThreeInstitutionsNet"]

# FinMind's investor-category names for this dataset (see module
# docstring) -- summed per group to match the same 外資/投信/自營商
# definitions the old TWSE-based version used.
FOREIGN_CATEGORIES = ("Foreign_Investor", "Foreign_Dealer_Self")
TRUST_CATEGORIES = ("Investment_Trust",)
DEALER_SPLIT_CATEGORIES = ("Dealer_self", "Dealer_Hedging")
DEALER_MERGED_CATEGORY = "Dealer"  # pre-2014-12-01 (and some OTC rows) use this single category instead of the split ones above


def _num(x):
    """Parse a FinMind buy/sell value into an int/float, or None if
    missing/unparseable. FinMind's docs show plain ints, but this stays
    defensive the same way twse_client-based parsing did, in case a row
    ever comes back as a string or with a placeholder value."""
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None


def _aggregate_day_rows(rows):
    """rows: every FinMind row for ONE (ticker, date) -- one dict per
    investor category, each with "name"/"buy"/"sell". Returns a dict
    with the four wide columns this module has always written (see
    module docstring for the category-grouping definitions). A category
    absent for this date (e.g. Foreign_Dealer_Self before 2018-01-15)
    simply contributes 0, which is correct -- there's nothing to add,
    not a missing/unknown value."""
    net_by_name = {}
    for row in rows:
        buy, sell = _num(row.get("buy")), _num(row.get("sell"))
        if buy is None and sell is None:
            continue
        net_by_name[row.get("name")] = (buy or 0) - (sell or 0)

    foreign = sum(net_by_name.get(name, 0) for name in FOREIGN_CATEGORIES)
    trust = sum(net_by_name.get(name, 0) for name in TRUST_CATEGORIES)
    dealer = sum(net_by_name.get(name, 0) for name in DEALER_SPLIT_CATEGORIES) \
        + net_by_name.get(DEALER_MERGED_CATEGORY, 0)
    return {
        "ForeignNet": foreign,
        "InvestmentTrustNet": trust,
        "DealerNet": dealer,
        "ThreeInstitutionsNet": foreign + trust + dealer,
    }


def fetch_ticker_institutional(code, start_date, end_date):
    """Returns {iso_date: {ForeignNet, InvestmentTrustNet, DealerNet,
    ThreeInstitutionsNet}} for this ticker over [start_date, end_date]
    (inclusive), or None if the FinMind request ultimately failed after
    retries (see finmind_client.get_json). An empty dict (not None)
    means the request succeeded but returned no rows at all -- e.g. the
    range is entirely non-trading days, or FinMind has no data for this
    ticker yet."""
    payload = finmind_client.get_json({
        "dataset": FINMIND_DATASET,
        "data_id": code,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    })
    if payload is None:
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        print(f"  [warn] {code}: FinMind response had no usable \"data\" list, treating as empty")
        return {}

    rows_by_date = defaultdict(list)
    for row in rows:
        iso_date = row.get("date")
        if iso_date:
            rows_by_date[iso_date].append(row)

    return {iso_date: dict(_aggregate_day_rows(day_rows), Date=iso_date)
            for iso_date, day_rows in rows_by_date.items()}


def _csv_path(code):
    return os.path.join(DATA_DIR, "institutional", f"{code}.csv")


def _load_existing(code):
    """Return {iso_date: row_dict} of what's already saved for this ticker."""
    path = _csv_path(code)
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows[row["Date"]] = row
    return rows


def _write_csv(code, rows_by_date):
    path = _csv_path(code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for iso in sorted(rows_by_date.keys()):
            writer.writerow(rows_by_date[iso])


def _history_start_date():
    """The first day of the same PRICE_HISTORY_MONTHS-months-back window
    price_data.py's _month_starts() computes for share prices -- e.g. with
    PRICE_HISTORY_MONTHS=36, if today is in September, this walks back 35
    more months and returns October 1st of two years prior. Reconstructed
    here (rather than imported) since price_data.py's version returns a
    list of (year, month) tuples, not a single start date -- same month
    arithmetic, just taking the oldest entry as a real date."""
    today = date.today()
    y, m = today.year, today.month
    for _ in range(PRICE_HISTORY_MONTHS - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return date(y, m, 1)


def run():
    os.makedirs(os.path.join(DATA_DIR, "institutional"), exist_ok=True)
    if not WATCHLIST:
        print("[institutional] watchlist is empty -- nothing to do")
        return

    history_floor = _history_start_date()
    today = date.today()

    n_fetched = 0      # made a FinMind request and got at least one day back
    n_up_to_date = 0    # already had today's date on file -- no request needed
    n_empty = 0         # request succeeded but returned zero rows
    n_failed = 0        # FinMind request failed after retries

    for stock in WATCHLIST:
        code = stock["code"]
        existing = _load_existing(code)
        # Always re-fetch from the latest date already on file (not the
        # day after it) rather than just from history_floor once
        # something's cached -- same "the most recent unit on file might
        # have been partial/since-revised" reasoning price_data.py uses
        # for its latest month, cheap here since it only adds a few days
        # to one request's range, not a whole extra request.
        start = max(history_floor, date.fromisoformat(max(existing))) if existing else history_floor
        if start > today:
            n_up_to_date += 1
            continue

        print(f"[institutional] {code} {stock['name']}: fetching {start.isoformat()} ~ {today.isoformat()} from FinMind")
        by_date = fetch_ticker_institutional(code, start, today)
        if by_date is None:
            n_failed += 1
            print(f"  [warn] {code}: FinMind request failed after retries -- "
                  f"keeping the {len(existing)} day(s) already on file")
            continue
        if not by_date:
            n_empty += 1
            print(f"  -> {code}: FinMind returned no rows for this range")
            continue

        existing.update(by_date)
        _write_csv(code, existing)
        n_fetched += 1
        print(f"  -> {code}: {len(by_date)} day(s) fetched/updated, {len(existing)} day(s) total on file")

    print(f"[institutional] {n_fetched} ticker(s) fetched from FinMind, {n_up_to_date} already up to date, "
          f"{n_empty} returned no rows, {n_failed} request(s) failed")

    total_rows_saved = sum(len(_load_existing(s["code"])) for s in WATCHLIST)
    if total_rows_saved == 0 and n_failed > 0:
        raise RuntimeError(
            "FinMind 完全沒有回應任何一檔股票的三大法人資料 -- 很可能是網路不通、"
            "暫時超過免費額度限制（預設每小時 300 次，未設定 token 的情況下），"
            "或 FinMind 服務本身異常，請稍等幾分鐘後再試一次。上面每一檔股票的 "
            "[warn] 訊息會顯示 FinMind 實際回傳的錯誤內容。"
        )


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise. Added 2026-09-11 after a
    # full backfill (run exactly this way, per the "more comfortable to
    # watch in a terminal" recommendation above) completed successfully
    # but was later found wiped by a Streamlit Cloud container restart --
    # this is what actually protects that result, and it's also what
    # lets the deployed app pick it up automatically instead of ever
    # needing to repeat the backfill there.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("institutional")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
