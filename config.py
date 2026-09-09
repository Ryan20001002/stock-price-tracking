"""
Watchlist and shared settings for the Taiwan stock tracker.

Edit DEFAULT_WATCHLIST below to add/remove companies, or use the "Manage
watchlist" section in the app.py sidebar -- either way works. `code` is
the TWSE stock code (4-5 digits, as used on twse.com.tw); `name` is only
used for labeling files and building news search queries.

WATCHLIST vs DEFAULT_WATCHLIST: WATCHLIST (the name every other script
imports) is loaded at the bottom of this file from data/watchlist.json if
that file exists (it's written by app.py's sidebar whenever you add/
remove a ticker there), otherwise it falls back to DEFAULT_WATCHLIST
below. So editing DEFAULT_WATCHLIST here is the "permanent code default";
using the webpage is the "runtime, persists across restarts, no code
edit" option -- data/watchlist.json (once created) takes priority over
whatever's written here.
"""

import json
import os

DATA_DIR = "data"
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

# A starter watchlist of large, liquid TWSE-listed companies spanning a
# few sectors. Add your own tickers here (find codes at isin.twse.com.tw
# or just search "<company name> 股票代號") -- or use app.py's sidebar
# instead, which doesn't require editing this file at all.
DEFAULT_WATCHLIST = [
    {"code": "0050", "name": "元大台灣50", "name_en": "TW0050"},
    {"code": "00878", "name": "國泰永續高股息", "name_en": "TW00878"},
    {"code": "006208", "name": "富邦台50", "name_en": "TW006208"},
]


def _load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            if loaded:
                return loaded
        except (json.JSONDecodeError, OSError):
            pass  # fall through to the default below
    return [dict(s) for s in DEFAULT_WATCHLIST]


def save_watchlist(watchlist):
    """Persists `watchlist` to data/watchlist.json (so it survives an app
    restart) and updates the WATCHLIST list below IN PLACE -- every other
    script does `from config import WATCHLIST`, which binds to this same
    list object, so mutating its contents (not rebinding the name) is
    what makes the change visible to already-imported scripts without
    reloading anything. Called by app.py's sidebar; you can also call it
    yourself from a script if you ever want to."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)
    WATCHLIST[:] = watchlist


WATCHLIST = _load_watchlist()

# Known stock/ETF splits, manually verified -- used by splits.py.
# yfinance's own split data for TWSE tickers turned out to be unreliable:
# it reported ZERO splits for 0050 on a live run even though 0050 did a
# real, well-documented 1-for-4 split, confirmed both in TWSE's own price
# history (2025-06-10 close 188.65 -> 2025-06-18 close 47.57) and public
# news coverage. So this table is the authoritative source; splits.py
# merges it with whatever yfinance DOES report (belt and suspenders) and
# separately runs a price-jump sanity check against each ticker's own
# price history to flag anything that looks like an un-catalogued split
# (see splits.detect_unexplained_jumps) so it can be added here.
# ratio > 1 = an N-for-1 split (share count x ratio, price / ratio).
KNOWN_SPLITS = {
    "0050": [("2025-06-18", 4.0)],
}

# How many months of daily price history to backfill on the first run.
# Subsequent runs only fetch months that are missing (see price_data.py),
# so this only matters the very first time you run the tool.
#
# Also used by institutional_data.py (三大法人買賣超) as of 2026-09-09, per
# explicit request to keep both histories covering the exact same time
# span -- institutional_data.py backfills the same PRICE_HISTORY_MONTHS
# calendar months back from today, just walked day-by-day instead of
# month-by-month. Be aware that's a MUCH more expensive backfill for that
# data source than for prices: TWSE's T86 report returns every stock for
# ONE day per call, so raising this number multiplies institutional_data's
# first-run request count directly (see its module docstring for the
# rough time cost at the current default).
PRICE_HISTORY_MONTHS = 36

# Seconds to wait between TWSE requests. TWSE throttles/blocks IPs that
# request too fast, so keep this conservative (>=1.5s recommended).
REQUEST_DELAY_SECONDS = 1.5

# How many news headlines to keep per company per run.
NEWS_ITEMS_PER_COMPANY = 15

# (DATA_DIR is defined near the top of this file, above WATCHLIST_FILE --
# kept there since WATCHLIST loading needs it before this point.)

# --- Dividend Discount Model (DDM) valuation settings, used by
# ddm_valuation.py. These are macro/market assumptions, not fetched data
# -- update them occasionally, they don't go stale from day to day.

# Risk-free rate: Taiwan 10-year government bond yield. Sourced 2026-09-09
# (1.91%, tradingeconomics.com) -- check periodically and update.
DDM_RISK_FREE_RATE = 0.0191

# Equity risk premium for Taiwan (mature-market ERP + country risk
# premium). Sourced 2026-09-09 from Aswath Damodaran's country risk
# premium dataset (pages.stern.nyu.edu/~adamodar) -- he republishes this
# periodically (usually a few times a year); worth refreshing occasionally.
DDM_EQUITY_RISK_PREMIUM = 0.0501

# How many years of future dividends to explicitly discount and sum. NO
# terminal-value formula is applied beyond this -- dividends after year N
# are simply not counted (see ddm_valuation.py's docstring for why: it
# trades "understates true value" for "no unstable r-g division"). Raised
# from 10 to 20 (2026-09-09, at your request) to get closer to a "full"
# valuation -- the trade-off is more reliance on the growth-rate
# assumption (20 years of compounding vs. 10 magnifies whatever the
# fitted growth model gets wrong, and is more likely to run into the
# DDM_MAX_ANNUAL_GROWTH safety clamp below). The printed sensitivity
# table still compares 5y/10y/20y side by side every run, so you can see
# how much of the valuation is coming from the years beyond 10.
DDM_FORECAST_HORIZON_YEARS = 20

# How many of the most recent COMPLETE calendar years of dividend history
# to use when fitting the growth models (Model A polynomial trend, Model
# B mean-reverting AR(1)) in ddm_valuation.py. Deliberately shorter than
# the fund's whole history: a longer lookback drags in years from before
# a payout-policy change (e.g. 0050 switched from annual to semi-annual
# distributions in 2017) or just a very different market regime, which
# can distort a "current trend" estimate. 5 is a reasonable default;
# raise it if a ticker doesn't have 5 complete years yet (the script
# falls back to whatever's available).
DDM_GROWTH_LOOKBACK_YEARS = 5

# Degree of the polynomial fitted to log(annual dividend) vs. year for
# the trend-based growth model (ddm_valuation.py's "Model A"). 1 = fits a
# constant growth rate (a straight line in log space). 2 = allows the
# growth rate itself to accelerate/decelerate over time. Higher degrees
# are not recommended with only a handful of years of data -- they'll
# overfit and extrapolate wildly (the DDM_MAX_ANNUAL_GROWTH clamp below
# exists partly to contain that).
DDM_POLYNOMIAL_DEGREE = 1

# Safety clamp applied to EVERY forecasted year's growth rate, from both
# growth models (the polynomial trend and the mean-reverting AR(1)
# model) -- keeps a runaway extrapolation or a near-unstable AR(1) fit
# from compounding into an absurd number over a 10-20 year horizon. This
# is a rail, not a data-driven estimate; if a ticker's printed output
# keeps hitting this bound every year, that's worth noticing, not just
# trusting.
DDM_MAX_ANNUAL_GROWTH = 0.25
