"""
Lexicon/dictionary-based sentiment scoring for headlines already fetched by
news_data.py -- a lightweight alternative to news_sentiment.py's Hugging
Face model, designed specifically to run INSIDE the deployed Streamlit app
itself (a real sidebar button, not a local-only script).

WHY THIS EXISTS (2026-10-10, by explicit request -- "Can't I run sentiment
classification on the webpage instead?"): news_sentiment.py's
transformers/torch-based model was deliberately kept local-only, because
loading a BERT model into Streamlit Community Cloud's shared, resource-
limited container risks crashing the WHOLE app (confirmed via research:
the platform's RAM cap fluctuates and tops out around 2.7GB, and "this app
has gone over its resource limits" is a documented common failure mode),
and its ephemeral disk means the ~400MB+ model would re-download from
Hugging Face on every cold start. This module has NEITHER problem: it's
pure Python, zero new pip dependencies, a few KB of word lists, and scores
a headline in microseconds -- safe to run live, on demand, inside the app.

METHODOLOGY, read this before citing it as a method in the dissertation:
this is plain DICTIONARY/LEXICON scoring, not a trained model. For each
headline: count how many POSITIVE_WORDS and NEGATIVE_WORDS appear as
substrings (Chinese financial-sentiment words are almost always 2-4
characters, so simple substring matching over the raw, untokenized string
is a standard simplified technique for this -- no word-segmentation
library needed, which is itself part of why this has zero new
dependencies), with a short negation check immediately before each match
(不/沒/無/未/非/別/莫/勿/並未/並非/難以) that flips that match's polarity.
The resulting score is (pos_hits - neg_hits) / (pos_hits + neg_hits),
clipped to [-1, +1], 0.0 if there are no hits at all -- the same [-1, +1]
scale news_sentiment.py's model-based score already uses, and this
module writes to the exact same data/news/news_sentiment.csv schema, so
price_direction.py's lagged_sentiment_features() reads either one's
output identically without any changes.

HONESTY, worth stating plainly for a dissertation methodology section:
- WORD LIST PROVENANCE: the original plan was to use NTUSD (National
  Taiwan University Sentiment Dictionary, Wang & Ku, LREC 2016), a
  well-established, citable academic resource -- but this development
  sandbox's network access is blocked for bulk external data retrieval
  (the same restriction that's affected every other external data source
  in this project: yfinance, FinMind, TPEx), so the actual NTUSD file
  could not be downloaded and verified here. POSITIVE_WORDS/NEGATIVE_WORDS
  below are a SELF-CURATED list instead, written by hand for this project,
  drawing on common Taiwanese financial-news reporting vocabulary (the
  same style of term already seen in this project's own news.csv
  headlines and in news_sentiment.py's own worked example, "0050盤中飆新
  高"). This is NOT the real NTUSD, and should be described as a
  self-built lexicon in any methodology write-up, not attributed to NTU's
  published dictionary. If you obtain the real NTUSD file (or another
  validated Chinese sentiment lexicon) yourself, it's a straightforward
  drop-in: replace/extend POSITIVE_WORDS and NEGATIVE_WORDS below, or
  load an external word list file at runtime.
- CRUDER THAN A TRAINED MODEL: no real context understanding, no
  sarcasm/idiom handling, a word counted by this scorer might be used in
  a non-sentiment sense in a specific headline, and only one of several
  possible negation windows is implemented. This trades real accuracy for
  being transparent, fully auditable (every word that drove a score is
  visible in the code below), deterministic, and safe to run live in a
  resource-constrained deployment -- a defensible trade-off to state
  explicitly, not hide.
- SAME LOOK-AHEAD-LEAKAGE CAVEAT AS news_sentiment.py: this module only
  SCORES headlines; it is still price_direction.py's job (via
  lagged_sentiment_features()) to only use headlines strictly before the
  prediction date. Nothing here changes that contract.

Usage: score_headlines() is called from app.py's new sidebar button
(no network access needed -- it only reads the already-fetched
data/news/news.csv and writes data/news/news_sentiment.csv), or can be
run standalone with `python news_sentiment_lexicon.py`.
"""

import csv
import os
import re
from email.utils import parsedate_to_datetime

from config import DATA_DIR

NEWS_CSV = os.path.join(DATA_DIR, "news", "news.csv")
SENTIMENT_CSV = os.path.join(DATA_DIR, "news", "news_sentiment.csv")

# Same output schema as news_sentiment.py (the Hugging Face model's output)
# so price_direction.py's reader works unchanged regardless of which of
# the two scored a given headline.
FIELDNAMES = ["code", "published", "published_iso", "title", "label",
              "p_positive", "p_neutral", "p_negative", "score"]

# How many characters immediately before a sentiment-word match to check
# for a negation marker. Chinese negation almost always sits directly
# before the word it negates ("不漲" = "not rising"), so a short window is
# both sufficient and avoids accidentally flipping an unrelated earlier
# word in the same headline.
NEGATION_WINDOW = 3

NEGATION_WORDS = ["不", "沒", "無", "未", "非", "別", "莫", "勿", "並未", "並非", "難以", "毫無"]

# Self-curated financial-reporting vocabulary (see module docstring for
# why this isn't NTUSD). Deliberately includes both price-direction terms
# (most of what Google News financial headlines consist of) and general
# positive/negative business-news vocabulary (earnings, labor, risk).
POSITIVE_WORDS = [
    # price / momentum
    "飆漲", "飆股", "飆新高", "大漲", "強漲", "走揚", "走強", "攀升", "揚升", "上揚", "上漲",
    "創新高", "創高", "改寫新高", "歷史新高", "強彈", "反彈", "回升", "回穩", "止跌回升",
    "翻紅", "翻揚", "收紅", "收漲", "紅盤", "衝高", "衝上", "站穩", "突破", "挺進",
    "補漲", "填息", "完成填息", "蜜月行情", "多方", "多頭", "高檔", "搶進", "買盤湧入",
    # business / earnings
    "獲利成長", "營收成長", "營收創新高", "盈餘成長", "獲利亮眼", "表現亮眼", "優於預期",
    "超預期", "財報亮眼", "營運轉強", "訂單暢旺", "熱銷", "熱絡", "旺季效應", "擴大投資",
    "擴產", "展店", "展望樂觀", "看好", "看漲", "加碼", "調升目標價", "升評",
    # general sentiment
    "樂觀", "穩健", "強勁", "利多", "佳績", "榮景", "復甦", "成長動能",
]

NEGATIVE_WORDS = [
    # price / momentum
    "重挫", "大跌", "崩盤", "崩跌", "走弱", "下滑", "下挫", "下跌", "跌破", "破底",
    "探底", "創新低", "改寫新低", "歷史新低", "殺盤", "急殺", "跳水", "蒸發", "縮水",
    "翻黑", "收黑", "收跌", "黑盤", "套牢", "停損", "拋售", "賣壓", "賣壓沉重",
    "空方", "空頭", "低檔", "貼息", "熊市", "恐慌性拋售", "融資斷頭",
    # business / earnings
    "獲利衰退", "營收衰退", "虧損", "虧損擴大", "獲利不如預期", "不如預期", "低於預期", "財報不佳",
    "營運轉弱", "訂單萎縮", "需求疲弱", "淡季效應", "裁員", "減產", "停工", "關廠",
    "展望保守", "展望悲觀", "看壞", "看跌", "減碼", "調降目標價", "降評",
    # general sentiment / risk
    "悲觀", "疲弱", "疲軟", "利空", "示警", "警訊", "風暴", "危機", "動盪", "恐慌",
    "衰退", "違約", "倒閉", "破產", "地雷股",
]

# Longest-first so a longer phrase (e.g. "止跌回升") is tried before a
# shorter substring it contains ("回升"), avoiding double-counting the
# same span under two different words.
_POSITIVE_SORTED = sorted(set(POSITIVE_WORDS), key=len, reverse=True)
_NEGATIVE_SORTED = sorted(set(NEGATIVE_WORDS), key=len, reverse=True)


def _find_matches(text, words_sorted):
    """Returns a list of (start_index, word) for every non-overlapping
    occurrence of any word in words_sorted within text, scanning
    longest-word-first so a longer phrase claims its span before a
    shorter word that happens to be its substring gets a separate hit."""
    claimed = [False] * len(text)
    matches = []
    for word in words_sorted:
        start = 0
        while True:
            idx = text.find(word, start)
            if idx == -1:
                break
            span = range(idx, idx + len(word))
            if not any(claimed[i] for i in span):
                for i in span:
                    claimed[i] = True
                matches.append((idx, word))
            start = idx + 1
    return matches


def _is_negated(text, match_start):
    window_start = max(0, match_start - NEGATION_WINDOW)
    window = text[window_start:match_start]
    return any(neg in window for neg in NEGATION_WORDS)


def score_text(text):
    """Scores one piece of text. Returns a dict: pos_hits/neg_hits (after
    negation flipping), score (pos_hits - neg_hits) / (pos_hits +
    neg_hits), clipped to [-1, 1] and 0.0 if there are no hits at all,
    and matched_words (list of (word, polarity) actually counted, for
    spot-checking -- polarity is "positive"/"negative" AFTER any
    negation flip, so a negated positive word like "不樂觀" shows up here
    as ("樂觀", "negative")."""
    pos_hits = 0
    neg_hits = 0
    matched = []

    for idx, word in _find_matches(text, _POSITIVE_SORTED):
        negated = _is_negated(text, idx)
        if negated:
            neg_hits += 1
            matched.append((word, "negative"))
        else:
            pos_hits += 1
            matched.append((word, "positive"))

    for idx, word in _find_matches(text, _NEGATIVE_SORTED):
        negated = _is_negated(text, idx)
        if negated:
            pos_hits += 1
            matched.append((word, "positive"))
        else:
            neg_hits += 1
            matched.append((word, "negative"))

    total = pos_hits + neg_hits
    if total == 0:
        score = 0.0
    else:
        score = max(-1.0, min(1.0, (pos_hits - neg_hits) / total))

    return {"pos_hits": pos_hits, "neg_hits": neg_hits, "score": score, "matched_words": matched}


def _load_news_rows():
    if not os.path.exists(NEWS_CSV):
        raise FileNotFoundError(
            f"{NEWS_CSV} doesn't exist yet -- fetch news first (sidebar "
            f"\"抓取新聞\" button, or news_data.py)."
        )
    rows = []
    with open(NEWS_CSV, newline="", encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def _load_already_scored_links():
    if not os.path.exists(SENTIMENT_CSV):
        return set()
    with open(SENTIMENT_CSV, newline="", encoding="utf-8-sig") as f:
        # Same (code, title) proxy-key convention as news_sentiment.py --
        # see that module's _load_already_scored_links() for why.
        return {(r["code"], r["title"]) for r in csv.DictReader(f)}


def score_headlines(force=False):
    """Scores every headline in news.csv not already in
    news_sentiment.csv (skip-if-already-scored, same pattern as this
    project's other per-item steps) and appends the results -- or, if
    force=True, re-scores everything (useful right after an edit to
    POSITIVE_WORDS/NEGATIVE_WORDS, since the stored score won't update on
    its own otherwise). Returns the full list of scored rows (existing +
    newly scored). No network access of any kind -- purely local text
    processing, safe to call from the deployed app itself."""
    news_rows = _load_news_rows()
    already_scored = set() if force else _load_already_scored_links()

    existing_output_rows = []
    if not force and os.path.exists(SENTIMENT_CSV):
        with open(SENTIMENT_CSV, newline="", encoding="utf-8-sig") as f:
            existing_output_rows = list(csv.DictReader(f))

    to_score = [r for r in news_rows if (r["code"], r["title"]) not in already_scored]
    print(f"[news_sentiment_lexicon] {len(to_score)} new headline(s) to score, "
          f"{len(already_scored)} already scored and skipped")

    new_rows = []
    for row in to_score:
        result = score_text(row["title"])
        score = result["score"]
        # Heuristic, NOT a real probability (a lexicon count isn't a
        # calibrated classifier) -- kept only so the output schema matches
        # news_sentiment.py's model-based columns for anything that reads
        # them (e.g. a future UI table showing both methods side by side).
        p_positive = max(0.0, score)
        p_negative = max(0.0, -score)
        p_neutral = max(0.0, 1.0 - abs(score))
        if score > 0.15:
            label = "positive"
        elif score < -0.15:
            label = "negative"
        else:
            label = "neutral"

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
            "label": label,
            "p_positive": round(p_positive, 4),
            "p_neutral": round(p_neutral, 4),
            "p_negative": round(p_negative, 4),
            "score": round(score, 4),
        })

    all_rows = existing_output_rows + new_rows
    os.makedirs(os.path.dirname(SENTIMENT_CSV), exist_ok=True)
    with open(SENTIMENT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"  -> saved {len(all_rows)} scored headline(s) to {SENTIMENT_CSV}")

    if new_rows:
        n_with_hits = sum(1 for r in new_rows if r["score"] != 0.0)
        print(f"\n  {n_with_hits} of {len(new_rows)} new headline(s) matched at least one "
              f"lexicon word (the rest scored 0.0 -- no financial-sentiment word recognized).")
        print("  Spot-check a few scored headlines (inspect these before trusting it):")
        for r in new_rows[:8]:
            print(f"    [{r['label']:>8} score={r['score']:+.2f}] {r['title']}")

    return all_rows


def run(force=False):
    """Entry point matching this project's other modules' run() convention
    (see app.py's sidebar wiring). No network access, no sync_category --
    purely local recompute over already-fetched data/news/news.csv, same
    as predict_dividends.run()/ddm_valuation.run()."""
    score_headlines(force=force)


if __name__ == "__main__":
    run()
