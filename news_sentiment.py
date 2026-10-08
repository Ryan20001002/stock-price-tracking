"""
Score each headline already fetched by news_data.py for sentiment, using
a Hugging Face Chinese financial-sentiment classifier, and write a daily,
per-ticker, LAGGED sentiment feature usable by a price-prediction model.

Model: yiyanghkust/finbert-tone-chinese (Apache 2.0) -- a BERT-base
classifier fine-tuned on ~8k Chinese analyst-report sentences, outputting
Neutral/Positive/Negative. Two caveats worth re-checking once you have
real output in front of you, NOT assumed here:
  1. It was trained on formal, SIMPLIFIED-Chinese analyst language. Your
     headlines (news_data.py queries Google News with ceid=TW:zh-Hant)
     are casual, TRADITIONAL-Chinese retail-media clickbait. That's a
     real script + register mismatch -- inspect the printed examples
     below before trusting the scores.
  2. Headline sentiment is NOT the same as genuinely new information.
     Taiwanese retail financial headlines routinely just DESCRIBE a price
     move that already happened ("0050盤中飆新高" = "0050 surged to a new
     intraday high") -- scoring that and using it to predict the SAME
     day's forward return is close to feeding the model a text paraphrase
     of the price move itself, a disguised form of look-ahead leakage.
     This is why score_headlines() below stores the raw per-headline
     score with its own timestamp, and daily_sentiment_features() is a
     SEPARATE step that the caller must explicitly lag (see its
     docstring) before using it as a predictive feature -- the two are
     kept apart on purpose so lagging can't be forgotten accidentally.

Usage:
    python news_sentiment.py
Reads data/news/news.csv (from news_data.py). Writes
data/news/news_sentiment.csv (one row per headline, with its sentiment
score) and prints a handful of real examples so you can eyeball whether
the model's calls look sane on your actual headlines before relying on
it for anything.

Requires `transformers` and `torch` (deliberately NOT part of this
project's requirements.txt -- those are heavy, GPU-friendly packages
with no reason to load inside the deployed Streamlit container, which
never calls score_headlines() itself; see price_direction.py's module
docstring). Install them yourself to run this script locally: `pip
install transformers torch`. Note the first run downloads the model
from Hugging Face, so it needs a working internet connection and will be
slow the first time, fast after that since it's cached locally.

GITHUB PUSH (2026-10-08, same pattern as news_data.py's own __main__
block, added so price_direction.py's deployed app copy can actually see
this): running `python news_sentiment.py` standalone also pushes
data/news/*.csv -- which by then includes this script's own
news_sentiment.csv -- to GitHub-backed storage if [github_data] is
configured (see market_data_sync.py), exactly like every other fetch
script already does. Without this, news_sentiment.csv would only ever
exist on YOUR machine: the deployed app has no way to run this script
itself (no transformers/torch there, by design), so the only way its
"股價方向預測" tab ever sees real sentiment data is if a local run like
this one pushes it. No-op if GitHub-backed storage isn't configured --
see market_data_sync.py's own docstring for what that means in practice.
"""

import csv
import os
from email.utils import parsedate_to_datetime

from config import DATA_DIR

NEWS_CSV = os.path.join(DATA_DIR, "news", "news.csv")
SENTIMENT_CSV = os.path.join(DATA_DIR, "news", "news_sentiment.csv")
MODEL_NAME = "yiyanghkust/finbert-tone-chinese"

# yiyanghkust/finbert-tone-chinese's own label mapping (see its model
# card -- the raw pipeline output is LABEL_0/1/2, not human-readable).
LABEL_MAP = {"LABEL_0": "neutral", "LABEL_1": "positive", "LABEL_2": "negative"}

FIELDNAMES = ["code", "published", "published_iso", "title", "label",
              "p_positive", "p_neutral", "p_negative", "score"]


def _load_news_rows():
    if not os.path.exists(NEWS_CSV):
        raise FileNotFoundError(
            f"{NEWS_CSV} doesn't exist yet -- run news_data.py (or the "
            f"app's 抓取新聞 button) at least once first."
        )
    rows = []
    with open(NEWS_CSV, newline="", encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def _load_already_scored_links():
    if not os.path.exists(SENTIMENT_CSV):
        return set()
    with open(SENTIMENT_CSV, newline="", encoding="utf-8-sig") as f:
        # keyed by (code, title) since news.csv's own link is the natural
        # unique id but isn't carried into this file's current columns --
        # if you want exact-link dedup instead, add "link" to FIELDNAMES
        # and key on that; title+code is a reasonable proxy in practice.
        return {(r["code"], r["title"]) for r in csv.DictReader(f)}


def score_headlines(force=False):
    """Scores every headline in news.csv not already in news_sentiment.csv
    (skip-if-already-scored, same pattern as this project's other
    per-item-expensive steps) and appends the results. Returns the full
    list of scored rows (existing + newly scored)."""
    from transformers import pipeline  # imported lazily so this module's
    # pure functions (daily_sentiment_features below) can be imported and
    # unit-tested without requiring transformers/torch to be installed.

    news_rows = _load_news_rows()
    already_scored = set() if force else _load_already_scored_links()

    existing_output_rows = []
    if not force and os.path.exists(SENTIMENT_CSV):
        with open(SENTIMENT_CSV, newline="", encoding="utf-8-sig") as f:
            existing_output_rows = list(csv.DictReader(f))

    to_score = [r for r in news_rows if (r["code"], r["title"]) not in already_scored]
    print(f"[news_sentiment] {len(to_score)} new headline(s) to score, "
          f"{len(already_scored)} already scored and skipped")

    if to_score:
        classifier = pipeline(
            "text-classification",
            model=MODEL_NAME,
            top_k=None,  # return all 3 class probabilities, not just the top label
        )
        titles = [r["title"] for r in to_score]
        # batch for speed -- one call for all headlines rather than one per headline
        all_scores = classifier(titles, batch_size=16, truncation=True)
    else:
        all_scores = []

    new_rows = []
    for row, scores in zip(to_score, all_scores):
        probs = {LABEL_MAP[s["label"]]: s["score"] for s in scores}
        p_pos = probs.get("positive", 0.0)
        p_neu = probs.get("neutral", 0.0)
        p_neg = probs.get("negative", 0.0)
        top_label = max(probs, key=probs.get)

        try:
            pub_dt = parsedate_to_datetime(row["published"])
            pub_iso = pub_dt.date().isoformat()
        except (TypeError, ValueError):
            pub_iso = ""

        new_rows.append({
            "code": row["code"],
            "published": row["published"],
            "published_iso": pub_iso,
            "title": row["title"],
            "label": top_label,
            "p_positive": round(p_pos, 4),
            "p_neutral": round(p_neu, 4),
            "p_negative": round(p_neg, 4),
            # signed score in [-1, +1]: simple, interpretable, and the
            # natural thing to average across headlines in a day -- NOT
            # the model's own output, just a convenience transform of it.
            "score": round(p_pos - p_neg, 4),
        })

    all_rows = existing_output_rows + new_rows
    os.makedirs(os.path.dirname(SENTIMENT_CSV), exist_ok=True)
    with open(SENTIMENT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"  -> saved {len(all_rows)} scored headline(s) to {SENTIMENT_CSV}")

    if new_rows:
        print("\n  Spot-check a few of today's scores (inspect these before trusting the model):")
        for r in new_rows[:8]:
            print(f"    [{r['label']:>8} score={r['score']:+.2f}] {r['title']}")

    return all_rows


def daily_sentiment_features(scored_rows, code, as_of_date, lookback_days=3):
    """Returns (mean_score, headline_count) using ONLY headlines published
    STRICTLY BEFORE as_of_date (an ISO date string), within the trailing
    lookback_days -- i.e. the feature usable to predict the forward return
    starting AT as_of_date, with no leakage from that day's own news.

    Caller contract: if you're predicting a return that starts at the
    close of trading day D, pass as_of_date=D so "today's" headlines
    (which may describe today's own price move) are excluded by
    construction, not by caller discipline alone.

    Returns (0.0, 0) if there are no matching headlines -- a day/ticker
    with no news is common for these watchlist sizes and should be
    treated as "no signal", not an error."""
    from datetime import date, timedelta
    as_of = date.fromisoformat(as_of_date)
    window_start = (as_of - timedelta(days=lookback_days)).isoformat()

    matches = [
        float(r["score"]) for r in scored_rows
        if r["code"] == code and r["published_iso"]
        and window_start <= r["published_iso"] < as_of_date
    ]
    if not matches:
        return 0.0, 0
    return sum(matches) / len(matches), len(matches)


if __name__ == "__main__":
    score_headlines()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py and this module's own docstring for why this
    # matters here specifically) -- no-op otherwise. Mirrors
    # news_data.py's __main__ block.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("news")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] scored locally, but GitHub backup failed: {e}")
