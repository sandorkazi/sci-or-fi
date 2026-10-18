"""Reclaim the axis saturation from the corpus that has actually been measured.

A sub-signal's ``saturation`` is the raw density at which it scores 10. rubric-v1
and rubric-v2 fixed those constants at the 95th percentile of one reference
corpus — Star Trek: The Original Series, 79 episodes — which was defensible when
TOS was the only thing measured and is not defensible now. Every other franchise
and source sits far below a TOS-calibrated p95, so the upper half of a 0–10 axis
is permanently empty.

This module recomputes each sub-signal's saturation as the 95th percentile of
that sub-signal's own value across every *valid* measurement in the repository
(see ``rubric["measurement"]["minWords"]``), so "10" means "top 5% of what we have
actually measured" and the scale is used end to end.

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


def subsignals_by_axis() -> dict[str, dict[str, list[float]]]:
    """Every sub-signal's raw value, pooled over all signals files in the repo."""
    out: dict[str, dict[str, list[float]]] = {}
    for path in sorted(config.DATA.glob("signals/*/*.jsonl")):
        for row in config.read_jsonl(path):
            for sub in row.get("subsignals") or []:
                out.setdefault(row["axis"], {}).setdefault(sub["id"], []).append(sub["density"])
    return out


def calibrate(rubric: dict) -> dict:
    """Return a copy of ``rubric`` with saturations measured on the corpus."""
    min_words = int((rubric.get("measurement") or {}).get("minWords", 0))
    pooled: dict[str, dict[str, list[float]]] = {}
    franchises: set[str] = set()
    for path in sorted(config.DATA.glob("signals/*/*.jsonl")):
        franchises.add(path.parent.name)
        for row in config.read_jsonl(path):
            if row["words"] < min_words:
                continue
            for sub in row.get("subsignals") or []:
                pooled.setdefault(row["axis"], {}).setdefault(sub["id"], []).append(sub["density"])

    out = {**rubric, "version": rubric.get("version", "rubric") + "-calibrated"}
    out["axes"] = {}
    measured = {}
    for axis, spec in rubric["axes"].items():
        subs = []
        for sub in spec["subsignals"]:
            values = pooled.get(axis, {}).get(sub["id"], [])
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
