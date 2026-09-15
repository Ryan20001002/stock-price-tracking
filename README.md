# Taiwan Stock Tracker

Pulls share price history, official dividend distribution data, and recent
news headlines for a watchlist of TWSE-listed companies, using only free,
no-key-required public data sources. This is step one of the project:
building up a clean historical dataset that the later prediction app will
train on.

## Project structure

```
config.py               Watchlist + all settings (edit this, not the scripts below)
main.py                 Orchestrator: python main.py [--prices] [--dividends] [--news] [--market-value] [--institutional] [--market-index]
app.py                   Streamlit webpage over everything below: streamlit run app.py

twse_client.py           Shared rate-limited/retrying HTTP client
price_data.py             Daily OHLC price history (TWSE STOCK_DAY)
dividend_data.py          Dividend payment history (yfinance)
news_data.py               Recent headlines (Google News RSS)
market_value_data.py    Shares outstanding + market value (opt-in; TWSE fund dataset + yfinance fallback)
institutional_data.py  三大法人 (foreign/investment-trust/dealer) daily net buy-sell (opt-in; FinMind)
market_index_data.py  TAIEX + TPEx (OTC) index history (opt-in; yfinance -- see "Market indices" below)
splits.py                     Stock-split detection/adjustment, used by predict_dividends.py

predict_dividends.py     Next-payment prediction (two methods) -- also the shared, split-adjusted
                         data loaders (load_dividends/load_prices) that ddm_valuation.py imports
ddm_valuation.py          Dividend Discount Model valuation (standalone: python ddm_valuation.py)

data/                    All fetched/computed output (gitignored -- regenerate by running the
                         scripts above, don't expect this folder in a fresh git clone)
  prices/<code>.csv          Daily OHLC per ticker
  dividends/dividends.csv   Combined dividend history for the whole watchlist
  dividends/*.csv            Prediction/valuation outputs (dividend_prediction.csv,
                             dividend_prediction_slots.csv, ddm_valuation.csv)
  news/news.csv               Combined headlines for the whole watchlist
  shares/<code>.csv          Accumulated shares-outstanding snapshots per ticker
  market_value/<code>.csv   Daily market value per ticker (price x shares outstanding)
  institutional/<code>.csv  Daily 外資/投信/自營商 net buy-sell per ticker
  market_index/<code>.csv  Daily OHLC for TAIEX/TPEX (see "Market indices" below)

requirements.txt        pip install -r requirements.txt
.gitignore
README.md               You are here
```

Every script other than `main.py` can also be run directly (e.g.
`python ddm_valuation.py`) for just that one step -- `main.py` just calls
them in sequence based on which flags you pass it.

## Data sources

| Data | Source | Notes |
|---|---|---|
| Share price (daily OHLC + volume) | TWSE legacy `STOCK_DAY` report (`www.twse.com.tw`) | One HTTP call per stock per month; historical backfill loops over months. Works for both stocks and ETFs. |
| Dividends | `yfinance` (Yahoo Finance) per-ticker dividend history | Actual paid distributions with ex-dividend date + amount. Tries the `.TW` (TWSE) symbol first, falls back to `.TWO` (TPEx/OTC) if empty. |
| News | Google News RSS search, scoped to Traditional Chinese / Taiwan | No API key; returns recent headlines with title, source, publish date, and link. |
| Shares outstanding / market value | TWSE OpenAPI dataset `t187ap47_L` (falls back to `yfinance` for non-fund tickers) | Opt-in. Confirmed working live for all 3 watchlist tickers -- see "Shares outstanding and market value" below. |
| 三大法人買賣超 (foreign/investment-trust/dealer net buy-sell) | TWSE legacy `T86` report (`www.twse.com.tw`) | Opt-in. One HTTP call per **day** (covers every listed stock at once, then filtered down to the watchlist) rather than per stock -- see "Institutional investor net trading" below. |

All of this is public data these providers publish for anyone to query,
but none of it has a documented rate limit or terms-of-service guarantee,
so this tool is deliberately slow and polite (throttled requests, retries
with backoff) rather than fast. Don't crank up the request rate.

Dividends deliberately do **not** use TWSE's own `t187ap45_L` dataset
("上市公司股利分派情形") — that dataset only covers individual listed
*companies'* board-resolved distributions and has no ETF data at all, so
it returns nothing for tickers like 0050/00878/006208. `yfinance` covers
both uniformly and gives actual paid amounts + dates, which is also the
more directly usable shape for the later prediction step.

## Setup

```
pip install -r requirements.txt
```

Requires Python 3.8+. Dependencies: `requests`, `yfinance` (which pulls in
`pandas` as well), `numpy` (used directly by `ddm_valuation.py`'s
regression/AR(1) growth models), and `streamlit` (the webpage, including
its simple built-in username/password login -- see "Login and personal
watchlists" below).

## Usage

```
python main.py                # fetch everything: prices + dividends + news
python main.py --prices       # just price history
python main.py --dividends    # just dividend data
python main.py --news         # just news headlines
python main.py --market-value # shares outstanding + market value (opt-in --
                               # NOT included in the plain `python main.py` run above)
```

Output lands under `data/`:

```
data/
  prices/
    2330.csv       # one CSV per ticker: Date, Open, High, Low, Close, Change, Volume, TradeValue, Transactions
    2317.csv
    ...
  dividends/
    dividends.csv  # one row per dividend payment: code, name, name_en, symbol, ex_dividend_date, dividend_per_share
  news/
    news.csv       # one row per headline, all watchlist companies combined
  shares/
    0050.csv       # (opt-in, see below) date, shares_outstanding, source
  market_value/
    0050.csv       # (opt-in, see below) Date, Close, SharesOutstanding, MarketValue
```

**Price history** is incremental: the first run backfills
`PRICE_HISTORY_MONTHS` (default 36) months per ticker, which takes a
while (roughly `tickers x months` requests, ~1.5s apart). After that,
re-running only fetches the current month plus any months genuinely
missing from the CSV, so daily/weekly re-runs are fast.

**Dividends and news** are small enough that each run just re-fetches and
overwrites the CSV with a fresh snapshot.

## Webpage (Streamlit)

```
streamlit run app.py
```

Run this from the project folder, same as the commands above -- it opens
a dashboard in your browser (usually `http://localhost:8501`, opened for
you automatically). You'll be asked to log in first, create an account,
or continue as a guest (see "Login and personal watchlists" below -- no
setup needed, it just works), then you get a tab per data source (Prices,
Dividends, Market value, DDM valuation, News) plus an Overview tab with
quick per-ticker metrics, all scoped to your own personal watchlist. The
sidebar has a "我的追蹤清單" section for adding/removing tickers from your
own list -- see "Editing the watchlist" below.

The Prices tab shows a candlestick chart (紅漲綠跌, the Taiwan market
convention -- the opposite of the US red/green) plus a daily volume bar
chart, with a 1週/1個月/3個月/6個月/1年/3年/全部/自訂 date-range picker.
Directly below that (same tab, same ticker, same date range -- no second
selector needed) are the three 三大法人買賣超 (外資/投信/自營商 net
buy-sell) bar charts, see "Institutional investor net trading" below.

`app.py` doesn't contain any data-fetching or calculation logic of its
own -- it only reads whatever's already in `data/*.csv` and displays it,
and its sidebar buttons call the exact same `run()` functions the
command-line scripts above use (`price_data.run()`,
`ddm_valuation.run()`, etc.), so refreshing from the browser does exactly
what running the matching command in PowerShell does, just with a click.
Each fetch button hits TWSE/yfinance live and is just as throttled as
running the script directly, so it can take a little while -- the
console output each script normally prints is captured and shown in an
expandable panel under the button, so you're not left staring at a blank
spinner.

If `data/` is empty (e.g. right after a fresh `git clone`), every tab
will say so and point at the sidebar button that fills it in. The two
"Recompute" buttons (dividend prediction, DDM valuation) don't hit the
network at all -- they just reprocess whatever's already on disk, so
they're fast and safe to click freely.

**Putting this online:** the setup above runs entirely on your own
machine. To get a real, shareable URL (useful for showing your
dissertation committee without your laptop open), the free option is
[Streamlit Community Cloud](https://streamlit.io/cloud) -- it deploys
straight from a GitHub repo, which is exactly what you're setting up
separately. Once this project is pushed to GitHub, deploying there is:
sign in with your GitHub account, pick this repo and `app.py` as the
entry point, and it builds and hosts it for you. One thing to check once
you get there: whether TWSE/yfinance are reachable from Streamlit Cloud's
servers the same way they are from your own machine -- rate limits and
network access can behave differently on a hosted service, so the first
live "Fetch" click there is worth watching closely.

**One thing to know about login on Streamlit Community Cloud specifically:**
accounts (and personal watchlists) are stored in `data/users.json`, the
same local file as everything else under `data/` -- see "Login and
personal watchlists" below for what that means in practice (accounts
don't survive the app "sleeping" and waking back up).

## Editing the watchlist

There are now two different, deliberately separate lists — see "Login and
personal watchlists" below for the full reasoning:

- **`config.WATCHLIST`** — the shared/global registry every fetch script
  (`price_data.py`, `dividend_data.py`, etc.) reads. Adding a ticker here
  means "start collecting data for this ticker, for everyone who uses
  this app." Backed by `data/watchlist.json`, which takes priority over
  `DEFAULT_WATCHLIST` in `config.py` once it exists (e.g. because someone
  has added a ticker at least once). Edit `DEFAULT_WATCHLIST` in code if
  you want to change the very-first-run default.
- **Your personal watchlist** — which of the tickers already in that
  shared registry *you* want to see, set from `app.py`'s "我的追蹤清單"
  sidebar section after logging in. This is per-account, not per-file —
  see the next section.

In practice you mostly only interact with the second one: the sidebar's
add-ticker form handles both automatically — if the ticker you're adding
is brand new to the whole app, it gets added to the shared registry too,
with the Chinese/English names you typed; if it's already known, only
your personal list changes. You can find a TWSE code by searching
"`<company/ETF name>` 股票代號" or looking it up on `isin.twse.com.tw`.

`data/watchlist.json` is generated/local state, same as everything else
under `data/` — excluded by `.gitignore`, so it won't get committed to
your GitHub repo. A fresh clone starts back at `DEFAULT_WATCHLIST` until
someone adds tickers again (which then also seeds the shared registry for
every account that logs in afterward).

Other knobs in `config.py`:
- `PRICE_HISTORY_MONTHS` — how many months of price history to backfill.
- `REQUEST_DELAY_SECONDS` — pause between TWSE requests (raise this if you
  start seeing repeated `[warn] request failed` messages, which usually
  means TWSE is throttling you).
- `NEWS_ITEMS_PER_COMPANY` — how many headlines to keep per company.

## Login and personal watchlists

The webpage asks you to log in before showing anything — a simple
built-in username/password account, created right there on the login
screen (a "註冊新帳號" tab next to "登入"). No Google account, no OAuth,
no external setup of any kind — it works the moment you run `streamlit
run app.py`. There's also a **guest mode** ("以訪客身分瀏覽") for anyone
who doesn't want an account: it lets you search for and add ANY TWSE
ticker, including one the app has never seen before, but the resulting
watchlist only lives in that browser tab's session state — nothing about
the guest themselves is written to `data/users.json` or anywhere else on
disk, and it's gone the moment the tab is closed or "結束訪客模式" is
clicked. Adding a brand-new ticker does write to the shared
`config.WATCHLIST` registry (same as it would for a logged-in account) —
that's expanding the public, shared list of tickers the app tracks at
all, not information about the guest, so it doesn't conflict with the
"don't store anything about a guest" rule. If a guest adds a ticker
that's new to the whole app, no data exists for it yet until someone
(the guest themselves, or anyone else) clicks a "更新資料" fetch button.

Two lists exist and are kept deliberately separate:

- The **shared/global registry** (`config.WATCHLIST`, above) — which
  tickers the app collects data for at all. Shared across everyone, since
  there's no reason to fetch the same TWSE/yfinance data twice for
  different people.
- Each account's own **personal watchlist** — which of those tickers
  *that account* wants to see. Every tab (Overview, Prices, Dividends,
  Market value, DDM valuation, News) only shows this list, not the full
  shared registry.

Accounts, password hashes, and personal watchlists all live in one local
file, `data/users.json` (via `user_store.py` — see its docstring for the
full design and trade-offs). A few things worth knowing:

- **Passwords aren't stored in plain text.** Each account gets its own
  random salt, and only a hash of the password is saved — but this is a
  simple, non-audited implementation, not a vetted auth library, so
  don't reuse a password here that matters elsewhere.
- **There's no identity verification.** Anyone who knows the app's URL
  can register any username they like — fine for a dissertation project
  used by yourself and a few known people, not meant as a public-facing
  login system.
- **No email verification or password reset.** Losing a password means
  making a new account (or manually editing `data/users.json`, which is
  plain JSON).
- **On Streamlit Community Cloud specifically:** `data/users.json` is a
  local file, same as every CSV under `data/` — it does **not** survive
  the app "sleeping" after inactivity and waking back up, or a redeploy
  (see "Putting this online" above). That means accounts created there
  can disappear and need to be recreated. Running locally, this file just
  persists normally like any other file on disk. If accounts need to
  survive Community Cloud restarts reliably, that would mean moving this
  storage to an external service later (e.g. a small hosted database) —
  not something the current design does.

Nothing here needs `.streamlit/secrets.toml` any more — an earlier
version of this feature used Google sign-in and a Google Sheet, which
did; if you still have a `.streamlit/secrets.toml` file (or its
`.example` template) from that, it's safe to delete.

## Shares outstanding and market value (opt-in)

```
python main.py --market-value
# or directly:
python market_value_data.py
```

Requires `data/prices/<code>.csv` to already exist (run `--prices` at
least once first).

**Data source, and why it changed:** the first version of this tried
`yfinance` for shares outstanding, same as the dividend fetcher -- but
live testing showed it returns *nothing* for these ETFs (no
`get_shares_full()` history, no `.info` snapshot either). It turns out
Yahoo just doesn't track ETF unit counts well for TWSE-listed funds. TWSE
publishes this itself, though: its OpenAPI dataset `t187ap47_L`
("基金基本資料彙總表" -- fund basic information) has a
"發行單位數/轉換數" (units outstanding) field, one call covering every
fund. Confirmed live: 0050 = 22,325,000,000 units, 006208 = 1,860,540,000
units (both as of 2026-09-03). `yfinance` is kept as a fallback for any
ticker *not* found in that fund dataset (e.g. an ordinary stock, which
wouldn't appear in a fund-only list).

Since TWSE's dataset only ever gives "the figure as of today," there's no
way to get retroactive history from it -- instead, each run **adds**
today's figure to `data/shares/<code>.csv` (one row per date you've run
it), so running this regularly builds up a real day-by-day series over
time, the same way `price_data.py` accumulates price history.

1. Fetch the whole TWSE fund dataset (one call), look up each ticker's
   units outstanding by code, append today's value to
   `data/shares/<code>.csv` (with a `source` column: `twse_fund_dataset`
   or, for the yfinance fallback, `history`/`snapshot_only`).
2. Join that against the daily price history, forward-filling the most
   recently known shares-outstanding figure onto each trading day, and
   compute `MarketValue = Close x SharesOutstanding`. Saved to
   `data/market_value/<code>.csv`.

For an ordinary stock this is standard market capitalization. For an ETF
it's a proxy for assets under management (AUM) -- unit price x units
outstanding -- not a company's market cap in the usual sense.

**Still opt-in.** Confirmed working live for all 3 watchlist tickers
(2026-09-07): 0050 = 22,067,500,000 units (≈NT$2.43T market value), 00878
= 18,830,290,000 units (≈NT$648B), 006208 = 1,860,540,000 units
(≈NT$468B). Keep in mind: there's no row in `data/market_value/<code>.csv`
for any date *before* your first collected snapshot -- the forward-fill
has nothing to fill in with that far back, so those earlier trading days
are simply skipped rather than approximated (the file fills in and gets
more complete the longer you keep running this regularly). Also, ETF
units outstanding can still shift between report dates (authorized
participants create/redeem daily), so even the real day-by-day series is
an approximation, not a precise intraday figure.

## Institutional investor net trading (三大法人買賣超, opt-in)

```
python main.py --institutional
# or directly:
python institutional_data.py
```

Fetches daily net buy/sell volume (買賣超 -- shares bought minus shares
sold that day; positive = net bought, negative = net sold) for the three
groups TWSE tracks separately: 外資 (foreign investors), 投信
(investment trusts), and 自營商 (securities dealers).

**Backfill window is deliberately identical to the price history's** --
this fetches the same `config.PRICE_HISTORY_MONTHS` calendar months back
from today (default 36, i.e. 3 years) that `price_data.py` does, per
explicit request to keep the two histories covering the same span. It's
computed independently (walking day-by-day rather than month-by-month)
but always lands on the same starting month.

**Data source and why the first run is slow:** TWSE's `T86` report
(`三大法人買賣超日報`) is shaped differently from the price/dividend
sources above -- one call returns *every* listed stock for *one*
calendar day, rather than one stock's whole history in a call. That
means a full backfill costs roughly one request **per trading day**, not
per ticker -- at the default 36-month window, that's on the order of
700-780 requests, or roughly **20-30+ minutes** at
`REQUEST_DELAY_SECONDS`'s pace (more with network latency/retries). This
is a one-time cost: every run after the first only fetches days still
missing for at least one watchlist ticker (same incremental idea as
`price_data.py`), so daily re-runs are fast. Progress is checkpointed to
disk every 20 requests and printed to the console, so an interrupted run
doesn't lose everything and a re-run picks up roughly where it left off.

Because of that first-run cost, running `python institutional_data.py`
directly (so you can watch progress in a terminal) is more comfortable
for the very first backfill than clicking the button in the app and
waiting on a spinner. In the app it's folded into "🔄 一鍵抓取全部資料"
(fetched last, after prices/dividends/news/market-value) but also has its
own standalone sidebar button ("抓取三大法人買賣超") if you just want to
refresh this one thing. On the command line it's still opt-in via
`--institutional` (kept out of `python main.py`'s plain default run, same
as `--market-value`, so a bare `python main.py` stays fast) -- but note
raising `PRICE_HISTORY_MONTHS` raises this fetcher's cost right along
with it, since the two are now tied together.

Field names are read out of TWSE's response by name, not position (TWSE
has reworded/reordered T86's columns before). 外資 net is the sum of the
two sub-columns TWSE publishes (`外陸資買賣超股數(不含外資自營商)` +
`外資自營商買賣超股數`) since there's no single official combined
column; 投信 and 自營商 net are each TWSE's own published aggregate
column, used as-is rather than re-derived from finer sub-splits.

Saved to `data/institutional/<code>.csv`. In the app, this shows up
directly below the candlestick/volume charts on the **Prices** tab (not
a separate tab) -- three bar charts (外資/投信/自營商 net buy-sell) for
whichever ticker and date range you've already picked there, no second
selector needed.

## Market indices (TAIEX / TPEx)

```
python main.py --market-index
```

Fetches daily OHLC history for Taiwan's two headline market indices, via
`yfinance`:

- **TAIEX** -- the TWSE main-board weighted index (台股加權指數), Yahoo
  Finance symbol `^TWII`.
- **TPEx** -- the Taipei Exchange composite index (櫃買指數), Taiwan's
  over-the-counter "second board" alongside the TWSE-listed market
  everything else in this project tracks, Yahoo Finance symbol `^TWOII`.

Added 2026-09-15, by request ("add a page to store TAIEX and TSEA").
"TSEA" isn't a real ticker or index name -- a clarifying question
confirmed the TPEx/OTC index above is what was meant, so that's what's
implemented; the code (`market_index_data.py`) keeps "TSEA" as a comment
only, in case that name comes up again.

Unlike every other fetch script in this project, these two aren't
per-WATCHLIST-ticker data -- they're market-wide index values, so they
don't depend on (or show up in) your personal watchlist at all. Output
is saved to `data/market_index/TAIEX.csv` and `data/market_index/TPEX.csv`,
and shown in the app's own "大盤指數" tab (a dropdown to pick which
index, the same quick-range chart/table as the 股價 tab uses per-ticker).
Like `--market-value` and `--institutional`, this is opt-in for now, not
part of the default `python main.py` run -- see `market_index_data.py`'s
own docstring for why (it's new, and untested against a live Yahoo
Finance response from the environment it was built in -- built and unit-
tested against a mocked response instead; run it once and check the
output before relying on it).

**Trading volume / trading value** (added 2026-09-15, by request -- "add a
column to store total value of transactions, and add the unit for the
trading quantity"). `yfinance` has no trading-value (turnover, in NT
dollars) field at all for any ticker, and its Volume field is documented
as unreliable specifically for index tickers like these two (an index
itself isn't "traded" -- only its constituent stocks are; see
`market_index_data.py`'s docstring for the upstream yfinance issue this is
based on). So the two columns are sourced differently per index, and
that's surfaced honestly rather than papered over:

- **TAIEX** -- Volume (成交量, unit: 股/shares) and TradeValue (成交金額,
  unit: 元/NT dollars) both come from TWSE's own free, no-API-key FMTQIK
  report (the whole-market daily summary), overwriting yfinance's
  unreliable Volume figure. This is real, verified-live official exchange
  data.
- **TPEx** -- no free whole-market turnover source could be found (TPEx's
  own site blocks every deeper path from this project's build
  environment, and the one TPEx OpenAPI endpoint found is per-stock, not
  a whole-market total). TradeValue is left genuinely **blank** for TPEx
  rather than estimated or fabricated -- the app's 大盤指數 tab explains
  this gap in its own caption, and skips drawing an empty TradeValue chart
  for TPEx rather than showing a misleading blank one. TPEx's Volume still
  comes from `yfinance` as before, with the same index-ticker reliability
  caveat as always.

Both `PRICE_COLUMNS_ZH` (股價 tab, per-ticker) and `MARKET_INDEX_COLUMNS_ZH`
(大盤指數 tab) now label Volume/TradeValue with their units (成交量（股）／
成交金額（元）) on screen -- the underlying CSV column names themselves are
unchanged (`Volume`, `TradeValue`), so nothing that reads those CSVs
elsewhere in this project needed updating.

## Keeping data fresh

Since this only fetches — it doesn't schedule itself — the simplest way to
keep the dataset current is to run `python main.py` on a recurring basis
(e.g. once a day after market close, ~14:00 Taiwan time). On Windows,
Task Scheduler can run `python main.py` on a schedule; happy to wire that
up (or a simpler always-on version) if useful.

## Predicting the next dividend: two methods, side by side (trailing 1 year)

```
python predict_dividends.py
```

Reads `data/dividends/dividends.csv` (and `data/prices/<code>.csv` for
Method B) and, per ticker, restricts to the **trailing 1 year** of
payments (365 days back from that ticker's most recent payment on file)
-- deliberately a short window, not the fund's whole multi-year history,
so results reflect its current payout trend rather than being dragged
around by years-old performance. It then computes two predictions from
that same window:

- **Method A -- growth rate**: assumes the dividend *amount* grows at a
  constant annual rate, computed two ways that work together:
  - **The whole-year annual growth rate** (frequency fix + longer-window
    fix, both 2026-09-14): a trailing multi-year **total** of payments
    compared to the multi-year total immediately before that -- a
    rolling period-over-period comparison, not a payment-to-payment
    ratio (frequency fix) -- with each comparison window spanning
    `GROWTH_WINDOW_YEARS` (2 by default, not 1) so one unusually large or
    small year can't dominate the whole estimate on its own (longer-window
    fix, by explicit follow-up request: "I don't think one year is
    enough"). The resulting period ratio is then **annualized** (nth
    root) so it's still expressed as a true per-year rate. This matters
    because a fund's payments aren't evenly sized across the year: 0050
    and 006208, for instance, consistently pay more in one payday than
    the other -- comparing consecutive payments (the original approach)
    mistook that normal seasonal split for "growth" or "decline"
    depending on which half of the year you looked at; comparing full
    trailing periods to each other cancels the seasonal pattern out and
    isolates real year-over-year change, regardless of whether a fund
    pays annually, semi-annually, or quarterly. Reported for
    transparency as a read on the fund's overall payout trend, and used
    as a fallback (see next point).
  - **The actual next-payment prediction** (same-month fix, 2026-09-14):
    applying the whole-year rate straight to "whichever payment was most
    recent" turned out to have its own problem for a fund that pays
    unevenly -- e.g. 0050 pays more in January than July every year, so
    predicting right after a July payment by growing July's (smaller)
    amount produces a number sized for the wrong payday, since the
    *actual* next payment is the following (larger) January. Fixed by
    predicting each payment from its own calendar-month's history
    instead: the upcoming payment's month is identified from the fund's
    current payment cycle (read off the trailing 1 year, so old one-off
    payments in stale months from years back can't hijack this -- see
    below), that month's own payments over up to the last 5 years are
    pulled, and a growth rate is computed as the **geometric mean** of
    the year-over-year ratios between consecutive same-month payments --
    applied to that slot's own most recent payment. A slot with fewer
    than `SAME_MONTH_MIN_SAMPLES` (3, raised from 2 in the same
    longer-window follow-up) same-month payments on file falls back to
    the whole-year rate instead, applied to that slot's own last payment.
    `predicted_next_1yr_total` is rebuilt the same way, bottom-up: every
    slot in the current cycle gets its own prediction and they're summed.
  Needs roughly `2 * GROWTH_WINDOW_YEARS` (4 by default) years of payment
  history for the whole-year rate (a full trailing window plus a full
  prior window to compare it to); with less than that, there's nothing to
  fall back on either, and Method A reports "insufficient data."
- **Method B -- yield**: assumes the dividend *yield* (dividend ÷ share
  price) holds roughly steady, which can fit an ETF better since payout
  scales with the fund's price/NAV level rather than its own growth
  curve. Matches each payment to the closing share price on/before that
  date, computes `yield_i = dividend_i / price_i`, takes the plain
  (arithmetic, not geometric -- yield is a level, not a compounding step)
  mean, and predicts next payment = latest share price × mean yield.

Output is saved to two files: `data/dividends/dividend_prediction.csv`
(one row per ticker -- both methods' summary figures, including the
prediction for whichever payment slot comes up next) and, new
2026-09-14 alongside the app's dashboard redesign,
`data/dividends/dividend_prediction_slots.csv` (one row per ticker
**per payment month** -- e.g. 0050 gets a January row AND a July row,
each with its own last-payment amount, same-month growth rate, and
predicted next amount for that slot). The single-row file only ever
shows the soonest-upcoming slot; the per-slot file is what lets you see
every payday's own prediction at once, which is what the dashboard's
"逐月預測" (month-by-month) table under each ticker in the 股利 tab
renders.

**Split-adjusted.** 0050 did a 1-for-4 split on 2025-06-18 (confirmed
both in TWSE's own price history and in public news coverage).
`load_dividends()`/`load_prices()` (in `predict_dividends.py`, shared by
this script and `ddm_valuation.py`) rescale everything before a split to
current-share terms, so a growth rate or trend computed across the split
date compares like with like. This doesn't change the numbers above --
the trailing-1yr window here only spans post-split payments right now --
but it matters a lot for `ddm_valuation.py`'s multi-year growth fit,
which does span the split; see that section.

Note on *how* splits are found (`splits.py`): the first version of this
tried to auto-detect splits purely from yfinance's `.splits` field --
on a live run that came back **completely empty** for 0050, the same
kind of Taiwan-ETF data gap already hit once with shares outstanding.
So `KNOWN_SPLITS` in `config.py` is now the authoritative source (0050's
2025-06-18 split is listed there, manually verified), yfinance's
`.splits` is merged in as a bonus if it ever does have something, and a
price-jump sanity check (`detect_unexplained_jumps`) scans each ticker's
own price history and prints a warning if it finds something split-sized
that isn't in `KNOWN_SPLITS` -- so an un-catalogued future split (006208
splitting, say -- there's been speculation about this, unconfirmed) gets
flagged instead of silently corrupting results. If that warning ever
fires, verify it and add the real event to `KNOWN_SPLITS`.

**Latest run** (against the data pulled above, using the same-month +
longer-window-fixed Method A):

| Ticker | Whole-year growth (annualized, 2yr windows) | Next payment slot | Same-month growth (that slot) | Method A prediction | Method B: mean yield | Method B prediction |
|---|---|---|---|---|---|---|
| 0050 (元大台灣50) | +97.5% | January (5yr same-month history) | +49.5% | **1.4953** | 0.989% (price 109.90) | **1.0870** |
| 006208 (富邦台50) | +38.3% | November (5yr same-month history) | +20.4% | **4.1513** | 2.226% (price 251.65) | **5.6027** |
| 00878 (國泰永續高股息) | +21.6% | November (5yr same-month history) | +9.3% | **0.4373** | 2.325% (price 34.39) | **0.7994** |

(The whole-year figures above already reflect the LONGER-WINDOW FIX --
comparing 2-year totals on each side, then annualizing -- rather than the
single-year comparison used earlier. 0050 dropped from +202.6% to +97.5%
and 006208 from +334.0% to +38.3% purely from widening the comparison
window; see the caveats below for why 0050's number is still elevated.)

Notice the whole-year growth rate and the actual prediction can diverge
sharply (0050: +97.5% whole-year vs. +49.5% for the January slot
specifically) -- that's the same-month fix doing its job: the whole-year
number is still a fair read on the fund's aggregate payout trend, but the
prediction itself comes from the specific slot's own history, not from
scaling whichever payment happened to be most recent. This run also
surfaced two real caveats worth understanding rather than taking the
numbers at face value:

- **0050's January history still partly straddles its 2025-06-18
  split.** Of the 5 Januaries used (2022-2026), the 4 pre-split ones are
  all divided by 4 to stay in current-share terms while 2026's isn't --
  so the single ratio crossing that boundary (0.169 -> 1.00, roughly 6x)
  dominates the geometric mean and inflates the +49.5% figure well above
  what 0050's real post-split payout trend probably is. This fades out
  naturally as more post-split Januaries accumulate (by January 2027
  there'll be 2 post-split years in the window instead of 1); until then,
  treat 0050's Method A prediction with extra skepticism -- Method B's
  1.0870, which doesn't depend on multi-year history at all, is arguably
  more trustworthy right now.
- **006208's November slot is dominated by one big jump.** Its 5
  Novembers are 1.641, 1.03, 0.861, 0.9, 3.448 -- the last one nearly
  quadrupling the year before it drives most of the geometric mean's
  +20.4%. That's a real reported payment, not a seasonal or split
  artifact (unlike 0050, 006208 hasn't split), but a geometric mean over
  just 4 year-over-year steps still isn't a large enough sample to be
  robust to one outsized payment. Worth checking whether that jump
  reflects a one-time special distribution or a genuine step-change in
  payout policy before trusting the extrapolation.
- **00878** is the cleanest read here: no split, and while its August
  slot shows a similarly large jump (0.66 -> 1.01, +37.8%, see the
  per-slot breakdown the script prints), the slot actually being
  predicted -- November, +9.3% -- is comparatively steady. Method A
  (0.4373) and Method B (0.7994) still disagree by roughly 2x even so,
  which is itself the useful signal: even on the cleanest ticker here,
  no single number should be taken as settled.

Neither method is "more correct" in general -- they encode different
assumptions about *why* a fund's distribution changes (chasing a target
dollar amount vs. chasing a target yield), and this data doesn't settle
which fits better yet. See `predict_dividends.py`'s docstring for the
full caveat list (including the two above, plus how stale one-off
payments from years ago are excluded from the current payment cycle).
Worth watching both against whatever the next real payment turns out to
be. Treat both as transparent baselines, not investment advice.

## DDM valuation: is the current price justified by future dividends?

```
python ddm_valuation.py
```

This is a different question from the section above: instead of "what's
the next payment likely to be," it asks "given a projected stream of
future dividends, what should this unit be worth today, and how does
that compare to its actual price?" That's a Dividend Discount Model (DDM)
-- the standard finance approach for valuing something purely from the
cash it's expected to pay out, discounted back to today at a required
rate of return.

**Design choice: a finite horizon, not a Gordon Growth terminal value.**
The textbook DDM assumes a company pays dividends forever and collapses
"forever" into one formula, `D/(r-g)`, using a single assumed long-run
growth rate `g`. We deliberately did not build it that way here, because
that formula divides by `(r - g)` -- when the required return and the
growth rate are close (common for a mature fund), that division blows up
and the result stops meaning anything. We hit exactly this in testing:
a small tweak to the assumed growth rate swung one ticker's "intrinsic
value" by more than 30x. So instead:

- **Required rate of return (r)** is estimated once via CAPM
  (`r = risk-free rate + beta x equity risk premium`, beta estimated by
  regressing each ticker's daily returns against the TAIEX) and held
  **constant** across the whole horizon -- not modeled as changing over
  time.
- **Growth rate** is where the modeling effort goes instead. Rather than
  one assumed number, two different models are fit to each ticker's own
  *annual* dividend history -- not the trailing-1-year window used above
  (that's a deliberately short window for a next-payment guess), but also
  not the fund's whole history: only the most recent
  `DDM_GROWTH_LOOKBACK_YEARS` complete calendar years (`config.py`,
  default 5). A longer lookback drags in years from a different
  payout-policy regime -- e.g. 0050 switched from annual to semi-annual
  distributions in 2017 -- which distorts a "current trend" read more
  than it helps. Two models are fit on that window, side by side:
  - **Model A -- polynomial trend**: fits a straight line (or a
    configurable-degree curve) to log(annual dividend) vs. year, i.e.
    "how has the payout actually been trending."
  - **Model B -- mean-reverting (AR(1))**: fits `g_(t+1) = mu + phi*(g_t
    - mu)` to the year-over-year growth rates themselves, i.e. "growth
    tends to drift back toward its own long-run average `mu`, at a
    speed `phi` estimated from the data" -- rather than assuming a fixed
    terminal growth rate by hand.
  Both models then compound the forecast forward from the **trailing
  12-month actual dividend total**, not the last complete calendar year.
  That matters: a first version of this anchored on the last *complete*
  year, and for 0050 that meant projecting from 2025's total (1.035)
  even though 2026's payments so far (1.60) already exceed it -- the
  model was projecting *below* dividends the fund had already paid.
  Anchoring on the trailing 12 months fixes that.
- **No terminal value.** Dividends are projected and discounted only for
  an explicit horizon (`DDM_FORECAST_HORIZON_YEARS` in `config.py`,
  default 10 -- try 20 too, the script prints both in its sensitivity
  table) and then simply stop. Nothing beyond the horizon is valued. This
  is honestly a simplification in the other direction (it *understates*
  value for a fund expected to keep paying indefinitely), but it fully
  removes the unstable division above -- there's no `(r - g)` anywhere in
  this script.

Config knobs (`config.py`): `DDM_RISK_FREE_RATE` and
`DDM_EQUITY_RISK_PREMIUM` feed the CAPM discount rate (currently Taiwan's
10-year bond yield and Damodaran's Taiwan equity risk premium, both
sourced 2026-09-09 -- these are macro assumptions, not fetched data, so
refresh them occasionally); `DDM_FORECAST_HORIZON_YEARS` sets how many
years to discount; `DDM_GROWTH_LOOKBACK_YEARS` sets how many recent
complete years feed the growth fit; `DDM_POLYNOMIAL_DEGREE` sets Model
A's curve degree; `DDM_MAX_ANNUAL_GROWTH` caps every projected year's
growth rate (from either model) so a runaway extrapolation can't compound
into nonsense over 10-20 years.

Output (both models' intrinsic values, beta, r, and the underlying
dividend history) is saved to `data/dividends/ddm_valuation.csv`, and the
console output additionally prints each model's year-by-year projected
path plus a sensitivity table (intrinsic value at r-1%, r, r+1%, crossed
with 5y/10y/20y horizons) so the assumptions' impact is visible rather
than hidden behind one final number.

**Split adjustment matters a lot here.** 0050's 5-year growth-fit window
(2021-2025) spans its 2025-06-18 1-for-4 split. Before this was handled,
the 2025 annual total was silently summing a pre-split payment (in
old-share terms) with a post-split payment (in new-share terms) --
comparing incompatible units and producing a nonsense "2025 total." Now
that `load_dividends()`/`load_prices()` split-adjust automatically (see
above), 0050's fitted annual series is internally consistent, and the
same fix removes a one-day ~-75% price "return" that would otherwise
have distorted the beta calculation. The script prints a
`[split-adjusted]` line per ticker whenever a split was found.

**Confirmed against two live runs so far** (2026-09-09): the first,
before split adjustment existed, gave beta=0.980 for 0050 (r=6.82%) --
close to the theoretical ~1.0 for a fund tracking its own benchmark
index, as expected. The second run tested the *first version* of the
split-adjustment fix, which tried to auto-detect splits purely from
yfinance's `.splits` field -- that field came back **completely empty**
for all three tickers on that run, so no adjustment was actually
applied and the 2025 dividend total still showed the old, wrong,
mixed-basis 1.035. Fixed by making `KNOWN_SPLITS` (config.py) the
authoritative source instead of relying on yfinance (see the note in the
"Predicting the next dividend" section above) -- not yet re-run live
with that fix; the sandbox test against your real staged data now shows
2025's total correctly as ~0.53. Worth a third live run to confirm.

0050 still reads meaningfully
overvalued by this model even after the anchoring fix, which is worth
understanding rather than distrusting: 0050 pays out a small fraction of
its value as cash (recent yield well under 2%) because most of its
return comes from price/NAV appreciation of its holdings, not
distributions -- a finite-horizon *dividend* DDM structurally can't
capture that for a fund like this. It's a better fit for a
higher-payout-ratio fund; compare 00878, where the two models read much
closer to (or above) the actual price. This is exactly the ETF-vs-DDM
mismatch in the caveats below, now visible in real numbers rather than
just as a warning.

Also confirmed via public sources (not just the yfinance payment count):
0050 pays semi-annually (Jan/Jul) since a 2017 policy change, and 00878
pays quarterly (Feb/May/Aug/Nov) since its 2020 launch -- both match what
`dividends.csv` already has, so no data bug there.

CAVEATS (in addition to the ones already listed above for the dividend
predictions, which still apply -- small samples, ETF-specific quirks):
- Split detection now relies on `KNOWN_SPLITS` (config.py) as the
  authoritative source, since yfinance's own `.splits` data proved
  unreliable for these tickers (came back empty even for 0050's real,
  confirmed split). That means a genuinely new split won't get picked up
  automatically -- it has to be added to `KNOWN_SPLITS` manually. The
  `detect_unexplained_jumps` price-jump sanity check (`splits.py`) is the
  safety net for this: it prints a `[!]` warning if a ticker's raw price
  history shows a split-sized single-day jump not already in
  `KNOWN_SPLITS`. If that warning ever fires, verify the event and add
  it -- until it's added, that ticker's cross-time comparisons are
  silently back to being wrong in the way described above.
- A finite-horizon PV is a *floor*, not a fair-value estimate -- it's
  "worth at least this much from the next N years of dividends alone,"
  ignoring everything after year N.
- With a 5-year lookback, growth is fit from as few as 5-6 annual data
  points (fewer for 00878, listed 2020). `DDM_GROWTH_LOOKBACK_YEARS`
  trades off longer-but-possibly-stale history against
  shorter-but-noisier -- 5 is a starting point, not a proven-optimal
  choice.
- A negative-phi AR(1) fit (seen live on 006208 and 00878) makes the
  projected growth rate alternate sign year to year, damping toward `mu`
  -- that's expected AR(1) behavior with few data points, not a bug, but
  it can look like an odd zig-zag in the printed path.
- Beta is estimated from under a year of price history so far; treat it
  as provisional until more accumulates.
- These are ETFs, not single companies -- their price is kept close to
  NAV by the creation/redemption arbitrage mechanism, not primarily by
  investors discounting the fund's own dividend history, and (as seen
  with 0050 above) some of these funds return most of their value via
  price appreciation rather than distributions, which a dividend-only DDM
  can't see. Read any "over/undervalued" result as a rough sanity check,
  not a signal, and never as investment advice.

## Next steps (per the project goal)

1. Let the price/dividend history build up for a while (or backfill
   further back if a longer training window is wanted -- TWSE's
   `STOCK_DAY` history in practice goes back to a stock's listing date).
2. Feature engineering: join price + dividend history per ticker, add
   derived features (moving averages, dividend yield over time, etc).
3. Move beyond the geometric-mean baseline above to a real modeling
   approach (e.g. a simple time-series baseline first -- ARIMA/Prophet --
   before anything fancier), and a clearer target definition (next
   dividend amount? next quarter's closing price? a return over N days?).
4. Build the prediction pipeline and, eventually, the app / dashboard on
   top of it.

Happy to start on any of these once there's a sense of which ticker(s)
and prediction horizon matter most.
