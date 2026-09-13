"""
Cumulative return series for a ticker, computed two ways: including
dividends (total return) and excluding them (price return) -- from data
already collected by price_data.py/dividend_data.py. No new fetching:
this module is pure calculation over data/prices/<code>.csv and
data/dividends/dividends.csv, reusing predict_dividends.py's
load_prices()/load_dividends() so it gets split-adjustment (splits.py)
for free, for the same reason ddm_valuation.py already reuses them -- a
return computed across an un-adjusted split boundary would show a fake
~-75% "crash" on 0050's 2025-06-18 split day instead of the real return.

Definitions
-----------
price_return_series(code): a normalized index starting at 1.0 on the
first available price date, tracking ONLY the split-adjusted closing
price. This is what holding the shares would look like if every dividend
paid along the way vanished instead of being received -- useful only as
the "no dividends" baseline to compare against, not a real investor
outcome.

total_return_series(code): also starts at 1.0, but each dividend is
reinvested where it's paid: on its ex-dividend date, the fraction
(dividend / that day's closing price) of the position is treated as
buying more units, compounding forward from there. This is the standard
"total return index" definition (what TWSE's own indices and most
benchmarks use) rather than just adding dividends as flat cash, since
reinvestment is what actually compounds over multiple payments.

Both series are indexed to the SAME starting point, so they can be
plotted together and compared directly: the gap between them at any
later date is, precisely, how much of this ticker's return the price
alone doesn't capture.

cumulative_return(code, start_date, end_date, method) collapses either
series down to a single number over a chosen date range -- e.g. "what
was 2330's total return over the last 3 years" -- by re-basing the index
to 1.0 at start_date instead of the ticker's earliest price on file.
"""

from predict_dividends import load_dividends, load_prices  # noqa: F401  (load_prices re-exported for callers)


def dividends_by_code():
    """Returns {code: [(date, amount), ...]}, split-adjusted -- a thin
    wrapper around predict_dividends.load_dividends() that drops its
    `meta` (name/name_en) return value, which nothing here needs."""
    rows_by_code, _ = load_dividends()
    return rows_by_code


def price_return_series(code, prices=None):
    """Sorted list of (date, index_value), index_value == 1.0 on the
    first date, tracking price only (dividends excluded -- see module
    docstring). prices: optional pre-loaded, already split-adjusted
    [(date, close), ...] (pass this in when the caller already has a
    date-range-filtered slice, e.g. from the app's own date picker, to
    avoid re-reading the CSV and re-applying split adjustment) --
    defaults to load_prices(code). Returns [] if there's no price data
    at all, or if the first close on file is 0 (nothing to index against)."""
    if prices is None:
        prices = load_prices(code)
    if not prices:
        return []
    base = prices[0][1]
    if not base:
        return []
    return [(d, close / base) for d, close in prices]


def total_return_series(code, prices=None, dividends=None):
    """Sorted list of (date, index_value), index_value == 1.0 on the
    first date, with every dividend reinvested at its ex-dividend date's
    closing price (see module docstring). prices/dividends: optional
    pre-loaded data, same idea as price_return_series -- default to
    load_prices(code) / this ticker's slice of dividends_by_code().

    Dividends dated before the first date in `prices` are ignored (a
    caller that passes a date-range-filtered `prices` slice but the
    ticker's FULL dividend history would otherwise see every older
    dividend get dumped into day one's return, inflating it); a caller
    that wants those excluded from day one specifically should filter
    `dividends` itself to the same range before calling."""
    if prices is None:
        prices = load_prices(code)
    if not prices:
        return []
    base_date, base_close = prices[0]
    if not base_close:
        return []
    if dividends is None:
        dividends = dividends_by_code().get(code, [])
    dividends = sorted(d for d in dividends if d[0] >= base_date)

    units = 1.0
    out = []
    div_idx = 0
    for d, close in prices:
        while div_idx < len(dividends) and dividends[div_idx][0] <= d:
            _, amount = dividends[div_idx]
            if close:
                units *= 1 + (amount / close)
            div_idx += 1
        out.append((d, units * close / base_close))
    return out


def cumulative_return(code, start_date=None, end_date=None, method="total"):
    """Single scalar return over [start_date, end_date] (inclusive;
    leaving either as None means "from the first/to the last price on
    file"). method: "total" (dividends reinvested -- the default, see
    module docstring) or "price" (dividends excluded). Returns None when
    there isn't enough price data in range to compute anything (fewer
    than 2 trading days -- e.g. a ticker just added to the watchlist, or
    a range picked before this ticker had any price history)."""
    prices = load_prices(code)
    if start_date is not None:
        prices = [(d, c) for d, c in prices if d >= start_date]
    if end_date is not None:
        prices = [(d, c) for d, c in prices if d <= end_date]
    if len(prices) < 2:
        return None

    if method == "price":
        series = price_return_series(code, prices=prices)
    elif method == "total":
        dividends = dividends_by_code().get(code, [])
        dividends = [(d, a) for d, a in dividends
                     if (start_date is None or d >= start_date)
                     and (end_date is None or d <= end_date)]
        series = total_return_series(code, prices=prices, dividends=dividends)
    else:
        raise ValueError(f"method must be 'price' or 'total', got {method!r}")

    return series[-1][1] - 1.0 if series else None
