"""
Per-user personal watchlist storage, backed by a private Google Sheet via
a service account.

A user's personal watchlist is stored as just a list of ticker CODES
(e.g. ["0050", "2330"]) -- which of the tickers already known to the app
(config.WATCHLIST, the shared/global registry every fetch script reads)
THIS particular logged-in person wants to see. It is deliberately NOT a
full copy of each ticker's name/name_en/etc, and it does NOT duplicate
any market data -- prices, dividends, news, and market value are fetched
once and shared across every user, since there's no reason to hit
TWSE/yfinance separately per person. Logging in only changes what each
person is looking AT, never what data gets collected.

This needs a one-time manual setup outside of this codebase (a Google
Cloud service account + a private Google Sheet it can read/write) --
see the README's "Login and personal watchlists" section for the exact
steps. Until that's done, every call here will raise, which app.py
handles by falling back to a not-logged-in-friendly message rather than
crashing the whole page.

Local file storage (like config.py's data/watchlist.json) was NOT used
for this because it doesn't survive Streamlit Community Cloud restarts
(see README/project notes on ephemeral storage there) -- a Google Sheet
is a small, free, always-on place for this handful of small per-user
records to actually persist once the app is deployed, not just when
running locally.
"""

import json
from datetime import datetime, timezone

import gspread
import streamlit as st

WORKSHEET_NAME = "watchlists"
HEADER = ["email", "codes_json", "updated_at"]


@st.cache_resource(show_spinner=False)
def _client():
    """One gspread client, reused across every user's session in this
    process -- this is a shared connection object, not per-user data, so
    caching it process-wide (rather than per-session) is safe and avoids
    re-authenticating on every single rerun."""
    return gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))


@st.cache_resource(show_spinner=False)
def _worksheet():
    sh = _client().open_by_key(st.secrets["gsheets"]["spreadsheet_id"])
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=100, cols=len(HEADER))
        ws.update([HEADER])
    return ws


def _find_row(ws, email):
    """Returns (row_index, row_values) for this email's row (row_index is
    1-indexed, matching gspread/Sheets' own convention), or (None, None)
    if this user has no row yet."""
    values = ws.get_all_values()
    for i, row in enumerate(values[1:], start=2):  # row 1 is the header
        if row and row[0] == email:
            return i, row
    return None, None


def load_user_codes(email):
    """Returns this user's saved list of ticker codes, or None if they
    have no saved watchlist yet (first login, or the sheet doesn't have a
    row for them) -- callers should fall back to a sensible default."""
    ws = _worksheet()
    _, row = _find_row(ws, email)
    if row is None or len(row) < 2 or not row[1]:
        return None
    try:
        return json.loads(row[1])
    except (ValueError, TypeError):
        return None


def save_user_codes(email, codes):
    """Persists this user's list of ticker codes, creating their row if
    this is their first save."""
    ws = _worksheet()
    row_index, _ = _find_row(ws, email)
    now = datetime.now(timezone.utc).isoformat()
    row = [email, json.dumps(codes), now]
    if row_index is None:
        ws.append_row(row)
    else:
        ws.update(f"A{row_index}:C{row_index}", [row])
