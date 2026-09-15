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
STOCK_DAY-style report for an index's OWN price history, so Open/High/
Low/Close come from yfinance instead (the same library already used
elsewhere in this project, for splits.py's split data and
market_value_data.py's shares-outstanding fallback).

CAVEAT on yfinance's OHLC, consistent with every other yfinance-based
addition in this project (see splits.py's and institutional_data.py's/
finmind_client.py's docstrings for the same situation): the sandboxed
environment this was built in has no network path to Yahoo Finance to
test against live (outbound HTTPS there is allowlisted to package
registries only, confirmed directly -- a request to Yahoo Finance came
back 403 from the proxy), so the yfinance side of this was built and
unit-tested against a MOCKED response, not an observed real one. `^TWII`
(TAIEX) is the same ticker family this project's other yfinance calls
already use, so it's lower-risk; `^TWOII` (TPEx) was only confirmed by
cross-checking Yahoo Finance's own Taiwan-region site
(tw.stock.yahoo.com), not by an actual yfinance pull. FIRST REAL RUN is
the real test for both -- if either comes back empty, double check the
symbol is still current on Yahoo Finance before assuming the code itself
is wrong.

TRADING VOLUME / TRADING VALUE (added 2026-09-15, by explicit follow-up
request -- "add a column to store total value of transactions, and add
the unit for the trading quantity"): researched rather than assumed,
since getting this wrong would silently present fabricated or
meaningless numbers as real data:

  - yfinance's `.history()` does NOT return a trading-VALUE (turnover in
    currency terms) column at all, for any ticker -- only Open/High/Low/
    Close/Volume/Dividends/Stock Splits. And its Volume field is well
    documented as unreliable for INDEX tickers specifically (an index
    itself isn't "traded" -- only its constituents are): yfinance's own
    GitHub issue tracker (ranaroussi/yfinance#2397) shows ^NDX returning
    all-zero Volume for its entire history, closed by the maintainers as
    "not planned." So neither figure the user asked for can honestly
    come from yfinance.
  - TWSE (the exchange behind TAIEX) DOES publish exactly this, for
    free, with no API key: the FMTQIK report
    (https://www.twse.com.tw/exchangeReport/FMTQIK), the whole-TWSE-
    market daily summary -- 成交股數 (total shares traded, unit: 股),
    成交金額 (total trade value, unit: 元/NT dollars), 成交筆數
    (transaction count), and TAIEX's own closing value, one row per
    trading day. VERIFIED LIVE (fetched directly, 2026-09-15): a
    `date=YYYYMM01` parameter returns that whole calendar month, exactly
    like STOCK_DAY's own convention (see price_data.py) -- so this reuses
    that same month-loop incremental pattern. This is what
    `_fetch_taiex_turnover()` below uses for TAIEX's Volume/TradeValue.
  - TPEx has NO equivalent free, whole-market endpoint that could be
    found or verified (this sandbox's outbound access to tpex.org.tw
    itself returns 403 on every path deeper than its root page, and no
    third-party wrapper documents a whole-market aggregate -- the one
    concrete TPEx OpenAPI endpoint found, tpex_mainboard_daily_close_
    quotes, is PER-STOCK, not a whole-market total, so summing it would
    mean one API call per OTC-listed stock per day -- the same expensive
    pattern this project already abandoned for institutional_data.py's
    old TWSE T86 approach, see that module's docstring). So TPEx's
    TradeValue is left BLANK (not fabricated, not silently zero) rather
    than presented as real data it isn't -- see the CAVEATS section and
    app.py's UI caption for how this is surfaced. TPEx's Volume still
    comes from yfinance as before, with the same reliability caveat as
    above (may be 0/meaningless for this index ticker) -- that's the
    honest state of what's available today, not a claim it's correct.
"""

import os
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import twse_client
from config import DATA_DIR, PRICE_HISTORY_MONTHS

INDEXES = [
    {"code": "TAIEX", "yf_symbol": "^TWII", "name": "台股加權指數", "name_en": "TAIEX"},
    {"code": "TPEX", "yf_symbol": "^TWOII", "name": "櫃買指數", "name_en": "TPEx Index"},
]

# Only TAIEX has a verified free whole-market turnover source (TWSE's
# FMTQIK -- see module docstring); TPEx's TradeValue stays blank.
TURNOVER_SOURCE_CODES = {"TAIEX"}

FIELDNAMES = ["Date", "Open", "High", "Low", "Close", "Volume", "TradeValue"]

FMTQIK_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"


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
            row = rows_by_date[iso]
            # .get(field, "") rather than writing row directly: a row
            # loaded from a CSV saved before TradeValue existed (or any
            # future field added the same way) won't have that key at
            # all, and csv.DictWriter raises on a missing key by default
            # -- this keeps an old on-disk file forward-compatible
            # instead of crashing the very next run that touches it.
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def _roc_to_iso(roc_date):
    """Convert an ROC date string like '115/08/01' to '2026-08-01' --
    same conversion as price_data.py's/market_value_data.py's own
    _roc_to_iso, duplicated locally rather than imported (this project's
    established convention -- see market_value_data.py's own copy -- for
    keeping fetch scripts independent of each other's internals)."""
    y, m, d = roc_date.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def _num(s):
    """Parse a TWSE numeric field ('1,234.56', '--', '') into a float or
    None -- same idea as price_data.py's own _num."""
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
    """Yield (year, month) tuples for the last n_months, oldest first --
    same idea as price_data.py's own _month_starts."""
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


def _months_with_turnover(existing_rows):
    """Which (year, month)s already have a REAL (non-empty) TradeValue
    in existing_rows -- a row loaded from a CSV saved before this feature
    existed has no TradeValue key at all (missing/None), so every such
    month is correctly treated as "not yet fetched" and gets backfilled
    on the next run, without any explicit migration step."""
    present = set()
    for iso, row in existing_rows.items():
        if row.get("TradeValue"):
            y, m = int(iso[:4]), int(iso[5:7])
            present.add((y, m))
    return present


def _fetch_taiex_turnover(existing_rows):
    """Returns {iso_date: (shares, ntd_value)} for TAIEX's whole-TWSE-
    market daily turnover, from TWSE's FMTQIK report (see module
    docstring -- the only verified-live source for this project). Same
    month-by-month incremental strategy as price_data.py's
    fetch_ticker_prices: skip a month already covered by a real
    TradeValue on file, except always re-fetch the most recent such
    month (it may have been partial when last saved)."""
    result = {}
    present_months = _months_with_turnover(existing_rows)
    target_months = _month_starts(PRICE_HISTORY_MONTHS)
    latest_present = max(present_months) if present_months else None

    for (y, m) in target_months:
        if (y, m) in present_months and (y, m) != latest_present:
            continue
        date_param = f"{y:04d}{m:02d}01"
        payload = twse_client.get_json(FMTQIK_URL, params={"date": date_param, "response": "json"})
        if not payload or payload.get("stat") != "OK":
            continue  # common/expected for a month with no trading days yet
        for row in payload.get("data", []):
            try:
                iso = _roc_to_iso(row[0])
            except (ValueError, IndexError):
                continue
            shares = _num(row[1]) if len(row) > 1 else None
            value = _num(row[2]) if len(row) > 2 else None
            if shares is not None and value is not None:
                result[iso] = (shares, value)
    return result


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
            # Placeholder Volume from yfinance -- overwritten with TWSE's
            # real 成交股數 for TAIEX in fetch_index() below (yfinance's
            # own Volume is unreliable for an index ticker; see module
            # docstring). Kept as-is for TPEx, which has no verified
            # alternative source yet.
            "Volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else "",
            # TradeValue has no yfinance equivalent at all -- filled in
            # for TAIEX only, in fetch_index() below; stays blank for
            # TPEx (no verified free whole-market source -- see docstring).
            "TradeValue": "",
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
    if not new_rows and code not in TURNOVER_SOURCE_CODES:
        print("  -> no data returned")
        return

    existing.update(new_rows)

    if code in TURNOVER_SOURCE_CODES:
        turnover = _fetch_taiex_turnover(existing)
        for iso, (shares, value) in turnover.items():
            if iso not in existing:
                # FMTQIK reported a trading day yfinance's OHLC didn't --
                # rare (the two sources should track the same trading
                # calendar) but keep the turnover figures rather than
                # silently dropping them; OHLC for this date is simply
                # left blank until/unless a later yfinance fetch fills it.
                existing[iso] = {"Date": iso, "Open": "", "High": "", "Low": "", "Close": "", "Volume": "", "TradeValue": ""}
            existing[iso]["Volume"] = shares
            existing[iso]["TradeValue"] = value
        print(f"  -> {len(turnover)} day(s) of TWSE 成交股數/成交金額 merged in (FMTQIK)")

    if not existing:
        print("  -> no data returned")
        return

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
