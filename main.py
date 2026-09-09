"""
Entry point: pulls all data sources for the watchlist defined in
config.py.

Usage:
    python main.py                # fetch prices + dividends + news
    python main.py --prices       # only price history
    python main.py --dividends    # only dividend data
    python main.py --news         # only news headlines
    python main.py --market-value # shares outstanding + market value (opt-in, see below)
(flags can be combined, e.g. `python main.py --prices --dividends`)

--market-value is NOT part of the default "run everything" pass yet --
it's new and hasn't been confirmed to work against live data the way the
other three have, and it depends on data/prices/<code>.csv already
existing, so run --prices at least once first. Run it explicitly
(`python main.py --market-value`) until you've checked its output looks
right, then fold it into your regular routine if it does.
"""

import argparse

import price_data
import dividend_data
import news_data
import market_value_data


def main():
    parser = argparse.ArgumentParser(description="Fetch TWSE stock data for the watchlist.")
    parser.add_argument("--prices", action="store_true", help="fetch share price history")
    parser.add_argument("--dividends", action="store_true", help="fetch dividend distribution data")
    parser.add_argument("--news", action="store_true", help="fetch recent news headlines")
    parser.add_argument("--market-value", action="store_true",
                         help="fetch shares outstanding and compute daily market value (opt-in, see module docstring)")
    args = parser.parse_args()

    # If no specific flag is given, run the three established fetchers --
    # --market-value is opt-in only (see docstring above) until confirmed.
    run_all = not (args.prices or args.dividends or args.news or args.market_value)

    if run_all or args.prices:
        print("\n=== Share prices ===")
        price_data.run()

    if run_all or args.dividends:
        print("\n=== Dividends ===")
        dividend_data.run()

    if run_all or args.news:
        print("\n=== News ===")
        news_data.run()

    if args.market_value:
        print("\n=== Shares outstanding + market value ===")
        market_value_data.run()

    print("\nDone. Data saved under ./data/")


if __name__ == "__main__":
    main()
