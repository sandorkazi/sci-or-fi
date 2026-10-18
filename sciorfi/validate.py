"""Validation gates for the pipeline. Returns a list of problems (empty == ok)."""
from __future__ import annotations

import statistics

from . import config, pipeline, validate_schema


def run(franchise_id: str, date: str, rubric: dict) -> list[str]:
    problems: list[str] = []
    snapshot_file = config.snapshot_path(franchise_id, date)
    if not snapshot_file.exists():
        return [f"missing snapshot: {snapshot_file.relative_to(config.ROOT)} (run: sciorfi ingest)"]

    snapshot = config.read_json(snapshot_file)
    episodes = snapshot["episodes"]

    # --- licensing gate: snapshots must never contain source prose --------
    for ep in episodes:
        if "texts" in ep or "summary" in ep:
            problems.append(f"{ep.get('id','?')}: snapshot contains source prose; snapshots must carry facts and IDs only")
            break

    # --- snapshot -------------------------------------------------------
    problems += validate_schema.validate_episodes(episodes)
    seen: set[str] = set()
    for ep in episodes:
        if ep["id"] in seen:
            problems.append(f"duplicate episode id {ep['id']}")
        seen.add(ep["id"])
        if ep["airDate"] and ep["year"] is not None and int(ep["airDate"][:4]) != ep["year"]:
            problems.append(f"{ep['id']}: year {ep['year']} != airDate year {ep['airDate'][:4]}")

    for series_id in sorted({e["series"] for e in episodes}):
        seasons = sorted({e["season"] for e in episodes if e["series"] == series_id})
        for season in seasons:
            numbers = sorted(e["episode"] for e in episodes if e["series"] == series_id and e["season"] == season)
            expected = list(range(1, len(numbers) + 1))
            if numbers != expected:
                problems.append(f"{series_id} S{season}: episode numbers {numbers} are not contiguous from 1")

    # --- signals --------------------------------------------------------
    signals_file = config.signals_path(franchise_id, date)
    if not signals_file.exists():
        return problems + [f"missing signals: {signals_file.relative_to(config.ROOT)} (run: sciorfi signals)"]

    rows = config.read_jsonl(signals_file)
    problems += validate_schema.validate_signals(rows, seen)
    covered: dict[str, set[str]] = {}
    for row in rows:
        covered.setdefault(row["source"], set()).add(row["episodeId"])

    for source in config.effective_sources(franchise_id, rubric):
        got = len(covered.get(source, ()))
        # Thin coverage is expected and allowed — TVmaze has no film entries, a
        # wiki search does not resolve every episode title, and a franchise wiki
        # only covers its own franchise. It is surfaced in the rendered page. A
        # source that contributes nothing at all is a real error.
        if got == 0:
            problems.append(
                f"coverage: source {source!r} covers 0/{len(episodes)} episodes — is it wired up?"
            )

    # --- derived --------------------------------------------------------
    derived_files = sorted((config.derived_dir(franchise_id, date)).glob(f"*-{rubric['version']}.json"))
    if not derived_files:
        return problems + ["no derived output (run: sciorfi score)"]

    derived = config.read_json(max(derived_files, key=lambda p: p.stat().st_mtime))
    problems += validate_schema.validate_derived(derived)

    # Reproducibility: same inputs must produce the same numbers.
    recomputed = pipeline.score_scope(
        franchise_id, date, rubric, scope_sources=derived["scope"]["sources"]
    )
    for series_a, series_b in zip(derived["series"], recomputed["series"]):
        for season_a, season_b in zip(series_a["seasons"], series_b["seasons"]):
            for axis in ("sci", "fi"):
                if (season_a[axis] or {}).get("mean") != (season_b[axis] or {}).get("mean"):
                    problems.append(f"non-deterministic score: {series_a['id']} S{season_a['season']} {axis}")

    # Scope integrity: a season's combined n must equal its episode count.
    for series in derived["series"]:
        for season in series["seasons"]:
            for axis in ("sci", "fi"):
                data = season[axis]
                if data and data.get("n", 0) > season["episodeCount"] * len(derived["scope"]["sources"]):
                    problems.append(f"{series['id']} S{season['season']} {axis}: n={data['n']} exceeds coverage")
                if data and not (0 <= data["mean"] <= 10):
                    problems.append(f"{series['id']} S{season['season']} {axis}: mean {data['mean']} out of range")

    return problems
