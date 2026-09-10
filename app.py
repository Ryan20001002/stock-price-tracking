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

Login and personal watchlists
------------------------------
Visitors sign in with a simple username/password account (created from
the same login screen). Two DIFFERENT lists exist, deliberately kept
separate:

- config.WATCHLIST -- the shared/global registry every fetch script reads
  (price_data.py, dividend_data.py, etc.). Adding a ticker here means
  "start collecting data for this ticker, for everyone." Still backed by
  data/watchlist.json exactly as before login existed.
- Each logged-in account's PERSONAL watchlist -- just which of the
  tickers already in the shared registry THEY want to see, stored
  per-account in data/users.json via user_store.py (see that module's
  docstring for the design and its trade-offs, especially around
  Streamlit Community Cloud's storage not surviving a restart). Every
  display tab below shows only the current session's personal
  watchlist, resolved against config.WATCHLIST for each ticker's name.

No external setup is needed for this -- accounts and passwords are
created and checked entirely by user_store.py, with no Google Cloud
project, OAuth client, or service account involved.

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
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import price_data
import dividend_data
import news_data
import market_value_data
import predict_dividends
import ddm_valuation
import user_store
import institutional_data

st.set_page_config(page_title="台灣股票追蹤器", layout="wide")

# Every st.dataframe table gets a small hover-only toolbar (download/search/
# fullscreen icons) that Streamlit doesn't expose any official parameter to
# resize -- confirmed via Streamlit's own st.dataframe docs (no toolbar-
# related argument exists as of the version pinned in requirements.txt).
# On a touch screen, "hover-only" also means it can be awkward to even make
# it appear. [data-testid="stElementToolbar"] is Streamlit's actual (but
# undocumented/internal) DOM hook for this toolbar -- widely used in the
# Streamlit community for exactly this kind of styling tweak, but it's not
# a stable public API, so a future Streamlit upgrade could rename it and
# silently stop this working (harmless if so -- the toolbar just goes back
# to its small default size, nothing breaks).
st.markdown(
    """
    <style>
    [data-testid="stElementToolbar"] {
        opacity: 1 !important;  /* always visible, not just on hover -- hover doesn't exist on touch */
    }
    [data-testid="stElementToolbar"] button {
        min-width: 2.75rem !important;   /* ~44px, the standard minimum touch-target size */
        min-height: 2.75rem !important;
    }
    [data-testid="stElementToolbar"] svg {
        width: 1.25rem !important;
        height: 1.25rem !important;
    }
    /* The button that expands the sidebar again after it's collapsed (a
       small arrow, normally fixed near the top-left corner) reportedly
       disappears on some mobile browsers -- force it to stay visible,
       on top of everything else, regardless of theme/scroll position.
       Covers both the current (stSidebarCollapsedControl) and older
       (collapsedControl) internal testids Streamlit has used for this,
       since which one applies depends on the Streamlit version. */
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        visibility: visible !important;
        display: flex !important;
        opacity: 1 !important;
        z-index: 999999 !important;
    }
    /* (2026-09-10) User report: "sometimes when we scroll, the app shuts
       down and we have to re-login." This is almost certainly mobile
       Chrome/Safari's native pull-to-refresh gesture -- overscrolling past
       the very top (or bottom) of the page is interpreted by the BROWSER
       itself as "reload this page", which is a genuine navigation, not a
       Streamlit rerun. A real page reload opens a brand new browser
       session, so st.session_state (including auth_user/is_guest) is
       wiped clean -- that's why it looks like the whole app "shut down"
       and always demands a fresh login afterwards, rather than just
       glitching visually. overscroll-behavior tells the browser not to
       treat overscroll as a refresh trigger, while leaving normal
       in-page scrolling completely untouched. Applied to both html and
       body since browser support for which element to target varies.*/
    html, body {
        overscroll-behavior-y: contain !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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

INSTITUTIONAL_COLUMNS_ZH = {
    "Date": "日期", "ForeignNet": "外資淨買賣超（股）",
    "InvestmentTrustNet": "投信淨買賣超（股）", "DealerNet": "自營商淨買賣超（股）",
    "ThreeInstitutionsNet": "三大法人合計淨買賣超（股）",
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


# Taiwan market color convention is the OPPOSITE of the US/Western one:
# red (紅) = price up, green (綠) = price down. Getting this backwards on a
# candlestick chart would silently mislead every reading of it, so it's
# called out explicitly here rather than left as an unexplained hex pair.
CANDLESTICK_UP_COLOR = "#d64545"    # red -- 漲
CANDLESTICK_DOWN_COLOR = "#2e7d32"  # green -- 跌


# Shared by every chart below. History of the mobile touch problem, kept
# here since the fix escalated twice:
#   1st report ("plots ruined") -> added config={"responsive": True}. Did
#      NOT fix it -- that setting only handles resizing on rotation, not
#      touch capture.
#   2nd report ("drifts to a weird zoomed range when scrolling", with a
#      screenshot showing a millisecond-scale x-axis) -> diagnosed as
#      Plotly's default dragmode='zoom' treating a scroll swipe that starts
#      on the chart as a zoom-drag. Fixed with fixedrange=True on both axes
#      + dragmode=False (still set below, as defense in depth).
#   3rd report ("crashes if you touch the middle of the chart, not the
#      sides", with an annotated screenshot) -> dragmode=False stops
#      DRAGGING from zooming, but Plotly can still attach its own
#      touchstart/touchmove listeners for hover-tracking even when dragging
#      is disabled, and that was apparently still enough to intercept/desync
#      the touch and crash the page. staticPlot=True is the actual fix this
#      time -- it skips attaching ANY JS event listeners to the chart at
#      all, so a touch that starts on it behaves exactly like touching a
#      plain image: nothing intercepts it, the browser's native scroll just
#      takes over immediately. Trade-off: hover tooltips no longer work on
#      these charts on any device -- acceptable since the exact same
#      numbers are already in the data table right below every chart.
_FIXED_AXES = dict(xaxis=dict(fixedrange=True), yaxis=dict(fixedrange=True), dragmode=False)
_MOBILE_CHART_CONFIG = {"staticPlot": True, "responsive": True}


def render_candlestick(df):
    """df needs Date/Open/High/Low/Close columns already filtered to the
    desired date range. Renders a candlestick chart -- no return value."""
    fig = go.Figure(data=[go.Candlestick(
        x=df["Date"], open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        increasing_line_color=CANDLESTICK_UP_COLOR, increasing_fillcolor=CANDLESTICK_UP_COLOR,
        decreasing_line_color=CANDLESTICK_DOWN_COLOR, decreasing_fillcolor=CANDLESTICK_DOWN_COLOR,
        name="股價",
    )])
    fig.update_layout(
        xaxis=dict(rangeslider=dict(visible=False), fixedrange=True),
        yaxis=dict(fixedrange=True),
        dragmode=False,
        # b=40 gives the x-axis date labels enough room on narrow screens;
        # autosize=True lets Plotly redraw when the container width changes
        # (e.g. rotating the phone or switching from portrait to landscape).
        margin=dict(l=10, r=10, t=10, b=40),
        height=400,
        autosize=True,
    )
    st.plotly_chart(fig, use_container_width=True, config=_MOBILE_CHART_CONFIG)


def render_bar(df, column, label, color="#4c78a8"):
    """df needs Date and `column` already filtered to the desired date
    range. Renders a simple bar chart -- used for volume and, on the
    institutional-investors tab, net buy/sell by investor type."""
    fig = go.Figure(data=[go.Bar(x=df["Date"], y=df[column], marker_color=color, name=label)])
    fig.update_layout(
        **_FIXED_AXES,
        margin=dict(l=10, r=10, t=10, b=40),
        height=200,
        showlegend=False,
        autosize=True,
    )
    st.plotly_chart(fig, use_container_width=True, config=_MOBILE_CHART_CONFIG)


# --- "Remember me" persistent login (2026-09-10) -----------------------------
# Fixes the "app crashes/reloads on mobile and I have to log back in every
# time" complaint at its root: a real browser reload wipes st.session_state
# entirely, so normally there's nothing left to know who was logged in.
# This does NOT store the account's password anywhere in the browser --
# see user_store.py's "Remember me" section for the full design (a random
# per-login token, only its hash kept on disk, invalidated on logout). Here
# in app.py, the two things user_store.py can't do itself are: writing the
# token into an actual browser cookie, and reading that cookie back on a
# later visit -- Streamlit has no Set-Cookie API, so the write goes through
# a tiny injected <script> (components.html renders it in an iframe that
# Streamlit marks allow-same-origin, so document.cookie set there does land
# in the real page's cookie jar -- a known, if unofficial, pattern for this
# in the Streamlit community since there's no first-party alternative).
REMEMBER_COOKIE_NAME = "stock_tracker_remember"
REMEMBER_COOKIE_MAX_AGE_SECONDS = 30 * 86400


def _set_remember_cookie(username, token):
    value = urllib.parse.quote(f"{username}:{token}")
    st.components.v1.html(
        f"""<script>
        var secure = (window.location.protocol === "https:") ? "; Secure" : "";
        document.cookie = "{REMEMBER_COOKIE_NAME}={value}; Max-Age={REMEMBER_COOKIE_MAX_AGE_SECONDS}; "
            + "Path=/; SameSite=Lax" + secure;
        </script>""",
        height=0,
    )


def _clear_remember_cookie():
    st.components.v1.html(
        f"""<script>
        document.cookie = "{REMEMBER_COOKIE_NAME}=; Max-Age=0; Path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _remember_cookie_login():
    """Tries once per browser session to log someone back in from the
    "remember me" cookie. Silently does nothing (falls through to the
    normal login screen) if there's no cookie, it's expired/invalid, or
    this Streamlit version doesn't expose st.context.cookies (older
    versions don't -- degrade gracefully rather than crash the app)."""
    raw_cookie = None
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        raw_cookie = st.context.cookies.get(REMEMBER_COOKIE_NAME)
    if not raw_cookie:
        return False
    cookie_user, sep, cookie_token = urllib.parse.unquote(raw_cookie).rpartition(":")
    if sep and user_store.verify_remember_token(cookie_user, cookie_token):
        st.session_state["auth_user"] = cookie_user
        return True
    return False


# --- Login gate --------------------------------------------------------------
# Simple username/password accounts, handled entirely by user_store.py (see
# its docstring for the design and trade-offs) -- no external setup needed.
# A visitor can also browse as a GUEST instead: same features, but nothing
# about them (no account, no personal watchlist) is ever written to disk --
# see IS_GUEST below for exactly what that changes.

if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "is_guest" not in st.session_state:
    st.session_state["is_guest"] = False
if "tried_remember_cookie" not in st.session_state:
    st.session_state["tried_remember_cookie"] = False

# Only attempted once per browser session (the latch above) -- not reset on
# logout, deliberately: right after an explicit logout the clearing script
# may not have run in the browser yet, and re-checking immediately could
# log the same person right back in against their own action. Deferring to
# a real page reload (a fresh session, latch reset to False) is fine since
# the cookie will genuinely be gone by then.
if (
    st.session_state["auth_user"] is None
    and not st.session_state["is_guest"]
    and not st.session_state["tried_remember_cookie"]
):
    st.session_state["tried_remember_cookie"] = True
    if _remember_cookie_login():
        st.rerun()

if st.session_state["auth_user"] is None and not st.session_state["is_guest"]:
    st.title("台灣股票追蹤器")
    st.write("請先登入，才能查看與管理你自己的追蹤清單；或者選擇不登入，以訪客身分瀏覽。")

    tab_login, tab_signup = st.tabs(["登入", "註冊新帳號"])

    with tab_login:
        with st.form("login_form"):
            login_username = st.text_input("使用者名稱")
            login_password = st.text_input("密碼", type="password")
            remember_me = st.checkbox("記住我（這台裝置 30 天內不用重新登入）", value=True)
            if st.form_submit_button("登入", type="primary"):
                clean_username = login_username.strip()
                if user_store.verify_login(clean_username, login_password):
                    st.session_state["auth_user"] = clean_username
                    if remember_me:
                        remember_token = user_store.create_remember_token(clean_username)
                        if remember_token:
                            _set_remember_cookie(clean_username, remember_token)
                    st.rerun()
                else:
                    st.error("使用者名稱或密碼錯誤。")
        st.caption(
            "「記住我」的原理：登入時會產生一組隨機的登入權杖存在瀏覽器裡（不是密碼本身），"
            "手機閃退、斷線或重新整理時可以用它自動幫你登入，登出後這組權杖會立即失效。"
        )

    with tab_signup:
        st.caption("這是很單純的使用者名稱／密碼帳號，沒有 email 驗證或忘記密碼功能，適合自己或小群體使用。")
        with st.form("signup_form"):
            new_username = st.text_input("選擇使用者名稱")
            new_password = st.text_input("設定密碼", type="password")
            new_password_confirm = st.text_input("再輸入一次密碼", type="password")
            if st.form_submit_button("建立帳號"):
                if new_password != new_password_confirm:
                    st.error("兩次輸入的密碼不一致。")
                else:
                    ok, msg = user_store.create_account(new_username, new_password)
                    (st.success if ok else st.error)(msg)

    st.divider()
    st.caption(
        "不想建立帳號？可以用訪客身分瀏覽 -- 一樣能查看股價／股利／市值等資料、"
        "自己組一份暫時的追蹤清單，但這份清單只存在這次瀏覽階段，關閉分頁或登出"
        "後就會消失，不會留下任何跟你有關的紀錄。"
    )
    if st.button("以訪客身分瀏覽"):
        st.session_state["is_guest"] = True
        st.rerun()

    st.stop()

IS_GUEST = st.session_state["is_guest"]
USER_NAME = "訪客" if IS_GUEST else st.session_state["auth_user"]


# --- Personal watchlist (this logged-in account, or this guest session) -----

def _load_personal_codes():
    """A brand-new account (and the "shouldn't normally happen" None
    case) starts with an EMPTY personal watchlist -- deliberately no
    auto-added default tickers, so a new account never sees any share's
    data until they've actually added one themselves. Every tab below is
    written to handle an empty watchlist gracefully (see NO_WATCHLIST_MSG)
    rather than assuming at least one ticker. A guest session ALWAYS
    starts empty too, and never reads from user_store.py at all -- there's
    no account for it to look anything up under."""
    if IS_GUEST:
        return []
    return user_store.load_user_codes(USER_NAME) or []


if "personal_codes" not in st.session_state:
    st.session_state["personal_codes"] = _load_personal_codes()


def _save_personal_codes():
    """No-op for a guest session -- this is the one thing that actually
    delivers "don't store any information for them": a guest's watchlist
    only ever lives in st.session_state (this browser tab's memory for
    this session), and this function is the only place anything about a
    personal watchlist gets written to disk, so simply not calling
    user_store.save_user_codes() here is sufficient. Nothing else needs a
    guest-specific branch."""
    if IS_GUEST:
        return
    user_store.save_user_codes(USER_NAME, st.session_state["personal_codes"])


def personal_watchlist():
    """This session's personal watchlist, resolved against the shared
    registry (config.WATCHLIST) for name/name_en. A code the user saved
    that isn't (or isn't yet) in the shared registry is silently dropped
    here rather than crashing the page."""
    by_code = {s["code"]: s for s in config.WATCHLIST}
    return [by_code[c] for c in st.session_state["personal_codes"] if c in by_code]


def watchlist_name(code):
    return next((s["name"] for s in config.WATCHLIST if s["code"] == code), code)


# --- Sidebar: account ---------------------------------------------------

st.sidebar.title("帳號")
if IS_GUEST:
    st.sidebar.write(f"👤 {USER_NAME}")
    st.sidebar.caption("訪客模式：這份追蹤清單只存在這次瀏覽階段，不會被儲存。")
    if st.sidebar.button("結束訪客模式"):
        st.session_state["is_guest"] = False
        st.session_state.pop("personal_codes", None)
        st.rerun()
else:
    st.sidebar.write(f"👤 {USER_NAME}")
    if st.sidebar.button("登出"):
        user_store.clear_remember_token(USER_NAME)
        _clear_remember_cookie()
        st.session_state["auth_user"] = None
        st.session_state.pop("personal_codes", None)
        st.rerun()
st.sidebar.divider()

# --- Sidebar: manage personal watchlist ----------------------------------

st.sidebar.title("我的追蹤清單")
if IS_GUEST:
    st.sidebar.caption(
        "訪客模式下這份清單只存在這次瀏覽階段，不會被儲存，也換不到別台裝置。"
        "可以搜尋、加入任何台股代碼，包含全站第一次出現的全新代號；如果是全新代號，"
        "加入後請到下面「更新資料」按一下抓取按鈕，才會開始有資料可以看。"
    )
else:
    st.sidebar.caption(
        "只有你自己看得到、改得到的清單，登入後會自動儲存，換裝置登入也看得到。"
        "新增一檔股票時，如果這檔股票還沒有人抓過資料，會一併加進共用的抓取清單"
        "（下面「更新資料」抓的就是這份共用清單），所以第一次加入後記得按抓取按鈕。"
    )

if not personal_watchlist():
    st.sidebar.caption("（目前是空的，用下面的表單新增你想追蹤的第一檔股票）")

for stock in personal_watchlist():
    col_label, col_remove = st.sidebar.columns([4, 1])
    col_label.write(f"{stock['code']} · {stock['name']}")
    if col_remove.button("✕", key=f"remove_{stock['code']}", help=f"從我的清單移除 {stock['code']}"):
        st.session_state["personal_codes"] = [
            c for c in st.session_state["personal_codes"] if c != stock["code"]
        ]
        _save_personal_codes()
        st.rerun()

with st.sidebar.form("add_ticker_form", clear_on_submit=True):
    st.caption("新增股票代號（台股代碼，例如 2330 代表台積電）")
    new_code = st.text_input("代號")
    new_name = st.text_input("中文名稱（選填，僅在這檔股票是全站第一次加入時需要）")
    new_name_en = st.text_input("英文名稱（選填）")
    if st.form_submit_button("加入我的清單"):
        code = new_code.strip()
        is_new_to_app = not any(s["code"] == code for s in config.WATCHLIST)
        if not code:
            st.sidebar.error("請先輸入股票代號。")
        elif code in st.session_state["personal_codes"]:
            st.sidebar.warning(f"{code} 已經在你的追蹤清單中。")
        else:
            if is_new_to_app:
                # Brand new to the whole app -- add it to the shared
                # registry too, so the Fetch buttons below start pulling
                # data for it. Other users' personal lists are untouched.
                #
                # Guests can trigger this too (2026-09-10, per explicit
                # request -- "all available stocks to search"): this write
                # is SHARED/public data (which tickers the app tracks at
                # all), not information ABOUT the guest, so it doesn't
                # conflict with guest mode's "don't store anything about
                # THEM" rule -- that rule covers data/users.json and the
                # guest's own personal_codes, not the shared registry.
                new_entry = {
                    "code": code,
                    "name": new_name.strip() or code,
                    "name_en": new_name_en.strip() or code,
                }
                config.save_watchlist(list(config.WATCHLIST) + [new_entry])
            st.session_state["personal_codes"] = st.session_state["personal_codes"] + [code]
            _save_personal_codes()
            st.rerun()

st.sidebar.divider()

# --- Sidebar: refresh controls ----------------------------------------------

st.sidebar.title("更新資料")
st.sidebar.caption(
    "以下每個按鈕抓的是共用資料（所有登入使用者的追蹤清單合起來），跟在 "
    "PowerShell 執行對應指令（例如 `python price_data.py`）完全相同。這會即時"
    "連線 TWSE/yfinance，並刻意放慢速度 -- 詳見 config.py 的 "
    "REQUEST_DELAY_SECONDS -- 如果看起來很慢，請不要重複點擊。"
)

st.sidebar.caption(
    "一次抓取全部資料（股價／股利／新聞會同時進行，比一個一個點快很多；"
    "流通股數／市值需要用到當天的股價，所以接著抓；三大法人買賣超的資料來源"
    "每次連線只能拿到「一天、全部股票」的資料，回溯歷史要一天一天抓，所以"
    "最後才抓、也最慢——回溯的時間長度跟股價一樣，都是 config.py 的 "
    "PRICE_HISTORY_MONTHS，預設 36 個月，第一次抓可能需要 20-30 分鐘以上，"
    "之後只會補新的交易日，會快很多）："
)
if st.sidebar.button("🔄 一鍵抓取全部資料", use_container_width=True, type="primary"):
    run_parallel_and_log([
        ("抓取股價 (price_data.py)", price_data.run),
        ("抓取股利 (dividend_data.py)", dividend_data.run),
        ("抓取新聞 (news_data.py)", news_data.run),
    ])
    run_and_log("抓取流通股數／市值 (market_value_data.py)", market_value_data.run)
    run_and_log("抓取三大法人買賣超 (institutional_data.py)", institutional_data.run)
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
if st.sidebar.button("抓取三大法人買賣超", use_container_width=True):
    run_and_log("抓取三大法人買賣超 (institutional_data.py)", institutional_data.run)

st.sidebar.divider()
st.sidebar.caption("以下兩個按鈕只會重新計算已存在的本機資料，速度快，不會連線網路。")
if st.sidebar.button("重新計算股利預測", use_container_width=True):
    run_and_log("重新計算股利預測 (predict_dividends.py)", predict_dividends.run)
if st.sidebar.button("重新計算 DDM 估值", use_container_width=True):
    run_and_log("重新計算 DDM 估值 (ddm_valuation.py)", ddm_valuation.run)

# --- Header ------------------------------------------------------------------

st.title("台灣股票追蹤器")

my_watchlist = personal_watchlist()

# Shared across every tab below -- a brand-new (or emptied-out) personal
# watchlist means there's nothing of THIS account's to show yet. Every tab
# checks `if not my_watchlist` and shows this instead of any share's data,
# rather than falling back to some default set of tickers.
NO_WATCHLIST_MSG = (
    "你的追蹤清單目前是空的，還沒有加入任何股票。請在左側「我的追蹤清單」輸入股票代號，"
    "按「加入我的清單」，這裡才會顯示對應的股價／股利／市值等資訊。"
)

if not my_watchlist:
    st.info("👋 " + NO_WATCHLIST_MSG, icon="👋")
else:
    st.caption("我的追蹤清單：" + "、".join(f"{s['code']} {s['name']}" for s in my_watchlist))

tab_overview, tab_prices, tab_dividends, tab_market_value, tab_ddm, tab_news = st.tabs(
    ["總覽", "股價", "股利", "市值", "DDM 估值", "新聞"]
)

# --- Overview ------------------------------------------------------------------

with tab_overview:
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        cols = st.columns(len(my_watchlist))
        for col, stock in zip(cols, my_watchlist):
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
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        codes = [s["code"] for s in my_watchlist]
        picked = st.selectbox("選擇股票代號", codes, format_func=lambda c: f"{c} {watchlist_name(c)}")
        prices = load_csv(os.path.join(config.DATA_DIR, "prices", f"{picked}.csv"))
        if prices is None:
            st.info("尚無股價資料 -- 請在側邊欄點擊「抓取股價」。")
        else:
            prices["Date"] = pd.to_datetime(prices["Date"])
            prices = prices.sort_values("Date")
            min_date = prices["Date"].min().date()
            max_date = prices["Date"].max().date()

            # Quick presets (in days, counting back from the latest date on
            # file) plus a custom start/end option. Keyed per-ticker so
            # switching between stocks in the dropdown above doesn't carry
            # one ticker's chosen range over to another's widget state.
            RANGE_PRESET_DAYS = {
                "1週": 7, "1個月": 30, "3個月": 91, "6個月": 182,
                "1年": 365, "3年": 365 * 3,
            }
            range_options = list(RANGE_PRESET_DAYS) + ["全部", "自訂..."]
            range_choice = st.radio(
                "顯示區間", range_options, index=range_options.index("全部"),
                horizontal=True, key=f"price_range_{picked}",
            )

            if range_choice == "自訂...":
                col_start, col_end = st.columns(2)
                start_date = col_start.date_input(
                    "起始日期", value=min_date, min_value=min_date, max_value=max_date,
                    key=f"price_start_{picked}",
                )
                end_date = col_end.date_input(
                    "結束日期", value=max_date, min_value=min_date, max_value=max_date,
                    key=f"price_end_{picked}",
                )
                if start_date > end_date:
                    st.warning("起始日期不能晚於結束日期，已自動交換兩者。")
                    start_date, end_date = end_date, start_date
            elif range_choice == "全部":
                start_date, end_date = min_date, max_date
            else:
                end_date = max_date
                start_date = max(min_date, max_date - pd.Timedelta(days=RANGE_PRESET_DAYS[range_choice]))

            filtered = prices[(prices["Date"].dt.date >= start_date) & (prices["Date"].dt.date <= end_date)]
            if filtered.empty:
                st.info("這個區間內沒有股價資料，請試試其他區間。")
            else:
                render_candlestick(filtered)
                st.caption("成交量（整體成交股數）")
                render_bar(filtered, "Volume", "成交量")
                st.dataframe(
                    display_table(filtered.sort_values("Date", ascending=False), labels=PRICE_COLUMNS_ZH),
                    use_container_width=True, hide_index=True,
                )

            # --- 三大法人買賣超, directly below the price chart -- shares the
            # same ticker (picked) and date range (start_date/end_date) as
            # the price section above, so there's one selector/range picker
            # for both instead of a second, separate one.
            st.divider()
            st.subheader("三大法人買賣超")
            inst = load_csv(os.path.join(config.DATA_DIR, "institutional", f"{picked}.csv"))
            if inst is None:
                st.info("尚無三大法人買賣超資料 -- 請在側邊欄點擊「抓取三大法人買賣超」。")
            else:
                inst["Date"] = pd.to_datetime(inst["Date"])
                inst_filtered = inst[(inst["Date"].dt.date >= start_date) & (inst["Date"].dt.date <= end_date)]
                if inst_filtered.empty:
                    st.info("這個區間內沒有三大法人資料，請試試其他區間，或在側邊欄重新抓取。")
                else:
                    st.caption("外資淨買賣超（股）-- 正值＝淨買超，負值＝淨賣超")
                    render_bar(inst_filtered, "ForeignNet", "外資淨買賣超", color="#e45756")
                    st.caption("投信淨買賣超（股）")
                    render_bar(inst_filtered, "InvestmentTrustNet", "投信淨買賣超", color="#4c78a8")
                    st.caption("自營商淨買賣超（股）")
                    render_bar(inst_filtered, "DealerNet", "自營商淨買賣超", color="#54a24b")
                    st.dataframe(
                        display_table(inst_filtered.sort_values("Date", ascending=False), labels=INSTITUTIONAL_COLUMNS_ZH),
                        use_container_width=True, hide_index=True,
                    )
            st.caption(
                "資料來源為證交所三大法人買賣超日報（T86）。外資＝外陸資（不含外資自營商）"
                "＋外資自營商；投信、自營商為證交所公告的官方合計數字。回溯的時間長度跟"
                "股價一樣（config.py 的 PRICE_HISTORY_MONTHS），第一次抓取會比較久，請先"
                "在側邊欄點擊「抓取三大法人買賣超」再耐心等待。"
            )

# --- Dividends -------------------------------------------------------------------

with tab_dividends:
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        divs = load_csv(os.path.join(config.DATA_DIR, "dividends", "dividends.csv"))
        pred = load_csv(os.path.join(config.DATA_DIR, "dividends", "dividend_prediction.csv"))

        if divs is None and pred is None:
            st.info("尚無股利資料 -- 請在側邊欄點擊「抓取股利」。")
        else:
            for i, stock in enumerate(my_watchlist):
                code = stock["code"]
                st.subheader(f"{code} {stock['name']}")

                st.caption("股利發放紀錄")
                ticker_divs = divs[divs["code"] == code] if divs is not None else None
                if ticker_divs is None or ticker_divs.empty:
                    st.caption("尚無股利發放紀錄 -- 請在側邊欄點擊「抓取股利」。")
                else:
                    # code/name/name_en dropped from the per-ticker table --
                    # they're constant within this ticker's section (already
                    # shown in the subheader above), so repeating them on
                    # every row is just noise.
                    st.dataframe(
                        display_table(
                            ticker_divs.drop(columns=["code", "name", "name_en"], errors="ignore")
                                       .sort_values("ex_dividend_date", ascending=False),
                            labels=DIVIDEND_COLUMNS_ZH,
                        ),
                        use_container_width=True, hide_index=True,
                    )

                st.caption("下次配息預測（兩種方法，近1年資料）")
                ticker_pred = pred[pred["code"] == code] if pred is not None else None
                if ticker_pred is None or ticker_pred.empty:
                    st.caption("尚無預測結果 -- 請在側邊欄點擊「重新計算股利預測」（需要先有股利與股價資料）。")
                else:
                    st.dataframe(
                        display_table(
                            ticker_pred.drop(columns=["code", "name", "name_en"], errors="ignore"),
                            labels=DIVIDEND_PREDICTION_COLUMNS_ZH,
                        ),
                        use_container_width=True, hide_index=True,
                    )

                if i < len(my_watchlist) - 1:
                    st.divider()

            st.caption(
                "方法A：以幾何平均成長率推算下一次配息金額。方法B：以平均殖利率 × 最新股價"
                "估算。兩者假設不同，結果經常不一致 -- 詳細原因與所有注意事項請見 README 的 "
                "'Predicting the next dividend' 章節，使用前請勿只憑其中一個數字下定論。"
            )

# --- Market value -------------------------------------------------------------------

with tab_market_value:
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        for stock in my_watchlist:
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
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        ddm = load_csv(os.path.join(config.DATA_DIR, "dividends", "ddm_valuation.csv"))
        if ddm is None:
            st.info("尚無 DDM 估值結果 -- 請在側邊欄點擊「重新計算 DDM 估值」（需要先有股利與股價資料）。")
        else:
            my_codes = [s["code"] for s in my_watchlist]
            ddm_mine = ddm[ddm["code"].isin(my_codes)]
            if ddm_mine.empty:
                st.info("你的追蹤清單中的股票尚無 DDM 估值結果 -- 請在側邊欄點擊「重新計算 DDM 估值」。")
            else:
                st.dataframe(
                    display_table(ddm_mine, column_order=DDM_COLUMN_ORDER, labels=DDM_COLUMNS_ZH),
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
    if not my_watchlist:
        st.info(NO_WATCHLIST_MSG)
    else:
        news = load_csv(os.path.join(config.DATA_DIR, "news", "news.csv"))
        if news is None:
            st.info("尚無新聞資料 -- 請在側邊欄點擊「抓取新聞」。")
        else:
            my_codes = [s["code"] for s in my_watchlist]
            news_mine = news[news["code"].isin(my_codes)]
            if news_mine.empty:
                st.info("你的追蹤清單中的股票尚無新聞資料 -- 請在側邊欄點擊「抓取新聞」。")
            else:
                for _, row in news_mine.iterrows():
                    st.markdown(f"**[{row['title']}]({row['link']})**")
                    st.caption(f"{row['company']} ({row['code']}) · {row['source']} · {row['published']}")
