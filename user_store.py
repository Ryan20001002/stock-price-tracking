"""
Simple username/password accounts, with each account's personal watchlist
stored alongside it -- all in one local file, data/users.json.

This REPLACES an earlier Google-account-login design (Google OAuth client
+ a private Google Sheet via a service account), which needed more manual
setup (Google Cloud project, OAuth consent scopes, a service account, a
shared Sheet) than this project needs. Trade-offs of this simpler design,
written down rather than silently assumed:

- No real identity verification. "Logging in" here just means typing a
  username and password someone invented when creating that account --
  there's no email verification and no password reset flow, and nothing
  stops someone from picking a weak password. Fine for a small
  dissertation project where the people using it are known and trusted;
  NOT meant for a public app with untrusted strangers signing up.
- Passwords are never stored in plain text -- each account gets its own
  random salt, and only a PBKDF2-HMAC-SHA256 hash of the password (never
  the password itself) is saved to disk. Still, this is a from-scratch,
  non-audited implementation, not a vetted auth library -- don't reuse a
  password here that matters elsewhere (banking, email, etc).
- Storage is a local file (data/users.json), the same mechanism already
  used for data/watchlist.json. That means it's excluded by .gitignore
  (nobody's password hash ends up in the git repo), but -- same as every
  other file under data/ -- it does NOT survive a Streamlit Community
  Cloud restart (the app "sleeping" after inactivity, or a redeploy):
  accounts and personal watchlists created there would need to be
  recreated after that happens. Running locally, or on an always-on
  host, this file just persists normally like any other file on disk.
  See the README's "Login and personal watchlists" section.

A user's personal watchlist is stored as just a list of ticker CODES
(e.g. ["0050", "2330"]) -- which of the tickers already known to the app
(config.WATCHLIST, the shared/global registry every fetch script reads)
THIS account wants to see. It does NOT duplicate any market data --
prices, dividends, news, and market value are fetched once and shared
across every account, since there's no reason to hit TWSE/yfinance
separately per person. Logging in only changes what each account is
looking AT, never what data gets collected.
"""

import hashlib
import hmac
import json
import os
import secrets as _secrets
import time

USERS_FILE = os.path.join("data", "users.json")
PBKDF2_ITERATIONS = 200_000
REMEMBER_TOKEN_DAYS = 30


def _load_all():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(users):
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _hash_password(password, salt_hex):
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex()


def create_account(username, password):
    """Creates a new account with an empty personal watchlist. Returns
    (success: bool, message: str) -- message is a ready-to-display
    Mandarin string either way (why it failed, or that it worked)."""
    username = username.strip()
    if not username:
        return False, "請輸入使用者名稱。"
    if len(password) < 4:
        return False, "密碼至少需要 4 個字元。"
    users = _load_all()
    if username in users:
        return False, "這個使用者名稱已經有人使用了，請換一個。"
    salt_hex = _secrets.token_hex(16)
    users[username] = {
        "salt": salt_hex,
        "password_hash": _hash_password(password, salt_hex),
        "watchlist_codes": [],
    }
    _save_all(users)
    return True, "帳號建立成功，請切換到「登入」分頁登入。"


def verify_login(username, password):
    """True if this username/password pair is correct."""
    user = _load_all().get(username)
    if user is None:
        return False
    return _hash_password(password, user.get("salt", "")) == user.get("password_hash")


def load_user_codes(username):
    """Returns this account's saved list of ticker codes, or None if the
    account doesn't exist (shouldn't normally happen post-login) --
    callers should fall back to a sensible default in that case."""
    user = _load_all().get(username)
    return None if user is None else user.get("watchlist_codes", [])


def save_user_codes(username, codes):
    """Persists this account's list of ticker codes."""
    users = _load_all()
    if username in users:
        users[username]["watchlist_codes"] = codes
        _save_all(users)


# --- "Remember me" persistent login (2026-09-10) -----------------------------
# Added after a mobile bug report where the app's whole session would reset
# (a real browser page reload -- mobile pull-to-refresh, a dropped
# Streamlit connection, etc. -- wipes st.session_state) and force a fresh
# login every time. This is the standard, secure way to fix that WITHOUT
# doing what "store the password so I don't have to log in again" would
# literally mean: the actual password is never written here, and never put
# in the browser. Instead, on a successful login the account gets a
# separate random token; only a plain SHA-256 hash of THAT token is saved
# here (never the raw token), and the raw token itself is put in a browser
# cookie by app.py. Later, app.py can check "does this cookie's token hash-
# match what's on file for this username, and hasn't it expired" to log
# someone back in automatically -- without ever needing their password
# again, and without this file (or a copy of data/users.json) being enough
# on its own to log in as anyone (the raw token in the cookie is required,
# and it's never written to disk anywhere). Logging out invalidates it
# immediately (clear_remember_token), so a leftover cookie -- browser
# history, a shared computer -- stops working right away.
#
# Deliberately a PLAIN sha256 hash here, not the slow PBKDF2 used for
# passwords above: the token itself already has 256 bits of randomness
# from secrets.token_urlsafe(32), so there's no weak-password/dictionary-
# attack risk a slow hash would defend against -- it only needs to not be
# trivially reversible if data/users.json ever leaked, which sha256 already
# gives here.

def create_remember_token(username):
    """Generates a new "remember me" token for `username`, saves only its
    hash + an expiry here, and returns the RAW token -- the only time it's
    ever available in cleartext -- for the caller to store in a browser
    cookie. Returns None if the account doesn't exist. Overwrites any
    previous token for this account (logging in with "remember me" on a
    new browser invalidates the old one)."""
    users = _load_all()
    if username not in users:
        return None
    token = _secrets.token_urlsafe(32)
    users[username]["remember_token_hash"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
    users[username]["remember_token_expires"] = time.time() + REMEMBER_TOKEN_DAYS * 86400
    _save_all(users)
    return token


def verify_remember_token(username, token):
    """True if `token` is the current, non-expired "remember me" token for
    `username`. Used to silently re-authenticate someone whose
    st.session_state got wiped, without asking for their password again."""
    if not username or not token:
        return False
    user = _load_all().get(username)
    if user is None:
        return False
    stored_hash = user.get("remember_token_hash")
    expires = user.get("remember_token_expires")
    if not stored_hash or not expires or time.time() > expires:
        return False
    candidate_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored_hash, candidate_hash)


def clear_remember_token(username):
    """Invalidates this account's "remember me" token (called on explicit
    logout) so a leftover copy of the cookie can no longer log back in."""
    users = _load_all()
    if username in users:
        users[username].pop("remember_token_hash", None)
        users[username].pop("remember_token_expires", None)
        _save_all(users)
