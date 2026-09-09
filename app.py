"""
Streamlit dashboard for the Taiwan stock tracker.

This file adds NO new data-fetching or calculation logic of its own --
it's a thin webpage wrapped around the scripts that already exist:

  1. It reads whatever's already sitting in data/*.csv and displays it as
     tables and charts.
  2. Its sidebar buttons call each script's existing run() function --
     the exact same code `python price_data.py` or `python
     ddm_valuation.py` runs from PowerShell -- so refreshing from the
     browser does exactly what refreshing from the command line does,
     just with a click instead of a command.

That split matters: if a bug ever shows up, it's in the one script that
also runs standalone (easy to test with `python thatscript.py`), never in
some separate copy of the logic living only inside this file.

The on-screen text (titles, buttons, captions, table headers) is in
Mandarin -- this is a UI-only translation layer. Every underlying script,
CSV column name, and file name stays in English, since other scripts
depend on those exact names.

Usage:
    streamlit run app.py
Run this from the project folder (the same place you already run
`python main.py` from) -- it reads/writes the same relative data/ paths
everything else does. If data/ is empty (a fresh clone), every tab will
just say so and point you at the matching sidebar button.
"""

import contextlib
import io
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

import config
import price_data
import dividend_data
import news_data
import market_value_data
import predict_dividends
import ddm_valuation

st.set_page_config(page_title="台灣股票追蹤器", layout="wide")


def run_and_log(label, fn):
    """Runs one of the existing scripts' run() functions, capturing its
    print() output (which would otherwise vanish into the terminal that
    launched streamlit) so it shows up in the browser instead. `label` is
    the Mandarin display name shown in the spinner/expander -- it usually
    also names the underlying .py file so it's easy to cross-reference
    with the README/command line."""
    buf = io.StringIO()
    try:
        with st.spinner(f"正在執行「{label}」-- 這會即時連線 TWSE/yfinance，且刻意放慢速度，可能需要一點時間..."):
            with contextlib.redirect_stdout(buf):
                fn()
        st.success(f"「{label}」執行完成。")
    except Exception as e:
        st.error(f"「{label}」執行失敗：{e}")
    log = buf.getvalue()
    if log:
        with st.expander(f"「{label}」執行紀錄", expanded=False):
            st.code(log)


class _ThreadCapturingStdout:
    """Lets several run() functions run concurrently in different threads
    while still capturing each one's print() output separately.
    contextlib.redirect_stdout alone can't do this -- it swaps out
    sys.stdout globally, so overlapping redirections from different
    threads would stomp on each other and mix their output together. This
    object replaces sys.stdout exactly once; each thread that calls
    .capture(buf) then gets its OWN writes routed to its own buffer, while
    every other thread (and the main thread, until it captures too) keeps
    writing straight through to the real stdout."""

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._local = threading.local()

    def write(self, s):
        (getattr(self._local, "buf", None) or self._real).write(s)

    def flush(self):
        (getattr(self._local, "buf", None) or self._real).flush()

    def isatty(self):
        return False

    def capture(self, buf):
        self._local.buf = buf

    def release(self):
        self._local.buf = None


if not isinstance(sys.stdout, _ThreadCapturingStdout):
    sys.stdout = _ThreadCapturingStdout(sys.stdout)


def _run_capturing(fn):
    buf = io.StringIO()
    sys.stdout.capture(buf)
    try:
        fn()
        return buf.getvalue(), None
    except Exception as e:
        return buf.getvalue(), str(e)
    finally:
        sys.stdout.release()


def run_parallel_and_log(jobs):
    """jobs: list of (label, fn). Runs every fn() in its own thread AT THE
    SAME TIME, then shows each one's result once all of them are done.
    Safe to run together because price_data.py, dividend_data.py, and
    news_data.py each talk to a DIFFERENT external service (TWSE's legacy
    price report, yfinance/Yahoo, Google News) -- running them concurrently
    doesn't send requests any faster to any single one of them
    (REQUEST_DELAY_SECONDS still throttles each script's own calls exactly
    as before), it just overlaps the waiting time instead of adding it up.
    Wall-clock time drops to roughly whichever one is slowest -- usually
    price history on a first run -- instead of the sum of all of them."""
    names = "、".join(label for label, _ in jobs)
    with st.spinner(f"正在同時抓取「{names}」-- 這樣比一個一個抓還快，仍需要一點時間..."):
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futures = {ex.submit(_run_capturing, fn): label for label, fn in jobs}
            results = {futures[f]: f.result() for f in as_completed(futures)}
    for label, _ in jobs:
        log, error = results[label]
        if error:
            st.error(f"「{label}」執行失敗：{error}")
        else:
            st.success(f"「{label}」執行完成。")
        if log:
            with st.expander(f"「{label}」執行紀錄", expanded=False):
                st.code(log)


def load_csv(path):
    if not os.path.exists(path):
        return None
    # dtype={"code": str}: a ticker code like "0050" or "00878" is all
    # digits, so pandas would otherwise infer it as an integer and silently
    # drop the leading zeros (0050 -> 50, 00878 -> 878) -- forcing it to
    # stay a string keeps it exactly as printed everywhere else (the
    # sidebar, the CSV files themselves, the file names). Harmless to pass
    # for CSVs that don't have a "code" column at all (e.g. the per-ticker
    # price/market-value files) -- pandas just ignores the unused key.
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str})
    return df if not df.empty else None


def watchlist_name(code):
    return next((s["name"] for s in config.WATCHLIST if s["code"] == code), code)


# --- Column-name translations for on-screen tables --------------------------
# The underlying CSVs keep their English column names -- every script that
# reads them (predict_dividends.py, ddm_valuation.py, etc.) depends on those
# exact names, so nothing here changes them. These mappings only control
# what's shown on screen in this file.

PRICE_COLUMNS_ZH = {
    "Date": "日期", "Open": "開盤價", "High": "最高價", "Low": "最低價",
    "Close": "收盤價", "Change": "漲跌", "Volume": "成交量",
    "TradeValue": "成交金額", "Transactions": "成交筆數",
}

DIVIDEND_COLUMNS_ZH = {
    "code": "代號", "name": "中文名稱", "name_en": "英文名稱", "symbol": "代碼",
    "ex_dividend_date": "除息日", "dividend_per_share": "每股股利",
}

DIVIDEND_PREDICTION_COLUMNS_ZH = {
    "code": "代號", "name": "中文名稱", "name_en": "英文名稱",
    "window_start": "統計起始日", "window_end": "統計結束日",
    "method_a_n_payments": "方法A_配息次數",
    "method_a_geometric_mean_growth": "方法A_幾何平均成長率",
    "method_a_predicted_next_payment": "方法A_預測下次配息",
    "method_a_predicted_next_1yr_total": "方法A_預測未來1年總配息",
    "method_b_n_payments": "方法B_配息次數",
    "method_b_mean_yield": "方法B_平均殖利率",
    "method_b_latest_price": "方法B_最新股價",
    "method_b_predicted_next_payment": "方法B_預測下次配息",
}

MARKET_VALUE_COLUMNS_ZH = {
    "Date": "日期", "Close": "收盤價", "SharesOutstanding": "流通單位數",
    "MarketValue": "市值（新台幣）",
}

# DDM valuation: reordered so the model-estimated prices sit right next to
# the actual latest price, for an easy side-by-side comparison -- inputs
# and diagnostics (beta, r, phi, sample sizes) follow after.
DDM_COLUMN_ORDER = [
    "code", "name", "name_en",
    "latest_price", "latest_price_date",
    "poly_pv_intrinsic_value", "poly_pct_diff_vs_price",
    "mr_pv_intrinsic_value", "mr_pct_diff_vs_price",
    "beta", "discount_rate_r", "mr_phi",
    "n_overlapping_days", "n_complete_years_on_file",
    "n_years_used_for_growth_fit", "anchor_d0_trailing_12mo", "horizon_years",
]
DDM_COLUMNS_ZH = {
    "code": "代號", "name": "中文名稱", "name_en": "英文名稱",
    "latest_price": "最新股價", "latest_price_date": "股價日期",
    "poly_pv_intrinsic_value": "估值_多項式模型",
    "poly_pct_diff_vs_price": "與市價差異%_多項式模型",
    "mr_pv_intrinsic_value": "估值_均值回歸模型",
    "mr_pct_diff_vs_price": "與市價差異%_均值回歸模型",
    "beta": "貝他值(Beta)", "discount_rate_r": "折現率(r)",
    "mr_phi": "均值回歸係數(phi)",
    "n_overlapping_days": "重疊交易日數",
    "n_complete_years_on_file": "完整年度資料數",
    "n_years_used_for_growth_fit": "成長率估計年數",
    "anchor_d0_trailing_12mo": "近12個月股利基準值",
    "horizon_years": "折現年限(年)",
}


def display_table(df, column_order=None, labels=None):
    """Returns a copy of df ready for on-screen display: columns reordered
    (if column_order is given -- present columns first in that order, any
    leftovers appended after) and renamed to Mandarin (if labels is given).
    Only affects what's shown here -- the DataFrame passed in, and the CSV
    it came from, are untouched."""
    out = df
    if column_order:
        ordered = [c for c in column_order if c in out.columns]
        leftover = [c for c in out.columns if c not in ordered]
        out = out[ordered + leftover]
    if labels:
        out = out.rename(columns=labels)
    return out


# --- Sidebar: manage watchlist ------------------------------------------

st.sidebar.title("追蹤清單")
st.sidebar.caption(
    "在這裡新增或移除股票代號 -- 這就是所有程式共用的 WATCHLIST，下面的抓取按鈕"
    "與每個分頁顯示的內容都會跟著改變。變更會立即儲存（存到 data/watchlist.json），"
    "下次打開這個頁面時也會保留。剛新增的股票代號會先顯示「尚無資料」，直到你按下"
    "對應的抓取按鈕。"
)

for stock in list(config.WATCHLIST):
    col_label, col_remove = st.sidebar.columns([4, 1])
    col_label.write(f"{stock['code']} · {stock['name']}")
    if col_remove.button("✕", key=f"remove_{stock['code']}", help=f"移除 {stock['code']}"):
        if len(config.WATCHLIST) == 1:
            st.sidebar.error("無法移除最後一檔股票，請先新增其他股票代號。")
        else:
            config.save_watchlist([s for s in config.WATCHLIST if s["code"] != stock["code"]])
            st.rerun()

with st.sidebar.form("add_ticker_form", clear_on_submit=True):
    st.caption("新增股票代號（台股代碼，例如 2330 代表台積電）")
    new_code = st.text_input("代號")
    new_name = st.text_input("中文名稱（選填）")
    new_name_en = st.text_input("英文名稱（選填）")
    if st.form_submit_button("新增"):
        code = new_code.strip()
        if not code:
            st.sidebar.error("請先輸入股票代號。")
        elif any(s["code"] == code for s in config.WATCHLIST):
            st.sidebar.warning(f"{code} 已經在追蹤清單中。")
        else:
            new_entry = {
                "code": code,
                "name": new_name.strip() or code,
                "name_en": new_name_en.strip() or code,
            }
            config.save_watchlist(list(config.WATCHLIST) + [new_entry])
            st.rerun()

st.sidebar.divider()

# --- Sidebar: refresh controls ----------------------------------------------

st.sidebar.title("更新資料")
st.sidebar.caption(
    "以下每個按鈕執行的程式碼，跟在 PowerShell 執行對應指令（例如 "
    "`python price_data.py`）完全相同。這會即時連線 TWSE/yfinance，並刻意放慢"
    "速度 -- 詳見 config.py 的 REQUEST_DELAY_SECONDS -- 如果看起來很慢，請不要"
    "重複點擊。"
)

st.sidebar.caption(
    "一次抓取全部資料（股價／股利／新聞會同時進行，比一個一個點快很多；"
    "流通股數／市值需要用到當天的股價，所以最後才抓）："
)
if st.sidebar.button("🔄 一鍵抓取全部資料", use_container_width=True, type="primary"):
    run_parallel_and_log([
        ("抓取股價 (price_data.py)", price_data.run),
        ("抓取股利 (dividend_data.py)", dividend_data.run),
        ("抓取新聞 (news_data.py)", news_data.run),
    ])
    run_and_log("抓取流通股數／市值 (market_value_data.py)", market_value_data.run)
    st.sidebar.success("全部資料抓取完成！")

st.sidebar.caption("或者只更新其中一項：")
if st.sidebar.button("抓取股價", use_container_width=True):
    run_and_log("抓取股價 (price_data.py)", price_data.run)
if st.sidebar.button("抓取股利", use_container_width=True):
    run_and_log("抓取股利 (dividend_data.py)", dividend_data.run)
if st.sidebar.button("抓取新聞", use_container_width=True):
    run_and_log("抓取新聞 (news_data.py)", news_data.run)
if st.sidebar.button("抓取流通股數／市值", use_container_width=True):
    run_and_log("抓取流通股數／市值 (market_value_data.py)", market_value_data.run)

st.sidebar.divider()
st.sidebar.caption("以下兩個按鈕只會重新計算已存在的本機資料，速度快，不會連線網路。")
if st.sidebar.button("重新計算股利預測", use_container_width=True):
    run_and_log("重新計算股利預測 (predict_dividends.py)", predict_dividends.run)
if st.sidebar.button("重新計算 DDM 估值", use_container_width=True):
    run_and_log("重新計算 DDM 估值 (ddm_valuation.py)", ddm_valuation.run)

# --- Header ------------------------------------------------------------------

st.title("台灣股票追蹤器")
st.caption("追蹤清單：" + "、".join(f"{s['code']} {s['name']}" for s in config.WATCHLIST))

tab_overview, tab_prices, tab_dividends, tab_market_value, tab_ddm, tab_news = st.tabs(
    ["總覽", "股價", "股利", "市值", "DDM 估值", "新聞"]
)

# --- Overview ------------------------------------------------------------------

with tab_overview:
    cols = st.columns(len(config.WATCHLIST))
    for col, stock in zip(cols, config.WATCHLIST):
        code = stock["code"]
        prices = load_csv(os.path.join(config.DATA_DIR, "prices", f"{code}.csv"))
        mv = load_csv(os.path.join(config.DATA_DIR, "market_value", f"{code}.csv"))
        with col:
            st.subheader(f"{code}")
            st.caption(stock["name"])
            if prices is not None:
                latest = prices.iloc[-1]
                st.metric("最新收盤價", f"{latest['Close']:.2f}", help=f"資料日期：{latest['Date']}")
            else:
                st.caption("尚無股價資料。")
            if mv is not None:
                latest_mv = mv.iloc[-1]
                st.metric("市值（新台幣）", f"{latest_mv['MarketValue']:,.0f}")
    st.info("第一次使用請從側邊欄抓取資料，之後也可以用來更新資料。", icon="ℹ️")

# --- Prices ----------------------------------------------------------------------

with tab_prices:
    codes = [s["code"] for s in config.WATCHLIST]
    picked = st.selectbox("選擇股票代號", codes, format_func=lambda c: f"{c} {watchlist_name(c)}")
    prices = load_csv(os.path.join(config.DATA_DIR, "prices", f"{picked}.csv"))
    if prices is None:
        st.info("尚無股價資料 -- 請在側邊欄點擊「抓取股價」。")
    else:
        prices["Date"] = pd.to_datetime(prices["Date"])
        st.line_chart(prices.set_index("Date")["Close"])
        st.dataframe(
            display_table(prices.sort_values("Date", ascending=False), labels=PRICE_COLUMNS_ZH),
            use_container_width=True, hide_index=True,
        )

# --- Dividends -------------------------------------------------------------------

with tab_dividends:
    st.subheader("股利發放紀錄")
    divs = load_csv(os.path.join(config.DATA_DIR, "dividends", "dividends.csv"))
    if divs is None:
        st.info("尚無股利資料 -- 請在側邊欄點擊「抓取股利」。")
    else:
        st.dataframe(
            display_table(divs.sort_values("ex_dividend_date", ascending=False), labels=DIVIDEND_COLUMNS_ZH),
            use_container_width=True, hide_index=True,
        )

    st.subheader("下次配息預測（兩種方法，近1年資料）")
    pred = load_csv(os.path.join(config.DATA_DIR, "dividends", "dividend_prediction.csv"))
    if pred is None:
        st.info("尚無預測結果 -- 請在側邊欄點擊「重新計算股利預測」（需要先有股利與股價資料）。")
    else:
        st.dataframe(
            display_table(pred, labels=DIVIDEND_PREDICTION_COLUMNS_ZH),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "方法A：以幾何平均成長率推算下一次配息金額。方法B：以平均殖利率 × 最新股價"
            "估算。兩者假設不同，結果經常不一致 -- 詳細原因與所有注意事項請見 README 的 "
            "'Predicting the next dividend' 章節，使用前請勿只憑其中一個數字下定論。"
        )

# --- Market value -------------------------------------------------------------------

with tab_market_value:
    for stock in config.WATCHLIST:
        code = stock["code"]
        mv = load_csv(os.path.join(config.DATA_DIR, "market_value", f"{code}.csv"))
        st.subheader(f"{code} {stock['name']}")
        if mv is None:
            st.caption("尚無市值資料 -- 請在側邊欄點擊「抓取流通股數／市值」。")
            continue
        latest = mv.sort_values("Date").iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("收盤價", f"{latest['Close']:.2f}")
        c2.metric("流通股數", f"{int(latest['SharesOutstanding']):,}")
        c3.metric("市值（新台幣）", f"{latest['MarketValue']:,.0f}")
        st.dataframe(
            display_table(mv.sort_values("Date", ascending=False), labels=MARKET_VALUE_COLUMNS_ZH),
            use_container_width=True, hide_index=True,
        )
    st.caption(
        "對 ETF 而言，這個數字是資產規模（AUM）的代表值（單位淨值 × 流通單位數），"
        "並非一般公司市值的概念。只會從你開始執行抓取的那天起，每天累積一筆資料 -- "
        "詳見 README 說明為何沒有回溯歷史資料。"
    )

# --- DDM valuation -------------------------------------------------------------------

with tab_ddm:
    ddm = load_csv(os.path.join(config.DATA_DIR, "dividends", "ddm_valuation.csv"))
    if ddm is None:
        st.info("尚無 DDM 估值結果 -- 請在側邊欄點擊「重新計算 DDM 估值」（需要先有股利與股價資料）。")
    else:
        st.dataframe(
            display_table(ddm, column_order=DDM_COLUMN_ORDER, labels=DDM_COLUMNS_ZH),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "與市價差異%（多項式模型 / 均值回歸模型）: 正值 = 模型認為目前價格被低估，"
            "負值 = 被高估。估值欄位就排在「最新股價」旁邊方便比較，但這只是有限年期內"
            "股利的現值加總（沒有終值），是一個下限，不是完整的公允價值估計。詳細方法論"
            "與所有注意事項請見 README 的 'DDM valuation' 章節，尤其像 0050 這種主要靠"
            "價差、股利配發相對少的 ETF，這個數字更不能單獨當作定論。"
        )

# --- News -------------------------------------------------------------------------

with tab_news:
    news = load_csv(os.path.join(config.DATA_DIR, "news", "news.csv"))
    if news is None:
        st.info("尚無新聞資料 -- 請在側邊欄點擊「抓取新聞」。")
    else:
        for _, row in news.iterrows():
            st.markdown(f"**[{row['title']}]({row['link']})**")
            st.caption(f"{row['company']} ({row['code']}) · {row['source']} · {row['published']}")
