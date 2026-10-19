"""Pipeline stages: ingest (snapshot) -> signals -> score.

Each stage reads the previous stage's committed output, so the rubric or the
scope can be changed without re-hitting a network source.
"""
from __future__ import annotations

import math
import re
import statistics

from . import config, lexicon, sources

# Assigned to series that do not pin a colour, so a franchise with 15 series and
# 14 films stays legible without hand-picking 29 hex values.
PALETTE = [
    "#378ADD", "#D85A30", "#1D9E75", "#7F77DD", "#EF9F27", "#D4537E",
    "#5DCAA5", "#AFA9EC", "#BA7517", "#E24B4A", "#85B7EB", "#F0997B",
    "#888780", "#ED93B1", "#639922", "#C77DBA", "#4C9F70", "#B98A3E",
]


def colour_for(meta: dict, index: int) -> str:
    return meta.get("color") or PALETTE[index % len(PALETTE)]


# ------------------------------------------------------- classic-era grouping
# Classic Doctor Who (1963–1989) was broadcast as 25-minute *parts* — TVmaze lists
# all 700 of them, each with a median 31-word summary — but it was written,
# produced and is remembered as *serials*: one story told over several weeks. The
# sources that carry it (Tardis, Wikipedia) do the same: there is one article per
# story, with sub-headings per part, not one article per part. Measuring parts
# therefore means measuring a fifth of a story's text against a whole story's plot,
# and it puts 700 works on the thinnest text in the corpus. A series that declares
# ``grouping: "serial"`` is collapsed to one work per serial at ingest.
_PART_WORDS = {
    w: i
    for i, w in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split(),
        1,
    )
}
# TVmaze's titling is inconsistent in three ways, all observed in the 700 parts:
_SERIAL_PATTERNS = (
    # "The Trial of a Time Lord, Part Two (The Mysterious Planet)" — 14 parts
    re.compile(r"^(?P<serial>.+?),\s*Part\s+(?P<num>[A-Za-z0-9]+)\s*\((?P<part>.*)\)\s*$"),
    # "The Cave of Skulls (An Unearthly Child, Part Two)" — 117 parts
    re.compile(r"^(?P<part>.*?)\s*\((?P<serial>.+?),\s*Part\s+(?P<num>[A-Za-z0-9]+)\)\s*$"),
    # "The Savages, Part One" — 568 parts
    re.compile(r"^(?P<serial>.+?),\s*Part\s+(?P<num>[A-Za-z0-9]+)\s*$"),
)


def parse_serial_part(title: str) -> tuple[str, int] | None:
    """(serial name, part number) from a TVmaze part title, or None if unlabelled."""
    for pattern in _SERIAL_PATTERNS:
        match = pattern.match(title or "")
        if not match:
            continue
        token = match.group("num").casefold()
        number = _PART_WORDS.get(token)
        if number is None:
            try:
                number = int(token)
            except ValueError:
                return None
        return match.group("serial").strip(), number
    return None


def group_parts_into_serials(parts: list[dict]) -> list[dict]:
    """Collapse TVmaze parts into one work per serial, numbered by broadcast order.

    A part with no recognisable ", Part N" label (``Mission to the Unknown``, a
    single-episode serial) becomes a one-part serial named after itself. Every
    part of a serial carries the same TVmaze summary, so the first part's summary
    is the serial's summary and nothing is lost by keeping the first part's id.
    """
    grouped: dict[tuple[int, str], list[dict]] = {}
    for part in parts:
        parsed = parse_serial_part(part["title"])
        serial, number = parsed if parsed else (part["title"], 1)
        grouped.setdefault((part["season"], serial), []).append({**part, "partNumber": number})

    serials: list[dict] = []
    for (season, serial), members in grouped.items():
        members.sort(key=lambda m: (m["partNumber"], m["episode"]))
        first = members[0]
        serials.append(
            {
                **first,                       # keeps the first part's id, airdate and summary
                "season": season,
                "title": serial,               # the wiki is searched on the serial name, not a part title
                "parts": len(members),
                "partTitles": [m["title"] for m in members],
                "partIds": [m["sourceIds"].get("tvmaze") for m in members],
            }
        )

    serials.sort(key=lambda s: (s["season"], s["episode"]))
    position: dict[int, int] = {}
    for serial in serials:
        position[serial["season"]] = position.get(serial["season"], 0) + 1
        serial["episode"] = position[serial["season"]]
    return serials


def _split_era(raw: list[dict], series: dict) -> list[dict]:
    """Keep the works in one era's half-open ``(season, serial)`` range.

    Eras cut across broadcast seasons: Hartnell's last serial and Troughton's first
    share season 4, and Davison's last and Colin Baker's first share season 21. The
    partition is therefore on the grouped serial, never on the season. An era that
    starts mid-season renumbers its own works from 1, so every (series, season) still
    has a contiguous 1..n and the chart's season label stays the broadcast season.
    """
    start, until = series["from"], series["until"]
    kept = [w for w in raw if (w["season"], w["episode"]) >= start and (until is None or (w["season"], w["episode"]) < until)]
    position: dict[int, int] = {}
    out = []
    for work in kept:
        position[work["season"]] = position.get(work["season"], 0) + 1
        out.append({**work, "episode": position[work["season"]]})
    return out


# ------------------------------------------------------------------- ingest
def build_snapshot(franchise_id: str, date: str, *, refresh: bool = False) -> dict:
    meta = config.franchise_meta(franchise_id)
    episodes: list[dict] = []
    provenance: list[dict] = []

    for series in config.series_entries(meta):
        sid = series["id"]
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if tvmaze_id is None:
            provenance.append({"source": "tvmaze", "series": sid, "skipped": "no tvmaze id"})
            continue
        raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
        grouping = series.get("grouping")
        if grouping == "serial":
            raw = group_parts_into_serials(raw)
        if series.get("from"):
            raw = _split_era(raw, series)
        if grouping == "serial":
            provenance.append(
                {
                    "source": "tvmaze",
                    "series": sid,
                    "id": tvmaze_id,
                    "grouping": "serial",
                    "serials": len(raw),
                    **(
                        {"splitFrom": series["splitFrom"], "from": list(series["from"]),
                         "until": list(series["until"]) if series.get("until") else None}
                        if series.get("splitFrom")
                        else {}
                    ),
                }
            )
        else:
            provenance.append({"source": "tvmaze", "series": sid, "id": tvmaze_id, "episodes": len(raw)})
        for ep in raw:
            air = ep.get("airDate")
            episodes.append(
                {
                    "id": f"{franchise_id}:{sid}:S{ep['season']:02d}E{ep['episode']:02d}",
                    "series": sid,
                    "kind": "serial" if ep.get("parts") else "episode",
                    "season": ep["season"],
                    "episode": ep["episode"],
                    "title": ep["title"],
                    "airDate": air,
                    "year": int(air[:4]) if air else None,
                    "sourceIds": ep["sourceIds"],
                    **({"parts": ep["parts"]} if ep.get("parts") else {}),
                }
            )

    # Films have no TVmaze presence, so their metadata is pinned in franchise.json
    # and their plot text is fetched from the wikis by exact article title (see
    # extract_signals), never by search.
    for film in meta.get("films", []):
        if not film.get("wikipedia"):
            provenance.append({"source": "wikipedia", "film": film["id"], "skipped": "no article pinned"})
            continue
        episodes.append(
            {
                "id": f"{franchise_id}:{film['id']}:S01E01",
                "series": film["id"],
                "kind": "film",
                "season": 1,
                "episode": 1,
                "title": film["name"],
                "airDate": None,
                "year": film.get("year"),
                "sourceIds": {},
                "wikipedia": film["wikipedia"],
                "wikiTitles": film.get("wikiTitles", {}),
            }
        )
        provenance.append({"source": "wikipedia", "film": film["id"], "article": film["wikipedia"]})

    episodes.sort(key=lambda e: (e["series"], e["season"], e["episode"]))
    # Note: source *text* is never written here. Snapshots carry facts and
    # identifiers only; see docs/redesign.md ("Licensing constraint").
    snapshot = {
        "franchise": franchise_id,
        "snapshotDate": date,
        "provenance": provenance,
        "episodeCount": len(episodes),
        "episodes": episodes,
    }
    config.write_json(config.snapshot_path(franchise_id, date), snapshot)
    return snapshot


def _wiki_targets(series: dict, scope_sources: list[str], declared: dict) -> list[tuple[str, str, str]]:
    """(source_id, api_url, search hint) for each MediaWiki source in scope.

    Wikipedia's hint is per series (franchise.json ``wikiHint``, because "Rose"
    needs "Doctor Who episode" and "The Man Trap" needs "Star Trek: The Original
    Series episode"); each franchise wiki carries one hint on the source itself.
    """
    targets: list[tuple[str, str, str]] = []
    if "wikipedia" in scope_sources and "wikipedia" in declared:
        hint = series.get("wikiHint") or f'{series["name"]} episode'
        targets.append(("wikipedia", sources.WIKI_API, hint))
    for source_id, spec in declared.items():
        if source_id in scope_sources and spec.get("api"):
            targets.append((source_id, spec["api"], spec.get("hint") or "episode"))
    return targets


# ------------------------------------------------------------------ signals
def extract_signals(
    franchise_id: str, date: str, rubric: dict, *, refresh: bool = False, on_progress=None
) -> list[dict]:
    snapshot = config.read_json(config.snapshot_path(franchise_id, date))
    episodes = snapshot["episodes"]
    meta = config.franchise_meta(franchise_id)
    compiled = lexicon.compile_axes(rubric)
    # Entities are curated per series. Coding a series against a franchise-wide
    # list would import another series' gods (TOS "Apollo" is not TNG's Apollo),
    # so a series with no list is coded lexicon-only instead.
    all_entities = config.franchise_entities(franchise_id)
    shared_entities = [e for e in all_entities if not e.get("series")]
    by_series: dict[str, list[dict]] = {}
    for entity in all_entities:
        if entity.get("series"):
            by_series.setdefault(entity["series"], []).append(entity)
    work_series = config.series_entries(meta)
    work_ids = {s["id"] for s in work_series} | {f["id"] for f in meta.get("films", [])}
    # An era inherits its container's curated list: every classic Doctor is coded
    # against the same 38 entities, so splitting the era does not change Fi's coder.
    entity_key = {s["id"]: s.get("entityGroup") or s["id"] for s in work_series}
    compiled_entities_by_series = {
        sid: lexicon.compile_entities(by_series.get(entity_key.get(sid, sid), []) + shared_entities)
        for sid in work_ids
    }
    scope_sources = config.effective_sources(franchise_id, rubric)
    declared_sources = meta.get("sources", {})

    # Source text is fetched here and held in memory only: it is never committed.
    texts: dict[str, dict[str, dict]] = {}
    for series in work_series:
        sid = series["id"]
        group = [e for e in episodes if e["series"] == sid]
        if not group:
            continue
        titles = [e["title"] for e in group]
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if "tvmaze" in scope_sources and tvmaze_id is not None:
            raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
            # Look up by episode id, not by (season, episode) number: a grouped
            # series numbers serials 1..n within the season, which no longer
            # matches TVmaze's own part numbering.
            by_id = {e["sourceIds"]["tvmaze"]: e for e in raw if e["sourceIds"].get("tvmaze")}
            for ep in group:
                hit = by_id.get((ep.get("sourceIds") or {}).get("tvmaze"))
                if hit and hit["text"]:
                    texts.setdefault(ep["id"], {})["tvmaze"] = {"text": hit["text"], "kind": hit["textKind"]}
        for source_id, api_url, hint in _wiki_targets(series, scope_sources, declared_sources):
            plots = sources.mediawiki_plots(api_url, titles, hint=hint, refresh=refresh)
            if on_progress:
                on_progress(f"{sid} {source_id}", len(plots), len(group))
            for ep in group:
                hit = plots.get(ep["title"])
                if hit:
                    texts.setdefault(ep["id"], {})[source_id] = {
                        "text": hit["text"],
                        "kind": hit["textKind"],
                    }

    # Films: no search. Wikipedia is pinned by article title; the franchise wikis
    # are looked up by the film's own name — Memory Alpha's film articles carry the
    # film title — with a per-film override where that is not so (the 1996 Doctor
    # Who film is Tardis' "Doctor Who (TV story)", not "Doctor Who (1996 ...)").
    film_wiki_sources = [("wikipedia", sources.WIKI_API)] + [
        (sid, spec["api"]) for sid, spec in declared_sources.items() if spec.get("api")
    ]
    film_scope = [sid for sid, _ in film_wiki_sources if sid in scope_sources]
    for ep in [e for e in episodes if e.get("kind") == "film"]:
        overrides = ep.get("wikiTitles") or {}
        for source_id, api_url in film_wiki_sources:
            if source_id not in scope_sources:
                continue
            title = (
                ep.get("wikipedia")
                if source_id == "wikipedia"
                else (overrides.get(source_id) or ep.get("title"))
            )
            if not title:
                continue
            hit = sources.mediawiki_plot_by_title(api_url, title, refresh=refresh)
            if hit:
                texts.setdefault(ep["id"], {})[source_id] = {"text": hit["text"], "kind": hit["textKind"]}
        if on_progress:
            on_progress(f"{ep['series']} films", len(texts.get(ep["id"], {})), len(film_scope))

    rows: list[dict] = []
    for ep in episodes:
        for source_id, payload in texts.get(ep["id"], {}).items():
            if source_id not in scope_sources:
                continue
            compiled_entities = compiled_entities_by_series.get(ep["series"], {})
            coded = lexicon.code_text(
                payload["text"],
                rubric,
                compiled=compiled,
                compiled_entities=compiled_entities,
                entity_coding=bool(compiled_entities),
            )
            for axis, data in coded.items():
                if not data["words"]:
                    continue
                rows.append(
                    {
                        "episodeId": ep["id"],
                        "series": ep["series"],
                        "season": ep["season"],
                        "episode": ep["episode"],
                        "year": ep["year"],
                        "axis": axis,
                        "sub": data["subsignals"][0]["id"] if data["subsignals"] else None,
                        "category": "plot-summary",
                        "source": source_id,
                        "textKind": payload["kind"],
                        "words": data["words"],
                        "hits": data["hits"],
                        "density": data["density"],
                        "score": data["score"],
                        "confidence": lexicon.confidence(data["words"]),
                        "subsignals": data["subsignals"],
                    }
                )

    rows.sort(key=lambda r: (r["series"], r["season"], r["episode"], r["source"], r["axis"]))
    config.write_jsonl(config.signals_path(franchise_id, date), rows)
    return rows


# -------------------------------------------------------------------- score
def map_score(subsignals: list[dict], axis: str, rubric: dict) -> tuple[float, list[str]]:
    """Apply a rubric's mapping to stored measurements.

    Signals hold *measurements* (densities); the rubric holds the *mapping*
    (saturation + weight). Re-scoring under a different rubric version therefore
    needs no re-extraction. Returns (score, missing_subsignals).

    An axis is combined by weighted mean by default: several coders giving evidence
    about *the same* quantity should average. ``combine: "max"`` says the axis is
    instead composed of one dense coder plus one sparse *recovery* coder — the Fi
    entity list exists to see nominal myth ("Apollo") that the mythic lexicon is
    silent on, not to disagree with it about episodes the lexicon already catches.
    A mean would let the sparse coder's zeros pull down the dense coder's hits, so
    the axis takes the strongest evidence either coder sees. The rule lives in the
    rubric, so it is versioned and reviewable like everything else.
    """
    spec = {s["id"]: s for s in rubric["axes"][axis]["subsignals"]}
    present = {d["id"] for d in subsignals}
    missing = [sid for sid in spec if sid not in present]
    combine = rubric["axes"][axis].get("combine", "weighted")
    weighted = 0.0
    weight_sum = 0.0
    best = 0.0
    for item in subsignals:
        sub = spec.get(item["id"])
        if sub is None:
            continue  # a measurement this rubric does not use
        saturation = float(sub["saturation"])
        weight = float(sub.get("weight", 1.0))
        frac = min(1.0, item["density"] / saturation) if saturation else 0.0
        weighted += weight * frac
        weight_sum += weight
        best = max(best, frac)
    if not weight_sum:
        return 0.0, missing
    frac = best if combine == "max" else weighted / weight_sum
    scale = rubric["scale"]
    return round(scale["min"] + (scale["max"] - scale["min"]) * frac, 3), missing


def _ci95(values: list[float], rubric: dict) -> float | None:
    n = len(values)
    if n < 2:
        # A single measurement has no estimable spread. Returning 0.0 would draw
        # the least certain point as the smallest, most certain bubble — the exact
        # inversion of the truth — so the uncertainty is published as unknown.
        return None
    sd = statistics.stdev(values)
    ci = rubric["uncertainty"]["z"] * sd / math.sqrt(n)
    if n < rubric["uncertainty"]["thinSampleBelow"]:
        ci *= rubric["uncertainty"]["thinSampleInflation"]
    return ci


def _ranks(values: list[float]) -> list[float]:
    """Mid-rank percentile of each value on a 0..10 scale, ties sharing one rank.

    Used for the normalised view: it is invariant to the saturation constants and
    to the systematic level differences between sources, which is exactly what a
    raw density is not.
    """
    n = len(values)
    if not n:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        p = ((i + j) / 2 + 0.5) / n
        for k in range(i, j + 1):
            out[order[k]] = 10 * p
        i = j + 1
    return out


def score_scope(
    franchise_id: str,
    date: str,
    rubric: dict,
    *,
    scope_sources: list[str] | None = None,
    scope_series: list[str] | None = None,
) -> dict:
    rows = config.read_jsonl(config.signals_path(franchise_id, date))
    meta = config.franchise_meta(franchise_id)
    scope_sources = scope_sources or config.effective_sources(franchise_id, rubric)
    work_series = config.series_entries(meta)
    if scope_series is None:
        scope_series = [s["id"] for s in work_series] + [f["id"] for f in meta.get("films", [])]

    # A density is only a measurement if there was enough prose to measure. TVmaze
    # "summaries" have a 33-word median and 92% of them contain none of the lexicon
    # at all, so their zero is the absence of text, not the absence of science.
    # Below the floor a source still counts as *covering* an episode (that is
    # published in the text basis), it just does not contribute a score.
    min_words = int((rubric.get("measurement") or {}).get("minWords", 0))

    # (series, season, axis, source) -> per-episode scores
    buckets: dict[tuple, list[float]] = {}
    densities: dict[tuple, list[float]] = {}
    coverage_gaps: dict[tuple, set] = {}
    for row in rows:
        if row["source"] not in scope_sources or row["series"] not in scope_series:
            continue
        if row["words"] < min_words:
            continue
        score, missing = map_score(row.get("subsignals") or [], row["axis"], rubric)
        if missing:
            coverage_gaps.setdefault((row["series"], row["axis"]), set()).update(missing)
        key = (row["series"], row["season"], row["axis"], row["source"])
        buckets.setdefault(key, []).append(score)
        densities.setdefault(key, []).append(row["density"])

    seasons: dict[tuple, dict] = {}
    for (series, season, axis, source), values in buckets.items():
        entry = seasons.setdefault((series, season), {})
        axis_entry = entry.setdefault(axis, {"perSource": {}, "year": None})
        ci = _ci95(values, rubric)
        axis_entry["perSource"][source] = {
            "n": len(values),
            "mean": round(statistics.fmean(values), 3),
            "density": round(statistics.fmean(densities[(series, season, axis, source)]), 3),
            "ci95": None if ci is None else round(ci, 3),
        }

    # Percentile rank: where this season's figure for a source sits in the
    # franchise's whole distribution for that source. Published next to the
    # measured score so the chart can also be read on a scale that is guaranteed to
    # use its range — the measured scale is a density, and densities are bunched.
    for axis in ("sci", "fi"):
        for source in scope_sources:
            keys, values = [], []
            for (series, season), entry in seasons.items():
                part = ((entry.get(axis) or {}).get("perSource") or {}).get(source)
                if part:
                    keys.append((series, season, source))
                    values.append(part["mean"])
            for (series, season, src), rank in zip(keys, _ranks(values)):
                seasons[(series, season)][axis]["perSource"][src]["rank"] = round(rank, 3)

    # Combine sources into a single per-season figure, weighted by episode count.
    for (series, season), axes in seasons.items():
        for axis, axis_entry in axes.items():
            parts = list(axis_entry["perSource"].values())
            total_n = sum(p["n"] for p in parts)
            if not total_n:
                continue
            mean = sum(p["mean"] * p["n"] for p in parts) / total_n
            density = sum(p["density"] * p["n"] for p in parts) / total_n
            if any(p["ci95"] is None for p in parts):
                ci = None  # an unknown in the sum makes the total unknown
            else:
                ci = math.sqrt(sum((p["n"] ** 2) * (p["ci95"] ** 2) for p in parts)) / total_n
            axis_entry.update(
                {
                    "n": total_n,
                    "mean": round(mean, 3),
                    "density": round(density, 3),
                    "ci95": None if ci is None else round(ci, 3),
                    "rank": round(sum(p["rank"] * p["n"] for p in parts) / total_n, 3),
                }
            )

    # Attach years (premiere..finale of the season) and season episode counts.
    snapshot = config.read_json(config.snapshot_path(franchise_id, date))
    spans: dict[tuple, tuple[int, int]] = {}
    counts: dict[tuple, int] = {}
    for ep in snapshot["episodes"]:
        key = (ep["series"], ep["season"])
        counts[key] = counts.get(key, 0) + 1
        year = ep["year"]
        if year is None:
            continue
        lo, hi = spans.get(key, (year, year))
        spans[key] = (min(lo, year), max(hi, year))

    # Works = series + films, kept in declaration order so the chart and the
    # legend are stable across runs.
    works = [{**s, "kind": s.get("kind", "series"), "order": i} for i, s in enumerate(work_series)]
    works += [
        {**f, "kind": "film", "order": len(work_series) + j}
        for j, f in enumerate(meta.get("films", []))
    ]
    by_id = {w["id"]: w for w in works}
    entity_counts_by_key: dict[str, int] = {}
    for ent in config.franchise_entities(franchise_id):
        series_key = ent.get("series")
        if series_key:
            entity_counts_by_key[series_key] = entity_counts_by_key.get(series_key, 0) + 1
    entity_counts = {
        s["id"]: entity_counts_by_key.get(s.get("entityGroup") or s["id"], 0)
        for s in work_series
    }

    # Text basis: how much evidence each work actually rests on. A franchise can
    # have an episode count and still be measured almost entirely on 30-word
    # summaries, so this is published next to the scores rather than buried.
    episodes_per_series: dict[str, int] = {}
    covered: dict[tuple, set] = {}
    measured: dict[tuple, set] = {}
    words_by: dict[tuple, list] = {}
    for ep in snapshot["episodes"]:
        episodes_per_series[ep["series"]] = episodes_per_series.get(ep["series"], 0) + 1
    for row in rows:
        if row["source"] not in scope_sources or row["series"] not in scope_series:
            continue
        key = (row["series"], row["source"])
        covered.setdefault(key, set()).add(row["episodeId"])
        if row["words"] >= min_words:
            measured.setdefault(key, set()).add(row["episodeId"])
        words_by.setdefault(key, []).append(row["words"])

    def text_basis(series_id: str) -> dict:
        sources_detail = {}
        for source in scope_sources:
            key = (series_id, source)
            words = words_by.get(key) or []
            sources_detail[source] = {
                "covered": len(covered.get(key, ())),
                "medianWords": int(statistics.median(words)) if words else 0,
                "measured": len(measured.get(key, ())),
            }
        return {
            "episodes": episodes_per_series.get(series_id, 0),
            "minWords": min_words,
            "sources": sources_detail,
        }

    # A grouped series counts *serials* as its works, so the number of original
    # transmission parts per season is carried alongside rather than lost.
    parts: dict[tuple, int] = {}
    for ep in snapshot["episodes"]:
        if ep.get("parts"):
            key = (ep["series"], ep["season"])
            parts[key] = parts.get(key, 0) + ep["parts"]

    out_series = []
    for series in sorted({k[0] for k in seasons}, key=lambda s: by_id.get(s, {}).get("order", 999)):
        work = by_id.get(series, {"id": series, "name": series, "kind": "series", "order": 999})
        out_seasons = []
        for (s_id, season) in sorted({k for k in seasons if k[0] == series}, key=lambda k: k[1]):
            axes = seasons[(s_id, season)]
            year, year_end = spans.get((s_id, season), (None, None))
            out_seasons.append(
                {
                    "season": season,
                    "year": year,
                    "yearEnd": year_end if year_end != year else None,
                    "episodeCount": counts.get((s_id, season), 0),
                    "partCount": parts.get((s_id, season)) or None,
                    "sci": axes.get("sci"),
                    "fi": axes.get("fi"),
                }
            )
        out_series.append(
            {
                "id": series,
                "name": work["name"],
                "short": work.get("short", series),
                "kind": work.get("kind", "series"),
                "unit": "serial" if work.get("grouping") == "serial" else work.get("kind", "series"),
                "color": colour_for(work, work.get("order", 0)),
                "ageRating": work.get("ageRating"),
                "entityCount": entity_counts.get(series, 0),
                "textBasis": text_basis(series),
                "seasons": out_seasons,
            }
        )

    scope = {
        "categories": sorted({r["category"] for r in rows if not scope_sources or r["source"] in scope_sources}),
        "sources": list(scope_sources),
        "series": list(scope_series),
    }
    # Works with no measurement at all: every source either missed them or supplied
    # less prose than the floor. They are absent from the chart rather than plotted at
    # zero, and the tally is published so that the absence is visible — an empty
    # region of the chart is a finding, and it should not look like full coverage.
    measured_ids = {
        row["episodeId"]
        for row in rows
        if row["source"] in scope_sources and row["series"] in scope_series and row["words"] >= min_words
    }
    series_of = {ep["id"]: ep["series"] for ep in snapshot["episodes"] if ep["series"] in scope_series}
    unmeasured_by_series: dict[str, int] = {}
    for ep_id, series_id in series_of.items():
        if ep_id not in measured_ids:
            unmeasured_by_series[series_id] = unmeasured_by_series.get(series_id, 0) + 1
    unmeasured = {
        "works": sum(1 for ep_id in series_of if ep_id not in measured_ids),
        "of": len(series_of),
        "minWords": min_words,
        "bySeries": dict(sorted(unmeasured_by_series.items(), key=lambda kv: -kv[1])),
    }
    return {
        "franchise": franchise_id,
        "franchiseName": meta["name"],
        "snapshotDate": date,
        "rubricVersion": rubric["version"],
        "scope": scope,
        "scopeHash": config.digest(scope),
        "generatedAt": config.now(),
        "unmeasured": unmeasured,
        "coverageGaps": [
            {"series": s, "axis": a, "missingSubsignals": sorted(m)}
            for (s, a), m in sorted(coverage_gaps.items())
        ],
        "series": out_series,
    }


def write_derived(franchise_id: str, date: str, derived: dict) -> str:
    name = f"{derived['scopeHash']}-{derived['rubricVersion']}.json"
    path = config.derived_dir(franchise_id, date) / name
    config.write_json(path, derived)
    return path.name
