"""
Dividend Discount Model (DDM) valuation -- finite-horizon version.

Estimates each ticker's "intrinsic" fair value per unit as the present
value of its dividends over an explicit N-year horizon (see
DDM_FORECAST_HORIZON_YEARS in config.py, default 20 -- try 10 or 30 too),
and compares that to the actual current market price.

Why this design (and not a classic Gordon-Growth DDM)
-------------------------------------------------------
An earlier version of this script used a two-stage "H-Model" DDM: a short
explicit forecast, then a Gordon Growth terminal value (D/(r-g)) standing
in for "forever after". Testing surfaced exactly the problem that model is
known for: Gordon Growth divides by (r - g), and when r and g are close --
common for a mature, moderate-growth fund -- that division blows up. A
mock run on 006208 produced an "intrinsic value" over 30x the real price
purely because r-g was thin, not because of anything meaningful about the
fund.

This version removes the terminal-value formula entirely and splits the
two jobs DDM asks of you:

1. Required rate of return (r): estimated once via CAPM
   (r = risk-free rate + beta x equity risk premium) and then held
   CONSTANT across the whole horizon. We are deliberately not trying to
   model r as time-varying too -- one source of modeling complexity at a
   time, per your steer.
2. Growth rate: this is where the modeling effort goes instead of into r.
   Rather than one noisy growth ratio glide-pathed to an assumed terminal
   number, we fit an actual model to the fund's own *annual* dividend
   history -- not the trailing-1yr window predict_dividends.py uses for
   next-payment prediction (that's a deliberately short window for a
   different question), but also not the fund's ENTIRE history: only the
   most recent DDM_GROWTH_LOOKBACK_YEARS complete calendar years
   (config.py, default 5). A longer lookback drags in years from a
   different payout-policy regime -- e.g. 0050 switched from annual to
   semi-annual distributions in 2017 -- which distorts a "current trend"
   read more than it helps. Two models are fit on that window, side by
   side so you can see where they agree/disagree:
     (a) Polynomial trend regression on log(annual dividend) vs. year
         (degree 1 = constant growth rate, degree 2 = accelerating or
         decelerating trend).
     (b) A mean-reverting AR(1) model on year-over-year log growth rates:
         g_(t+1) = mu + phi*(g_t - mu), where mu (long-run average growth)
         and phi (how fast growth reverts to that average, in [-1, 1]) are
         both ESTIMATED from the fund's own history, not assumed.
   The projection is then compounded forward from the trailing-12-month
   ACTUAL total (not the stale last complete calendar year) -- otherwise,
   if the current year's payments already exceed last year's total (which
   happens: 0050's 2026 payments so far already beat every prior full
   year on file), the model would start by projecting *below* dividends
   the fund has already paid, which is exactly the "weird" result an
   earlier version of this script produced for 0050.
3. Dividends are discounted only out to year N and then STOP -- nothing
   beyond the horizon is valued (no terminal value of any kind). This is
   a real simplification (see CAVEATS) but it is what removes the
   unstable (r - g) division: there's no division by a growth gap
   anywhere in this script anymore.

Split adjustment: both the dividend and price history used here come
from predict_dividends.py's load_dividends()/load_prices(), which now
split-adjust automatically (see splits.py) -- necessary because 0050 did
a 1-for-4 split on 2025-06-18, and comparing a pre-split dividend/price
to a post-split one without adjusting is comparing two different
share-count bases. This script prints a `[split-adjusted]` line per
ticker when a split was found.

Usage:
    python ddm_valuation.py
Reads data/dividends/dividends.csv, data/prices/<code>.csv, and pulls
TAIEX (^TWII) history via yfinance for the beta estimate. Writes
data/dividends/ddm_valuation.csv and prints a per-ticker breakdown plus a
sensitivity table (over r and horizon length -- the two things that still
matter now that terminal growth isn't in the picture).

CAVEATS -- read before treating any number here as a real valuation
---------------------------------------------------------------------
- Finite horizon = a floor, not a fair value. Cutting off the dividend
  stream at year N and valuing everything after it at zero understates
  true value for a fund expected to keep paying past year N (which, for
  an index ETF, is presumably indefinitely). Read the output as "PV of
  the next N years of dividends", not "what this is worth" -- and compare
  the 5y/10y/20y sensitivity rows (printed every run) to see how much of
  the total is coming from years 11-20, and how much is still being left
  out beyond that.
- Horizon raised from 10 to 20 years (2026-09-09): a longer horizon gets
  closer to a "full" valuation, but leans harder on the fitted growth
  rate holding up -- 20 years of compounding magnifies whatever the
  growth model gets wrong far more than 10 years did, and is more likely
  to run into the DDM_MAX_ANNUAL_GROWTH safety clamp. Worth occasionally
  re-checking the sensitivity table's 10y column against the 20y one.
- Small-sample growth fitting: with a 5-year lookback, the growth models
  fit on as few as 5-6 annual data points (fewer for 00878, listed 2020).
  A trend or AR(1) fit on a handful of points is still a rough estimate,
  not a robust statistical result -- more years of accumulated data will
  make this meaningfully better over time. DDM_GROWTH_LOOKBACK_YEARS
  (config.py) trades off two things: longer = more data points but risks
  mixing in a stale payout-policy regime; shorter = more current but
  noisier. 5 is a starting point, not a proven-optimal choice.
- Dividend distribution frequency (verified 2026-09-09 against public
  sources, not just yfinance's raw payment count): 0050 pays semi-annually
  (Jan/Jul) since a 2017 policy change; 00878 pays quarterly (Feb/May/
  Aug/Nov) since its 2020 launch. If a future run's payment count per
  year for either ticker looks different from that, it's worth checking
  whether the *policy* changed (search the fund manager's own
  announcements) rather than assuming the data pull is wrong.
- Growth is clamped to +/- DDM_MAX_ANNUAL_GROWTH per year (config.py) so
  a runaway polynomial extrapolation or a phi near +/-1 can't blow up a
  20-year compounding path. This is a safety rail, not a data-driven
  number -- if a ticker keeps hitting the clamp in the printed output,
  the fitted model is trying to extrapolate something the clamp is
  overriding, which is itself worth noticing.
- Beta (and therefore r) is estimated from whatever price history has
  accumulated so far (currently under a year) -- a longer history will
  give a more stable estimate. Needs at least 30 overlapping trading days
  with TAIEX or the ticker is skipped.
- DDM assumes a fund's own dividend history predicts its future payouts.
  These are ETFs -- their distributions reflect the aggregate of their
  underlying holdings, and their market price is kept close to NAV by the
  creation/redemption arbitrage mechanism, not by investors discounting
  the ETF's own dividend history the way they might a single stock. Treat
  any "over/undervalued" read here as a rough long-run sanity check, not
  a trading signal, and never as investment advice.
"""

import csv
import os
from collections import defaultdict
from datetime import date

import numpy as np
import yfinance as yf

from config import (
    DATA_DIR, DDM_RISK_FREE_RATE, DDM_EQUITY_RISK_PREMIUM,
    DDM_FORECAST_HORIZON_YEARS, DDM_POLYNOMIAL_DEGREE, DDM_MAX_ANNUAL_GROWTH,
    DDM_GROWTH_LOOKBACK_YEARS,
)
from predict_dividends import load_dividends, load_prices, last_year_window
from splits import fetch_splits

MARKET_INDEX_SYMBOL = "^TWII"  # TAIEX
MIN_YEARS_FOR_POLYNOMIAL = 2   # need >= 2 annual points for even a degree-1 fit
MIN_GROWTH_OBS_FOR_MEAN_REVERTING = 2  # need >= 2 yoy growth obs (3 annual points)


# ---------------------------------------------------------------------------
# CAPM / beta (unchanged approach from the earlier version: r stays constant)
# ---------------------------------------------------------------------------

def fetch_market_returns():
    """Daily returns for the TAIEX, as {date: return}. Empty dict on failure."""
    try:
        hist = yf.Ticker(MARKET_INDEX_SYMBOL).history(period="2y")
    except Exception as e:
        print(f"  [warn] could not fetch {MARKET_INDEX_SYMBOL} history: {e}")
        return {}
    if hist is None or hist.empty:
        return {}
    closes = hist["Close"]
    returns = {}
    prev = None
    for idx, close in closes.items():
        d = idx.date() if hasattr(idx, "date") else idx
        if prev is not None and prev > 0:
            returns[d] = close / prev - 1
        prev = close
    return returns


def compute_returns(prices):
    """prices: sorted list of (date, close). Returns {date: return}."""
    returns = {}
    prev = None
    for d, close in prices:
        if prev is not None and prev > 0:
            returns[d] = close / prev - 1
        prev = close
    return returns


def compute_beta(ticker_returns, market_returns):
    """Both {date: return}. Returns (beta, n_overlapping_days); beta is
    None if fewer than 30 overlapping dates or zero market variance."""
    common_dates = sorted(set(ticker_returns) & set(market_returns))
    n = len(common_dates)
    if n < 30:
        return None, n
    xs = np.array([market_returns[d] for d in common_dates])
    ys = np.array([ticker_returns[d] for d in common_dates])
    var_x = xs.var()
    if var_x == 0:
        return None, n
    cov = ((xs - xs.mean()) * (ys - ys.mean())).mean()
    return float(cov / var_x), n


# ---------------------------------------------------------------------------
# Annual dividend series + the two growth models
# ---------------------------------------------------------------------------

def build_annual_dividend_series(payments):
    """payments: list of (date, amount). Returns (complete_years, partial)
    where complete_years is a sorted [(year, total), ...] for every
    calendar year strictly before the current one that has payments on
    file, and partial is (current_year, total_so_far) or None. The
    current year is excluded from the fitted series since it's usually
    not yet complete and would understate/distort the growth trend."""
    by_year = defaultdict(float)
    for d, amt in payments:
        by_year[d.year] += amt
    current_year = date.today().year
    complete = sorted((y, t) for y, t in by_year.items() if y < current_year)
    partial = (current_year, by_year[current_year]) if current_year in by_year else None
    return complete, partial


def _clip_growth(g):
    return max(-DDM_MAX_ANNUAL_GROWTH, min(DDM_MAX_ANNUAL_GROWTH, g))


def trailing_12mo_total(payments):
    """Sum of dividends paid in the trailing 365 days (same window
    predict_dividends.py uses for its next-payment estimate). Used here
    only as the ANCHOR LEVEL the projection compounds forward from -- see
    the "anchor" note in fit_polynomial_growth/fit_mean_reverting_growth
    for why the last *complete calendar year* total is the wrong anchor
    when the current year's actual payments already exceed it."""
    if not payments:
        return None
    _, _, windowed = last_year_window(payments)
    if not windowed:
        return None
    return sum(amt for _, amt in windowed)


def fit_polynomial_growth(annual_series, n_forecast, current_d0=None, degree=DDM_POLYNOMIAL_DEGREE):
    """Fits log(annual dividend) ~ polynomial(year index) using the
    complete-year series (this is what estimates the *growth rate*), but
    compounds the forecast forward from current_d0 if given (the trailing
    12-month actual total) instead of the last complete year's total.

    Anchor note: the complete-year series always excludes the current,
    still-accumulating year (see build_annual_dividend_series), so its
    last point can already be a year or more stale by the time this runs.
    If the trailing-12-month total is available and is a more current
    reading than that last complete year, anchoring the projection there
    keeps the model from projecting *below* dividends the fund has
    already actually paid.

    Returns a dict, or None if too little data."""
    if len(annual_series) < MIN_YEARS_FOR_POLYNOMIAL:
        return None
    totals = [t for _, t in annual_series]
    if any(t <= 0 for t in totals):
        return None

    t = np.arange(len(totals), dtype=float)
    log_d = np.log(totals)
    deg = min(degree, len(totals) - 1)  # can't fit degree >= n points
    coeffs = np.polyfit(t, log_d, deg)
    poly = np.poly1d(coeffs)

    last_t = t[-1]
    last_d = totals[-1]
    anchor_d = current_d0 if (current_d0 is not None and current_d0 > 0) else last_d
    forecast = []
    prev_d = anchor_d
    for i in range(1, n_forecast + 1):
        raw_g = float(np.exp(poly(last_t + i) - poly(last_t + i - 1)) - 1)
        g = _clip_growth(raw_g)
        d_i = prev_d * (1 + g)
        forecast.append((i, g, d_i))
        prev_d = d_i

    return {"degree_used": deg, "forecast": forecast, "coeffs": coeffs.tolist(), "anchor_d0": anchor_d}


def fit_mean_reverting_growth(annual_series, n_forecast, current_d0=None):
    """Fits an AR(1) model to year-over-year log growth of annual
    dividends: g_(t+1) = mu + phi*(g_t - mu) -- mu and phi are estimated
    from the complete-year series only (that's real, non-overlapping
    annual data). The forecast then compounds forward from current_d0
    (trailing 12-month total) if given, same anchor reasoning as
    fit_polynomial_growth above. If current_d0 represents a jump from the
    last complete year (e.g. this year's actual payments already running
    well above last year's), that jump is fed in as the AR(1)'s *most
    recent* observed growth, so the model reverts FROM the current actual
    trend toward its long-run mean -- rather than reverting from a
    year-plus-old data point and ignoring what's already happened since.

    Returns a dict, or None if too little data (needs >= 3 annual points
    -> >= 2 growth obs)."""
    totals = [t for _, t in annual_series]
    if len(totals) < MIN_GROWTH_OBS_FOR_MEAN_REVERTING + 1:
        return None
    if any(t <= 0 for t in totals):
        return None

    log_totals = np.log(np.array(totals))
    g = np.diff(log_totals)  # yoy log-growth series
    if len(g) < MIN_GROWTH_OBS_FOR_MEAN_REVERTING:
        return None

    x = g[:-1]
    y = g[1:]
    var_x = x.var()
    if var_x == 0:
        phi = 0.0
    else:
        cov = ((x - x.mean()) * (y - y.mean())).mean()
        phi = float(cov / var_x)
    phi = max(-0.95, min(0.95, phi))  # keep the process mean-reverting/stationary

    c = y.mean() - phi * x.mean()
    mu = c / (1 - phi) if abs(1 - phi) > 1e-6 else float(y.mean())

    last_g = float(g[-1])
    last_d = totals[-1]
    anchor_d = current_d0 if (current_d0 is not None and current_d0 > 0) else last_d
    if current_d0 is not None and current_d0 > 0 and last_d > 0:
        # Use the actual growth from the last complete year to the
        # trailing-12mo anchor as the "most recent" growth observation
        # the AR(1) reverts from, instead of the older complete-year
        # yoy figure -- see docstring.
        recent_g_log = float(np.log(current_d0) - np.log(last_d))
    else:
        recent_g_log = last_g

    forecast = []
    prev_g_log = recent_g_log
    prev_d = anchor_d
    for i in range(1, n_forecast + 1):
        g_log = mu + phi * (prev_g_log - mu)
        raw_g = float(np.exp(g_log) - 1)
        g_clipped = _clip_growth(raw_g)
        d_i = prev_d * (1 + g_clipped)
        forecast.append((i, g_clipped, d_i))
        prev_g_log = g_log
        prev_d = d_i

    return {"phi": phi, "mu_log_growth": mu, "n_growth_obs": len(g), "forecast": forecast,
            "anchor_d0": anchor_d, "recent_growth_used": float(np.exp(recent_g_log) - 1)}


# ---------------------------------------------------------------------------
# Discounting -- finite horizon only, no terminal value
# ---------------------------------------------------------------------------

def pv_finite_horizon(forecast, r, n_years=None):
    """forecast: [(year_index, growth, dividend), ...]. Discounts only the
    first n_years entries (default: all of them) at constant rate r."""
    entries = forecast if n_years is None else forecast[:n_years]
    return sum(d_i / (1 + r) ** t for t, _, d_i in entries)


def print_sensitivity_table(forecast, r):
    """Sensitivity to r (rows) and horizon length (columns) -- the two
    levers left in this model now that there's no terminal g to wobble."""
    horizons = sorted(set(h for h in (5, 10, min(len(forecast), DDM_FORECAST_HORIZON_YEARS), 20) if h <= len(forecast)))
    r_options = [r - 0.01, r, r + 0.01]
    header = "           " + "".join(f"N={h:>3}y    " for h in horizons)
    print(header)
    for r_s in r_options:
        cells = [f"{pv_finite_horizon(forecast, r_s, h):>8.2f}" for h in horizons]
        print(f"  r={r_s * 100:5.2f}%  " + "  ".join(cells))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("[ddm] fetching TAIEX (^TWII) history for beta estimation...")
    market_returns = fetch_market_returns()
    print(f"  -> {len(market_returns)} TAIEX daily return(s) available\n")

    rows_by_code, meta = load_dividends()
    summary_rows = []
    horizon = DDM_FORECAST_HORIZON_YEARS

    for code in sorted(rows_by_code):
        name_en = meta[code]["name_en"]
        print(f"[ddm] {code} {meta[code]['name']}")

        splits = fetch_splits(code)
        if splits:
            print("  [split-adjusted] " + ", ".join(f"{d.isoformat()}: 1-for-{r:g} split" for d, r in splits)
                  + " -- dividend/price history before each date rescaled to current-share terms (see splits.py)")

        prices = load_prices(code)
        if not prices:
            print(f"  [warn] no price data for {code} -- skipping\n")
            continue
        latest_price_date, latest_price = prices[-1]

        ticker_returns = compute_returns(prices)
        beta, n_overlap = compute_beta(ticker_returns, market_returns)
        if beta is None:
            print(f"  [warn] only {n_overlap} overlapping TAIEX/price day(s) (need >= 30) to estimate beta -- skipping\n")
            continue
        r = DDM_RISK_FREE_RATE + beta * DDM_EQUITY_RISK_PREMIUM
        print(f"  beta={beta:.3f} (n={n_overlap} overlapping days)  ->  r (CAPM, held constant) = "
              f"{DDM_RISK_FREE_RATE * 100:.2f}% + {beta:.3f} x {DDM_EQUITY_RISK_PREMIUM * 100:.2f}% = {r * 100:.2f}%")

        annual_series, partial = build_annual_dividend_series(rows_by_code[code])
        full_series_str = ", ".join(f"{y}={t:.4f}" for y, t in annual_series) or "(none)"
        print(f"  complete-year dividend totals on file: {full_series_str}")

        # Only fit the growth models on the most recent DDM_GROWTH_LOOKBACK_YEARS
        # complete years -- older years can span a different payout-policy
        # regime (e.g. 0050's 2017 switch from annual to semi-annual
        # distributions) and would distort a "current trend" read.
        fit_series = annual_series[-DDM_GROWTH_LOOKBACK_YEARS:] if DDM_GROWTH_LOOKBACK_YEARS else annual_series
        if len(fit_series) < len(annual_series):
            print(f"  -> fitting growth models on the most recent {len(fit_series)} year(s) only: "
                  + ", ".join(f"{y}={t:.4f}" for y, t in fit_series))

        anchor_d0 = trailing_12mo_total(rows_by_code[code])
        if partial:
            print(f"  ({partial[0]} so far, excluded from the fitted series as incomplete: {partial[1]:.4f})")
        if anchor_d0 is not None:
            print(f"  -> anchoring the projection at the trailing-12-month actual total: {anchor_d0:.4f} "
                  f"(not the last complete year -- see ddm_valuation.py docstring)")

        poly = fit_polynomial_growth(fit_series, horizon, current_d0=anchor_d0)
        mr = fit_mean_reverting_growth(fit_series, horizon, current_d0=anchor_d0)

        if poly is None and mr is None:
            print(f"  [warn] not enough complete-year dividend history to fit a growth model "
                  f"(have {len(fit_series)} year(s) in the fitting window, need >= {MIN_YEARS_FOR_POLYNOMIAL}) -- skipping\n")
            continue

        row = {
            "code": code, "name": meta[code]["name"], "name_en": name_en,
            "beta": round(beta, 4), "n_overlapping_days": n_overlap,
            "discount_rate_r": round(r, 6),
            "n_complete_years_on_file": len(annual_series),
            "n_years_used_for_growth_fit": len(fit_series),
            "anchor_d0_trailing_12mo": round(anchor_d0, 4) if anchor_d0 is not None else "",
            "horizon_years": horizon,
            "latest_price": latest_price, "latest_price_date": latest_price_date.isoformat(),
        }

        if poly is not None:
            pv_poly = pv_finite_horizon(poly["forecast"], r)
            diff_poly = (pv_poly / latest_price - 1) * 100
            print(f"  [Model A: polynomial trend, degree={poly['degree_used']}]")
            print("    projected path: " + ", ".join(
                f"yr{t}={d:.4f}({g * 100:+.1f}%)" for t, g, d in poly["forecast"][:min(10, horizon)]
            ) + (" ..." if horizon > 10 else ""))
            print(f"    PV over {horizon}y @ r={r * 100:.2f}% = {pv_poly:.4f}  vs. price {latest_price:.2f}  "
                  f"-> {'UNDER' if diff_poly > 0 else 'OVER'}valued by {abs(diff_poly):.1f}%")
            row["poly_pv_intrinsic_value"] = round(pv_poly, 4)
            row["poly_pct_diff_vs_price"] = round(diff_poly, 2)
        else:
            print(f"  [Model A: polynomial trend] skipped -- need >= {MIN_YEARS_FOR_POLYNOMIAL} complete years")
            row["poly_pv_intrinsic_value"] = ""
            row["poly_pct_diff_vs_price"] = ""

        if mr is not None:
            pv_mr = pv_finite_horizon(mr["forecast"], r)
            diff_mr = (pv_mr / latest_price - 1) * 100
            print(f"  [Model B: mean-reverting AR(1)] phi={mr['phi']:.3f}  "
                  f"long-run mean growth={float(np.exp(mr['mu_log_growth']) - 1) * 100:+.1f}%  "
                  f"(fitted on {mr['n_growth_obs']} yoy growth obs)")
            print("    projected path: " + ", ".join(
                f"yr{t}={d:.4f}({g * 100:+.1f}%)" for t, g, d in mr["forecast"][:min(10, horizon)]
            ) + (" ..." if horizon > 10 else ""))
            print(f"    PV over {horizon}y @ r={r * 100:.2f}% = {pv_mr:.4f}  vs. price {latest_price:.2f}  "
                  f"-> {'UNDER' if diff_mr > 0 else 'OVER'}valued by {abs(diff_mr):.1f}%")
            row["mr_pv_intrinsic_value"] = round(pv_mr, 4)
            row["mr_pct_diff_vs_price"] = round(diff_mr, 2)
            row["mr_phi"] = round(mr["phi"], 4)
        else:
            print(f"  [Model B: mean-reverting AR(1)] skipped -- need >= {MIN_GROWTH_OBS_FOR_MEAN_REVERTING + 1} complete years")
            row["mr_pv_intrinsic_value"] = ""
            row["mr_pct_diff_vs_price"] = ""
            row["mr_phi"] = ""

        print("  sensitivity (PV at nearby r assumptions and horizon lengths, using Model A's path "
              "if available else Model B's):")
        print_sensitivity_table((poly or mr)["forecast"], r)
        print()

        summary_rows.append(row)

    out_path = os.path.join(DATA_DIR, "dividends", "ddm_valuation.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = ["code", "name", "name_en", "beta", "n_overlapping_days", "discount_rate_r",
                  "n_complete_years_on_file", "n_years_used_for_growth_fit", "anchor_d0_trailing_12mo",
                  "horizon_years",
                  "poly_pv_intrinsic_value", "poly_pct_diff_vs_price",
                  "mr_pv_intrinsic_value", "mr_pct_diff_vs_price", "mr_phi",
                  "latest_price", "latest_price_date"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    run()
