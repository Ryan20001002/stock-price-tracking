"""
PROTOTYPE, now wired into the app (2026-10-08) -- 5-trading-day
probability-of-move model for every ticker in the watchlist.

STATUS, read this before trusting anything this module prints or the
"股價方向預測" tab shows: this is an experimental research feature for
the dissertation's share-price-prediction half, not a finished,
validated predictor. A genuine walk-forward backtest against this
project's own price history (see the calibration numbers this module
saves, and the "股價方向預測" tab's own caveat banner) found that the
momentum/volatility/volume feature set does NOT reliably beat the
simple historical base rate for calling direction -- a result consistent
with the efficient-markets literature reviewed for this dissertation,
and reported here without spin rather than hidden. Treat every number
from this module as a research finding to discuss in the dissertation,
not as a trading signal.

Design (see predict_dividends.py's style of CAVEATS section for the
house convention of documenting *why*, not just *what*): rather than
training a separate yes/no classifier per move-size threshold (3%/5%/
10%), which starves on the rare 10% case (only a handful of real
occurrences out of a few hundred 5-day windows per ticker), this
predicts the 5-day return's conditional MEAN (via a simple Ridge
regression) and conditional VOLATILITY (via the walk-forward training
window's own residual std), then derives P(return >= threshold) for ANY
threshold from the normal CDF -- this uses every data point for every
threshold instead of only the rare tail events. A separate logistic
regression "P(up)" model is run alongside it purely as a simpler point
of comparison for the direction call, together with two honest
baselines: the trailing historical base rate (not 50/50) and naive
persistence (same sign as the trailing 5-day return).

Feature families, reusing data already collected elsewhere in this
project rather than fetching anything new:
  - Price momentum (5/10/20-day) and realized volatility (10/20-day),
    from predict_dividends.load_prices()'s already split-adjusted
    (splits.py) closing prices -- see that module for why un-adjusted
    prices across a split boundary would badly corrupt any cross-time
    return here (0050's 2025-06-18 4-for-1 split is the concrete case).
  - Volume ratio (today's volume vs. its own trailing 20-day average),
    from data/prices/<code>.csv directly (volume isn't part of
    predict_dividends.load_prices()'s return value), split-adjusted the
    OPPOSITE way from price (multiplied, not divided, by the same
    per-date factor -- see splits.adjustment_factor()).
  - Days since the last ex-dividend date (as a fraction of a year,
    capped at 1.0), from predict_dividends.load_dividends().
  - NEWS SENTIMENT (by explicit request, 2026-10-08): mean_sentiment and
    log1p(headline_count), from data/news/news_sentiment.csv if it
    exists. Two scorers can produce this file, both writing the same
    schema so this module needs zero changes to read either one:
      * news_sentiment_lexicon.py (added 2026-10-10, by explicit
        request -- "ship to A"): a dependency-free, self-curated
        Chinese financial-sentiment word-list scorer. Runs INSIDE the
        deployed app itself via a sidebar button (no network, no heavy
        model, done in microseconds) -- this is now the normal way
        news_sentiment.csv gets populated, local run no longer
        required. See that module's own docstring for the full
        methodology and its honest caveats (the word list is NOT the
        real NTUSD dictionary -- that couldn't be sourced from this
        project's dev sandbox -- it's hand-curated and should be
        described as such in any methodology write-up).
      * news_sentiment.py (the original approach): a Hugging Face
        BERT classifier (yiyanghkust/finbert-tone-chinese), more
        accurate in principle but must be run LOCALLY -- it needs
        `transformers`/`torch`, which this app's requirements.txt
        deliberately does NOT include, since that model and its
        weights have no reason to load inside the deployed Streamlit
        container (and risk crashing it -- see that module's
        docstring). A local run of it overwrites whatever
        news_sentiment_lexicon.py had scored.
    Both are lagged the same no-leakage way (see
    lagged_sentiment_features() below, a local reimplementation of
    news_sentiment.daily_sentiment_features()'s contract): only
    headlines strictly BEFORE the prediction date count, because
    Taiwanese retail headlines often just describe a price move that
    ALREADY happened -- same-day sentiment would be a disguised form of
    look-ahead leakage. If news_sentiment.csv doesn't exist yet (true
    until either scorer has been run at least once), both sentiment
    columns are a harmless constant 0.0/0.0 for every row: the model
    mechanically learns nothing from a constant, so its fitted weight
    for those two features will show as (very close to) zero until
    real, varying sentiment data exists.

Validation: walk-forward (expanding window), retrained every
REFIT_EVERY trading days, never using any future data at the time of a
prediction -- see walk_forward().

Needs a minimum amount of price history per ticker (MIN_TRAIN + a few
HORIZON-sized windows) before it can run a walk-forward evaluation at
all; run() skips (with a printed reason, not an error) any ticker that
doesn't have enough yet rather than failing the whole run.
"""

import csv
import math
import os
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from scipy.stats import norm

from config import DATA_DIR, WATCHLIST
import splits
from predict_dividends import load_dividends, load_prices as load_prices_close

OUTPUT_DIR = os.path.join(DATA_DIR, "predictions")
SUMMARY_CSV = os.path.join(OUTPUT_DIR, "price_direction_summary.csv")
WEIGHTS_CSV = os.path.join(OUTPUT_DIR, "price_direction_weights.csv")
CALIBRATION_CSV = os.path.join(OUTPUT_DIR, "price_direction_calibration.csv")
SENTIMENT_CSV = os.path.join(DATA_DIR, "news", "news_sentiment.csv")

HORIZON = 5              # trading days ahead
MIN_TRAIN = 252          # ~1 year before the first walk-forward prediction
REFIT_EVERY = 20         # retrain every ~1 month of trading days
THRESHOLDS = [3, 5, 10]  # percent
SENTIMENT_LOOKBACK_DAYS = 3
N_CALIBRATION_BUCKETS = 5
# build_features() needs this many leading days for its longest momentum/
# volatility window (20) plus HORIZON trailing days for the target -- a
# ticker with fewer total price rows than this has literally nothing to
# evaluate and is skipped outright (not even the "latest prediction").
MIN_ROWS_FOR_ANY_FEATURE = 20 + HORIZON

FEATURE_NAMES = [
    "mom_5", "mom_10", "mom_20", "vol_10", "vol_20", "vol_ratio",
    "days_since_ex_div_frac", "mean_sentiment", "log1p_headline_count",
]


def load_volume(code):
    """Returns a sorted [(date, volume), ...] for this ticker, split-
    adjusted the OPPOSITE way from price (multiplied, not divided, by
    the same per-date factor -- see splits.py's module docstring for why
    volume and price move in opposite directions across a split).
    volume is None for any row missing it (price_data.py's CSV always
    has it in practice, but this stays defensive rather than assuming).
    Returns [] if there's no price file for this ticker yet."""
    path = os.path.join(DATA_DIR, "prices", f"{code}.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("Close"):
                continue
            y, m, d = row["Date"].split("-")
            dt = date(int(y), int(m), int(d))
            vol = float(row["Volume"]) if row.get("Volume") else None
            rows.append((dt, vol))
    rows.sort(key=lambda r: r[0])
    splits_list = splits.fetch_splits(code)
    return [
        (d, v * splits.adjustment_factor(d, splits_list) if v is not None else None)
        for d, v in rows
    ]


def load_sentiment_rows():
    """Returns every scored-headline row from data/news/news_sentiment.csv
    (as produced either by news_sentiment_lexicon.py's in-app sidebar
    button, or by a local run of news_sentiment.py -- see this module's
    own docstring for how the two scorers relate), or [] if that file
    doesn't exist on this container yet. Returning [] is what makes
    lagged_sentiment_features() fall back to the inert (0.0, 0) default
    for every row, so this still runs end-to-end without real sentiment
    data -- it just can't learn anything from a feature that's
    constant."""
    if not os.path.exists(SENTIMENT_CSV):
        return []
    rows = []
    with open(SENTIMENT_CSV, newline="", encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def lagged_sentiment_features(scored_rows, code, as_of_date, lookback_days=SENTIMENT_LOOKBACK_DAYS):
    """Same no-leakage contract as news_sentiment.daily_sentiment_features()
    (kept as a plain, dependency-light copy here rather than an import,
    so this module never needs transformers/torch just to run): returns
    (mean_score, headline_count) using ONLY headlines for `code`
    published STRICTLY BEFORE as_of_date (a date object), within the
    trailing lookback_days. Returns (0.0, 0) when there's no match --
    either genuinely no news that day, or (until either sentiment scorer
    has been run at least once) because scored_rows is always []."""
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


def build_features(code, sentiment_rows):
    """Returns (X, y, dates) for this ticker -- see the module docstring
    for the feature list. X[i] is usable at the close of dates[i] (i.e.
    built only from information available by that day); y[i] is the
    realized forward HORIZON-day return (percent). Rows without enough
    trailing/leading history are dropped. Returns (None, None, None) if
    there isn't even enough price history to build one feature row."""
    closes = load_prices_close(code)          # [(date, close)], split-adjusted
    volumes = dict(load_volume(code))          # {date: volume}, split-adjusted
    ex_div_dates = sorted(d for d, _ in load_dividends()[0].get(code, []))

    if len(closes) < MIN_ROWS_FOR_ANY_FEATURE:
        return None, None, None

    dates_list = [d for d, _ in closes]
    close_vals = [c for _, c in closes]
    vol_vals = [volumes.get(d) for d in dates_list]
    n = len(closes)

    X, y, dates = [], [], []
    for i in range(20, n - HORIZON):
        c = close_vals[i]
        mom_5 = close_vals[i] / close_vals[i - 5] - 1
        mom_10 = close_vals[i] / close_vals[i - 10] - 1
        mom_20 = close_vals[i] / close_vals[i - 20] - 1

        daily_rets_10 = [close_vals[j] / close_vals[j - 1] - 1 for j in range(i - 9, i + 1)]
        daily_rets_20 = [close_vals[j] / close_vals[j - 1] - 1 for j in range(i - 19, i + 1)]
        vol_10 = float(np.std(daily_rets_10, ddof=1))
        vol_20 = float(np.std(daily_rets_20, ddof=1))

        window_vols = vol_vals[i - 19:i + 1]
        if vol_vals[i] and all(window_vols):
            vol_ratio = vol_vals[i] / (sum(window_vols) / 20)
        else:
            vol_ratio = 1.0

        d = dates_list[i]
        past_ex_div = [ed for ed in ex_div_dates if ed <= d]
        days_since_ex_div = (d - past_ex_div[-1]).days if past_ex_div else 365

        mean_sentiment, headline_count = lagged_sentiment_features(sentiment_rows, code, d)

        feat = [mom_5, mom_10, mom_20, vol_10, vol_20, vol_ratio,
                min(days_since_ex_div, 365) / 365.0,
                mean_sentiment, math.log1p(headline_count)]

        fwd_return = (close_vals[i + HORIZON] / c - 1) * 100

        X.append(feat)
        y.append(fwd_return)
        dates.append(d)

    if not X:
        return None, None, None
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


def calibration_table(probs, outcomes, n_buckets=N_CALIBRATION_BUCKETS):
    """Bucket predictions by predicted probability and compare to the
    realized frequency in each bucket -- the standard 'reliability'
    check for a probability forecast."""
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


def run():
    sentiment_rows = load_sentiment_rows()
    if sentiment_rows:
        print(f"[price_direction] loaded {len(sentiment_rows)} scored headline(s) from news_sentiment.csv")
    else:
        print("[price_direction] no data/news/news_sentiment.csv -- mean_sentiment/"
              "log1p_headline_count will be a constant 0.0/0.0 for every ticker below "
              "(click the app's \"重新計算新聞情緒（詞典法，實驗性）\" sidebar button, or "
              "run news_sentiment.py locally and let it push to GitHub, to change this).")

    summary_rows = []
    weight_rows = []
    calibration_rows = []

    for stock in WATCHLIST:
        code = stock["code"]
        name = stock["name"]
        print(f"\n{code} {name}")
        print("-" * 60)

        X, y, dates = build_features(code, sentiment_rows)
        if X is None:
            print(f"  skipped -- not enough price history yet "
                  f"(need >= {MIN_ROWS_FOR_ANY_FEATURE} trading days on file)")
            continue
        if len(X) < MIN_TRAIN + 1:
            print(f"  skipped -- {len(X)} usable feature row(s), need >= {MIN_TRAIN + 1} "
                  f"for even one walk-forward evaluation point")
            continue

        results = walk_forward(X, y)
        actual_up = [r["actual_up"] for r in results]
        p_logit = [r["p_up_logit"] for r in results]
        p_normal = [r["p_up_normal"] for r in results]
        base_rate = [r["train_base_rate"] for r in results]
        persistence = [r["persistence_pred_up"] for r in results]

        logit_brier = brier_score(p_logit, actual_up)
        base_rate_brier = brier_score(base_rate, actual_up)
        normal_brier = brier_score(p_normal, actual_up)
        beats_base_rate = logit_brier < base_rate_brier

        print(f"  walk-forward points: {len(results)}  |  true base rate (eval period): "
              f"{np.mean(actual_up)*100:.1f}% up")
        print(f"  Brier -- logistic: {logit_brier:.4f}  base-rate-only: {base_rate_brier:.4f}  "
              f"normal-approx: {normal_brier:.4f}  "
              f"({'beats' if beats_base_rate else 'does NOT beat'} the base-rate baseline)")

        n_sentiment_rows = sum(1 for x in X if x[8] != 0.0)

        # Latest live prediction, using ALL available data to fit.
        logit_full = LogisticRegression(max_iter=1000, C=1.0).fit(X, (y > 0).astype(int))
        ridge_full = Ridge(alpha=10.0).fit(X, y)
        resid_full = y - ridge_full.predict(X)
        sigma_full = float(np.std(resid_full, ddof=1))
        x_latest = X[-1:]
        mean_hat_latest = float(ridge_full.predict(x_latest)[0])
        p_up_latest = float(logit_full.predict_proba(x_latest)[0, 1])

        threshold_probs = {}
        for t in THRESHOLDS:
            threshold_probs[f"p_up_{t}"] = 1 - norm.cdf((t - mean_hat_latest) / sigma_full)
            threshold_probs[f"p_down_{t}"] = norm.cdf((-t - mean_hat_latest) / sigma_full)

        print(f"  latest (as of {dates[-1]}): P(up 5d)={p_up_latest*100:.1f}%  "
              f"mean={mean_hat_latest:+.2f}%  sigma={sigma_full:.2f}%")

        summary_rows.append({
            "code": code,
            "name": name,
            "data_start": dates[0].isoformat(),
            "data_end": dates[-1].isoformat(),
            "n_feature_rows": len(X),
            "n_walk_forward_points": len(results),
            "n_rows_with_sentiment": n_sentiment_rows,
            "eval_true_up_rate": round(float(np.mean(actual_up)), 4),
            "logistic_brier": round(logit_brier, 4),
            "base_rate_only_brier": round(base_rate_brier, 4),
            "normal_approx_brier": round(normal_brier, 4),
            "beats_base_rate": beats_base_rate,
            "naive_persistence_accuracy": round(accuracy(persistence, actual_up), 4),
            "latest_date": dates[-1].isoformat(),
            "latest_p_up_logistic": round(p_up_latest, 4),
            "latest_mean_5d_return_pct": round(mean_hat_latest, 4),
            "latest_sigma_pct": round(sigma_full, 4),
            **{f"latest_{k}": round(v, 4) for k, v in threshold_probs.items()},
        })

        for name_feat, ridge_coef, logit_coef in zip(FEATURE_NAMES, ridge_full.coef_, logit_full.coef_[0]):
            weight_rows.append({
                "code": code,
                "feature": name_feat,
                "ridge_mean_model_coef": round(float(ridge_coef), 6),
                "logistic_p_up_model_coef": round(float(logit_coef), 6),
            })

        for p_mean, o_mean, cnt in calibration_table(p_logit, actual_up):
            calibration_rows.append({
                "code": code,
                "predicted_p_up": round(float(p_mean), 4),
                "realized_freq_up": round(float(o_mean), 4),
                "n": cnt,
            })

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    summary_fields = [
        "code", "name", "data_start", "data_end", "n_feature_rows",
        "n_walk_forward_points", "n_rows_with_sentiment", "eval_true_up_rate",
        "logistic_brier", "base_rate_only_brier", "normal_approx_brier",
        "beats_base_rate", "naive_persistence_accuracy", "latest_date",
        "latest_p_up_logistic", "latest_mean_5d_return_pct", "latest_sigma_pct",
    ] + [f"latest_p_up_{t}" for t in THRESHOLDS] + [f"latest_p_down_{t}" for t in THRESHOLDS]
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\nSaved {SUMMARY_CSV}")

    with open(WEIGHTS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "feature", "ridge_mean_model_coef", "logistic_p_up_model_coef"])
        writer.writeheader()
        writer.writerows(weight_rows)
    print(f"Saved {WEIGHTS_CSV}")

    with open(CALIBRATION_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "predicted_p_up", "realized_freq_up", "n"])
        writer.writeheader()
        writer.writerows(calibration_rows)
    print(f"Saved {CALIBRATION_CSV}")


if __name__ == "__main__":
    run()
