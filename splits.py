"""
Stock/ETF split adjustment.

TWSE's STOCK_DAY price reports and yfinance's raw `.dividends` property
are NOT split-adjusted -- they report the actual traded price / actual
per-unit cash payment at the time, in whatever share-count regime was in
effect then. When a ticker splits, comparing a raw value from before the
split to one from after it is comparing apples to oranges.

This bit us concretely: 0050 did a 1-for-4 split effective 2025-06-18
(confirmed both by TWSE's own historical prices -- 2025-06-10 close
188.65 -> 2025-06-18 close 47.57 -- and by public news coverage of the
split). A first version of this module tried to auto-detect splits
purely from yfinance's `.splits` field -- on a live run that came back
COMPLETELY EMPTY for 0050 (yfinance simply doesn't have it), which is the
same kind of Taiwan-ETF data gap we already hit once with shares
outstanding. So this version uses a manually-verified table
(KNOWN_SPLITS in config.py) as the authoritative source, merges in
whatever yfinance's `.splits` DOES report (in case it has something we
don't), and separately runs a price-jump sanity check
(detect_unexplained_jumps) against each ticker's own price history so an
un-catalogued split gets flagged instead of silently corrupting results.

A pre-split value needs to be divided by the split ratio to be expressed
in current-share terms, matching post-split values. A RATIO computed
across two values from the SAME point in time (like same-day dividend
yield = dividend / price) is naturally unaffected by a split either way;
it's only comparisons ACROSS time of raw, un-rescaled amounts (a growth
rate, a trend fit, a daily-return series for beta) that break.
"""

import logging
from datetime import date

import yfinance as yf

from config import KNOWN_SPLITS

# Trying the ".TWO" (OTC) suffix for a TWSE-main-board-only ticker like
# 0050 always fails with a noisy "possibly delisted" message -- that's
# expected (we're just trying both suffixes since we don't track which
# board each ticker is on), not a real problem, so silence it here rather
# than alarming you on every run.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

SUFFIXES_TO_TRY = [".TW", ".TWO"]

# A day-over-day price ratio outside this range is flagged as a possible
# un-catalogued split by detect_unexplained_jumps -- 0.6/1.6 comfortably
# brackets common split ratios (2, 3, 4, 5...) and their reverse-split
# inverses while still being well outside any plausible single-day
# trading move for one of these funds.
JUMP_RATIO_LOW = 0.6
JUMP_RATIO_HIGH = 1.6

_splits_cache = {}


def _fetch_yfinance_splits(code):
    for suffix in SUFFIXES_TO_TRY:
        try:
            s = yf.Ticker(f"{code}{suffix}").splits
        except Exception:
            continue
        if s is not None and len(s) > 0:
            return [(idx.date() if hasattr(idx, "date") else idx, float(ratio))
                    for idx, ratio in s.items()]
    return []


def fetch_splits(code):
    """Returns a sorted list of (date, ratio) split events for this
    ticker (ratio > 1 means an N-for-1 split: share count x ratio, price
    /ratio). Combines KNOWN_SPLITS (config.py -- authoritative, manually
    verified) with anything yfinance's `.splits` additionally reports;
    on a date collision the manually-verified entry wins. Cached per code
    for the life of the process."""
    if code in _splits_cache:
        return _splits_cache[code]

    by_date = dict(_fetch_yfinance_splits(code))
    for d_str, ratio in KNOWN_SPLITS.get(code, []):
        y, m, d = d_str.split("-")
        by_date[date(int(y), int(m), int(d))] = ratio

    result = sorted(by_date.items())
    _splits_cache[code] = result
    return result


def adjustment_factor(event_date, splits):
    """Cumulative divisor to convert a raw value dated event_date into
    current-share terms: the product of every split ratio that occurred
    AFTER event_date (this data point predates those splits, so it's
    still expressed in the pre-those-splits share count)."""
    factor = 1.0
    for split_date, ratio in splits:
        if event_date < split_date:
            factor *= ratio
    return factor


def adjust_series(dated_values, splits):
    """dated_values: [(date, value), ...] in any order. Returns a new
    list, same order, with each value divided by the cumulative split
    ratio of every split that happened after that date -- i.e. rescaled
    to current-share terms. A no-op (returns the input as a plain list)
    if splits is empty."""
    if not splits:
        return list(dated_values)
    return [(d, v / adjustment_factor(d, splits)) for d, v in dated_values]


def detect_unexplained_jumps(prices, splits):
    """prices: sorted [(date, close), ...], RAW/unadjusted. Flags any
    day-over-day price ratio outside [JUMP_RATIO_LOW, JUMP_RATIO_HIGH]
    that doesn't fall within 3 days of an already-known split date --
    i.e. something that looks split-sized but isn't accounted for by
    fetch_splits(). Returns a list of (date, prev_close, close, ratio)
    for anything found (usually empty). This is a safety net, not a
    replacement for KNOWN_SPLITS -- if this ever finds something, add
    the real event to KNOWN_SPLITS in config.py rather than relying on
    the heuristic ratio guess."""
    known_dates = [d for d, _ in splits]

    def near_known(d):
        return any(abs((d - kd).days) <= 3 for kd in known_dates)

    flagged = []
    prev = None
    for d, close in prices:
        if prev is not None and prev[1] > 0:
            ratio = close / prev[1]
            if (ratio < JUMP_RATIO_LOW or ratio > JUMP_RATIO_HIGH) and not near_known(d):
                flagged.append((d, prev[1], close, ratio))
        prev = (d, close)
    return flagged
