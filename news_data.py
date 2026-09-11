"""
Fetch recent news headlines for each company in the watchlist via Google
News' public RSS search feed (no API key required) and save them to
data/news/news.csv.

Skip-if-already-fetched (2026-09-11, by explicit request): a company's
news is fetched from Google News AT MOST ONCE, ever -- once a ticker has
been attempted, run() leaves its saved headlines untouched on every
later call and only fetches tickers new to the watchlist. This is a
deliberate trade-off, not an accident: Google News RSS has no "just tell
me what's new since X" query, only "recent items right now", so unlike
price_data.py/institutional_data.py's genuine date-range incrementality,
"skip already-covered data" here means skip the ticker entirely. The
real cost -- and it's a real one for something called "news" -- is that
this ticker will never get fresher headlines again on its own; pass
force=True (or check the "強制重新抓取" box in the app sidebar) to
re-fetch specific or all tickers when you want current headlines.

"Already fetched" is tracked in data/news/_fetched_codes.json (a plain
list of ticker codes), not inferred from whether a ticker has any rows
in news.csv -- a company with genuinely zero matching headlines would
otherwise be re-fetched forever, since it never accumulates a row to
mark it "done".
"""

import csv
import json
import os
import time
import xml.etree.ElementTree as ET

import requests

from config import WATCHLIST, NEWS_ITEMS_PER_COMPANY, REQUEST_DELAY_SECONDS, DATA_DIR

RSS_URL = "https://news.google.com/rss/search"
FETCHED_CODES_FILE = os.path.join(DATA_DIR, "news", "_fetched_codes.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _fetch_rss(query):
    params = {"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
    try:
        resp = requests.get(RSS_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as e:
        print(f"  [warn] news fetch failed for query={query!r}: {e}")
        return None


def fetch_company_news(code, name, name_en):
    # `requests` percent-encodes this for us via `params=` in _fetch_rss --
    # do NOT also urllib.parse.quote() it here, that double-encodes the
    # string (e.g. '"' becomes the literal characters %22 in the query
    # Google receives) and silently returns zero results for everything.
    query = f'"{name}" OR {code}'
    root = _fetch_rss(query)
    rows = []
    if root is not None:
        items = root.findall("./channel/item")[:NEWS_ITEMS_PER_COMPANY]
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            rows.append({
                "code": code,
                "company": name,
                "company_en": name_en,
                "published": pub_date,
                "source": source,
                "title": title,
                "link": link,
            })
    print(f"[news] {code} {name}: {len(rows)} headlines")
    time.sleep(REQUEST_DELAY_SECONDS)
    return rows


FIELDNAMES = ["code", "company", "company_en", "published", "source", "title", "link"]


def _load_existing_rows():
    """Return {code: [row dict, ...]} from the existing news.csv, or {}
    if it doesn't exist yet."""
    out_path = os.path.join(DATA_DIR, "news", "news.csv")
    by_code = {}
    if os.path.exists(out_path):
        with open(out_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                by_code.setdefault(row["code"], []).append(row)
    return by_code


def _load_fetched_codes():
    if os.path.exists(FETCHED_CODES_FILE):
        try:
            with open(FETCHED_CODES_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_fetched_codes(codes):
    os.makedirs(os.path.dirname(FETCHED_CODES_FILE), exist_ok=True)
    with open(FETCHED_CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(codes), f)


def run(force=False, codes_to_force=None):
    """force=True re-fetches EVERY watchlist ticker's news regardless of
    what's already on file (the old, pre-2026-09-11 behavior).
    codes_to_force (an iterable of ticker codes) re-fetches only those
    specific tickers. force=True takes priority over codes_to_force if
    both are given."""
    existing = _load_existing_rows()
    fetched_codes = _load_fetched_codes()
    force_codes = set(codes_to_force or ())

    all_rows = []
    n_skipped = 0
    n_fetched = 0
    for stock in WATCHLIST:
        code = stock["code"]
        already_done = code in fetched_codes and not force and code not in force_codes
        if already_done:
            all_rows.extend(existing.get(code, []))
            n_skipped += 1
            continue
        all_rows.extend(fetch_company_news(code, stock["name"], stock["name_en"]))
        fetched_codes.add(code)
        n_fetched += 1

    out_path = os.path.join(DATA_DIR, "news", "news.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    _save_fetched_codes(fetched_codes)

    print(f"  -> saved {len(all_rows)} total headlines to {out_path} "
          f"({n_fetched} ticker(s) fetched, {n_skipped} already on file and skipped)")


if __name__ == "__main__":
    run()
    # Push to GitHub-backed storage if [github_data] is configured (see
    # market_data_sync.py) -- no-op otherwise. Added 2026-09-11, see
    # price_data.py's __main__ block for why.
    import market_data_sync
    import github_json_store
    try:
        market_data_sync.push_category("news")
    except github_json_store.GitHubStorageError as e:
        print(f"[warn] fetched locally, but GitHub backup failed: {e}")
