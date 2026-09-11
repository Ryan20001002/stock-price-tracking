"""
Small, generic helper for reading/writing files in a GitHub repo via the
Contents API -- JSON documents, arbitrary text (CSVs), and directory
listings. Factored out (2026-09-11) so user_store.py (data/users.json --
accounts), config.py (data/watchlist.json -- the shared ticker registry),
and market_data_sync.py (data/<category>/*.csv -- the actual fetched
market data, added the same day after a full institutional-data backfill
was found to vanish on a container restart) can all persist on Streamlit
Community Cloud the same way, instead of duplicating this logic three
times. None of those three import each other; all three import this one.

Nothing in here knows anything about accounts, watchlists, or stock
data -- it just reads/writes files (JSON or plain text) and lists
directories at a given path in a given repo, given a token.
"""

import base64
import json

import requests

GITHUB_API_BASE = "https://api.github.com"


class GitHubStorageError(RuntimeError):
    """Raised whenever GitHub-backed storage can't be reached or isn't set
    up correctly (bad token, wrong permissions, wrong repo name, etc).
    Carries a ready-to-display Mandarin message so callers can show it
    with st.error() -- catching this SPECIFIC type (not a bare Exception)
    is what lets a real bug elsewhere still crash loudly instead of being
    silently swallowed."""


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _diagnose(action, exc):
    """Turns a requests.HTTPError from the GitHub API into a
    GitHubStorageError with a specific, actionable Mandarin message."""
    status = exc.response.status_code if exc.response is not None else None
    detail = ""
    if exc.response is not None:
        try:
            detail = exc.response.json().get("message", "")
        except Exception:
            detail = exc.response.text[:200]
    if status == 401:
        reason = "GitHub token 無效或已過期 -- 請確認貼進 Secrets 的 token 前後沒有多餘的空白或引號，且尚未過期。"
    elif status == 403:
        reason = ("GitHub token 沒有這個 repo 的寫入權限 -- 請確認 Fine-grained token 的 "
                   "Repository access 有勾選到這個私有 repo，且 Contents 權限設為 Read and write。")
    elif status == 404:
        reason = "找不到這個 repo -- 請確認 secrets 裡 repo 的值是「GitHub帳號/repo名稱」的格式，且拼字完全正確。"
    else:
        reason = f"GitHub API 回應了非預期的錯誤（狀態碼 {status}）。"
    message = f"{action}時發生錯誤：{reason}"
    if detail:
        message += f"（GitHub 回應：{detail}）"
    return GitHubStorageError(message)


def _read_raw(token, repo, path):
    """Returns (content_str, sha) for a FILE (not a directory) path, or
    (None, None) if it doesn't exist yet. `sha` is GitHub's current
    version marker for the file -- required to overwrite it without
    clobbering a concurrent write. NOTE: GitHub also returns 404 for a
    private repo the token can't see at all, so a persistent "file not
    found" right after setup can mean that, not just a genuinely missing
    file; verify the repo name and token's repo access if that happens."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=10)
    except requests.RequestException as e:
        raise GitHubStorageError(f"連線 GitHub 失敗：{e}") from e
    if resp.status_code == 404:
        return None, None
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise _diagnose("讀取資料", e) from e
    payload = resp.json()
    if isinstance(payload, list):
        raise GitHubStorageError(f"讀取資料時發生錯誤：{path} 是資料夾，不是檔案。")
    content = base64.b64decode(payload["content"]).decode("utf-8")
    return content, payload["sha"]


def _write_raw(token, repo, path, content_str, sha, commit_message):
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
    body = {
        "message": commit_message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha
    try:
        resp = requests.put(url, headers=_headers(token), json=body, timeout=10)
    except requests.RequestException as e:
        raise GitHubStorageError(f"連線 GitHub 失敗：{e}") from e
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 409:
            raise  # a stale sha -- let the caller's retry loop handle this one specifically
        raise _diagnose("寫入資料", e) from e


def read_json(token, repo, path, default):
    """Returns (data, sha). (default, None) if the file doesn't exist
    yet."""
    content, sha = _read_raw(token, repo, path)
    if content is None:
        return default, None
    return (json.loads(content) if content.strip() else default), sha


def write_json(token, repo, path, data, sha, commit_message="Update data"):
    _write_raw(token, repo, path, json.dumps(data, ensure_ascii=False, indent=2), sha, commit_message)


def save_with_retry(token, repo, path, data, commit_message="Update data"):
    """Read-modify-write with one retry on a 409 (someone else wrote in
    between our read and our write) -- refetches the current sha once and
    tries again rather than losing the write outright. Not built for real
    concurrent-write volume, but plenty for this app's traffic."""
    for attempt in range(2):
        _, sha = read_json(token, repo, path, default=None)
        try:
            write_json(token, repo, path, data, sha, commit_message=commit_message)
            return
        except requests.HTTPError as e:
            if attempt == 0 and e.response is not None and e.response.status_code == 409:
                continue
            raise


def read_text(token, repo, path, default=None):
    """Like read_json, but for a plain-text file (e.g. a CSV) -- no JSON
    parsing. Returns (text, sha), or (default, None) if the file doesn't
    exist yet."""
    content, sha = _read_raw(token, repo, path)
    if content is None:
        return default, None
    return content, sha


def write_text(token, repo, path, content_str, sha, commit_message="Update data"):
    _write_raw(token, repo, path, content_str, sha, commit_message)


def save_text_with_retry(token, repo, path, content_str, commit_message="Update data"):
    """Text-file equivalent of save_with_retry -- same read-modify-write-
    with-one-retry-on-409 shape, just writing raw text (a CSV's contents)
    instead of a JSON-encoded object."""
    for attempt in range(2):
        _, sha = read_text(token, repo, path)
        try:
            write_text(token, repo, path, content_str, sha, commit_message=commit_message)
            return
        except requests.HTTPError as e:
            if attempt == 0 and e.response is not None and e.response.status_code == 409:
                continue
            raise


def list_dir(token, repo, path):
    """Returns a list of filenames (not full paths, and only regular
    files -- not subdirectories) directly inside this directory of the
    repo, or [] if the directory doesn't exist yet (nothing has ever been
    pushed there) or is genuinely empty. Raises GitHubStorageError on any
    other failure (bad token, wrong permissions, etc -- same diagnostics
    as everything else in this module)."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=10)
    except requests.RequestException as e:
        raise GitHubStorageError(f"連線 GitHub 失敗：{e}") from e
    if resp.status_code == 404:
        return []
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise _diagnose("列出資料夾內容", e) from e
    payload = resp.json()
    if not isinstance(payload, list):
        return []  # `path` pointed at a file, not a directory -- shouldn't happen given our own path scheme
    return [entry["name"] for entry in payload if entry.get("type") == "file"]
