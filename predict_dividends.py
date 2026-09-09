"""
Predict each ticker's next dividend payment two different ways, both
using only the trailing 1 year of payments (not the fund's whole
multi-year history):

  Method A -- growth rate: assumes the dividend *amount* grows at a
  constant rate. Computes the growth ratio between each consecutive pair
  of payments, takes the geometric mean of those ratios, and applies it
  to the most recent payment.

  Method B -- yield: assumes the dividend *yield* (dividend / share
  price) holds roughly steady, which can fit an ETF better since payout
  scales with the fund's price/NAV level rather than following its own
  growth curve. Matches each payment to the share price on/before that
  date, averages the resulting yields, and multiplies by the latest
  share price.

Both are printed and saved side by side per ticker so they can be
compared directly -- see the CAVEATS section below for why they often
disagree and what that disagreement means.

Usage:
    python predict_dividends.py
Reads data/dividends/dividends.csv (from dividend_data.py) and
data/prices/<code>.csv (from price_data.py, needed for Method B only --
Method A still runs without it). Writes
data/dividends/dividend_prediction.csv and prints a summary.

CAVEATS (read before trusting the output)
------------------------------------------
- Very small samples: these ETFs pay 2-4 times/year, so each method
  averages just 1-3 numbers over the trailing year. A single unusual
  payment can swing either prediction a lot.
- Method A, twice-a-year tickers (0050, 006208): the one growth ratio
  compares *different* months (e.g. January's payment to July's), not
  the same month a year apart -- a fund that routinely pays more in one
  period than the other will show that seasonal gap as "growth" even
  with no real trend behind it.
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


def method_a_growth_rate(windowed_payments):
    """Geometric mean of payment-to-payment growth. Returns a dict of
    results, or None if fewer than 2 payments in the window."""
    amounts = [amt for _, amt in windowed_payments]
    if len(amounts) < 2:
        return None

    ratios = []
    for prev_v, cur_v in zip(amounts, amounts[1:]):
        if prev_v > 0:
            ratios.append(cur_v / prev_v - 1)
    if not ratios:
        return None

    product = 1.0
    for r in ratios:
        product *= (1 + r)
    gm = product ** (1 / len(ratios)) - 1

    last_amount = amounts[-1]
    predicted_next = last_amount * (1 + gm)
    trailing_total = sum(amounts)

    return {
        "n_payments": len(amounts),
        "ratios": ratios,
        "geometric_mean_growth": gm,
        "last_payment_amount": last_amount,
        "predicted_next_payment": predicted_next,
        "trailing_1yr_total": trailing_total,
        "predicted_next_1yr_total": trailing_total * (1 + gm),
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

        result_a = method_a_growth_rate(windowed)
        result_b = method_b_yield(windowed, prices)

        name_en = meta[code]["name_en"]
        label_a = f"{result_a['predicted_next_payment']:.4f} (GM {result_a['geometric_mean_growth']*100:+.1f}%)" if result_a else "insufficient data"
        label_b = f"{result_b['predicted_next_payment']:.4f} (yield {result_b['mean_yield']*100:.3f}%)" if result_b else "no price data"
        print(f"{code:<8}{name_en:<10}{label_a:<28}{label_b}")

        if result_a:
            print("    [A] payments used: " + ", ".join(f"{d.isoformat()}={amt:.4f}" for d, amt in windowed))
            print("    [A] growth ratios: " + ", ".join(f"{r * 100:+.1f}%" for r in result_a["ratios"]))
            print(f"    [A] trailing 1yr total: {result_a['trailing_1yr_total']:.4f}  ->  "
                  f"projected next-1yr total: {result_a['predicted_next_1yr_total']:.4f}")
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
            "method_a_n_payments": result_a["n_payments"] if result_a else "",
            "method_a_geometric_mean_growth": round(result_a["geometric_mean_growth"], 6) if result_a else "",
            "method_a_predicted_next_payment": round(result_a["predicted_next_payment"], 4) if result_a else "",
            "method_a_predicted_next_1yr_total": round(result_a["predicted_next_1yr_total"], 4) if result_a else "",
            "method_b_n_payments": result_b["n_payments"] if result_b else "",
            "method_b_mean_yield": round(result_b["mean_yield"], 6) if result_b else "",
            "method_b_latest_price": result_b["latest_price"] if result_b else "",
            "method_b_predicted_next_payment": round(result_b["predicted_next_payment"], 4) if result_b else "",
        })

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    fieldnames = ["code", "name", "name_en", "window_start", "window_end",
                  "method_a_n_payments", "method_a_geometric_mean_growth",
                  "method_a_predicted_next_payment", "method_a_predicted_next_1yr_total",
                  "method_b_n_payments", "method_b_mean_yield", "method_b_latest_price",
                  "method_b_predicted_next_payment"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Saved {OUTPUT_CSV}")


if __name__ == "__main__":
    run()
