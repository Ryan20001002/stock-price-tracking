"""
Watchlist and shared settings for the Taiwan stock tracker.

Edit WATCHLIST to add/remove companies. `code` is the TWSE stock code
(4-5 digits, as used on twse.com.tw); `name` is only used for labeling
files and building news search queries.
"""

# A starter watchlist of large, liquid TWSE-listed companies spanning a
# few sectors. Add your own tickers here (find codes at isin.twse.com.tw
# or just search "<company name> 股票代號").
WATCHLIST = [
    {"code": "0050", "name": "元大台灣50", "name_en": "TW0050"},
    {"code": "00878", "name": "國泰永續高股息", "name_en": "TW00878"},
    {"code": "006208", "name": "富邦台50", "name_en": "TW006208"},
]

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
PRICE_HISTORY_MONTHS = 36

# Seconds to wait between TWSE requests. TWSE throttles/blocks IPs that
# request too fast, so keep this conservative (>=1.5s recommended).
REQUEST_DELAY_SECONDS = 1.5

# How many news headlines to keep per company per run.
NEWS_ITEMS_PER_COMPANY = 15

DATA_DIR = "data"

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
# trades "understates true value" for "no unstable r-g division"). Try
# both 10 and 20 and compare -- the script's sensitivity table also shows
# this side by side automatically.
DDM_FORECAST_HORIZON_YEARS = 10

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
