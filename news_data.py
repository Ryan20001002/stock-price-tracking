"""
Fetch recent news headlines for each company in the watchlist via Google
News' public RSS search feed (no API key required) and save them to
data/news/news.csv.

Each run overwrites the CSV with a fresh pull of the latest headlines
(Google News RSS only ever returns "recent" items, so there's nothing
meaningful to accumulate incrementally without a paid news API).
"""

import csv
import os
import time
import xml.etree.ElementTree as ET

import requests

from config import WATCHLIST, NEWS_ITEMS_PER_COMPANY, REQUEST_DELAY_SECONDS, DATA_DIR

RSS_URL = "https://news.google.com/rss/search"
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


def run():
    all_rows = []
    for stock in WATCHLIST:
        all_rows.extend(fetch_company_news(stock["code"], stock["name"], stock["name_en"]))

    out_path = os.path.join(DATA_DIR, "news", "news.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = ["code", "company", "company_en", "published", "source", "title", "link"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  -> saved {len(all_rows)} total headlines to {out_path}")


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
