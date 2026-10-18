"""Pipeline stages: ingest (snapshot) -> signals -> score.

Each stage reads the previous stage's committed output, so the rubric or the
scope can be changed without re-hitting a network source.
"""
from __future__ import annotations

import math
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


# ------------------------------------------------------------------- ingest
def build_snapshot(franchise_id: str, date: str, *, refresh: bool = False) -> dict:
    meta = config.franchise_meta(franchise_id)
    episodes: list[dict] = []
    provenance: list[dict] = []

    for series in meta["series"]:
        sid = series["id"]
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if tvmaze_id is None:
            provenance.append({"source": "tvmaze", "series": sid, "skipped": "no tvmaze id"})
            continue
        raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
        provenance.append({"source": "tvmaze", "series": sid, "id": tvmaze_id, "episodes": len(raw)})
        for ep in raw:
            air = ep.get("airDate")
            episodes.append(
                {
                    "id": f"{franchise_id}:{sid}:S{ep['season']:02d}E{ep['episode']:02d}",
                    "series": sid,
                    "kind": "episode",
                    "season": ep["season"],
                    "episode": ep["episode"],
                    "title": ep["title"],
                    "airDate": air,
                    "year": int(air[:4]) if air else None,
                    "sourceIds": ep["sourceIds"],
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
    work_ids = {s["id"] for s in meta["series"]} | {f["id"] for f in meta.get("films", [])}
    compiled_entities_by_series = {
        sid: lexicon.compile_entities(by_series.get(sid, []) + shared_entities) for sid in work_ids
    }
    scope_sources = config.effective_sources(franchise_id, rubric)
    declared_sources = meta.get("sources", {})

    # Source text is fetched here and held in memory only: it is never committed.
    texts: dict[str, dict[str, dict]] = {}
    for series in meta["series"]:
        sid = series["id"]
        group = [e for e in episodes if e["series"] == sid]
        if not group:
            continue
        titles = [e["title"] for e in group]
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if "tvmaze" in scope_sources and tvmaze_id is not None:
            raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
            by_number = {(e["season"], e["episode"]): e for e in raw}
            for ep in group:
                hit = by_number.get((ep["season"], ep["episode"]))
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
    """
    spec = {s["id"]: s for s in rubric["axes"][axis]["subsignals"]}
    present = {d["id"] for d in subsignals}
    missing = [sid for sid in spec if sid not in present]
    weighted = 0.0
    weight_sum = 0.0
    for item in subsignals:
        sub = spec.get(item["id"])
        if sub is None:
            continue  # a measurement this rubric does not use
        saturation = float(sub["saturation"])
        weight = float(sub.get("weight", 1.0))
        frac = min(1.0, item["density"] / saturation) if saturation else 0.0
        weighted += weight * frac
        weight_sum += weight
    if not weight_sum:
        return 0.0, missing
    scale = rubric["scale"]
    return round(scale["min"] + (scale["max"] - scale["min"]) * weighted / weight_sum, 3), missing


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
    if scope_series is None:
        scope_series = [s["id"] for s in meta["series"]] + [f["id"] for f in meta.get("films", [])]

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
    works = [{**s, "kind": s.get("kind", "series"), "order": i} for i, s in enumerate(meta["series"])]
    works += [
        {**f, "kind": "film", "order": len(meta["series"]) + j}
        for j, f in enumerate(meta.get("films", []))
    ]
    by_id = {w["id"]: w for w in works}
    entity_counts: dict[str, int] = {}
    for ent in config.franchise_entities(franchise_id):
        series_key = ent.get("series")
        if series_key:
            entity_counts[series_key] = entity_counts.get(series_key, 0) + 1

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
