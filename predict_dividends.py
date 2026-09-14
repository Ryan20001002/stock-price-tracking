"""
Predict each ticker's next dividend payment two different ways:

  Method A -- growth rate: assumes the dividend *amount* grows at a
  constant annual rate. Compares a trailing multi-year total payout to
  the multi-year total before that (see FREQUENCY FIX below for why
  TOTALS, not individual payments, and LONGER-WINDOW FIX for why
  multi-year, not just 1 year) and applies the resulting annualized
  growth rate to the relevant upcoming payment (see SAME-MONTH
  PREDICTION FIX for how "relevant" is determined).

  Method B -- yield: assumes the dividend *yield* (dividend / share
  price) holds roughly steady, which can fit an ETF better since payout
  scales with the fund's price/NAV level rather than following its own
  growth curve. Matches each payment (still using just the trailing 1
  year, unlike Method A -- see below) to the share price on/before that
  date, averages the resulting yields, and multiplies by the latest
  share price.

Both are printed and saved side by side per ticker so they can be
compared directly -- see the CAVEATS section below for why they often
disagree and what that disagreement means.

FREQUENCY FIX (2026-09-14, by explicit request)
------------------------------------------------
Method A used to compute the growth ratio between each consecutive PAIR
of payments in the trailing 1 year and geometric-mean those ratios. That
silently broke for any ticker paying more than once a year: with
semi-annual payments (0050, 006208), the "growth ratio" between January's
and July's payment is really measuring the fund's normal seasonal split
between the two periods, not a real trend -- a fund that always pays more
in January than July would show a large "decline" every single year
even with flat or growing total payouts. This was already called out as
a known limitation in this module's own CAVEATS, but never fixed.

`ddm_valuation.py`'s two growth models already avoid this exact problem,
by aggregating every payment into CALENDAR-YEAR totals
(build_annual_dividend_series) before fitting any growth rate -- a full
year's total is directly comparable to the next, regardless of how many
payments made it up. Method A now does the scaled-down equivalent of
that: compare the trailing-12-month total to the 12-month total before
it (two ROLLING years, not calendar years, to stay consistent with the
"trailing 1 year, not the fund's whole history" design elsewhere in this
module). This needs ~2 years of payment history now, not 1 (there must
be a full prior 12-month period to compare against) -- a ticker with
less history has no annual growth rate to work with at all (Method B
still works off however many payments exist within 1 year, unchanged).

SAME-MONTH PREDICTION FIX (2026-09-14, by explicit follow-up request)
-----------------------------------------------------------------------
The FREQUENCY FIX above corrected the *growth rate* itself, but a second,
related problem remained in how that growth rate got turned into a
predicted NEXT PAYMENT amount: the original code just multiplied the
most recent payment by the growth rate, regardless of which payment that
was. For a fund that pays unevenly across the year -- e.g. 0050 paying a
consistently larger amount in January than in July -- that silently
assumed the upcoming payment is the same *size category* as the last
one. Run right after a July payment, the old code predicted the next
payment (which is actually the following January -- historically the
LARGER of the two) by growing July's SMALLER amount, producing a
prediction sized like the wrong payday.

Fixed by predicting each payment from its own slot's history instead of
from whichever payment happened to be most recent:
  1. The fund's CURRENT payment cycle is read off the trailing 1-year
     window (0050's current cycle is months {1, 7}; 00878's is
     {2, 5, 8, 11}; etc.) -- this is what a "slot" means below. Restricted
     to the trailing year (not all-time history) on purpose: real
     histories accumulate one-off/transition-period payments in months a
     fund doesn't regularly pay in anymore, and reading the cycle off the
     WHOLE history would let a years-old stray payment hijack which month
     gets treated as "next" (found live testing this fix: 0050 has a
     stray 0.0575 top-up dated 2022-08-02).
  2. The upcoming payment's slot is whichever month follows the most
     recent payment's month in that cycle (wrapping into next year if the
     most recent payment was the year's last slot).
  3. That slot's own history -- up to the last SAME_MONTH_MAX_YEARS (5)
     years of payments made in that specific month -- is pulled, and a
     growth rate is computed as the GEOMETRIC MEAN of the year-over-year
     ratios between consecutive same-month payments (the same
     "correct way to average a growth rate" principle the pre-FREQUENCY-FIX
     code used, just now restricted to comparing January-to-January
     instead of January-to-July, so it never re-introduces the seasonality
     bug). That growth rate is applied to the slot's own most recent
     payment.
  4. If a slot doesn't have at least SAME_MONTH_MIN_SAMPLES (2) same-month
     payments yet (common for newer ETFs), it falls back to the
     whole-year trailing/prior-total growth rate from the FREQUENCY FIX
     above, applied to that slot's own last payment instead of a
     same-month-specific rate -- still slot-correct, just a less precise
     growth estimate. `predicted_next_payment_basis` in the output says
     which one was actually used ("same_month" or "fallback_whole_year").
  5. `predicted_next_1yr_total` is rebuilt the same way, bottom-up: every
     distinct slot gets its own prediction (same_month or fallback, as
     available) and they're summed -- rather than the old approach of
     scaling the whole trailing-year total by one growth rate, which
     didn't account for slots of different sizes either.
The whole-year annual_growth_rate/trailing/prior totals from the
FREQUENCY FIX are still computed and reported (they remain a correct,
frequency-agnostic read on the fund's overall payout trend) -- they're
just no longer what predicted_next_payment is derived from directly.

LONGER-WINDOW FIX (2026-09-14, by explicit follow-up request: "I don't
think one year of data is enough, extend it to at least 2 years")
-----------------------------------------------------------------------
Two more places still leaned on just 1 year of data per comparison, and
both were widened:
  1. The whole-year growth rate (FREQUENCY FIX above) originally compared
     a trailing 1-year TOTAL to the 1-year total before it. That's still
     only two data points, so one unusually large or small year could
     swing the whole estimate (this is exactly what happened with
     006208's real data in the README's worked example -- a single
     payment nearly quadrupling year-over-year drove a large headline
     growth number off just that one comparison). GROWTH_WINDOW_YEARS
     (default 2) now controls how many years each side of the comparison
     spans -- e.g. with the default, a trailing 2-year TOTAL is compared
     to the 2-year total before that, smoothing over one unusual single
     year on either side. The resulting period-over-period ratio is then
     ANNUALIZED (nth root, n = GROWTH_WINDOW_YEARS) so `annual_growth_rate`
     stays a true per-year rate no matter how many years the comparison
     windows span -- this matters because it's applied directly to a
     single payment to predict the next one (predicted = last x (1 +
     annual_growth_rate)), which would be wrong if the rate weren't
     annualized first. Needs roughly 2*GROWTH_WINDOW_YEARS years of
     payment history now (a full trailing window plus a full prior
     window to compare it to) -- more than before, but a more stable
     estimate once it's available.
  2. The same-month growth rate's (SAME-MONTH PREDICTION FIX above)
     minimum sample size, SAME_MONTH_MIN_SAMPLES, was raised from 2 to 3
     -- a slot now needs at least 3 years of that same month's payments
     on file before its own same-month geometric-mean rate is trusted
     over the whole-year fallback, for the same single-outlier-year
     reason.
Note the trailing-1-year window used to detect the fund's CURRENT
payment cycle (which months count as slots -- see point 1 under
SAME-MONTH PREDICTION FIX) was deliberately NOT widened: that window's
whole job is excluding years-old one-off payments from being mistaken
for a real payday, and widening it would let exactly that kind of stale
payment back in.

Usage:
    python predict_dividends.py
Reads data/dividends/dividends.csv (from dividend_data.py) and
data/prices/<code>.csv (from price_data.py, needed for Method B only --
Method A still runs without it). Writes
data/dividends/dividend_prediction.csv and prints a summary.

CAVEATS (read before trusting the output)
------------------------------------------
- Very small samples: these ETFs pay 2-4 times/year, so Method B
  averages just 1-4 numbers over the trailing year, and Method A's
  annual growth rate is a single ratio of two GROWTH_WINDOW_YEARS-year
  totals (not averaged over many periods) -- a single unusual payment
  can still swing either prediction, just less easily than before the
  LONGER-WINDOW FIX widened each comparison window from 1 year to
  GROWTH_WINDOW_YEARS (default 2), and no longer via a seasonal-timing
  artifact at all (see FREQUENCY FIX above).
- Method A's same-month growth rate (see SAME-MONTH PREDICTION FIX) can
  still be swung by a single unusual payment, since it's a geometric
  mean over at most 5 data points, not a large sample -- and a slot with
  fewer than SAME_MONTH_MIN_SAMPLES (default 3) same-month payments on
  file falls back to the whole-year growth rate instead, which assumes
  that slot's payment grows at the same rate as the fund's overall
  payout (a real fund doesn't necessarily raise every payment in a year
  by the same amount).
- Which months COUNT as payment slots is read off the trailing 1-year
  window only (not the ticker's whole history -- see
  _distinct_payment_months()'s docstring), specifically so old one-off or
  transition-period payments in stale months (found live: 0050 has a
  stray 0.0575 top-up dated 2022-08-02, well outside its settled
  January/July cycle) can't hijack next-slot detection. A payment month
  that genuinely shifts from one year to the next (e.g. a payment that
  moves from December to January) can still thin that slot's own
  same-month growth history and push it toward the whole-year fallback
  more often than a fund with a perfectly stable calendar.
- Method B: matches dividends to prices by date (closing price on or
  just before the payment date) -- approximate, not an exact ex-dividend
  price adjustment. It also assumes the trailing-year average yield
  holds going forward, ignoring any real trend in payout policy.
- The two methods encode different assumptions about *why* a fund's
  distribution changes (chasing a target dollar amount vs. chasing a
  target yield) and often disagree substantially -- neither is "more
  correct" in general. Treat both as transparent baselines to compare
  against the next real payment, not as investment advice.
"""

import csv
import os
from collections import defaultdict
from datetime import date, timedelta

from config import DATA_DIR
from splits import fetch_splits, adjust_series, detect_unexplained_jumps

DIVIDENDS_CSV = os.path.join(DATA_DIR, "dividends", "dividends.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "dividends", "dividend_prediction.csv")

WINDOW_DAYS = 365


def load_dividends():
    """Returns (rows_by_code, meta). Payment amounts are split-adjusted
    (see splits.py) to a consistent current-share basis -- e.g. 0050's
    1-for-4 split on 2025-06-18 means every payment before that date is
    divided by 4 here, so a growth rate or trend computed across the
    split boundary compares like with like instead of quietly mixing two
    different share-count regimes."""
    rows_by_code = defaultdict(list)
    meta = {}
    with open(DIVIDENDS_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["code"]
            meta[code] = {"name": row["name"], "name_en": row["name_en"]}
            y, m, d = row["ex_dividend_date"].split("-")
            pay_date = date(int(y), int(m), int(d))
            amount = float(row["dividend_per_share"])
            rows_by_code[code].append((pay_date, amount))

    for code in rows_by_code:
        splits = fetch_splits(code)
        rows_by_code[code] = adjust_series(rows_by_code[code], splits)

    return rows_by_code, meta


def load_prices(code):
    """Return a sorted, split-adjusted list of (date, close) for this
    ticker (see splits.py), or [] if no file. Split-adjusting price
    history too (not just dividends) is what keeps a same-day yield
    (dividend/price) unchanged while still fixing cross-time comparisons
    like a return series used for beta."""
    path = os.path.join(DATA_DIR, "prices", f"{code}.csv")
    if not os.path.exists(path):
        return []
    prices = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("Close"):
                continue
            y, m, d = row["Date"].split("-")
            prices.append((date(int(y), int(m), int(d)), float(row["Close"])))
    prices.sort()
    splits = fetch_splits(code)
    jumps = detect_unexplained_jumps(prices, splits)
    for jump_date, prev_close, close, ratio in jumps:
        print(f"  [!] {code}: unexplained price jump on {jump_date.isoformat()} "
              f"({prev_close:.2f} -> {close:.2f}, ratio={ratio:.3f}) -- this looks like it could be "
              f"an un-catalogued split. Verify and, if so, add it to KNOWN_SPLITS in config.py; "
              f"until then this is NOT being split-adjusted and will distort any cross-time comparison.")
    prices = adjust_series(prices, splits)
    return prices


def price_on_or_before(prices, target_date):
    """Closing price on target_date, or the most recent prior trading day's
    close if target_date wasn't itself a trading day. prices must be sorted."""
    best = None
    for d, close in prices:
        if d > target_date:
            break
        best = close
    return best


def last_year_window(payments):
    """payments: list of (date, amount), any order. Returns (window_start,
    window_end, [(date, amount), ...]) sorted chronologically, restricted
    to the trailing WINDOW_DAYS ending at the latest payment on file."""
    payments = sorted(payments)
    latest_date = payments[-1][0]
    window_start = latest_date - timedelta(days=WINDOW_DAYS)
    windowed = [(d, amt) for d, amt in payments if d > window_start]
    return window_start, latest_date, windowed


GROWTH_WINDOW_YEARS = 2  # min years per comparison window, per explicit follow-up request (2026-09-14: "I don't think one year is enough")
GROWTH_WINDOW_DAYS = WINDOW_DAYS * GROWTH_WINDOW_YEARS


def method_a_growth_rate(all_payments, window_end):
    """Frequency-agnostic annual growth rate (see FREQUENCY FIX and
    LONGER-WINDOW FIX in the module docstring for why): compares a
    trailing GROWTH_WINDOW_YEARS-year TOTAL (ending at window_end) to the
    GROWTH_WINDOW_YEARS-year total immediately before it -- the same
    rolling window shifted back GROWTH_WINDOW_YEARS years -- then
    ANNUALIZES the resulting ratio (nth root, n = GROWTH_WINDOW_YEARS) so
    the returned rate is a true per-year rate no matter how many years
    each window spans. Applies that rate to the most recent payment to
    predict the next one (used directly by callers that don't need the
    SAME-MONTH PREDICTION FIX's per-slot precision, and as the fallback
    growth rate for thin slots inside predict_next_payment()).

    all_payments: this ticker's FULL payment history (any order) -- not
    just a single trailing window, since the "prior window" comparison
    period falls entirely outside the current one.
    window_end: the latest payment date on file -- the anchor both
    comparison windows are measured back from.

    Returns a dict, or None if the prior GROWTH_WINDOW_YEARS-year period
    has no (or zero-total) payments -- i.e. there isn't a full second
    window of history to compare against yet (roughly 2*GROWTH_WINDOW_YEARS
    years needed in total)."""
    current_window_start = window_end - timedelta(days=GROWTH_WINDOW_DAYS)
    current_window = sorted((d, amt) for d, amt in all_payments if current_window_start < d <= window_end)
    current_total = sum(amt for _, amt in current_window)
    if not current_window or current_total <= 0:
        return None

    prior_window_start = current_window_start - timedelta(days=GROWTH_WINDOW_DAYS)
    prior_window = [(d, amt) for d, amt in all_payments if prior_window_start < d <= current_window_start]
    prior_total = sum(amt for _, amt in prior_window)
    if prior_total <= 0:
        return None  # no full prior window of payments to compare against yet

    period_growth = current_total / prior_total  # raw growth over the whole GROWTH_WINDOW_YEARS-year period
    annual_growth = period_growth ** (1.0 / GROWTH_WINDOW_YEARS) - 1  # annualized, so it's usable per-payment

    last_amount = current_window[-1][1]
    predicted_next = last_amount * (1 + annual_growth)
    avg_annual_total = current_total / GROWTH_WINDOW_YEARS

    return {
        "n_payments": len(current_window),
        "n_payments_prior_window": len(prior_window),
        "growth_window_years": GROWTH_WINDOW_YEARS,
        "annual_growth_rate": annual_growth,
        "last_payment_amount": last_amount,
        "predicted_next_payment": predicted_next,
        "trailing_window_total": current_total,
        "prior_window_total": prior_total,
        "predicted_next_1yr_total": avg_annual_total * (1 + annual_growth),
    }


SAME_MONTH_MAX_YEARS = 5   # use at most this many years of same-month history
SAME_MONTH_MIN_SAMPLES = 3  # need at least this many same-month payments for a YoY growth rate (raised from 2, per the same "extend to 2+ years" follow-up request -- more years makes the geometric mean less swayed by one outlier year)


def _distinct_payment_months(payments):
    """The set of calendar months present in `payments`, sorted ascending
    -- e.g. {1, 7} for a fund that pays every January and July. This is
    what a payment "slot" means throughout this module.

    IMPORTANT: call this with only the CURRENT payment cycle's payments
    (in practice, the trailing-1yr `windowed` list), not a ticker's
    entire history. Real dividend histories accumulate one-off/transition
    -period payments in months the fund doesn't regularly pay in anymore
    (e.g. 0050 paid a stray 0.0575 top-up on 2022-08-02, well outside its
    settled January/July cycle) -- computing this from ALL-TIME history
    would treat every one of those as a permanent "slot," corrupting
    _next_payment_month() into picking a long-dead slot instead of the
    fund's actual next payment. A full trailing year is guaranteed to
    contain exactly one occurrence of every slot in the fund's CURRENT
    cycle (that's the same property last_year_window()'s 365-day window
    already relies on elsewhere in this module), so it's the right
    history to read the cycle off of."""
    return sorted({d.month for d, _ in payments})


def _same_month_history(all_payments, month, max_years=SAME_MONTH_MAX_YEARS):
    """All of this ticker's payments made in the given calendar month,
    chronological, capped to the most recent max_years of them."""
    matches = sorted((d, amt) for d, amt in all_payments if d.month == month)
    return matches[-max_years:] if max_years else matches


def _geometric_mean_growth(same_month_series):
    """same_month_series: chronological [(date, amount), ...], all from the
    SAME calendar month across different years. Returns the geometric mean
    of the year-over-year ratios between consecutive entries (the
    "correct way to average a growth rate," per this module's original
    Method A logic -- just now restricted to same-month comparisons so it
    can't reintroduce the seasonality bug), or None if there are fewer
    than 2 usable entries."""
    if len(same_month_series) < 2:
        return None
    ratios = [same_month_series[i][1] / same_month_series[i - 1][1]
              for i in range(1, len(same_month_series))
              if same_month_series[i - 1][1] > 0]
    if not ratios:
        return None
    product = 1.0
    for r in ratios:
        product *= r
    return product ** (1.0 / len(ratios)) - 1.0


def _next_payment_month(last_date, distinct_months):
    """Which month the NEXT payment falls in, given the fund's payment
    cycle: the first month in distinct_months strictly after last_date's
    month, wrapping around to the earliest month (next year) if
    last_date's month is the last slot of the year."""
    for m in distinct_months:
        if m > last_date.month:
            return m
    return distinct_months[0]


def _predict_slot(all_payments, month, fallback_growth, max_years=SAME_MONTH_MAX_YEARS):
    """Predict this one slot's next payment. Prefers a same-month growth
    rate (see _geometric_mean_growth); falls back to applying the
    whole-year fallback_growth (Method A's trailing/prior-total growth
    rate, from method_a_growth_rate) to this slot's own most recent
    payment when there isn't enough same-month history yet. Returns None
    only if this slot has no payment history at all AND no fallback_growth
    is available -- in practice this basically never happens for `month`
    values drawn from _distinct_payment_months(), since those are built
    from actual payments."""
    series = _same_month_history(all_payments, month, max_years)
    if len(series) >= SAME_MONTH_MIN_SAMPLES:
        growth = _geometric_mean_growth(series)
        if growth is not None:
            last_date, last_amount = series[-1]
            return {
                "predicted_amount": last_amount * (1 + growth),
                "growth_rate": growth,
                "n_years_used": len(series),
                "basis": "same_month",
                "last_amount": last_amount,
                "last_date": last_date,
                "month": month,
            }
    if series and fallback_growth is not None:
        last_date, last_amount = series[-1]
        return {
            "predicted_amount": last_amount * (1 + fallback_growth),
            "growth_rate": fallback_growth,
            "n_years_used": len(series),
            "basis": "fallback_whole_year",
            "last_amount": last_amount,
            "last_date": last_date,
            "month": month,
        }
    return None


def predict_next_payment(all_payments, window_start, window_end):
    """Top-level Method A prediction (see SAME-MONTH PREDICTION FIX in the
    module docstring). Combines the whole-year growth rate (still reported
    for transparency, and used as the fallback for thin slots) with a
    per-slot, same-month-history-based prediction for the specific next
    payment and a bottom-up rebuild of the next-12-months total.

    Returns a dict, or None if there isn't enough history to predict even
    the single next payment (no same-month history for that slot AND no
    whole-year growth rate to fall back on)."""
    whole_year = method_a_growth_rate(all_payments, window_end)
    fallback_growth = whole_year["annual_growth_rate"] if whole_year else None

    # The payment CYCLE (which months are currently "slots") is read off
    # the trailing 1yr window only, not the ticker's whole history -- see
    # _distinct_payment_months()'s docstring for why (old one-off/
    # transition-period payments in stale months would otherwise corrupt
    # next-slot detection). Each slot's own GROWTH is still computed from
    # up to SAME_MONTH_MAX_YEARS of that month's full history, via
    # _predict_slot() below -- only which months COUNT as slots is
    # restricted to the current cycle.
    trailing_window = sorted((d, amt) for d, amt in all_payments if window_start < d <= window_end)
    distinct_months = _distinct_payment_months(trailing_window)
    last_date = max(d for d, _ in all_payments)
    next_month = _next_payment_month(last_date, distinct_months)

    next_slot = _predict_slot(all_payments, next_month, fallback_growth)
    if next_slot is None:
        return None  # not enough history of any kind for the upcoming slot

    # Bottom-up 12-month total: every distinct slot gets its own
    # prediction (same-month if it has enough history, fallback
    # otherwise) and they're summed. A slot that can't be predicted at
    # all (no fallback_growth AND fewer than 2 same-month payments) is
    # simply left out of the total, which is then a partial/lower-bound
    # figure -- n_slots_predicted vs. n_slots_total says whether that
    # happened.
    slot_predictions = {}
    for m in distinct_months:
        slot = _predict_slot(all_payments, m, fallback_growth)
        if slot is not None:
            slot_predictions[m] = slot

    return {
        "annual_growth_rate": whole_year["annual_growth_rate"] if whole_year else None,
        "n_payments": whole_year["n_payments"] if whole_year else None,
        "n_payments_prior_window": whole_year["n_payments_prior_window"] if whole_year else None,
        "trailing_window_total": whole_year["trailing_window_total"] if whole_year else None,
        "prior_window_total": whole_year["prior_window_total"] if whole_year else None,
        "growth_window_years": whole_year["growth_window_years"] if whole_year else None,
        "predicted_next_payment": next_slot["predicted_amount"],
        "predicted_next_payment_month": next_month,
        "predicted_next_payment_growth_rate": next_slot["growth_rate"],
        "predicted_next_payment_n_years_used": next_slot["n_years_used"],
        "predicted_next_payment_basis": next_slot["basis"],
        "predicted_next_1yr_total": sum(s["predicted_amount"] for s in slot_predictions.values()),
        "n_slots_predicted": len(slot_predictions),
        "n_slots_total": len(distinct_months),
        "slot_predictions": slot_predictions,
    }


def method_b_yield(windowed_payments, prices):
    """Mean dividend yield over the window, applied to the latest price.
    Returns a dict of results, or None if no price data / no matches."""
    if not prices:
        return None

    yields = []
    detail = []
    for pay_date, amount in windowed_payments:
        price = price_on_or_before(prices, pay_date)
        if price is None or price <= 0:
            continue
        y = amount / price
        yields.append(y)
        detail.append((pay_date, amount, price, y))

    if not yields:
        return None

    mean_yield = sum(yields) / len(yields)
    latest_price_date, latest_price = prices[-1]
    predicted_next = latest_price * mean_yield

    return {
        "n_payments": len(yields),
        "detail": detail,
        "mean_yield": mean_yield,
        "latest_price": latest_price,
        "latest_price_date": latest_price_date,
        "predicted_next_payment": predicted_next,
    }


def run():
    rows_by_code, meta = load_dividends()

    summary_rows = []
    print(f"{'Ticker':<8}{'Name':<10}{'Method A: growth-rate':<28}{'Method B: yield-based'}")
    print("-" * 90)

    for code in sorted(rows_by_code):
        window_start, window_end, windowed = last_year_window(rows_by_code[code])
        prices = load_prices(code)

        result_a = predict_next_payment(rows_by_code[code], window_start, window_end)
        result_b = method_b_yield(windowed, prices)

        name_en = meta[code]["name_en"]
        if result_a:
            basis_tag = "" if result_a["predicted_next_payment_basis"] == "same_month" else " [whole-year fallback]"
            label_a = (f"{result_a['predicted_next_payment']:.4f} "
                       f"(month {result_a['predicted_next_payment_month']}, "
                       f"growth {result_a['predicted_next_payment_growth_rate']*100:+.1f}%){basis_tag}")
        else:
            label_a = "insufficient data"
        label_b = f"{result_b['predicted_next_payment']:.4f} (yield {result_b['mean_yield']*100:.3f}%)" if result_b else "no price data"
        print(f"{code:<8}{name_en:<10}{label_a:<40}{label_b}")

        if result_a:
            if result_a["annual_growth_rate"] is not None:
                yrs = result_a["growth_window_years"]
                print(f"    [A] trailing {yrs}yr total: {result_a['trailing_window_total']:.4f}  vs.  "
                      f"prior {yrs}yr total: {result_a['prior_window_total']:.4f} ({result_a['n_payments_prior_window']} payment(s))  "
                      f"->  annualized whole-{yrs}yr growth rate: {result_a['annual_growth_rate']*100:+.1f}%")
            print(f"    [A] next payment predicted for month {result_a['predicted_next_payment_month']}, "
                  f"basis={result_a['predicted_next_payment_basis']} "
                  f"({result_a['predicted_next_payment_n_years_used']} same-month year(s) used): "
                  f"{result_a['predicted_next_payment']:.4f}")
            for m in sorted(result_a["slot_predictions"]):
                slot = result_a["slot_predictions"][m]
                print(f"        month {m}: last={slot['last_amount']:.4f} ({slot['last_date'].isoformat()})  "
                      f"growth={slot['growth_rate']*100:+.1f}% ({slot['basis']}, {slot['n_years_used']}yr)  "
                      f"-> predicted={slot['predicted_amount']:.4f}")
            print(f"    [A] projected next-12mo total: {result_a['predicted_next_1yr_total']:.4f} "
                  f"({result_a['n_slots_predicted']}/{result_a['n_slots_total']} slot(s) predicted)")
        if result_b:
            print("    [B] payment/price/yield: " + ", ".join(
                f"{d.isoformat()}: div={amt:.4f} price={p:.2f} yield={y*100:.3f}%"
                for d, amt, p, y in result_b["detail"]))
            print(f"    [B] latest price: {result_b['latest_price']:.2f} ({result_b['latest_price_date']})")
        print()

        summary_rows.append({
            "code": code,
            "name": meta[code]["name"],
            "name_en": name_en,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "method_a_n_payments": result_a["n_payments"] if result_a and result_a["n_payments"] is not None else "",
            "method_a_growth_window_years": result_a["growth_window_years"] if result_a and result_a["growth_window_years"] is not None else "",
            "method_a_annual_growth_rate": round(result_a["annual_growth_rate"], 6) if result_a and result_a["annual_growth_rate"] is not None else "",
            "method_a_predicted_next_payment": round(result_a["predicted_next_payment"], 4) if result_a else "",
            "method_a_predicted_next_payment_month": result_a["predicted_next_payment_month"] if result_a else "",
            "method_a_predicted_next_payment_basis": result_a["predicted_next_payment_basis"] if result_a else "",
            "method_a_predicted_next_1yr_total": round(result_a["predicted_next_1yr_total"], 4) if result_a else "",
            "method_a_n_slots_predicted": result_a["n_slots_predicted"] if result_a else "",
            "method_a_n_slots_total": result_a["n_slots_total"] if result_a else "",
            "method_b_n_payments": result_b["n_payments"] if result_b else "",
            "method_b_mean_yield": round(result_b["mean_yield"], 6) if result_b else "",
            "method_b_latest_price": result_b["latest_price"] if result_b else "",
            "method_b_predicted_next_payment": round(result_b["predicted_next_payment"], 4) if result_b else "",
        })

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    fieldnames = ["code", "name", "name_en", "window_start", "window_end",
                  "method_a_n_payments", "method_a_growth_window_years", "method_a_annual_growth_rate",
                  "method_a_predicted_next_payment", "method_a_predicted_next_payment_month",
                  "method_a_predicted_next_payment_basis", "method_a_predicted_next_1yr_total",
                  "method_a_n_slots_predicted", "method_a_n_slots_total",
                  "method_b_n_payments", "method_b_mean_yield", "method_b_latest_price",
                  "method_b_predicted_next_payment"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Saved {OUTPUT_CSV}")


if __name__ == "__main__":
    run()
