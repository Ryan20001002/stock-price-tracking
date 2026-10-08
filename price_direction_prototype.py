"""
PROTOTYPE -- 5-trading-day probability-of-move model for Taiwan dividend ETFs.

Purpose: a research prototype, run against the user's own real (split-
adjusted) price and dividend history, to test whether a defensible
probability model can predict P(5-day return >= threshold) for several
thresholds (3%, 5%, 10%, up and down) -- NOT yet wired into the app.

Design choice (see conversation): rather than training a separate
yes/no classifier per threshold (which starves on the 10% case -- only
3-7 real occurrences out of ~700 5-day windows per ticker in this
sample), this predicts the 5-day return's conditional MEAN (via a
simple linear model) and conditional VOLATILITY (via trailing realized
vol scaled to the 5-day horizon), then derives P(return >= threshold)
for ANY threshold from the normal CDF. This uses every data point for
every threshold instead of only the rare tail events.

A logistic-regression "P(up)" model is run alongside it purely as a
simpler point of comparison for the direction call.

Data actually available in this dev environment: split-adjusted OHLCV
price history and dividend payment history for 0050 / 006208 / 00878
only (no DDM valuation output, no institutional-flow data, no TAIEX
index data were present locally -- see discussion in chat). Those two
remaining feature families (DDM valuation gap, 三大法人 flow, market
regime) are the next features to add once this runs against the live
app's data.

NEWS SENTIMENT (2026-10-08, by explicit request): wired in as two more
features -- mean_sentiment and log1p(headline_count), both computed by
daily_sentiment_features() below using ONLY headlines strictly before
the prediction date (see news_sentiment.py for the full lagging
rationale: same-day headlines often just describe a price move that
ALREADY happened, so using them to predict that same day's forward
return would be a disguised form of look-ahead leakage).

IMPORTANT CAVEAT, read before trusting any sentiment-related number this
script prints: this dev environment has no data/news/news_sentiment.csv
(news_data.py's own archive-fix, shipped the same day as this, means
news.csv has never actually accumulated historical depth before now --
see its module docstring). So EVERY row's sentiment feature here is the
same inert fallback (0.0, 0 headlines) -- the model mechanically can't
find anything in a constant feature, so sentiment's apparent effect in
THIS run is meaningless by construction, not evidence it doesn't matter.
This script still prints each feature's fitted weight so you can see,
once you run it for real against your own accumulated
news_sentiment.csv (give it some weeks/months to build up real daily
coverage first), whether sentiment actually earns a non-trivial weight
or stays near zero even with real data behind it.

Validation: walk-forward (expanding window), retrained every REFIT_EVERY
trading days, never using any future data at the time of a prediction.
Baselines: (a) unconditional historical base rate of "up" computed from
the training window only (not 50/50), (b) naive persistence (same sign
as the trailing 5-day return).
"""

import csv
import math
import os
from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from scipy.stats import norm

DATA_DIR = "/mnt/user-data/outputs/stock-tracker/data"
TICKERS = ["0050", "006208", "00878"]
KNOWN_SPLITS = {"0050": [(date(2025, 6, 18), 4.0)]}

HORIZON = 5          # trading days ahead
MIN_TRAIN = 252       # ~1 year before the first prediction is made
REFIT_EVERY = 20       # retrain every ~1 month of trading days
THRESHOLDS = [3, 5, 10]  # percent
SENTIMENT_LOOKBACK_DAYS = 3  # trailing window for the lagged sentiment features

FEATURE_NAMES = [
    "mom_5", "mom_10", "mom_20", "vol_10", "vol_20", "vol_ratio",
    "days_since_ex_div_frac", "mean_sentiment", "log1p_headline_count",
]


def load_prices(code):
    path = f"{DATA_DIR}/prices/{code}.csv"
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("Close"):
                continue
            y, m, d = r["Date"].split("-")
            rows.append({
                "date": date(int(y), int(m), int(d)),
                "close": float(r["Close"]),
                "volume": float(r["Volume"]) if r.get("Volume") else None,
            })
    rows.sort(key=lambda r: r["date"])
    splits = KNOWN_SPLITS.get(code, [])
    for r in rows:
        factor = 1.0
        for sd, ratio in splits:
            if r["date"] < sd:
                factor *= ratio
        r["close"] = r["close"] / factor
        if r["volume"] is not None:
            r["volume"] = r["volume"] * factor  # volume scales inversely to price on a split
    return rows


def load_ex_div_dates(code):
    path = f"{DATA_DIR}/dividends/dividends.csv"
    dates = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["code"] != code:
                continue
            y, m, d = r["ex_dividend_date"].split("-")
            dates.append(date(int(y), int(m), int(d)))
    return sorted(dates)


def load_sentiment_rows():
    """Returns every scored-headline row from data/news/news_sentiment.csv
    (as produced by news_sentiment.py), or [] if that file doesn't exist
    yet -- which it won't in this dev environment (see the module
    docstring's IMPORTANT CAVEAT). Returning [] here is what makes
    lagged_sentiment_features() fall back to the inert (0.0, 0) default
    for every row, rather than erroring out, so this prototype still runs
    end-to-end without real sentiment data -- it just can't learn anything
    from a feature that's constant."""
    path = os.path.join(DATA_DIR, "news", "news_sentiment.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def lagged_sentiment_features(scored_rows, code, as_of_date, lookback_days=SENTIMENT_LOOKBACK_DAYS):
    """Local re-implementation of news_sentiment.daily_sentiment_features()'s
    no-leakage contract (kept as a plain copy here, not an import, so this
    script never needs transformers/torch just to run): returns
    (mean_score, headline_count) using ONLY headlines for `code` published
    STRICTLY BEFORE as_of_date (a date object), within the trailing
    lookback_days. Returns (0.0, 0) when there's no matching headline --
    either because there's genuinely no news that day, or (in this dev
    environment) because scored_rows is always []."""
    if not scored_rows:
        return 0.0, 0
    as_of_iso = as_of_date.isoformat()
    window_start_iso = (as_of_date - timedelta(days=lookback_days)).isoformat()
    matches = [
        float(r["score"]) for r in scored_rows
        if r.get("code") == code and r.get("published_iso")
        and window_start_iso <= r["published_iso"] < as_of_iso
    ]
    if not matches:
        return 0.0, 0
    return sum(matches) / len(matches), len(matches)


def build_features(rows, ex_div_dates, sentiment_rows=None, code=None):
    """Returns (X, y, dates) where X[i] is the feature vector usable at
    close of rows[i]['date'] (i.e. using only information available by
    that day), y[i] is the realized forward HORIZON-day return (percent),
    and dates[i] is that day's date. Rows without enough trailing or
    leading history to compute all features/target are dropped.

    sentiment_rows/code: pass the output of load_sentiment_rows() and the
    ticker's code to add the two lagged news-sentiment features (see
    lagged_sentiment_features()). Leaving sentiment_rows as None (or [])
    makes both sentiment columns a constant 0.0/0.0 -- harmless, but see
    the module docstring's caveat about what that does and doesn't prove."""
    closes = [r["close"] for r in rows]
    volumes = [r["volume"] for r in rows]
    n = len(rows)

    X, y, dates = [], [], []
    for i in range(20, n - HORIZON):
        c = closes[i]
        mom_5 = closes[i] / closes[i - 5] - 1
        mom_10 = closes[i] / closes[i - 10] - 1
        mom_20 = closes[i] / closes[i - 20] - 1

        daily_rets_10 = [closes[j] / closes[j - 1] - 1 for j in range(i - 9, i + 1)]
        daily_rets_20 = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
        vol_10 = float(np.std(daily_rets_10, ddof=1))
        vol_20 = float(np.std(daily_rets_20, ddof=1))

        if volumes[i] and all(volumes[i - 19:i + 1]):
            vol_ratio = volumes[i] / (sum(volumes[i - 19:i + 1]) / 20)
        else:
            vol_ratio = 1.0

        d = rows[i]["date"]
        past_ex_div = [ed for ed in ex_div_dates if ed <= d]
        days_since_ex_div = (d - past_ex_div[-1]).days if past_ex_div else 365

        mean_sentiment, headline_count = lagged_sentiment_features(sentiment_rows, code, d)

        feat = [mom_5, mom_10, mom_20, vol_10, vol_20, vol_ratio,
                min(days_since_ex_div, 365) / 365.0,
                mean_sentiment, math.log1p(headline_count)]

        fwd_return = (closes[i + HORIZON] / c - 1) * 100

        X.append(feat)
        y.append(fwd_return)
        dates.append(d)

    return np.array(X), np.array(y), dates


def walk_forward(X, y):
    """Expanding-window walk-forward evaluation. Returns a list of dicts,
    one per predicted point, each a prediction made using only data
    strictly before it (retrained every REFIT_EVERY steps to keep this
    fast; predictions in between reuse the last fit, still never looking
    forward)."""
    n = len(X)
    results = []
    logit = None
    ridge_mean = None
    train_base_rate = None
    train_resid_std = None

    for i in range(MIN_TRAIN, n):
        if (i - MIN_TRAIN) % REFIT_EVERY == 0 or logit is None:
            X_train, y_train = X[:i], y[:i]
            up_train = (y_train > 0).astype(int)

            if len(np.unique(up_train)) >= 2:
                logit = LogisticRegression(max_iter=1000, C=1.0)
                logit.fit(X_train, up_train)
            train_base_rate = float(np.mean(up_train))

            ridge_mean = Ridge(alpha=10.0)
            ridge_mean.fit(X_train, y_train)
            resid = y_train - ridge_mean.predict(X_train)
            train_resid_std = float(np.std(resid, ddof=1))

        x_i = X[i:i + 1]
        actual = y[i]

        p_up_logit = float(logit.predict_proba(x_i)[0, 1]) if logit is not None else train_base_rate
        mean_hat = float(ridge_mean.predict(x_i)[0])
        sigma_hat = max(train_resid_std, 1e-6)
        p_up_normal = 1 - norm.cdf((0 - mean_hat) / sigma_hat)

        thresh_probs = {}
        for t in THRESHOLDS:
            thresh_probs[f"p_up_{t}"] = 1 - norm.cdf((t - mean_hat) / sigma_hat)
            thresh_probs[f"p_down_{t}"] = norm.cdf((-t - mean_hat) / sigma_hat)

        # naive persistence baseline: same direction as the day's own mom_5 feature (col 0)
        persistence_pred_up = 1 if X[i, 0] > 0 else 0

        results.append({
            "actual": actual,
            "actual_up": int(actual > 0),
            "p_up_logit": p_up_logit,
            "p_up_normal": p_up_normal,
            "mean_hat": mean_hat,
            "sigma_hat": sigma_hat,
            "train_base_rate": train_base_rate,
            "persistence_pred_up": persistence_pred_up,
            **thresh_probs,
        })

    return results


def brier_score(probs, outcomes):
    return float(np.mean([(p - o) ** 2 for p, o in zip(probs, outcomes)]))


def accuracy(preds, outcomes):
    return float(np.mean([1 if p == o else 0 for p, o in zip(preds, outcomes)]))


def calibration_table(probs, outcomes, n_buckets=5):
    """Bucket predictions by predicted probability decile/quintile and
    compare to the realized frequency in each bucket -- the standard
    'reliability' check for a probability forecast."""
    order = np.argsort(probs)
    probs_sorted = np.array(probs)[order]
    outcomes_sorted = np.array(outcomes)[order]
    n = len(probs)
    rows = []
    bucket_size = n // n_buckets
    for b in range(n_buckets):
        start = b * bucket_size
        end = (b + 1) * bucket_size if b < n_buckets - 1 else n
        p_mean = probs_sorted[start:end].mean()
        o_mean = outcomes_sorted[start:end].mean()
        rows.append((p_mean, o_mean, end - start))
    return rows


def main():
    sentiment_rows = load_sentiment_rows()
    if sentiment_rows:
        print(f"[sentiment] loaded {len(sentiment_rows)} scored headline(s) from news_sentiment.csv\n")
    else:
        print("[sentiment] no data/news/news_sentiment.csv found -- mean_sentiment/"
              "log1p_headline_count will be a constant 0.0/0.0 for every row below "
              "(see the module docstring's IMPORTANT CAVEAT). Run news_sentiment.py "
              "locally and re-run this script once it exists to get a real test.\n")

    for code in TICKERS:
        print("=" * 70)
        print(f"{code}")
        print("=" * 70)

        rows = load_prices(code)
        ex_div = load_ex_div_dates(code)
        X, y, dates = build_features(rows, ex_div, sentiment_rows=sentiment_rows, code=code)
        print(f"Usable rows: {len(X)}  (date range {dates[0]} to {dates[-1]})")

        results = walk_forward(X, y)
        n_eval = len(results)
        print(f"Walk-forward evaluated predictions: {n_eval} "
              f"(first {MIN_TRAIN} rows reserved for initial training)\n")

        actual_up = [r["actual_up"] for r in results]
        p_logit = [r["p_up_logit"] for r in results]
        p_normal = [r["p_up_normal"] for r in results]
        base_rate = [r["train_base_rate"] for r in results]
        persistence = [r["persistence_pred_up"] for r in results]

        print("-- Direction ('up over next 5 trading days') --")
        print(f"  True base rate in eval period: {np.mean(actual_up)*100:.1f}% up")
        print(f"  Mean train-only base rate used as baseline: {np.mean(base_rate)*100:.1f}%")
        print(f"  Logistic regression accuracy:  {accuracy([1 if p>=0.5 else 0 for p in p_logit], actual_up)*100:.1f}%")
        print(f"  Always-predict-base-rate accuracy: {accuracy([1 if b>=0.5 else 0 for b in base_rate], actual_up)*100:.1f}%")
        print(f"  Naive persistence accuracy:    {accuracy(persistence, actual_up)*100:.1f}%")
        print(f"  Logistic regression Brier score (lower=better): {brier_score(p_logit, actual_up):.4f}")
        print(f"  Base-rate-only Brier score (lower=better):      {brier_score(base_rate, actual_up):.4f}")
        print(f"  Normal-approx (ridge mean/vol) Brier score:     {brier_score(p_normal, actual_up):.4f}")

        print("\n  Calibration check (logistic P(up), 5 buckets: predicted vs realized frequency):")
        for p_mean, o_mean, cnt in calibration_table(p_logit, actual_up):
            print(f"    predicted {p_mean*100:5.1f}%  ->  realized {o_mean*100:5.1f}%  (n={cnt})")

        print("\n-- Threshold move probabilities (normal-approx model), calibration --")
        for t in THRESHOLDS:
            p_up_t = [r[f"p_up_{t}"] for r in results]
            actual_up_t = [1 if r["actual"] >= t else 0 for r in results]
            p_down_t = [r[f"p_down_{t}"] for r in results]
            actual_down_t = [1 if r["actual"] <= -t else 0 for r in results]
            print(f"  >= +{t}%: realized {np.mean(actual_up_t)*100:.1f}% of the time | "
                  f"mean predicted prob {np.mean(p_up_t)*100:.1f}% | Brier {brier_score(p_up_t, actual_up_t):.4f}")
            print(f"  <= -{t}%: realized {np.mean(actual_down_t)*100:.1f}% of the time | "
                  f"mean predicted prob {np.mean(p_down_t)*100:.1f}% | Brier {brier_score(p_down_t, actual_down_t):.4f}")

        # today's live prediction, using ALL data fit
        logit_full = LogisticRegression(max_iter=1000, C=1.0).fit(X, (y > 0).astype(int))
        ridge_full = Ridge(alpha=10.0).fit(X, y)
        resid_full = y - ridge_full.predict(X)
        sigma_full = float(np.std(resid_full, ddof=1))
        x_latest = X[-1:]
        mean_hat_latest = float(ridge_full.predict(x_latest)[0])
        p_up_latest = float(logit_full.predict_proba(x_latest)[0, 1])

        print(f"\n-- Latest available prediction (as of {dates[-1]}, uses ALL history to fit) --")
        print(f"  P(up over next 5 trading days) [logistic]: {p_up_latest*100:.1f}%")
        print(f"  Predicted mean 5-day return: {mean_hat_latest:+.2f}%  (residual sigma {sigma_full:.2f}%)")
        for t in THRESHOLDS:
            p_up_t = 1 - norm.cdf((t - mean_hat_latest) / sigma_full)
            p_down_t = norm.cdf((-t - mean_hat_latest) / sigma_full)
            print(f"  P(return >= +{t}%): {p_up_t*100:5.1f}%   P(return <= -{t}%): {p_down_t*100:5.1f}%")

        print("\n  Fitted feature weights (full-history fit; this is where sentiment's "
              "contribution would show up once it's a real, non-constant feature):")
        print(f"    {'feature':<24} {'ridge (mean model)':>20} {'logistic (P(up) model)':>24}")
        for name, ridge_coef, logit_coef in zip(FEATURE_NAMES, ridge_full.coef_, logit_full.coef_[0]):
            print(f"    {name:<24} {ridge_coef:>20.4f} {logit_coef:>24.4f}")
        n_sent = sum(1 for x in X if x[8] != 0.0)
        print(f"  ({n_sent} of {len(X)} rows had a non-zero headline count feature)")
        print()


if __name__ == "__main__":
    main()
