"""
Simple username/password accounts, with each account's personal watchlist
stored alongside it -- all in one JSON blob, data/users.json.

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
  the password itself) is saved. Still, this is a from-scratch,
  non-audited implementation, not a vetted auth library -- don't reuse a
  password here that matters elsewhere (banking, email, etc).

Storage backend -- two modes, picked automatically (2026-09-11):
- **Local file** (data/users.json), the original/default mode, used
  whenever no [github_data] section exists in Streamlit secrets. Fine when
  running locally (the file just persists on disk like any other file),
  but on Streamlit Community Cloud this does NOT survive a redeploy or the
  app "sleeping" after inactivity and waking back up -- the container's
  local disk is rebuilt fresh from GitHub each time, and data/ was never
  part of the git repo (see .gitignore) since it holds password hashes.
- **GitHub-backed** (recommended for anything deployed on Streamlit
  Community Cloud that needs accounts to actually persist): reads/writes
  users.json in a SEPARATE, PRIVATE GitHub repo via GitHub's Contents API,
  using a token kept in Streamlit secrets -- never in this file, never in
  git. Chosen over a proper hosted database because it reuses
  infrastructure already in use for this project (a GitHub account) rather
  than adding a new service to sign up for and learn, matching the same
  "avoid unnecessary external setup" reasoning that got Google OAuth
  replaced with this file in the first place. Turned on by adding a
  [github_data] section to .streamlit/secrets.toml (locally) or the app's
  Secrets settings (Streamlit Community Cloud) -- see
  .streamlit/secrets.toml.example for the exact format and setup steps.
  MUST be a private repo, and MUST be a different repo than the app's own
  code repo if that one is public -- writing password hashes into a public
  repo's history would defeat the point of hashing them at all.

A user's personal watchlist is stored as just a list of ticker CODES
(e.g. ["0050", "2330"]) -- which of the tickers already known to the app
(config.WATCHLIST, the shared/global registry every fetch script reads)
THIS account wants to see. It does NOT duplicate any market data --
prices, dividends, news, and market value are fetched once and shared
across every account, since there's no reason to hit TWSE/yfinance
separately per person. Logging in only changes what each account is
looking AT, never what data gets collected.

IMPORTANT: config.WATCHLIST itself (the shared registry -- which codes
exist for ANY account to add) is a SEPARATE file, data/watchlist.json,
loaded/saved by config.py, not this file. It has its own copy of the
local-vs-GitHub dual-mode logic (added 2026-09-11, after this file's
GitHub-backed mode alone turned out to be insufficient: accounts and
their personal code LISTS persisted fine, but a brand-new ticker added
via the sidebar -- which gets appended to config.WATCHLIST -- still
vanished on the next Streamlit Cloud redeploy/wake, silently dropping it
back out of every account's personal watchlist even though their own
saved code list still correctly included it). Both this file and
config.py share the actual GitHub read/write plumbing via
github_json_store.py rather than duplicating it twice.
"""

import hashlib
import hmac
import json
import os
import secrets as _secrets
import time

import streamlit as st

import github_json_store

USERS_FILE = os.path.join("data", "users.json")
PBKDF2_ITERATIONS = 200_000
REMEMBER_TOKEN_DAYS = 30

# Re-exported so existing call sites (app.py) that catch
# `user_store.GitHubStorageError` keep working unchanged -- the actual
# GitHub read/write logic now lives in github_json_store.py, shared with
# config.py's watchlist storage (see that module's docstring).
GitHubStorageError = github_json_store.GitHubStorageError


def _github_config():
    """Reads GitHub-backed storage settings from Streamlit secrets --
    [github_data] with token/repo/path keys (see
    .streamlit/secrets.toml.example). Returns None if that section isn't
    configured, which is the signal every function below uses to fall
    back to the original local-file behavior -- so this feature is purely
    opt-in and local development/testing without a token keeps working
    exactly as before."""
    try:
        section = st.secrets["github_data"]
        return section["token"], section["repo"], section.get("path", "users.json")
    except Exception:
        return None


def _load_all_local():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all_local(users):
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _load_all():
    """Every account/watchlist function below calls this -- never
    _load_all_local/_github_get directly -- so this is the one place that
    decides which storage backend is active.

    Deliberately lets GitHubStorageError propagate rather than swallowing
    it to {} (an earlier version did that, and it was a mistake -- a
    broken token/repo then silently looked exactly like "no accounts
    exist yet", which just produces a confusing "wrong username or
    password" on login instead of the actual, fixable problem). Callers in
    app.py catch GitHubStorageError specifically and show its message with
    st.error()."""
    cfg = _github_config()
    if cfg is None:
        return _load_all_local()
    token, repo, path = cfg
    users, _ = github_json_store.read_json(token, repo, path, default={})
    return users


def _save_all(users):
    cfg = _github_config()
    if cfg is None:
        _save_all_local(users)
        return
    token, repo, path = cfg
    github_json_store.save_with_retry(token, repo, path, users, commit_message="Update users.json")


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
