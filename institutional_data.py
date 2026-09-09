"""
Fetch daily net buy/sell volume (買賣超) by the three major institutional
investor groups -- 外資 (foreign investors), 投信 (investment trusts), and
自營商 (securities dealers) -- for each ticker in the watchlist, and keep a
running CSV per ticker under data/institutional/<code>.csv.

Backfill window: deliberately kept IDENTICAL to price_data.py's, i.e. the
same PRICE_HISTORY_MONTHS calendar months back from today (per explicit
user request, 2026-09-09, to keep the two histories aligned) -- see
_history_start_date() below, which reconstructs the exact same "N months
back" starting point price_data.py's _month_starts() uses, then this
walks forward one calendar day at a time (skipping weekends) rather than
one calendar month at a time.

**This makes a first-time full backfill expensive.** Data source: TWSE's
T86 report ("三大法人買賣超日報") is shaped differently from
price_data.py's STOCK_DAY report -- T86 returns EVERY listed stock for
ONE calendar day per call, rather than one stock for one month. That
means a full historical backfill costs roughly one network request PER
TRADING DAY (not per ticker, and not per month) -- at the default
PRICE_HISTORY_MONTHS=36 (3 years), that's on the order of 700-780 trading
days, i.e. 700-780 requests. At REQUEST_DELAY_SECONDS=1.5 that's a floor
of roughly 20-30 minutes for the very first run (network latency and any
retries add more on top). Every run AFTER the first is cheap again --
same incremental design as price_data.py: a calendar date already saved
for every watchlist ticker is skipped without a network call, so daily
re-runs only fetch the handful of new trading days since last time.

Because of that first-run cost, running this via `python
institutional_data.py` directly (so you can watch its progress in a
terminal) is more comfortable for the first backfill than clicking the
button in the app and waiting on a spinner -- though the app's "🔄 一鍵
抓取全部資料" button does include it (fetched last, sequentially, see
app.py), and there's also a standalone "抓取三大法人買賣超" button.

Field names are looked up BY NAME from the "fields" array TWSE returns in
each response, never by hard-coded position -- TWSE has changed column
order/wording in this report before. Verified live against the real TWSE
endpoint (2026-09-09).

    外資 (foreign investors) net = "外陸資買賣超股數(不含外資自營商)"
                                  + "外資自營商買賣超股數"
        (TWSE does not publish a single combined "外資" column; if a
        future response ever does include one under a recognizable name,
        _extract_foreign_net() below will use it directly instead.)
    投信 (investment trust) net  = "投信買賣超股數"                (official, used as-is)
    自營商 (dealer) net          = "自營商買賣超股數"              (official aggregate --
        deliberately NOT re-derived by summing the "自行買賣"/"避險"
        sub-columns; TWSE's own aggregate is the one to trust)
    三大法人合計 (all three combined), when present, is saved too under
    "ThreeInstitutionsNet" for convenience, straight from
    "三大法人買賣超股數" -- also not re-derived.

Every value is a NET number of shares for that day (positive = net
bought, negative = net sold), not a running total.
"""

import csv
import os
from datetime import date, timedelta

from config import WATCHLIST, DATA_DIR, PRICE_HISTORY_MONTHS
from twse_client import get_json

T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

FIELDNAMES = ["Date", "ForeignNet", "InvestmentTrustNet", "DealerNet", "ThreeInstitutionsNet"]

# Field-name candidates within the T86 response, in priority order.
CODE_FIELD_CANDIDATES = ["證券代號"]
FOREIGN_COMBINED_CANDIDATES = ["外資買賣超股數", "外資及陸資買賣超股數"]
FOREIGN_EXCL_DEALER_CANDIDATES = ["外陸資買賣超股數(不含外資自營商)", "外資買賣超股數(不含外資自營商)"]
FOREIGN_DEALER_CANDIDATES = ["外資自營商買賣超股數"]
TRUST_CANDIDATES = ["投信買賣超股數"]
DEALER_CANDIDATES = ["自營商買賣超股數"]
THREE_TOTAL_CANDIDATES = ["三大法人買賣超股數"]


def _num(s):
    """Parse a TWSE numeric field ('1,234', '-987', '--', '') into an int/float or None."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "--", "X"):
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def _first_present(fields, candidates):
    """Return the first name in `candidates` that appears in `fields`, or None."""
    field_set = set(fields)
    for name in candidates:
        if name in field_set:
            return name
    return None


def _extract_foreign_net(row, idx):
    """外資 net buy/sell for one row. Prefers a single combined column if
    TWSE ever publishes one; otherwise sums the two documented sub-columns."""
    if "combined" in idx:
        return _num(row[idx["combined"]])
    excl = _num(row[idx["excl_dealer"]]) if "excl_dealer" in idx else None
    dealer_arm = _num(row[idx["dealer_arm"]]) if "dealer_arm" in idx else None
    if excl is None and dealer_arm is None:
        return None
    return (excl or 0) + (dealer_arm or 0)


def _csv_path(code):
    return os.path.join(DATA_DIR, "institutional", f"{code}.csv")


def _load_existing(code):
    """Return {iso_date: row_dict} of what's already saved for this ticker."""
    path = _csv_path(code)
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows[row["Date"]] = row
    return rows


def _write_csv(code, rows_by_date):
    path = _csv_path(code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for iso in sorted(rows_by_date.keys()):
            writer.writerow(rows_by_date[iso])


def _history_start_date():
    """The first day of the same PRICE_HISTORY_MONTHS-months-back window
    price_data.py's _month_starts() computes for share prices -- e.g. with
    PRICE_HISTORY_MONTHS=36, if today is in September, this walks back 35
    more months and returns October 1st of two years prior. Reconstructed
    here (rather than imported) since price_data.py's version returns a
    list of (year, month) tuples, not a single start date -- same month
    arithmetic, just taking the oldest entry as a real date."""
    today = date.today()
    y, m = today.year, today.month
    for _ in range(PRICE_HISTORY_MONTHS - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return date(y, m, 1)


def _recent_calendar_dates():
    """Yield ISO date strings from _history_start_date() through today
    (oldest first), skipping Saturdays/Sundays -- T86 has nothing on
    those, so no point spending a request finding that out. Public
    holidays still cost one wasted request each (same trade-off
    price_data.py accepts for unlisted months)."""
    d = _history_start_date()
    today = date.today()
    out = []
    while d <= today:
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def run():
    os.makedirs(os.path.join(DATA_DIR, "institutional"), exist_ok=True)
    codes = [s["code"] for s in WATCHLIST]
    if not codes:
        print("[institutional] watchlist is empty -- nothing to do")
        return

    existing = {code: _load_existing(code) for code in codes}
    watchlist_codes = {c.strip() for c in codes}
    target_dates = _recent_calendar_dates()

    dates_needing_a_request = sum(
        1 for iso_date in target_dates if not all(iso_date in existing[code] for code in codes)
    )
    if dates_needing_a_request > 30:
        # A first-time full backfill at the default PRICE_HISTORY_MONTHS
        # (aligned with price_data.py's window, per user request) is on
        # the order of 700+ requests -- tell the user up front rather
        # than leaving them wondering if the button is stuck.
        est_minutes = dates_needing_a_request * 1.5 / 60
        print(f"[institutional] {dates_needing_a_request} day(s) need fetching -- "
              f"at ~1.5s/request that's at least ~{est_minutes:.0f} minute(s), likely more "
              f"with network latency/retries. This is a one-time cost; re-runs only fetch new days.")

    fetched_days = 0       # got a stat=="OK" response with at least one watchlist row matched
    skipped_days = 0       # every watchlist ticker already had this date on file, no request made
    no_trading_days = 0    # request succeeded but TWSE says stat != "OK" (weekend/holiday/not published yet)
    request_failed_days = 0  # get_json gave up after retries (network issue / TWSE blocking us)
    unmatched_days = 0     # stat == "OK" but none of OUR watchlist codes appeared in that day's data
    requests_made = 0

    for iso_date in target_dates:
        if all(iso_date in existing[code] for code in codes):
            skipped_days += 1
            continue  # every watchlist ticker already has this day on file

        date_param = iso_date.replace("-", "")
        payload = get_json(T86_URL, params={"date": date_param, "selectType": "ALL", "response": "json"})
        requests_made += 1
        if requests_made % 20 == 0:
            # A full backfill can now run for 20-30+ minutes (see the
            # module docstring) -- checkpoint progress to disk periodically
            # so an interrupted run (closed browser tab, killed terminal)
            # doesn't lose everything fetched so far, and print a progress
            # line since otherwise there'd be long silent stretches.
            for code in codes:
                _write_csv(code, existing[code])
            print(f"  ... {requests_made}/{dates_needing_a_request} request(s) made so far "
                  f"({fetched_days} fetched, {request_failed_days} failed, {no_trading_days} non-trading, "
                  f"{unmatched_days} unmatched) -- progress saved to disk")
        if payload is None:
            request_failed_days += 1
            continue  # get_json already printed a warning per retry; move on to the next date
        if payload.get("stat") != "OK":
            no_trading_days += 1
            continue  # weekend, holiday, or no data published yet for today

        fields = payload.get("fields") or []
        code_field = _first_present(fields, CODE_FIELD_CANDIDATES)
        if code_field is None:
            print(f"  [warn] {iso_date}: couldn't find the stock-code column in T86 response, skipping")
            continue

        idx = {"code": fields.index(code_field)}
        combined_field = _first_present(fields, FOREIGN_COMBINED_CANDIDATES)
        excl_field = _first_present(fields, FOREIGN_EXCL_DEALER_CANDIDATES)
        dealer_arm_field = _first_present(fields, FOREIGN_DEALER_CANDIDATES)
        trust_field = _first_present(fields, TRUST_CANDIDATES)
        dealer_field = _first_present(fields, DEALER_CANDIDATES)
        three_field = _first_present(fields, THREE_TOTAL_CANDIDATES)

        if combined_field:
            idx["combined"] = fields.index(combined_field)
        if excl_field:
            idx["excl_dealer"] = fields.index(excl_field)
        if dealer_arm_field:
            idx["dealer_arm"] = fields.index(dealer_arm_field)

        matched_this_day = 0
        for row in payload.get("data", []):
            code = str(row[idx["code"]]).strip()
            if code not in watchlist_codes:
                continue
            matched_this_day += 1
            existing[code][iso_date] = {
                "Date": iso_date,
                "ForeignNet": _extract_foreign_net(row, idx),
                "InvestmentTrustNet": _num(row[fields.index(trust_field)]) if trust_field else None,
                "DealerNet": _num(row[fields.index(dealer_field)]) if dealer_field else None,
                "ThreeInstitutionsNet": _num(row[fields.index(three_field)]) if three_field else None,
            }
        if matched_this_day:
            fetched_days += 1
        else:
            unmatched_days += 1
            print(f"  [warn] {iso_date}: TWSE returned {len(payload.get('data', []))} stocks "
                  f"but none matched our watchlist codes {sorted(watchlist_codes)}")

    for code in codes:
        _write_csv(code, existing[code])

    print(f"[institutional] {fetched_days} day(s) fetched, {skipped_days} already on file for every ticker, "
          f"{no_trading_days} non-trading day(s), {request_failed_days} request(s) failed, "
          f"{unmatched_days} day(s) with no watchlist match")
    for code in codes:
        print(f"  -> {code}: {len(existing[code])} days saved")

    total_rows_saved = sum(len(v) for v in existing.values())
    if total_rows_saved == 0 and target_dates:
        if request_failed_days > 0:
            raise RuntimeError(
                f"TWSE 完全沒有回應任何一天的三大法人資料（{request_failed_days} 次請求全部失敗）-- "
                "很可能是暫時被 TWSE 限制請求頻率或網路不通，請稍等幾分鐘後再試一次。"
            )
        if unmatched_days > 0:
            raise RuntimeError(
                "TWSE 有回應資料，但每一天的資料裡都找不到你追蹤清單中的股票代號 -- "
                "請確認 config.py 的 WATCHLIST／data/watchlist.json 裡的代號跟 TWSE 網站上的"
                "代號完全一致（例如是否多了空白、大小寫，或代號本身在 T86 這份報表中沒有揭露）。"
                "上面的 [warn] 訊息有列出 TWSE 當天實際回傳了哪些代號，可以比對看看。"
            )
        raise RuntimeError(
            "沒有抓到任何三大法人資料，但也沒有偵測到請求失敗或代號不符 -- 這種情況不預期會發生，"
            "請把這顆按鈕下方「執行紀錄」完整內容回報，方便進一步排查。"
        )


if __name__ == "__main__":
    run()
