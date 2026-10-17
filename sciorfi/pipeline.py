"""Pipeline stages: ingest (snapshot) -> signals -> score.

Each stage reads the previous stage's committed output, so the rubric or the
scope can be changed without re-hitting a network source.
"""
from __future__ import annotations

import math
import statistics

from . import config, lexicon, sources


# ------------------------------------------------------------------- ingest
def build_snapshot(franchise_id: str, date: str, *, refresh: bool = False) -> dict:
    meta = config.franchise_meta(franchise_id)
    episodes: list[dict] = []
    provenance: list[dict] = []

    for series in meta["series"]:
        sid = series["id"]
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if tvmaze_id is None:
            continue
        raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
        provenance.append({"source": "tvmaze", "series": sid, "id": tvmaze_id, "episodes": len(raw)})
        for ep in raw:
            air = ep.get("airDate")
            episodes.append(
                {
                    "id": f"{franchise_id}:{sid}:S{ep['season']:02d}E{ep['episode']:02d}",
                    "series": sid,
                    "season": ep["season"],
                    "episode": ep["episode"],
                    "title": ep["title"],
                    "airDate": air,
                    "year": int(air[:4]) if air else None,
                    "sourceIds": ep["sourceIds"],
                }
            )

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


# ------------------------------------------------------------------ signals
def extract_signals(
    franchise_id: str, date: str, rubric: dict, *, refresh: bool = False, on_progress=None
) -> list[dict]:
    snapshot = config.read_json(config.snapshot_path(franchise_id, date))
    episodes = snapshot["episodes"]
    meta = config.franchise_meta(franchise_id)
    compiled = lexicon.compile_axes(rubric)
    compiled_entities = lexicon.compile_entities(config.franchise_entities(franchise_id))
    scope_sources = rubric["scope"]["sources"]

    # Source text is fetched here and held in memory only: it is never committed.
    texts: dict[str, dict[str, dict]] = {}
    for series in meta["series"]:
        sid = series["id"]
        group = [e for e in episodes if e["series"] == sid]
        if not group:
            continue
        tvmaze_id = series.get("sourceIds", {}).get("tvmaze")
        if "tvmaze" in scope_sources and tvmaze_id is not None:
            raw = sources.tvmaze_episodes(tvmaze_id, refresh=refresh)
            by_number = {(e["season"], e["episode"]): e for e in raw}
            for ep in group:
                hit = by_number.get((ep["season"], ep["episode"]))
                if hit and hit["text"]:
                    texts.setdefault(ep["id"], {})["tvmaze"] = {"text": hit["text"], "kind": hit["textKind"]}
        if "wikipedia" in scope_sources:
            plots = sources.wikipedia_plots([e["title"] for e in group], refresh=refresh)
            if on_progress:
                on_progress(sid, len(plots), len(group))
            for ep in group:
                hit = plots.get(ep["title"])
                if hit:
                    texts.setdefault(ep["id"], {})["wikipedia"] = {"text": hit["text"], "kind": hit["textKind"]}

    rows: list[dict] = []
    for ep in episodes:
        for source_id, payload in texts.get(ep["id"], {}).items():
            if source_id not in scope_sources:
                continue
            coded = lexicon.code_text(
                payload["text"], rubric, compiled=compiled, compiled_entities=compiled_entities
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


def _ci95(values: list[float], rubric: dict) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    sd = statistics.stdev(values)
    ci = rubric["uncertainty"]["z"] * sd / math.sqrt(n)
    if n < rubric["uncertainty"]["thinSampleBelow"]:
        ci *= rubric["uncertainty"]["thinSampleInflation"]
    return ci


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
    scope_sources = scope_sources or rubric["scope"]["sources"]
    scope_series = scope_series or [s["id"] for s in meta["series"]]

    # (series, season, axis, source) -> per-episode scores
    buckets: dict[tuple, list[float]] = {}
    densities: dict[tuple, list[float]] = {}
    coverage_gaps: dict[tuple, set] = {}
    for row in rows:
        if row["source"] not in scope_sources or row["series"] not in scope_series:
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
        axis_entry["perSource"][source] = {
            "n": len(values),
            "mean": round(statistics.fmean(values), 3),
            "density": round(statistics.fmean(densities[(series, season, axis, source)]), 3),
            "ci95": round(_ci95(values, rubric), 3),
        }

    # Combine sources into a single per-season figure, weighted by episode count.
    for (series, season), axes in seasons.items():
        for axis, axis_entry in axes.items():
            parts = list(axis_entry["perSource"].values())
            total_n = sum(p["n"] for p in parts)
            if not total_n:
                continue
            mean = sum(p["mean"] * p["n"] for p in parts) / total_n
            density = sum(p["density"] * p["n"] for p in parts) / total_n
            ci = math.sqrt(sum((p["n"] ** 2) * (p["ci95"] ** 2) for p in parts)) / total_n
            axis_entry.update(
                {
                    "n": total_n,
                    "mean": round(mean, 3),
                    "density": round(density, 3),
                    "ci95": round(ci, 3),
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

    order = {s["id"]: i for i, s in enumerate(meta["series"])}
    out_series = []
    for series in sorted({k[0] for k in seasons}, key=lambda s: order.get(s, 99)):
        series_meta = next(s for s in meta["series"] if s["id"] == series)
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
                "name": series_meta["name"],
                "short": series_meta.get("short", series),
                "color": series_meta.get("color", "#888780"),
                "ageRating": series_meta.get("ageRating"),
                "seasons": out_seasons,
            }
        )

    scope = {
        "categories": sorted({r["category"] for r in rows if not scope_sources or r["source"] in scope_sources}),
        "sources": list(scope_sources),
        "series": list(scope_series),
    }
    return {
        "franchise": franchise_id,
        "franchiseName": meta["name"],
        "snapshotDate": date,
        "rubricVersion": rubric["version"],
        "scope": scope,
        "scopeHash": config.digest(scope),
        "generatedAt": config.now(),
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
