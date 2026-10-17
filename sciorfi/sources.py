"""Source adapters. Each adapter is a thin, cache-backed reader for one source.

Adding a source means adding a function here and declaring it in the rubric's
``scope`` (and the franchise's ``sources`` block). Nothing downstream needs to
change: the pipeline groups signals by ``(category, source)``.
"""
from __future__ import annotations

import html
import re
import urllib.parse

from . import http

WIKI_API = "https://en.wikipedia.org/w/api.php"
TVMAZE_API = "https://api.tvmaze.com"


# --------------------------------------------------------------------- helpers
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(text: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def strip_wikitext(text: str) -> str:
    """Best-effort wikitext -> plain prose. Good enough for lexicon counting."""
    text = re.sub(r"<ref[^>]*?/>", " ", text, flags=re.I)
    text = re.sub(r"<ref.*?</ref>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)  # innermost templates, twice below
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.S)  # tables
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # [[a|b]] -> b
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^[#*:;].*$", " ", text, flags=re.M)  # list/indent lines
    return _WS.sub(" ", text).strip()


def plot_section(wikitext: str) -> str | None:
    """Return the prose of a Plot/Synopsis section, if the article has one."""
    pattern = re.compile(
        r"^==+\s*(plot|synopsis|plot summary|story)\s*==+\s*$(.*?)(?=^==+[^=\n].*==+\s*$|\Z)",
        re.I | re.M | re.S,
    )
    match = pattern.search(wikitext)
    if not match:
        return None
    body = strip_wikitext(match.group(2))
    return body or None


# --------------------------------------------------------------------- TVmaze
def tvmaze_episodes(show_id: int, *, refresh: bool = False) -> list[dict]:
    raw = http.fetch(f"{TVMAZE_API}/shows/{show_id}/episodes", refresh=refresh)
    episodes = []
    for ep in raw:
        if ep.get("season") in (None, 0):  # specials / unnumbered
            continue
        episodes.append(
            {
                "season": int(ep["season"]),
                "episode": int(ep["number"] or 0),
                "title": ep.get("name") or "",
                "airDate": ep.get("airdate") or None,
                "sourceIds": {"tvmaze": ep.get("id")},
                "text": _plain(ep.get("summary") or ""),
                "textKind": "summary",
            }
        )
    episodes.sort(key=lambda e: (e["season"], e["episode"]))
    return episodes


# ------------------------------------------------------------------ Wikipedia
def _wiki_query(params: dict, *, refresh: bool = False) -> dict:
    params = {"format": "json", "formatversion": 2, **params}
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    return http.fetch(url, refresh=refresh)


def wikipedia_plots(titles: list[str], *, refresh: bool = False) -> dict[str, dict]:
    """Map episode title -> {"title", "text", "resolvedBy"} for titles with a Plot section.

    Uses the search *generator* so that resolution and content retrieval happen in
    one request per title, and so that ambiguous titles ("Arena", "Miri") resolve
    to the episode article rather than to a film or a disambiguation page. A
    candidate is accepted only if it actually has a Plot section.
    """
    out: dict[str, dict] = {}
    for title in titles:
        data = _wiki_query(
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{title} Star Trek: The Original Series episode",
                "gsrlimit": 5,
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
            },
            refresh=refresh,
        )
        pages_obj = data.get("query", {}).get("pages") or []
        pages = list(pages_obj.values()) if isinstance(pages_obj, dict) else list(pages_obj)
        pages.sort(key=lambda p: p.get("index", 99))
        for page in pages:
            revs = page.get("revisions") or []
            if not revs:
                continue
            plot = plot_section(revs[0]["slots"]["main"]["content"])
            if not plot:
                continue
            out[title] = {
                "title": page["title"],
                "text": plot,
                "textKind": "plot-section",
                "resolvedBy": "search",
            }
            break
    return out
