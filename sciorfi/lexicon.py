"""Coders: turn source text into per-episode signals.

Two coders exist in rubric-v1/v2:

* ``lexicon`` — counts vocabulary from a term list (measured, full coverage).
* ``entity``  — counts mentions of named entities curated per franchise. The
  judgement lives in a visible, reviewable list rather than hidden inside a
  score.

A *coding* coder (human or LLM) applying the reserved judgement sub-signals is a
drop-in sibling: it must emit the same signal records.
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z']+")


def _alt_regex(terms: list[str], *, case_sensitive: bool = False) -> re.Pattern:
    pattern = rf"\b(?:{'|'.join(re.escape(t) for t in terms)})\b"
    return re.compile(pattern) if case_sensitive else re.compile(pattern, re.I)


def compile_entities(entities: list[dict]) -> dict[str, list[dict]]:
    """Group curated entities by axis, each with its own compiled regex.

    ``caseSensitive`` exists because some entities are named after common nouns:
    "the Beast" must match the Beast of *The Impossible Planet*, and must not be
    matched by "a beast" in any other episode's prose. The flag is per entity and
    visible in ``entities.json``, so the choice is reviewable rather than implicit.
    """
    grouped: dict[str, list[dict]] = {}
    for entity in entities or []:
        grouped.setdefault(entity["axis"], []).append(
            {
                "name": entity["name"],
                "regex": _alt_regex(
                    entity.get("terms") or [entity["name"]],
                    case_sensitive=bool(entity.get("caseSensitive")),
                ),
            }
        )
    return grouped


def compile_axes(rubric: dict) -> dict[str, list[dict]]:
    """Compile the lexicon sub-signals, grouped by axis."""
    compiled: dict[str, list[dict]] = {}
    for axis, spec in rubric["axes"].items():
        subs = []
        for sub in spec["subsignals"]:
            if sub["coder"] != "lexicon":
                continue
            subs.append(
                {
                    "id": sub["id"],
                    "weight": float(sub.get("weight", 1.0)),
                    "saturation": float(sub["saturation"]),
                    "regex": _alt_regex(sub["patterns"]),
                }
            )
        compiled[axis] = subs
    return compiled


def confidence(words: int, *, full_confidence_at: int = 250) -> float:
    """Confidence in a measurement, driven by how much text it rests on."""
    return round(min(1.0, words / full_confidence_at), 2)


def _score_subsignal(axis_spec: dict) -> float:
    return float(axis_spec.get("weight", 1.0))


def code_text(
    text: str,
    rubric: dict,
    *,
    entities: list[dict] | None = None,
    compiled: dict | None = None,
    compiled_entities: dict | None = None,
    entity_coding: bool = True,
) -> dict:
    """Code one piece of text.

    Returns ``{axis: {hits, words, density, score, subsignals}}``. Each axis is
    scored from its own sub-signals only, so the axes stay independent.

    ``entity_coding=False`` drops the entity-coder sub-signals entirely. It is
    used for a series that has no curated entity list: the honest statement is
    "this axis is lexicon-only here", not an entity term silently contributing
    zero and halving the axis.
    """
    compiled = compiled if compiled is not None else compile_axes(rubric)
    compiled_entities = (
        compiled_entities if compiled_entities is not None else compile_entities(entities or [])
    )
    scale = rubric["scale"]
    words = len(_WORD.findall(text or ""))
    text = text or ""

    out: dict[str, dict] = {}
    for axis, spec in rubric["axes"].items():
        detail: list[dict] = []
        weighted = 0.0
        weight_sum = 0.0
        lexicon_hits = 0.0

        for sub in compiled.get(axis, []):
            hits = len(sub["regex"].findall(text))
            lexicon_hits += hits
            density = (1000.0 * hits / words) if words else 0.0
            frac = min(1.0, density / sub["saturation"]) if sub["saturation"] else 0.0
            weighted += sub["weight"] * frac
            weight_sum += sub["weight"]
            detail.append(
                {
                    "id": sub["id"],
                    "coder": "lexicon",
                    "hits": hits,
                    "density": round(density, 3),
                    "saturation": sub["saturation"],
                    "frac": round(frac, 4),
                    "weight": sub["weight"],
                }
            )

        entity_subs = [s for s in spec["subsignals"] if s["coder"] == "entity"] if entity_coding else []
        if entity_subs:
            items = compiled_entities.get(axis, [])
            mentions = 0
            matched: list[str] = []
            for item in items:
                n = len(item["regex"].findall(text))
                if n:
                    matched.append(item["name"])
                    mentions += n
            density = (1000.0 * mentions / words) if words else 0.0
            for sub in entity_subs:
                saturation = float(sub["saturation"])
                frac = min(1.0, density / saturation) if saturation else 0.0
                weight = _score_subsignal(sub)
                weighted += weight * frac
                weight_sum += weight
                detail.append(
                    {
                        "id": sub["id"],
                        "coder": "entity",
                        "hits": mentions,
                        "density": round(density, 3),
                        "saturation": saturation,
                        "frac": round(frac, 4),
                        "weight": weight,
                        "matched": matched,
                    }
                )

        density = (1000.0 * lexicon_hits / words) if words else 0.0
        frac_total = (weighted / weight_sum) if weight_sum else 0.0
        out[axis] = {
            "hits": round(lexicon_hits, 2),
            "words": words,
            "density": round(density, 3),
            "score": round(scale["min"] + (scale["max"] - scale["min"]) * frac_total, 3),
            "subsignals": detail,
        }
    return out
