"""Source adapters. Each adapter is a thin, cache-backed reader for one source.

Adding a source means adding a function here and declaring it in the rubric's
``scope`` (and the franchise's ``sources`` block). Nothing downstream needs to
change: the pipeline groups signals by ``(category, source)``.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse

from . import http

WIKI_API = "https://en.wikipedia.org/w/api.php"
TVMAZE_API = "https://api.tvmaze.com"

# Wikimedia's read API has no hard rate limit, but a full Star Trek + Doctor Who
# ingest is ~1,900 requests. A small pause keeps us a good citizen without
# materially slowing the run.
POLITE_PAUSE_SECONDS = 0.05


# --------------------------------------------------------------------- helpers
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(text: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _polite_pause() -> None:
    if POLITE_PAUSE_SECONDS:
        time.sleep(POLITE_PAUSE_SECONDS)


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
    """Return the prose of a plot-like section, including its sub-sections.

    Section names differ by wiki: Wikipedia uses "Plot", Tardis has both a
    spoiler-light "Synopsis" and a detailed "Plot", Memory Alpha has "Summary".
    They are tried in that order so the richest section wins, and a level-2
    "Plot" split into "=== Part I ===" / "=== Part II ===" sub-sections — as on
    multi-part episode articles such as "Emissary (Star Trek: Deep Space Nine)" —
    is captured whole rather than truncated to nothing, because the section runs
    to the next heading of the *same level*.
    """
    for name in ("plot", "plot summary", "synopsis", "summary", "story"):
        pattern = re.compile(
            rf"^(={{2,}})\s*{re.escape(name)}\s*\1\s*$(.*?)(?=^\1[^=].*?\1\s*$|\Z)",
            re.I | re.M | re.S,
        )
        match = pattern.search(wikitext)
        if not match:
            continue
        body = strip_wikitext(match.group(2))
        if body:
            return body
    return None


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


# ------------------------------------------------------------------ MediaWiki
# Wikipedia and the Fandom franchise wikis (Memory Alpha for Star Trek, Tardis
# for Doctor Who) all speak the same MediaWiki API, so one adapter serves them
# all; only the endpoint, the search hint and the section names differ. Memory
# Alpha in particular covers every Star Trek episode, including the streaming
# era that Wikipedia has no per-episode articles for.
def _api_query(api_url: str, params: dict, *, refresh: bool = False) -> dict:
    params = {"format": "json", "formatversion": 2, **params}
    url = api_url + "?" + urllib.parse.urlencode(params)
    return http.fetch(url, refresh=refresh)


def _mediawiki_search(api_url: str, query: str, *, limit: int = 5, refresh: bool = False) -> list[str]:
    """Cheap title-only search (snippets, no article content)."""
    data = _api_query(
        api_url,
        {"action": "query", "list": "search", "srsearch": query, "srlimit": limit},
        refresh=refresh,
    )
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


_WORDS = re.compile(r"[a-z0-9]+")


def _candidate_preference(candidate: str, hint: str, rank: int) -> tuple:
    """Rank candidates: prefer the one whose disambiguator shares most with the hint.

    Counting shared words — not merely testing for one — matters on Tardis: "An
    Unearthly Child" returns both "(audio story)" and "(TV story)", and against
    the hint "TV story" the latter shares two words and the former one.
    """
    disambiguator = candidate[len(base_title(candidate)) :]
    shared = len(
        set(_WORDS.findall(disambiguator.casefold())) & set(_WORDS.findall(hint.casefold()))
    )
    return (-shared, rank)


def _mediawiki_content(api_url: str, titles: list[str], *, refresh: bool = False) -> dict[str, str]:
    """Fetch wikitext for many titles at once (MediaWiki allows up to 50 per query).

    This is the difference between a ~110-minute ingest and a ~20-minute one: the
    search *generator* returns full content for every hit, so a 5-hit search
    downloaded five whole articles per episode. Here the search is title-only and
    the content is fetched in batches instead.
    """
    out: dict[str, str] = {}
    batch = 20
    for start in range(0, len(titles), batch):
        chunk = titles[start : start + batch]
        data = _api_query(
            api_url,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "redirects": 1,
                "titles": "|".join(chunk),
            },
            refresh=refresh,
        )
        query = data.get("query", {})
        pages = query.get("pages") or []
        pages = list(pages.values()) if isinstance(pages, dict) else list(pages)
        for page in pages:
            if page.get("missing"):
                continue
            revs = page.get("revisions") or []
            if revs:
                out[page["title"]] = revs[0]["slots"]["main"]["content"]
        # A requested title may resolve through a redirect or normalisation; alias
        # the requested name to the fetched article so lookups by the searched
        # title still find their content.
        for alias in (query.get("redirects") or []) + (query.get("normalized") or []):
            src, dst = alias.get("from"), alias.get("to")
            if src and dst in out:
                out[src] = out[dst]
        _polite_pause()
    return out


_BASE_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_BASE_PART = re.compile(r"\s*,\s*part\s+[ivx0-9]+\b.*$", re.I)


def base_title(text: str) -> str:
    """The *identity* of an episode title: drop a trailing disambiguator and a
    ", Part N" suffix, so a TVmaze episode title can be matched against a wiki
    article title ("Code of Honor" == "Code of Honor (Star Trek: The Next
    Generation)")."""
    text = (text or "").strip()
    text = _BASE_PAREN.sub("", text)
    text = _BASE_PART.sub("", text)
    return text.strip().strip('"').strip()


def same_work(episode_title: str, article_title: str) -> bool:
    """True when an article title is plausibly *about* the episode.

    This is the guard against the failure mode that makes scores untrustworthy:
    a search always returns *something*, and accepting the first hit with a Plot
    section resolved Lower Decks' "Second Contact" to a Next Generation episode
    and Doctor Who's "Rose" to "The Star Beast". An article is accepted only if
    its title is the episode's title, with or without a disambiguator.
    """
    return base_title(episode_title).casefold() == base_title(article_title).casefold()


def mediawiki_plots(
    api_url: str, titles: list[str], *, hint: str = "episode", refresh: bool = False
) -> dict[str, dict]:
    """Map a work's title -> {"title", "text", ...} for works with a plot section.

    Two phases (see ``_mediawiki_content``): a title-only search per work, then
    one batched content fetch for every candidate. A work is accepted against the
    first candidate that is genuinely about it (``same_work``) and has a plot
    section; otherwise it is left unresolved rather than filled with the wrong
    episode's plot.

    ``hint`` is the disambiguating tail of the search query. It matters:
    Wikipedia needs "Doctor Who episode" to resolve "Rose" to "Rose (Doctor Who
    episode)", and Tardis needs "TV story" to prefer "(TV story)" over
    "(audio story)".
    """
    candidates: dict[str, list[str]] = {}
    for title in titles:
        # Search on the *base* title: "Emissary, Part I" resolves only when
        # queried as "Emissary", and a film/part parenthetical only adds noise.
        candidates[title] = _mediawiki_search(
            api_url, f"{base_title(title)} {hint}".strip(), refresh=refresh
        )
        _polite_pause()

    wanted = sorted({c for hits in candidates.values() for c in hits})
    content = _mediawiki_content(api_url, wanted, refresh=refresh)

    out: dict[str, dict] = {}
    for title, hits in candidates.items():
        ordered = sorted(range(len(hits)), key=lambda i: _candidate_preference(hits[i], hint, i))
        for i in ordered:
            candidate = hits[i]
            if not same_work(title, candidate):
                continue
            wikitext = content.get(candidate)
            if not wikitext:
                continue
            plot = plot_section(wikitext)
            if not plot:
                continue
            out[title] = {
                "title": candidate,
                "text": plot,
                "textKind": "plot-section",
                "resolvedBy": "search",
            }
            break
    return out


def mediawiki_plot_by_title(api_url: str, title: str, *, refresh: bool = False) -> dict | None:
    """Fetch a known article by exact title (used for films, where the canonical
    article title is pinned in franchise.json). No search, so no ambiguity."""
    content = _mediawiki_content(api_url, [title], refresh=refresh)
    wikitext = content.get(title)
    if not wikitext:
        return None
    plot = plot_section(wikitext)
    if not plot:
        return None
    return {"title": title, "text": plot, "textKind": "plot-section", "resolvedBy": "title"}


def wikipedia_plots(titles: list[str], *, hint: str = "episode", refresh: bool = False) -> dict[str, dict]:
    return mediawiki_plots(WIKI_API, titles, hint=hint, refresh=refresh)


def wikipedia_plot_by_title(title: str, *, refresh: bool = False) -> dict | None:
    return mediawiki_plot_by_title(WIKI_API, title, refresh=refresh)
