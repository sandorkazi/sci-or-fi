"""Standard-library HTTP with an on-disk cache.

Raw responses are cached under ``cache/`` (git-ignored), because several of the
sources we read are licensed in ways that forbid redistributing their text.
Only *derived* values (counts, densities, IDs) are committed — see
docs/redesign.md, "Licensing constraint".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

USER_AGENT = "sci-or-fi/0.1 (+https://github.com/masu33333/sci-or-fi; research)"
DEFAULT_TIMEOUT = 45
RETRIES = 3


class FetchError(RuntimeError):
    pass


def _cache_file(url: str, body: str | None) -> Path:
    key = config.digest(url + ("|" + body if body else ""), length=20)
    sub = ".".join(urllib.parse.urlparse(url).netloc.split(".")[-2:]) or "other"
    return config.CACHE / sub / f"{key}.json"


def fetch_text(url: str, *, refresh: bool = False, max_age_days: int = 30) -> str:
    """Fetch a non-JSON asset (used only for vendored third-party assets)."""
    path = config.CACHE / "assets" / (config.digest(url, length=20) + ".txt")
    if path.exists() and not refresh and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
        return path.read_text("utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
        text = resp.read().decode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return text


def fetch(url: str, *, body: str | None = None, refresh: bool = False, max_age_days: int = 30) -> dict:
    """GET (or POST if ``body``) a URL, returning parsed JSON, cached on disk."""
    path = _cache_file(url, body)
    if path.exists() and not refresh:
        age = time.time() - path.stat().st_mtime
        if age < max_age_days * 86400:
            return json.loads(path.read_text("utf-8"))

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = body.encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), "utf-8")
            return payload
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            break
        except Exception as exc:  # noqa: BLE001 - network breadth is the point
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            break
    raise FetchError(f"failed to fetch {url}: {last}") from last
