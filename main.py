"""
Entry point: pulls all data sources for the watchlist defined in
config.py.

Usage:
    python main.py                  # fetch prices + dividends + news
    python main.py --prices         # only price history
    python main.py --dividends      # only dividend data
    python main.py --news           # only news headlines
    python main.py --market-value   # shares outstanding + market value (opt-in, see below)
    python main.py --institutional  # 三大法人 net buy/sell history (opt-in, see below)
(flags can be combined, e.g. `python main.py --prices --dividends`)

--market-value is NOT part of the default "run everything" pass yet --
it's new and hasn't been confirmed to work against live data the way the
other three have, and it depends on data/prices/<code>.csv already
existing, so run --prices at least once first. Run it explicitly
(`python main.py --market-value`) until you've checked its output looks
right, then fold it into your regular routine if it does.

--institutional is also opt-in, but for a different reason: its TWSE
report (T86) returns every listed stock for ONE day per call, so a
backfill costs roughly one request per trading day rather than one per
ticker (see institutional_data.py's docstring) -- folding it into the
default run would make every plain `python main.py` noticeably slower.
Run it explicitly, or use the separate button in the app sidebar.

Speed: whenever more than one of prices/dividends/news is being fetched,
they now run CONCURRENTLY (in separate threads) instead of one after
another. This is safe because each one talks to a different external
service -- price_data.py hits TWSE's legacy price report, dividend_data.py
hits yfinance/Yahoo, news_data.py hits Google News -- so running them at
the same time doesn't send requests any faster to any single one of them
(REQUEST_DELAY_SECONDS still throttles each script's own calls exactly as
before); it just overlaps the waiting time instead of adding it up.
Wall-clock time drops to roughly whichever one is slowest (usually price
history on a first run backfilling PRICE_HISTORY_MONTHS of data), instead
of the sum of all three. --market-value always runs AFTER the others
finish, never alongside them, since it reads whatever's currently in
data/prices/<code>.csv -- running it concurrently with --prices could pick
up yesterday's file for a ticker price_data.py hasn't gotten to yet in
that same run.

The one downside of running concurrently: console output from different
scripts can interleave (you might see a price_data line and a news_data
line next to each other). If you want clean, one-source-at-a-time output
instead -- e.g. while debugging a specific fetcher -- pass --sequential.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import price_data
import dividend_data
import news_data
import market_value_data
import institutional_data


def _run_concurrently(jobs):
    """jobs: list of (label, fn). Runs each fn() in its own thread and
    waits for all of them to finish before returning."""
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = {ex.submit(fn): label for label, fn in jobs}
        for future in as_completed(futures):
            label = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"\n[error] {label} raised an exception: {e}")


def main():
    parser = argparse.ArgumentParser(description="Fetch TWSE stock data for the watchlist.")
    parser.add_argument("--prices", action="store_true", help="fetch share price history")
    parser.add_argument("--dividends", action="store_true", help="fetch dividend distribution data")
    parser.add_argument("--news", action="store_true", help="fetch recent news headlines")
    parser.add_argument("--market-value", action="store_true",
                         help="fetch shares outstanding and compute daily market value (opt-in, see module docstring)")
    parser.add_argument("--institutional", action="store_true",
                         help="fetch 三大法人 (foreign/investment-trust/dealer) net buy-sell history (opt-in, see module docstring)")
    parser.add_argument("--sequential", action="store_true",
                         help="fetch prices/dividends/news one at a time instead of concurrently "
                              "(slower, but keeps console output from interleaving -- handy for debugging)")
    args = parser.parse_args()

    # If no specific flag is given, run the three established fetchers --
    # --market-value and --institutional are opt-in only (see docstring above).
    run_all = not (args.prices or args.dividends or args.news or args.market_value or args.institutional)

    jobs = []
    if run_all or args.prices:
        jobs.append(("Share prices", price_data.run))
    if run_all or args.dividends:
        jobs.append(("Dividends", dividend_data.run))
    if run_all or args.news:
        jobs.append(("News", news_data.run))

    if jobs:
        if args.sequential or len(jobs) == 1:
            for label, fn in jobs:
                print(f"\n=== {label} ===")
                fn()
        else:
            names = ", ".join(label for label, _ in jobs)
            print(f"\n=== Fetching concurrently: {names} ===")
            _run_concurrently(jobs)

    if args.market_value:
        # Deliberately sequential, after the block above -- see the
        # docstring's "Speed" section for why.
        print("\n=== Shares outstanding + market value ===")
        market_value_data.run()

    if args.institutional:
        print("\n=== 三大法人買賣超 (institutional net buy/sell) ===")
        institutional_data.run()

    print("\nDone. Data saved under ./data/")


if __name__ == "__main__":
    main()
