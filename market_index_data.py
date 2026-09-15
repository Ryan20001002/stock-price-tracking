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
meaningless numbers as real data. This section was REVISED the same day
after a real user report (see the CORRECTION note below) -- read that
note first, it overrides part of the original reasoning kept here for
context:

  - yfinance's `.history()` does NOT return a trading-VALUE (turnover in
    currency terms) column at all, for any ticker -- only Open/High/Low/
    Close/Volume/Dividends/Stock Splits. So TradeValue can't honestly
    come from yfinance regardless of the Volume question below.
  - TWSE (the exchange behind TAIEX) DOES publish trade value, for free,
    with no API key: the FMTQIK report
    (https://www.twse.com.tw/exchangeReport/FMTQIK), the whole-TWSE-
    market daily summary -- 成交股數 (total shares traded, unit: 股),
    成交金額 (total trade value, unit: 元/NT dollars), 成交筆數
    (transaction count), and TAIEX's own closing value, one row per
    trading day. VERIFIED LIVE (fetched directly, 2026-09-15): a
    `date=YYYYMM01` parameter returns that whole calendar month, exactly
    like STOCK_DAY's own convention (see price_data.py) -- so this reuses
    that same month-loop incremental pattern. This is what
    `_fetch_taiex_turnover()` below uses for TAIEX's TradeValue.
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
    app.py's UI caption for how this is surfaced. **SUPERSEDED the same
    day -- see the TPEX TRADE VALUE ADDED section below: a real free
    source for this WAS found after all, later the same day.**

CORRECTION (2026-09-15, same day, in response to a real user report):
the FIRST version of this feature also overwrote TAIEX's `Volume` with
FMTQIK's 成交股數 figure, reasoning (from yfinance's own GitHub issue
tracker, ranaroussi/yfinance#2397) that Volume is documented-unreliable
for INDEX tickers -- but that issue is specifically about `^NDX`
(Nasdaq-100), a DIFFERENT ticker, and that assumption turned out not to
hold for `^TWII`. The user compared this page's TAIEX Volume against
Yahoo Finance's own `^TWII` page directly and found they no longer
matched, and that the ORIGINAL (pre-FMTQIK) numbers had matched Yahoo
Finance. Re-verified live from this sandbox (WebFetch, 2026-09-15):
  - `finance.yahoo.com/quote/^TWII/history` shows real, non-zero daily
    Volume in the low-millions range (e.g. 3,967,200 to 7,217,600 for
    late Aug/early Sep 2026) -- yfinance's `.history()` Volume for
    `^TWII` is NOT broken/all-zero after all.
  - `tw.stock.yahoo.com`'s `^TWII` page independently shows a "總量"
    figure in the same low-millions order of magnitude (8,705,759 on the
    day checked) -- consistent with the finance.yahoo.com figure, not
    with FMTQIK's number.
  - FMTQIK's 成交股數 is a fundamentally DIFFERENT, much larger quantity:
    the sum of shares traded across every individual stock listed on the
    whole TWSE market that day (billions of shares) -- not "TAIEX's own
    volume" the way Yahoo Finance/yfinance report it. These were never
    the same metric; overwriting one with the other was the bug, not a
    yfinance reliability problem.
  - **Fix: Volume for BOTH TAIEX and TPEx now comes from yfinance only,
    unmodified** (see `_rows_from_history()` below) -- matching what
    Yahoo Finance's own site shows, which is the behavior the user
    confirmed was correct before this feature touched Volume at all.
    FMTQIK is used ONLY for TradeValue now (TAIEX only, as above) --
    that figure has no yfinance equivalent at all, so there's no
    competing "correct" number to conflict with, unlike Volume.

RESTRUCTURED (2026-09-15, same day, still in response to the same user):
after the CORRECTION above, the user reported the page was "still wrong"
and asked explicitly to "use the original code to fetch the quantity of
transaction, and add a new function to grab total value of transaction" --
i.e. a harder separation than the CORRECTION made. Two things changed:

  1. **Architecture**: `fetch_index()` is now back to exactly what it was
     before the trading-value feature existed at all -- a plain OHLC+
     Volume fetch from yfinance, with NO FMTQIK/TradeValue-awareness
     inside it whatsoever (no `TURNOVER_SOURCE_CODES` branch, no merge
     logic). TradeValue is now fetched and written by a completely
     separate function, `fetch_taiex_trade_value()`, which only ever
     touches the `TradeValue` field of rows already on file -- it never
     writes Open/High/Low/Close/Volume. `run()` calls both, one after the
     other; either can also be called/tested/debugged on its own.
  2. **Historical data repair**: the CORRECTION fixed the code going
     forward, but `fetch_index()`'s incremental strategy only ever
     re-fetches from the latest date already on file onward -- it never
     revisits older dates already saved. That means any row written while
     the ORIGINAL bug was live (Volume overwritten with FMTQIK's
     whole-market figure, in the billions) would stay wrong in the CSV
     forever, even after the code fix -- which is almost certainly why the
     page was "still wrong" after the CORRECTION: the bug was gone, but
     the bad data it already wrote wasn't. `fetch_index()` now scans the
     existing rows on every run and detects this: a Volume figure over
     `VOLUME_CONTAMINATION_THRESHOLD` (100,000,000 shares) can only ever
     have come from FMTQIK's whole-market total -- real TAIEX/TPEx Volume,
     per every live Yahoo Finance figure checked in the CORRECTION, is in
     the low-to-mid millions, nowhere near that. Any contaminated date
     found forces `start` back to the EARLIEST such date instead of the
     latest existing date, so yfinance re-fetches (and overwrites) every
     contaminated row automatically, with no manual CSV surgery needed
     from the user. See `_find_contaminated_dates()` below.

TPEX TRADE VALUE ADDED (2026-09-15, same day, by explicit follow-up
request -- "How about fetch these numbers on tpex.org.tw?", after the user
asked why TPEx's TradeValue never shows up): the TRADING VOLUME / TRADING
VALUE section above said no free whole-market TPEx source could be found
-- that conclusion didn't hold up. Further web research turned up TPEx's
own "日成交量值指數" (Daily Volume & Index) report,
`st41_result.php`, at
https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41_result.php
-- TPEx's real equivalent of TWSE's FMTQIK, month-scoped the same way
(`d=<ROC year>/<month>`, e.g. `115/08` for August 2026, returning every
trading day in that month in one response). Since this sandbox's own
network access to tpex.org.tw is blocked (confirmed again -- every path
under /web/ 403s; see the earlier TRADING VOLUME / TRADING VALUE section),
this endpoint could NOT be verified by directly fetching it from here.
Instead, the user opened the URL directly in their own browser (normal,
unsandboxed internet access) and pasted back the real response --
**CONFIRMED LIVE via the user's own machine, not guessed from
documentation**. Confirmed real response shape (August 2026, 21 trading
days returned for one request):

    {"tables": [{"data": [["115/08/03", "682,447", "132,226,012",
                            "654,724", 362.89, 15.04], ...],
                 "fields": ["日期","成交張數","金額（仟元）","筆數",
                            "櫃買指數","漲/跌"],
                 "totalCount": 21}],
     "stat": "ok"}

Two unit conversions this report needs that FMTQIK's doesn't (confirmed
from the real response, not assumed):
  - `成交張數` (index 1) is in 張 -- board lots of 1,000 shares each -- not
    raw shares like FMTQIK's `成交股數`. Multiply by 1,000 for a
    shares-equivalent figure (kept in `_fetch_tpex_turnover()`'s return
    tuple for parity with `_fetch_taiex_turnover()`, but -- consistent
    with the rest of this module -- never applied to TPEx's `Volume`
    column, which stays yfinance-only; see the CORRECTION and RESTRUCTURED
    sections above for why that separation matters).
  - `金額（仟元）` (index 2) is in thousands of NT dollars, not raw NT
    dollars like FMTQIK's `成交金額`. Multiply by 1,000 to store the same
    unit this project's TradeValue column already uses for TAIEX.
  - Sanity-checked the conversion against the real numbers themselves:
    682,447 張 x 1,000 = 682,447,000 shares; at that volume, the reported
    132,226,012 (x1,000 =) NT$132.2 billion trade value implies an average
    price around NT$194/share -- a plausible whole-OTC-market average,
    which is the kind of cross-check this project's docstrings favor over
    trusting a field label alone.
  - The index-close and change fields (indices 4/5) come back as actual
    JSON numbers in the real response, not comma-formatted strings like
    the rest of the row -- `_num()` isn't even called on them, since
    `_fetch_tpex_turnover()` only needs indices 1/2.

**Implemented as a fully separate function, `fetch_tpex_trade_value()`,
mirroring `fetch_taiex_trade_value()` exactly** (per the RESTRUCTURED
section's established pattern) -- it only ever touches the `TradeValue`
field of rows already on file for TPEX.csv, never Open/High/Low/
Close/Volume. `run()` now calls both TradeValue functions.

**Remaining caveats, same honesty standard as every other TPEx-related
piece of this module:**
  - This endpoint's actual HTTP behavior (headers, retry needs, whether it
    blocks non-browser User-Agents the way tpex.org.tw's other paths seem
    to from this sandbox) has never been exercised by real Python code --
    only by the user's own browser. `tpex_client.py` was written to the
    same retry/backoff standard as `twse_client.py`, but its first REAL
    request only happens on the user's own first `--market-index` run.
  - **The user separately reported this report's own site only keeps
    about a year of history** -- a request for an older month is expected
    to come back with no/empty data, not an error, and is handled the
    same defensive way as FMTQIK's "no trading days yet this month" case
    (skip, don't abort the rest of the backfill). This means TPEx's
    TradeValue backfill will likely stay shorter than TAIEX's and the
    OHLC data's `PRICE_HISTORY_MONTHS` (36 months) -- not a bug if so.
"""

import os
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import twse_client
import tpex_client
from config import DATA_DIR, PRICE_HISTORY_MONTHS

INDEXES = [
    {"code": "TAIEX", "yf_symbol": "^TWII", "name": "台股加權指數", "name_en": "TAIEX"},
    {"code": "TPEX", "yf_symbol": "^TWOII", "name": "櫃買指數", "name_en": "TPEx Index"},
]

FIELDNAMES = ["Date", "Open", "High", "Low", "Close", "Volume", "TradeValue"]

FMTQIK_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"

# TPEx's own equivalent of FMTQIK -- see the module docstring's TPEX TRADE
# VALUE ADDED section for the confirmed response shape and unit conversions.
TPEX_TURNOVER_URL = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41_result.php"

# A Volume figure above this can only ever have come from the ORIGINAL bug
# (FMTQIK's whole-TWSE-market 成交股數 written into Volume) -- real TAIEX/
# TPEx Volume from yfinance, per every live Yahoo Finance figure checked
# during the 2026-09-15 CORRECTION, sits in the low-to-mid millions, orders
# of magnitude below this. See module docstring's RESTRUCTURED note and
# _find_contaminated_dates() below.
VOLUME_CONTAMINATION_THRESHOLD = 100_000_000


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
    """Returns {iso_date: (shares, ntd_value)} for the whole TWSE market's
    daily turnover, from TWSE's FMTQIK report (see module docstring --
    the only verified-live source for this project). Only `ntd_value` is
    actually used by callers now (as TAIEX's TradeValue) -- `shares` is
    kept in the return tuple but deliberately NOT applied to TAIEX's
    Volume (2026-09-15 CORRECTION: this is the whole-market total shares
    traded, not the same quantity as TAIEX's own Volume reported by
    Yahoo Finance/yfinance -- see module docstring). Same month-by-month
    incremental strategy as price_data.py's fetch_ticker_prices: skip a
    month already covered by a real TradeValue on file, except always
    re-fetch the most recent such month (it may have been partial when
    last saved)."""
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


def _fetch_tpex_turnover(existing_rows):
    """Returns {iso_date: (shares, ntd_value)} for the whole TPEx (OTC)
    market's daily turnover, from TPEx's own "日成交量值指數" report
    (`st41_result.php`) -- see the module docstring's TPEX TRADE VALUE
    ADDED section for how this was found and CONFIRMED LIVE (by the user
    opening the URL in their own browser, since this sandbox can't reach
    tpex.org.tw). Confirmed response shape, requesting `d=<ROC year>/<MM>`
    (e.g. "115/08"):

        {"tables": [{"data": [["115/08/03", "682,447", "132,226,012",
                                "654,724", 362.89, 15.04], ...],
                     "fields": ["日期","成交張數","金額（仟元）","筆數",
                                "櫃買指數","漲/跌"]}],
         "stat": "ok"}

    Two unit conversions this report needs that FMTQIK's doesn't (see
    docstring for the numbers this was sanity-checked against):
      - `成交張數` (index 1) is in 張 (board lots of 1,000 shares) --
        multiplied by 1,000 here for a shares-equivalent figure.
      - `金額（仟元）` (index 2) is in thousands of NT dollars --
        multiplied by 1,000 here to match FMTQIK's/this project's raw-NTD
        TradeValue convention.
    Same month-by-month incremental strategy as `_fetch_taiex_turnover()`.
    A month outside TPEx's own retention window for this report (the user
    reported it's roughly a year) is expected to come back with no usable
    data -- handled the same defensive way as FMTQIK's "no trading days
    yet this month" case: skip it, don't abort the rest of the backfill."""
    result = {}
    present_months = _months_with_turnover(existing_rows)
    target_months = _month_starts(PRICE_HISTORY_MONTHS)
    latest_present = max(present_months) if present_months else None

    for (y, m) in target_months:
        if (y, m) in present_months and (y, m) != latest_present:
            continue
        roc_param = f"{y - 1911}/{m:02d}"
        payload = tpex_client.get_json(
            TPEX_TURNOVER_URL, params={"l": "zh-tw", "d": roc_param, "o": "json"}
        )
        if not payload or payload.get("stat") != "ok":
            continue  # common/expected: no data yet, or outside TPEx's own retention window
        tables = payload.get("tables") or []
        if not tables:
            continue
        for row in tables[0].get("data", []):
            try:
                iso = _roc_to_iso(row[0])
            except (ValueError, IndexError):
                continue
            lots = _num(row[1]) if len(row) > 1 else None
            thousands_ntd = _num(row[2]) if len(row) > 2 else None
            if lots is not None and thousands_ntd is not None:
                result[iso] = (lots * 1000, thousands_ntd * 1000)
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
            # Volume comes straight from yfinance for BOTH indices, kept
            # as-is (NOT overwritten by FMTQIK -- see the module
            # docstring's 2026-09-15 CORRECTION note: FMTQIK's 成交股數
            # is the whole-TWSE-market total, a different and much
            # larger number than what Yahoo Finance/yfinance report as
            # `^TWII`'s own Volume; overwriting one with the other was a
            # real bug, caught via a live comparison against Yahoo
            # Finance's own site).
            "Volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else "",
            # TradeValue has no yfinance equivalent at all -- this fetch
            # never touches it either way; it's filled in separately by
            # fetch_taiex_trade_value()/fetch_tpex_trade_value() below,
            # each writing only their own index's file.
            "TradeValue": "",
        }
    return rows


def _find_contaminated_dates(existing_rows):
    """Dates whose on-file Volume is implausibly large to be real TAIEX/
    TPEx Volume -- i.e. it can only have been written by the ORIGINAL bug
    (FMTQIK's whole-TWSE-market 成交股數 overwriting Volume, before the
    2026-09-15 CORRECTION). Returns a list of iso date strings; empty if
    nothing looks contaminated. See VOLUME_CONTAMINATION_THRESHOLD and the
    module docstring's RESTRUCTURED note."""
    bad = []
    for iso, row in existing_rows.items():
        vol = _num(row.get("Volume"))
        if vol is not None and vol > VOLUME_CONTAMINATION_THRESHOLD:
            bad.append(iso)
    return bad


def fetch_index(code, yf_symbol, name_label):
    """Original-style fetch: plain OHLC + Volume from yfinance, nothing
    else -- no FMTQIK, no TradeValue awareness at all (2026-09-15,
    RESTRUCTURED back to this by explicit request after the CORRECTION's
    merge-logic fix alone wasn't enough -- see module docstring).
    TradeValue is handled entirely separately, by
    fetch_taiex_trade_value() below."""
    print(f"[market_index] {code} {name_label} ({yf_symbol})")
    existing = _load_existing(code)

    if existing:
        contaminated = _find_contaminated_dates(existing)
        if contaminated:
            # Force a re-fetch starting from the EARLIEST contaminated
            # date instead of the normal "latest date on file" -- this is
            # what actually repairs old rows written by the pre-CORRECTION
            # bug, since yfinance's returned rows overwrite whatever was
            # there before (see existing.update(new_rows) below).
            start = date.fromisoformat(min(contaminated))
            print(f"  [!] {len(contaminated)} day(s) on file look contaminated by the old Volume bug "
                  f"(Volume > {VOLUME_CONTAMINATION_THRESHOLD:,}, a scale that only ever came from FMTQIK's "
                  f"whole-market figure) -- re-fetching from {start.isoformat()} onward to repair them")
        else:
            # Normal incremental case: re-fetch starting from the latest
            # date already on file (inclusive, to reconfirm a possibly-
            # partial last day) through today -- same "always reconfirm
            # the most recent point" idea price_data.py uses for its most
            # recent month.
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


def fetch_taiex_trade_value():
    """Separate, independent fetch for TAIEX's TradeValue (TWSE FMTQIK's
    成交金額 -- see module docstring). Added 2026-09-15 as a hard split
    from fetch_index(), by explicit request, so this function ONLY ever
    reads and updates the `TradeValue` field of whatever rows are already
    on file for TAIEX.csv -- it never touches Open/High/Low/Close/Volume.
    TPEx has its own separate, symmetric function,
    fetch_tpex_trade_value() below (added later the same day, once a free
    TPEx source was found -- see module docstring). Safe to run on its
    own, independent of fetch_index()."""
    code = "TAIEX"
    print(f"[market_index] {code} trade value (TWSE FMTQIK)")
    existing = _load_existing(code)
    turnover = _fetch_taiex_turnover(existing)
    if not turnover:
        print("  -> no data returned")
        return

    for iso, (shares, value) in turnover.items():
        # `shares` (FMTQIK's whole-TWSE-market 成交股數) is deliberately
        # unused here -- it's a different, much larger quantity than
        # TAIEX's own Volume (see module docstring) and was never a valid
        # substitute for it; this function only ever writes `value`.
        if iso not in existing:
            # FMTQIK reported a trading day that isn't in TAIEX.csv yet --
            # rare (the two sources should track the same trading
            # calendar), but keep the TradeValue rather than dropping it;
            # OHLC/Volume for this date stays blank until fetch_index()
            # picks it up from yfinance on a later run.
            existing[iso] = {"Date": iso, "Open": "", "High": "", "Low": "", "Close": "", "Volume": "", "TradeValue": ""}
        existing[iso]["TradeValue"] = value

    _write_csv(code, existing)
    print(f"  -> {len(turnover)} day(s) of TWSE 成交金額 merged in (FMTQIK)")


def fetch_tpex_trade_value():
    """Separate, independent fetch for TPEx's TradeValue (TPEx's own
    st41_result.php whole-market report -- see the module docstring's
    TPEX TRADE VALUE ADDED section). Added 2026-09-15, mirroring
    fetch_taiex_trade_value() exactly: only ever reads and updates the
    `TradeValue` field of rows already on file for TPEX.csv, never
    Open/High/Low/Close/Volume. Closes the asymmetry this module has
    carried since TradeValue was first added (TAIEX had a verified free
    source, TPEx didn't) -- see the module docstring's caveats for what's
    still unverified about this specific source (never actually run from
    this sandbox; only confirmed via the user's own browser)."""
    code = "TPEX"
    print(f"[market_index] {code} trade value (TPEx 日成交量值指數)")
    existing = _load_existing(code)
    turnover = _fetch_tpex_turnover(existing)
    if not turnover:
        print("  -> no data returned")
        return

    for iso, (shares, value) in turnover.items():
        # `shares` (TPEx's 成交張數, converted to a shares-equivalent) is
        # deliberately unused here, same reasoning as
        # fetch_taiex_trade_value() -- it's a different quantity than
        # TPEx's own Volume (which stays yfinance-only) and was never a
        # valid substitute for it; this function only ever writes `value`.
        if iso not in existing:
            # TPEx's report covers a trading day yfinance's OHLC didn't --
            # rare, but keep the TradeValue rather than dropping it; OHLC/
            # Volume for this date stays blank until fetch_index() picks
            # it up from yfinance on a later run.
            existing[iso] = {"Date": iso, "Open": "", "High": "", "Low": "", "Close": "", "Volume": "", "TradeValue": ""}
        existing[iso]["TradeValue"] = value

    _write_csv(code, existing)
    print(f"  -> {len(turnover)} day(s) of TPEx 成交金額 merged in (st41_result.php)")


def run():
    for idx in INDEXES:
        fetch_index(idx["code"], idx["yf_symbol"], f'{idx["name"]} ({idx["name_en"]})')
    fetch_taiex_trade_value()
    fetch_tpex_trade_value()


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
