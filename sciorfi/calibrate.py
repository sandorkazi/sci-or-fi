"""Reclaim the axis saturation — and the source scale — from the measured corpus.

A sub-signal's ``saturation`` is the raw density at which it scores 10. rubric-v1
and rubric-v2 fixed those constants at the 95th percentile of one reference
corpus — Star Trek: The Original Series, 79 episodes — which was defensible when
TOS was the only thing measured and is not defensible now. Every other franchise
and source sits far below a TOS-calibrated p95, so the upper half of a 0–10 axis
is permanently empty.

This module recomputes two things from every *valid* measurement in the
repository (see ``rubric["measurement"]["minWords"]``):

1. **Saturation** — the 95th percentile of the sub-signal's own value pooled over
   every franchise and source, so "10" means "top 5% of what we have actually
   measured". This stays global: it is what keeps a franchise comparison honest
   (Doctor Who's Sci sits below Star Trek's because its densities are lower, not
   because each franchise is rescaled to fill the frame).
2. **Source standardisation** — a quantile map per (franchise, source, sub-signal)
   back onto the franchise's pooled distribution. Saturation is a single global
   constant and cannot fix the fact that a work's score moves with *which* sources
   resolved it; the quantile map does, by giving every source in a franchise one
   common scale. It is per franchise because the sources are: Tardis only ever
   measures Doctor Who, so pooling it with Star-Trek-heavy Memory Alpha would
   assert the two franchises measure the same thing.

Calibration is deliberately a separate, re-runnable step that writes a new rubric
file, rather than something `score` does on the fly: a score has to be a function
of a named rubric, and re-calibrating must be a reviewable event.
"""
from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path

from . import config


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    # inclusive: p95 of a small sample is a value in the sample, not an extrapolation
    return float(statistics.quantiles(values, n=100, method="inclusive")[94])


def _grid(values: list[float]) -> list[float]:
    """21-point quantile grid at p = 0, .05, … 1 — the source's shape, cheaply.

    The scorer inverts this to turn a density into a within-source percentile and
    reads the franchise's pooled grid back at that percentile, so a quantile map is
    all either side needs. A one-value sample stays a one-point grid.
    """
    if not values:
        return []
    ordered = sorted(values)
    if len(ordered) == 1:
        return [round(float(ordered[0]), 3)]
    quantiles = [float(q) for q in statistics.quantiles(ordered, n=20, method="inclusive")]
    return [round(float(p), 3) for p in [ordered[0], *quantiles, ordered[-1]]]


def _collect(min_words: int):
    """(pooled by sub-signal, by (franchise, source) by sub-signal) over valid rows."""
    pooled: dict[str, list[float]] = {}
    by_franchise_source: dict[str, dict[str, dict[str, list[float]]]] = {}
    franchises: set[str] = set()
    for path in sorted(config.DATA.glob("signals/*/*.jsonl")):
        franchise = path.parent.name
        franchises.add(franchise)
        for row in config.read_jsonl(path):
            if row["words"] < min_words:
                continue
            for sub in row.get("subsignals") or []:
                pooled.setdefault(sub["id"], []).append(sub["density"])
                by_franchise_source.setdefault(franchise, {}).setdefault(row["source"], {}).setdefault(
                    sub["id"], []
                ).append(sub["density"])
    return pooled, by_franchise_source, franchises


def standardisation_block(by_franchise_source: dict) -> dict:
    """Per-franchise pooled quantile grids + one grid per source within it."""
    by_franchise = {}
    for franchise, by_source in sorted(by_franchise_source.items()):
        merged: dict[str, list[float]] = {}
        per_source = {}
        for source, subs in sorted(by_source.items()):
            per_source[source] = {sid: _grid(values) for sid, values in sorted(subs.items())}
            for sid, values in subs.items():
                merged.setdefault(sid, []).extend(values)
        by_franchise[franchise] = {
            "pooled": {sid: _grid(values) for sid, values in sorted(merged.items())},
            "perSource": per_source,
        }
    return {
        "method": "quantile-per-source-within-franchise",
        "notes": (
            "Each source's within-source percentile is read back off its franchise's pooled "
            "distribution, so a work's score does not depend on which sources resolved it. "
            "Per franchise, because sources are nested by franchise (Tardis is Doctor Who only)."
        ),
        "byFranchise": by_franchise,
    }


def calibrate(rubric: dict) -> dict:
    """Return a copy of ``rubric`` with saturations and source scales measured on the corpus."""
    min_words = int((rubric.get("measurement") or {}).get("minWords", 0))
    pooled, by_franchise_source, franchises = _collect(min_words)

    out = {**rubric, "version": rubric.get("version", "rubric") + "-calibrated"}
    out["axes"] = {}
    measured = {}
    for axis, spec in rubric["axes"].items():
        subs = []
        for sub in spec["subsignals"]:
            values = pooled.get(sub["id"], [])
            new = _p95(values)
            measured[sub["id"]] = {
                "n": len(values),
                "p95": round(new, 3),
                "was": sub.get("saturation"),
            }
            subs.append(
                {
                    **sub,
                    "saturation": round(new, 3) if new else sub.get("saturation"),
                    "calibration": (
                        f"p95 of this sub-signal over {len(values)} valid measurements "
                        f"(>= {min_words} words of source text), all franchises and sources"
                    ),
                }
            )
        out["axes"][axis] = {**spec, "subsignals": subs}
    out["calibration"] = {
        "method": "p95 of the sub-signal's raw value over valid measurements",
        "minWords": min_words,
        "franchises": sorted(franchises),
        "subsignals": measured,
        "standardisation": standardisation_block(by_franchise_source),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    return out


def calibrate_file(source: str | Path, dest: str | Path, *, version: str | None = None) -> Path:
    rubric = config.load_rubric(str(source))
    out = calibrate(rubric)
    if version:
        out["version"] = version
    dest = Path(dest)
    config.write_json(dest, out)
    return dest
