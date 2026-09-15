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

TPEX OHLC GAP (2026-09-15, same day, by explicit follow-up report -- the user
uploaded their real TPEX.csv and it showed Open/High/Low/Close/Volume blank
for every trading day from 2026-07-20 onward, ~35 trading days, with only
TradeValue populated for those rows). Confirmed this is NOT the TradeValue
code's fault -- that blank-OHLC-with-TradeValue row shape is the documented,
intentional fallback (see fetch_taiex_trade_value()/fetch_tpex_trade_value()
docstrings) for a date the trade-value report has but the OHLC source
doesn't yet. The real problem is `fetch_index()`'s own yfinance call for
`^TWOII` (TPEx's OHLC/Volume source) returning nothing, sustained across
many separate runs over ~8 weeks -- not a single transient blip.

By explicit request, the architecture stays exactly as the RESTRUCTURED
section above already has it: `fetch_index()` (plain yfinance OHLC+Volume,
the "original method") is the ONLY thing that should ever write Open/High/
Low/Close/Volume; the TPEx trade-value report (the "new method") stays
scoped to the TradeValue column only, same as it always has been -- nothing
architectural changed here, only `fetch_index()`'s error visibility (see the
`raise_errors=True` change below).

Research done from this sandbox (still can't run yfinance directly here --
same standing network restriction as everywhere else in this module) found
a real, if not 100% conclusive, lead: `https://finance.yahoo.com/quote/%5ETWOII/`
and its `/history/` page both currently 404 live, even though Google still
has an old cached title for that page -- i.e. this looks like something that
broke recently, not a page that never existed. By contrast,
`https://tw.stock.yahoo.com/quote/%5ETWOII` (Yahoo's Taiwan-region site,
a different backend) still shows a live price for the same symbol as of
2026/09/03. yfinance's `.history()` calls Yahoo's global chart/query API,
the same backend the (now-404ing) finance.yahoo.com page would have used --
not the tw.stock.yahoo.com backend -- so this is consistent with `^TWOII`
having been dropped from Yahoo's global data feed around the same time the
CSV gap starts, while Taiwan's own Yahoo site keeps showing it from
elsewhere. Not proven from here, since the sandbox can't hit Yahoo's actual
data API directly (blocked by robots.txt for WebFetch) -- the real
`yf.Ticker("^TWOII").history(...)` error message, from the user's own
machine, is the actual confirmation.

**First fix applied**: `fetch_index()`'s yfinance call now passes
`raise_errors=True` (a real, documented `.history()` parameter). Before this,
yfinance could return a bare empty DataFrame with NO exception at all on
failure -- indistinguishable from "genuinely no trading today" -- which is
exactly what silently happened for `^TWOII` for two months. With
`raise_errors=True`, the same failure now raises (typically
`YFPricesMissingError`, e.g. "possibly delisted; no price data found"),
which is caught and printed. This alone did NOT fix the gap (the user
re-ran and reported "still the same") -- see the SECOND, actual bug below.

**SECOND fix (2026-09-15, same day, the REAL root cause)**: re-reading
`fetch_index()`'s incremental logic line by line surfaced a genuine code
bug, independent of whatever Yahoo/yfinance is or isn't doing for
`^TWOII` specifically -- this alone can fully explain a persistent gap
that never self-heals even if the underlying data source is fine:

  `start = date.fromisoformat(max(existing.keys()))` computed the fetch's
  start date from the LATEST key in `existing` -- but `existing` includes
  every row on file, including the blank-OHLC/TradeValue-only placeholder
  rows `fetch_taiex_trade_value()`/`fetch_tpex_trade_value()` write for a
  date their report has but yfinance hasn't supplied OHLC for yet. Once
  `fetch_tpex_trade_value()` had written a placeholder row for every date
  in the 2026-07-20 gap (one more added each run, per that run's own
  trade-value fetch), `max(existing.keys())` kept getting dragged forward
  to the LATEST such placeholder date -- effectively "today" -- on every
  subsequent run. That means `fetch_index()` was only ever asking yfinance
  for a tiny window starting almost at the present, and NEVER once
  re-requested the actual gap dates before it, no matter how many times it
  ran or whether `^TWOII` itself was working normally that day. This is
  consistent with "still the same" after the `raise_errors=True` fix alone:
  if yfinance happened to return even one valid recent bar with no
  exception, the console would print success, not an error -- while the
  35-day gap underneath stayed exactly as blank as before, because it was
  never re-requested.

  **Fix**: new `_latest_real_ohlc_date()` -- same idea as
  `_find_contaminated_dates()`'s existing self-heal pattern -- returns the
  latest date with a REAL (non-blank) Close on file, explicitly excluding
  placeholder rows. `fetch_index()`'s normal incremental case now starts
  from THAT date instead of `max(existing.keys())`, so the next run
  correctly re-requests the whole gap through today again, regardless of
  which run originally caused it. Combined with `raise_errors=True` above:
  if `^TWOII` is genuinely broken on Yahoo's side, this will now show a
  real per-run error message; if it isn't (this code bug was the whole
  story), the gap should now backfill on the very next run.

**THIRD pass (2026-09-15, same day, immediate follow-up -- user reported
the gap STILL wasn't closing after the second fix, and explicitly asked to
"change a method to fix this part")**: with both a visibility fix and a
real logic bug already fixed and still no OHLC for TPEx, the most likely
remaining explanation is the one flagged as a risk from the very start of
this module (see the CAVEAT section at the top): `^TWOII` itself may
genuinely no longer return usable data through yfinance's underlying Yahoo
Finance API, regardless of anything this module's own code does. The user
explicitly authorized switching away from yfinance for TPEx specifically
(TAIEX is UNCHANGED -- `^TWII` has been working the whole time, so it stays
on `fetch_index()`/yfinance exactly as before).

**New source for TPEx's OHLC+Volume: TPEx's own official index-history
report, NOT yfinance.** Found via research into how third-party Taiwan
market-data tools source TPEx's index history (an Apify listing
documenting its own sources named it explicitly): "櫃買指數(月查詢)"
(TPEx Index, monthly query), a page at
`https://www.tpex.org.tw/web/stock/iNdex_info/inxh/inx.php` -- the exact
TPEx counterpart to TWSE's own **directly verified-live** `MI_5MINS_HIST`
report (`https://www.twse.com.tw/indicesReport/MI_5MINS_HIST`, confirmed
via WebFetch this session: real TAIEX Open/High/Low/Close, ROC-dated,
month-scoped, e.g. `{"stat":"OK","fields":["日期","開盤指數","最高指數",
"最低指數","收盤指數"],"data":[["115/08/03","42,780.42","43,784.19",
"42,780.42","43,386.41"],...]}`). Following this project's now
three-times-confirmed `<page>.php` -> `<page>_result.php` naming
convention (FMTQIK, st41, and this project's own successful pattern
matching), the result endpoint is presumed to be
`https://www.tpex.org.tw/web/stock/iNdex_info/inxh/inx_result.php`,
same `l=zh-tw&d=<ROC year>/<MM>&o=json` parameters as st41.

**Honesty caveat, consistent with every other TPEx endpoint in this
module**: this specific URL and its exact field names/order have NOT
been confirmed live -- this sandbox is blocked from every tpex.org.tw
path under `/web/` (confirmed again this session, including this exact
guessed URL, which 403s the same way every other `/web/` path always
has). Unlike the OHLC-value guess itself, though, this is a LOW-RISK
guess: it reuses a URL pattern and parameter convention already
CONFIRMED correct twice on this exact site (FMTQIK-equivalent st41, and
the TradeValue report before it), pointed at a page TPEx's own site
structure confirms exists (`inx.php`, "櫃買指數(月查詢)" -- found via
search, matching the exact monthly-query shape every other report here
uses). `_fetch_tpex_index_ohlc()` also doesn't hardcode column
POSITIONS -- it matches columns by searching the response's own
`fields` list for the Chinese characters for open/高/低/收, so a
slightly different field order than TWSE's MI_5MINS_HIST (which this
guess is modeled on) should still parse correctly; only a wrong URL or
completely different response shape would need a fix. If the next run's
console output shows "no OHLC data returned" or names an unrecognized
field list, that tells us precisely what to correct -- paste it back
rather than guessing further.

**Volume**: also switched off yfinance for TPEx, reusing the ALREADY
CONFIRMED WORKING st41_result.php report's `成交張數` (board lots,
x1,000 for a shares-equivalent) -- the same report `fetch_tpex_trade_value()`
already fetches successfully for TradeValue (confirmed live on the
user's own machine: "10 day(s) merged in"). `fetch_tpex_index()` fetches
it again independently here (a second call to the same URL, same
month), rather than sharing `fetch_tpex_trade_value()`'s result --
consistent with this module's established RESTRUCTURED principle that
each concern fetches and writes for itself.
**SUPERSEDED by the SIXTH pass below -- this Volume decision was wrong.**

**TradeValue is completely unchanged** -- still `fetch_tpex_trade_value()`'s
job alone, called separately by `run()`; `fetch_tpex_index()` never reads
or writes it.

FIFTH pass (2026-09-15, same day, "the data right now are merged in an
incorrect way", from the user's own CSV export showing rows dated e.g.
"3937-09-15"): TPEX_INDEX_HISTORY_URL's date field turned out to be plain
Gregorian ("2026/09/15"), NOT ROC ("115/09/15") like every other TWSE/TPEx
report this module reads -- the OHLC parsing had reused `_roc_to_iso()`,
which added 1911 to an already-Gregorian year (2026 -> 3937), exactly
matching the reported garbage dates. This also explains why it looked like
a "merge" bug: the OHLC loop keyed its rows by the garbage date while
Volume (from st41, correctly ROC-parsed) and TradeValue (from a separate
fetch) both used the real date, so real OHLC and real TradeValue for the
same trading day landed in two different rows that never matched keys to
merge. Fixed with a dedicated `_tpex_index_date_to_iso()` (Gregorian-first,
ROC fallback) used only for this endpoint, plus `_purge_garbage_date_rows()`
to self-heal any already-written garbage rows on the next run. This also
confirms the guessed `inx_result.php` URL and field-matching ARE correct --
real live data was coming back the whole time, just under the wrong date.

SIXTH pass (2026-09-15, same day, "How about the plot here?" -- the
成交量 chart showed nothing before ~2026-07, then a sudden jump to
0.5B-1.5B-scale bars): the THIRD pass's Volume decision above (reuse
st41's 成交張數) was itself a mistake -- 成交張數 is TPEx's WHOLE-MARKET
board-lots total, not 櫃買指數's own trading volume, and is a ~700x
larger quantity than TPEX.csv's older, yfinance-sourced Volume
(~1,000,000 scale, same order as TAIEX's own yfinance Volume). This is
the exact same category of error as the TAIEX Volume/FMTQIK CORRECTION
documented above, just recurring here via a different report, and it
produced exactly the visual symptom reported: on a linear chart spanning
both eras, the old millions-scale bars are invisible next to the new
billions-scale ones. Fixed by no longer writing Volume from st41 in
`_fetch_tpex_index_ohlc_and_volume()`/`fetch_tpex_index()` at all (there
is currently no known free source for 櫃買指數's own per-index Volume
now that `^TWOII` is broken on yfinance -- left blank rather than
fabricated), and by clearing any already-written whole-market-scale
Volume value on the next run, reusing the existing
VOLUME_CONTAMINATION_THRESHOLD/_find_contaminated_dates machinery
fetch_index() already uses for the TAIEX side of this exact mistake.
"""

import os
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import twse_client
import tpex_client
from config import DATA_DIR, PRICE_HISTORY_MONTHS

# Only TAIEX still goes through fetch_index()/yfinance -- TPEX switched to
# fetch_tpex_index() (TPEx's own official reports) in the THIRD pass of the
# TPEX OHLC GAP fix; see module docstring and run() below. Kept as a list
# (rather than inlining TAIEX's own two values into run()) so a future
# yfinance-sourced index can still be added the same way TAIEX already is.
#
# IMPORTANT: this list is now purely about *fetch mechanism* (which indices
# go through the generic yfinance-based fetch_index() loop), NOT about which
# indices exist or should be selectable in the UI. app.py's 大盤指數 tab used
# to build its "選擇指數" dropdown options directly from INDEXES; once TPEX
# was removed here (THIRD pass), TPEX silently vanished from that dropdown
# even though TPEX.csv is still fetched (via fetch_tpex_index() below) and
# still populated on disk. Found 2026-09-15 from the user's screenshot
# ("Currently, the index directly disappear in the list"). Fixed by adding
# DISPLAY_INDEXES below, which app.py now reads instead -- see that comment.
INDEXES = [
    {"code": "TAIEX", "yf_symbol": "^TWII", "name": "台股加權指數", "name_en": "TAIEX"},
]

# The full set of indices the UI should offer, independent of which backend
# function fetches each one's CSV. TAIEX -> fetch_index()/yfinance (via the
# INDEXES loop above); TPEX -> fetch_tpex_index() (TPEx's own reports, THIRD
# pass). Add a new index here whenever one becomes selectable, regardless of
# which fetch path it uses.
DISPLAY_INDEXES = [
    {"code": "TAIEX", "name": "台股加權指數", "name_en": "TAIEX"},
    {"code": "TPEX", "name": "櫃買指數", "name_en": "TPEX"},
]

FIELDNAMES = ["Date", "Open", "High", "Low", "Close", "Volume", "TradeValue"]

FMTQIK_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"

# TPEx's own equivalent of FMTQIK -- see the module docstring's TPEX TRADE
# VALUE ADDED section for the confirmed response shape and unit conversions.
TPEX_TURNOVER_URL = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41_result.php"

# TPEx's own official index-history report ("櫃買指數(月查詢)") -- TPEx's
# counterpart to TWSE's directly-verified MI_5MINS_HIST. See the module
# docstring's THIRD pass section: this URL follows the same
# <page>.php -> <page>_result.php convention already confirmed correct
# twice on this site, but has NOT itself been confirmed live (this
# sandbox is blocked from every tpex.org.tw /web/ path, this one included).
TPEX_INDEX_HISTORY_URL = "https://www.tpex.org.tw/web/stock/iNdex_info/inxh/inx_result.php"

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


def _tpex_index_date_to_iso(date_str):
    """Parses TPEX_INDEX_HISTORY_URL's date field specifically -- CONFIRMED
    LIVE (2026-09-15, "the data right now are merged in an incorrect way",
    from the user's own CSV export showing rows dated e.g. "3937-09-15")
    to NOT be ROC-formatted like every other TPEx/TWSE report this module
    reads (st41, FMTQIK). It returns plain Gregorian y/m/d (e.g.
    "2026/09/15"), not ROC "115/09/15" -- running it through _roc_to_iso
    (+1911 on the year) produced exactly year+1911 (2026 -> 3937), which is
    what confirmed the mismatch. This is actually GOOD news buried in a bug
    report: it confirms the guessed inx_result.php URL/endpoint itself is
    correct and returning real live TPEx OHLC data (values matched TPEx's
    known ~380-410 range) -- only the date field's format assumption was
    wrong.

    This mis-parse cascaded into what looked like a merge bug: the OHLC
    loop in _fetch_tpex_index_ohlc_and_volume() keyed its rows by the
    garbage "3937-..." iso string, while the Volume loop (a few lines
    later, reading the ALREADY-CONFIRMED-WORKING st41 report, still
    correctly ROC-parsed) keyed its rows by the real "2026-..." iso string
    -- so `month_lots.get(iso)` never found a match, and separately
    fetch_tpex_trade_value() (a fully independent function/fetch) wrote
    TradeValue under the real "2026-..." date. The result: real OHLC and
    real TradeValue for the same actual trading day ended up split across
    two different CSV rows under two different dates, never merging into
    one -- exactly the "merged in an incorrect way" symptom reported.

    Handles whichever format actually shows up, so this keeps working even
    if TPEx changes it later: if the first segment already looks like a
    plausible Gregorian year (>= 1911), use it as-is; otherwise treat it as
    ROC and add 1911, same as _roc_to_iso."""
    y, m, d = date_str.split("/")
    y = int(y)
    if y < 1911:
        y += 1911
    return f"{y:04d}-{int(m):02d}-{int(d):02d}"


def _purge_garbage_date_rows(existing_rows):
    """Strips any row whose Date is not a plausible real calendar date --
    specifically the rows the TPEX_INDEX_HISTORY_URL date-format bug wrote
    (see _tpex_index_date_to_iso's docstring above): dates like
    "3937-09-15" (2026 + 1911) instead of "2026-09-15". Returns
    (cleaned_rows, removed_count). fetch_tpex_index() writes the cleaned
    rows back to disk immediately, even before re-fetching, so a garbage
    row never lingers -- and removing it means the real month it belongs
    to is no longer seen as "already covered" by _months_with_real_ohlc(),
    so the corrected fetch re-requests it and the real date/row takes its
    place."""
    cleaned = {}
    removed = 0
    this_year = date.today().year
    for iso, row in existing_rows.items():
        try:
            y = int(iso[:4])
        except (ValueError, TypeError):
            cleaned[iso] = row
            continue
        if y > this_year + 1:
            removed += 1
            continue
        cleaned[iso] = row
    return cleaned, removed


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


def _months_with_real_ohlc(existing_rows):
    """Which (year, month)s already have REAL (non-blank) OHLC on file --
    same shape as _months_with_turnover() above, but keyed off Close
    instead of TradeValue. Used by _fetch_tpex_index_ohlc()'s own month
    targeting so it stays independent of fetch_tpex_trade_value()'s month
    tracking -- a month that already has TradeValue (from that function)
    but is still missing OHLC is exactly the TPEX OHLC GAP scenario the
    module docstring describes, and must still be treated as "not yet
    fetched" here even though _months_with_turnover() would call it
    done."""
    present = set()
    for iso, row in existing_rows.items():
        if row.get("Close"):
            y, m = int(iso[:4]), int(iso[5:7])
            present.add((y, m))
    return present


def _tpex_table(payload):
    """Pulls (fields, data) out of a TPEx JSON payload, tolerating either
    response shape seen on this site so far: nested under "tables" (like
    st41_result.php) or flat at the top level (like TWSE's own
    FMTQIK/MI_5MINS_HIST). Returns ([], []) if neither shape matches."""
    if "tables" in payload:
        tables = payload.get("tables") or []
        if not tables:
            return [], []
        return tables[0].get("fields", []), tables[0].get("data", [])
    return payload.get("fields", []), payload.get("data", [])


def _find_col(fields, *keywords):
    """Index of the first field whose Chinese label contains ALL the
    given keyword characters (e.g. _find_col(fields, "收") for a "收盤
    指數" column) -- used instead of a hardcoded column position for
    _fetch_tpex_index_ohlc()'s response, since that endpoint's exact
    field order/names have not been confirmed live (see module
    docstring's THIRD pass caveat). Returns None if no field matches."""
    for i, f in enumerate(fields):
        if all(k in f for k in keywords):
            return i
    return None


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


def _fetch_tpex_index_ohlc_and_volume(existing_rows):
    """Returns {iso_date: (open, high, low, close)} for TPEx's 櫃買指數,
    sourced ENTIRELY from TPEx's own official OHLC report -- NOT yfinance.
    See module docstring's TPEX OHLC GAP THIRD pass section for why: after
    two yfinance-side fixes still left `^TWOII` returning nothing, the user
    explicitly authorized switching TPEx's OHLC source away from yfinance
    (TAIEX is unaffected).

    OHLC comes from TPEX_INDEX_HISTORY_URL ("櫃買指數(月查詢)") -- columns
    are found by matching the response's own `fields` list for 開/高/低/收
    rather than a hardcoded position, since this specific endpoint's exact
    field order/names have not been confirmed live (this sandbox is
    blocked from tpex.org.tw).

    Despite the name, this does NOT return Volume (removed in the SIXTH
    pass, 2026-09-15, "How about the plot here?" -- see module docstring):
    an earlier version of this function also pulled Volume from
    TPEX_TURNOVER_URL (st41_result.php)'s 成交張數 field, but that is
    TPEx's WHOLE-MARKET board-lots total, not 櫃買指數's own trading
    volume -- the exact same category of mistake this module's TAIEX side
    already caught and fixed once (see VOLUME_CONTAMINATION_THRESHOLD /
    the CORRECTION note above). There is currently no known free source
    for 櫃買指數's own per-index Volume now that `^TWOII` has broken on
    yfinance, so this function no longer fabricates one; fetch_tpex_index()
    leaves Volume untouched going forward. The function name/signature is
    kept for now rather than renamed, to keep this diff minimal.

    Month targeting is keyed off _months_with_real_ohlc() (Close
    presence), NOT _months_with_turnover() (TradeValue presence) --
    deliberately independent of fetch_tpex_trade_value()'s own month
    tracking, so a month that already has TradeValue but is still
    missing OHLC (the exact TPEX OHLC GAP scenario) is correctly
    re-requested here rather than skipped."""
    result = {}
    present_months = _months_with_real_ohlc(existing_rows)
    target_months = _month_starts(PRICE_HISTORY_MONTHS)
    latest_present = max(present_months) if present_months else None

    for (y, m) in target_months:
        if (y, m) in present_months and (y, m) != latest_present:
            continue
        roc_param = f"{y - 1911}/{m:02d}"

        ohlc_payload = tpex_client.get_json(
            TPEX_INDEX_HISTORY_URL, params={"l": "zh-tw", "d": roc_param, "o": "json"}
        )
        if ohlc_payload and str(ohlc_payload.get("stat", "")).lower() == "ok":
            fields, data = _tpex_table(ohlc_payload)
            idx_open = _find_col(fields, "開")
            idx_high = _find_col(fields, "高")
            idx_low = _find_col(fields, "低")
            idx_close = _find_col(fields, "收")
            if idx_close is None:
                print(f"  [!] TPEx index-history response for {roc_param} had no recognizable "
                      f"Close column -- fields were: {fields}")
            else:
                for row in data:
                    try:
                        # NOT _roc_to_iso -- this endpoint's date field is
                        # plain Gregorian, not ROC, unlike every other
                        # report this module reads. See
                        # _tpex_index_date_to_iso's docstring for the full
                        # story (found 2026-09-15 from a user-reported
                        # "merged in an incorrect way" bug).
                        iso = _tpex_index_date_to_iso(row[0])
                    except (ValueError, IndexError):
                        continue
                    c = _num(row[idx_close]) if idx_close < len(row) else None
                    if c is None:
                        continue
                    o = _num(row[idx_open]) if idx_open is not None and idx_open < len(row) else None
                    h = _num(row[idx_high]) if idx_high is not None and idx_high < len(row) else None
                    lo = _num(row[idx_low]) if idx_low is not None and idx_low < len(row) else None
                    result[iso] = (o, h, lo, c)
        # else: common/expected (outside retention window, or this guessed
        # URL is wrong) -- next run will retry this month.

    return result


def fetch_tpex_index():
    """TPEx's OHLC fetch -- NOT yfinance. See module docstring's TPEX OHLC
    GAP THIRD pass section: after raise_errors=True and the start-date
    placeholder-row fix both failed to close the gap, the user explicitly
    asked to change methods for TPEx specifically. TAIEX is UNCHANGED and
    still uses fetch_index()/yfinance (`^TWII` has been working this whole
    time) -- only TPEX.csv's fetch path changes here.

    Writes Open/High/Low/Close only (Volume as of the SIXTH pass -- see
    below); TradeValue is untouched, still fetch_tpex_trade_value()'s job
    alone. See _fetch_tpex_index_ohlc_and_volume() for the actual sourcing
    (TPEx's own official index-history report) and its honesty caveats
    (the OHLC endpoint's exact URL/field names are an educated guess, not
    yet confirmed live -- the console output here says exactly what came
    back if it doesn't parse).

    SIXTH pass (2026-09-15, "How about the plot here?" -- the user's
    成交量 chart showed nothing before ~2026-07, then a sudden jump to
    0.5B-1.5B-scale bars): this function used to also write Volume from
    st41's 成交張數 (TPEx's WHOLE-MARKET board-lots total), a completely
    different and ~700x larger quantity than TPEX.csv's older,
    yfinance-sourced Volume (~1,000,000 scale, same order as TAIEX's own
    yfinance Volume) -- the exact same category of mistake this module's
    TAIEX side already caught and fixed once (see the CORRECTION note in
    the module docstring and VOLUME_CONTAMINATION_THRESHOLD). On a linear
    chart spanning both eras, the old millions-scale bars are invisible
    next to the new billions-scale ones, which is exactly what the user's
    screenshot showed. Fixed by no longer writing Volume from st41 at all
    (there is no known free source for 櫃買指數's own per-index Volume now
    that `^TWOII` is broken) and by clearing any already-written
    whole-market-scale Volume value below, reusing the same
    VOLUME_CONTAMINATION_THRESHOLD/_find_contaminated_dates machinery
    fetch_index() already uses for TAIEX."""
    code = "TPEX"
    print(f"[market_index] {code} OHLC (TPEx 櫃買指數 月查詢, NOT yfinance)")
    existing = _load_existing(code)

    existing, purged = _purge_garbage_date_rows(existing)
    if purged:
        print(f"  [!] removed {purged} row(s) with an impossible date (e.g. year 3937) -- these were "
              f"written by the now-fixed TPEX_INDEX_HISTORY_URL date-format bug; the real date will "
              f"be re-fetched below")
        _write_csv(code, existing)

    # SIXTH pass: clear any Volume value that's really TPEx's whole-market
    # total (成交張數 x1,000), not 櫃買指數's own volume -- see this
    # function's docstring above. Left blank afterward, same as any other
    # field with no reliable source, rather than substituting a different,
    # much larger quantity.
    contaminated = _find_contaminated_dates(existing)
    if contaminated:
        for iso in contaminated:
            existing[iso]["Volume"] = ""
        print(f"  [!] cleared {len(contaminated)} Volume value(s) that were really TPEx's "
              f"whole-market 成交張數 total, not 櫃買指數's own volume (Volume > "
              f"{VOLUME_CONTAMINATION_THRESHOLD:,}) -- left blank, no fabricated substitute written")
        _write_csv(code, existing)

    fetched = _fetch_tpex_index_ohlc_and_volume(existing)
    if not fetched:
        print("  -> no OHLC data returned")
        return

    for iso, (o, h, lo, c) in fetched.items():
        row = existing.get(iso) or {
            "Date": iso, "Open": "", "High": "", "Low": "", "Close": "", "Volume": "", "TradeValue": ""
        }
        row["Open"] = o if o is not None else ""
        row["High"] = h if h is not None else ""
        row["Low"] = lo if lo is not None else ""
        row["Close"] = c
        # Volume intentionally left untouched -- see docstring above.
        existing[iso] = row

    _write_csv(code, existing)
    print(f"  -> {len(fetched)} trading day(s) of TPEx OHLC merged in (inx_result.php); "
          f"Volume left as-is (no reliable per-index source)")


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


def _latest_real_ohlc_date(existing_rows):
    """The latest date in existing_rows that has REAL OHLC on file (a
    non-blank Close) -- deliberately excludes TradeValue-only placeholder
    rows (the ones fetch_taiex_trade_value()/fetch_tpex_trade_value()
    write for a date their report has but yfinance hasn't supplied OHLC
    for yet: Open/High/Low/Close/Volume all blank, only TradeValue set).
    Returns None if there are no rows with real OHLC at all.

    THE TPEX OHLC GAP BUG (found 2026-09-15, after raise_errors=True alone
    didn't fix the user's reported gap): fetch_index()'s old incremental
    logic computed its start date as `max(existing.keys())` -- ALL keys,
    placeholder rows included. Once fetch_tpex_trade_value() had written
    blank-OHLC placeholder rows for the whole 2026-07-20..2026-09-14 gap
    (each run adding that run's date), `max(existing.keys())` kept getting
    dragged forward to the LATEST such placeholder date -- e.g. "today" --
    so fetch_index() only ever asked yfinance for a tiny window starting
    almost at the present, and never once re-requested the actual gap
    dates before it. This happened regardless of whether `^TWOII` itself
    was working: even a fully healthy yfinance call could never backfill
    those older blank rows under the old logic, because they were never
    included in the requested date range again. Using the latest REAL
    OHLC date instead (here) means `start` correctly walks back to before
    the whole gap, so every gap date gets re-requested on the next run."""
    real_dates = [iso for iso, row in existing_rows.items() if row.get("Close")]
    return max(real_dates) if real_dates else None


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
            # date that has REAL OHLC already on file (inclusive, to
            # reconfirm a possibly-partial last day) through today -- same
            # "always reconfirm the most recent point" idea price_data.py
            # uses for its most recent month. Deliberately NOT
            # `max(existing.keys())` -- see _latest_real_ohlc_date()'s
            # docstring (the TPEX OHLC GAP BUG) for why that was wrong: it
            # let TradeValue-only placeholder rows drag `start` past a
            # real, unfilled gap that then could never be re-requested.
            latest_real = _latest_real_ohlc_date(existing)
            if latest_real is not None:
                start = date.fromisoformat(latest_real)
            else:
                # Every row on file is a TradeValue-only placeholder (no
                # real OHLC anywhere yet) -- treat this the same as a
                # brand-new CSV rather than picking an arbitrary date.
                start = date.today() - timedelta(days=PRICE_HISTORY_MONTHS * 31)
    else:
        start = date.today() - timedelta(days=PRICE_HISTORY_MONTHS * 31)

    try:
        # raise_errors=True (added 2026-09-15, in response to the TPEX OHLC
        # GAP investigation -- see module docstring): without it, yfinance
        # can silently return an empty DataFrame with NO exception at all --
        # which is exactly what happened for `^TWOII` from 2026-07-20 onward,
        # and gave no way to tell "genuinely no trading today" apart from
        # "something is actually broken". With raise_errors=True, the same
        # failure instead raises (e.g. YFPricesMissingError: "possibly
        # delisted; no price data found"), which the except block below now
        # prints -- so the NEXT time this happens, the console output itself
        # says why, instead of just "no data returned".
        hist = yf.Ticker(yf_symbol).history(start=start.isoformat(), interval="1d", raise_errors=True)
    except Exception as e:
        print(f"  [!] fetch failed: {e}")
        return

    new_rows = _rows_from_history(hist)
    if not new_rows:
        print("  -> no data returned (empty result, but no exception raised)")
        return

    # Merge field-aware, NOT existing.update(new_rows) -- found this bug
    # while testing the TPEX OHLC GAP fix: _rows_from_history() always sets
    # "TradeValue": "" in every row it returns (yfinance has no TradeValue
    # of its own), and a blind dict.update() replaces the WHOLE existing
    # row for that date -- so the moment yfinance successfully returns OHLC
    # for a date that already had a real TradeValue (written earlier by
    # fetch_taiex_trade_value() as a placeholder row, or even after a
    # normal successful day), that TradeValue would silently get wiped
    # back to blank. Preserving whatever TradeValue is already on file
    # keeps this function honestly scoped to OHLC/Volume only, matching
    # what its own docstring already claimed.
    for iso, row in new_rows.items():
        if iso in existing:
            row["TradeValue"] = existing[iso].get("TradeValue", "")
        existing[iso] = row

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
    fetch_tpex_index()
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
