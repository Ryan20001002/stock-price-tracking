"""
Alternative dividend prediction: instead of extrapolating the dividend
growth rate (see predict_dividends.py), this predicts the next dividend
from the fund's *yield* -- dividend divided by share price -- on the
theory that for an ETF, a payment scales with the price/value of the fund
more directly than it follows any smooth growth trend.

Method
------
1. For each dividend payment in the trailing 1-year window (same window
   as predict_dividends.py, for comparability), find the share price on
   that date: the closing price on the payment date itself if it was a
   trading day, otherwise the most recent prior trading day's close.
2. Compute that payment's yield: yield_i = dividend_i / price_i
3. Take the (arithmetic) mean of those per-payment yields. Unlike the
   growth-rate script, this is a plain average, not a geometric mean --
   yield is a *level* (dividend as a fraction of price), not a chain of
   compounding growth steps, so there's nothing to compound-average here.
4. Predict the next dividend = latest available share price * mean yield.

Usage:
    python predict_dividends_yield.py
Reads data/dividends/dividends.csv and data/prices/<code>.csv (both
produced by dividend_data.py / price_data.py) and writes
data/dividends/dividend_prediction_yield.csv, plus prints a summary.

CAVEATS
-------
- Matches each dividend to a share price by date, using the closing price
  on or just before the payment date. If dividend timestamps and TWSE's
  own ex-dividend price adjustment don't line up exactly, yields will be
  slightly off -- treat them as approximate.
- Still only 2-4 data points per ticker (trailing 1 year), so the mean
  yield is a small sample, same caveat as the growth-rate approach.
- Assumes the SAME yield going forward as the trailing average -- ignores
  changes in payout policy, interest rates, or the underlying holdings'
  own dividend trends.
- Treat this as a second transparent baseline to compare against the
  growth-rate one, not investment advice.
"""

import csv
import os
from collections import defaultdict
from datetime import date, timedelta

from config import DATA_DIR

DIVIDENDS_CSV = os.path.join(DATA_DIR, "dividends", "dividends.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "dividends", "dividend_prediction_yield.csv")

WINDOW_DAYS = 365


def load_dividends():
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
    return rows_by_code, meta


def load_prices(code):
    """Return a sorted list of (date, close) for this ticker, or [] if no file."""
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


def run():
    rows_by_code, meta = load_dividends()

    summary_rows = []
    print(f"{'Ticker':<8}{'Name':<10}{'Payments used (last 1yr)':<28}{'Mean yield':<14}{'Latest price':<14}{'Predicted next dividend'}")
    print("-" * 110)

    for code in sorted(rows_by_code):
        payments = sorted(rows_by_code[code])
        prices = load_prices(code)
        if not prices:
            print(f"{code:<8}{meta[code]['name_en']:<10}no price data found (data/prices/{code}.csv) -- skipping")
            print()
            continue

        latest_pay_date = payments[-1][0]
        window_start = latest_pay_date - timedelta(days=WINDOW_DAYS)
        last_year_payments = [(d, amt) for d, amt in payments if d > window_start]

        yields = []
        detail = []
        for pay_date, amount in last_year_payments:
            price = price_on_or_before(prices, pay_date)
            if price is None or price <= 0:
                continue
            y = amount / price
            yields.append(y)
            detail.append((pay_date, amount, price, y))

        if not yields:
            print(f"{code:<8}{meta[code]['name_en']:<10}no matching price data for its dividend dates -- skipping")
            print()
            continue

        mean_yield = sum(yields) / len(yields)
        latest_price = prices[-1][1]
        latest_price_date = prices[-1][0]
        predicted_next = latest_price * mean_yield

        print(f"{code:<8}{meta[code]['name_en']:<10}{len(yields)} payments{'':<19}"
              f"{mean_yield * 100:>6.3f}%{'':<7}{latest_price:>8.2f} ({latest_price_date}){'':<2}"
              f"{predicted_next:>10.4f}")
        for pay_date, amount, price, y in detail:
            print(f"    {pay_date}: dividend={amount:.4f}  price={price:.2f}  yield={y * 100:.3f}%")
        print()

        summary_rows.append({
            "code": code,
            "name": meta[code]["name"],
            "name_en": meta[code]["name_en"],
            "n_payments_used": len(yields),
            "mean_yield_per_payment": round(mean_yield, 6),
            "latest_price": latest_price,
            "latest_price_date": latest_price_date.isoformat(),
            "predicted_next_dividend": round(predicted_next, 4),
        })

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    fieldnames = ["code", "name", "name_en", "n_payments_used", "mean_yield_per_payment",
                  "latest_price", "latest_price_date", "predicted_next_dividend"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Saved {OUTPUT_CSV}")


if __name__ == "__main__":
    run()
