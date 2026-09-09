"""
Fetch daily net buy/sell volume (買賣超) by the three major institutional
investor groups -- 外資 (foreign investors), 投信 (investment trusts), and
自營商 (securities dealers) -- for each ticker in the watchlist, and keep a
running CSV per ticker under data/institutional/<code>.csv.

Data source: TWSE's T86 report ("三大法人買賣超日報"), which is different
in shape from price_data.py's STOCK_DAY report -- T86 returns EVERY listed
stock for ONE calendar day per call, rather than one stock for one month.
That means a full historical backfill costs roughly one network request
PER TRADING DAY (not per ticker), which is far more expensive than
price_data.py's per-ticker-per-month calls. For that reason this uses its
own, much shorter backfill window (config.INSTITUTIONAL_HISTORY_DAYS,
default well under PRICE_HISTORY_MONTHS in trading-day terms) and is
wired to its own sidebar button in app.py rather than being folded into
"一鍵抓取全部資料" (which would otherwise become drastically slower).

Field names are looked up BY NAME from the "fields" array TWSE returns in
each response, never by hard-coded position -- TWSE has changed column
order/wording in this report before.

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

Re-running this script is cheap in the same spirit as price_data.py: a
calendar date already saved for every watchlist ticker is skipped without
a network call. Weekends/holidays cost one wasted request each (TWSE
returns stat != "OK" for those, same as price_data.py's STOCK_DAY on an
unlisted month) -- acceptable at this data volume.
"""

import csv
import os
from datetime import date, timedelta

from config import WATCHLIST, DATA_DIR, INSTITUTIONAL_HISTORY_DAYS
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


def _recent_calendar_dates(n_days):
    """Yield ISO date strings for the last n_days calendar days (oldest
    first), skipping Saturdays/Sundays -- T86 has nothing on those, so no
    point spending a request finding that out. Public holidays still cost
    one wasted request each (same trade-off price_data.py accepts for
    unlisted months)."""
    today = date.today()
    out = []
    for delta in range(n_days, -1, -1):
        d = today - timedelta(days=delta)
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            out.append(d.isoformat())
    return out


def run():
    os.makedirs(os.path.join(DATA_DIR, "institutional"), exist_ok=True)
    codes = [s["code"] for s in WATCHLIST]
    if not codes:
        print("[institutional] watchlist is empty -- nothing to do")
        return

    existing = {code: _load_existing(code) for code in codes}
    watchlist_codes = {c.strip() for c in codes}
    target_dates = _recent_calendar_dates(INSTITUTIONAL_HISTORY_DAYS)

    fetched_days = 0       # got a stat=="OK" response with at least one watchlist row matched
    skipped_days = 0       # every watchlist ticker already had this date on file, no request made
    no_trading_days = 0    # request succeeded but TWSE says stat != "OK" (weekend/holiday/not published yet)
    request_failed_days = 0  # get_json gave up after retries (network issue / TWSE blocking us)
    unmatched_days = 0     # stat == "OK" but none of OUR watchlist codes appeared in that day's data

    for iso_date in target_dates:
        if all(iso_date in existing[code] for code in codes):
            skipped_days += 1
            continue  # every watchlist ticker already has this day on file

        date_param = iso_date.replace("-", "")
        payload = get_json(T86_URL, params={"date": date_param, "selectType": "ALL", "response": "json"})
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
