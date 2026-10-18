"""Minimal JSON-Schema-subset validation, dependency-free.

The authoritative schemas live in ``data/schema/*.json`` and are documented for
readers; this module enforces the parts of them that matter at runtime.
"""
from __future__ import annotations

from . import config

_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}


def _load_schema(name: str) -> dict:
    return config.read_json(config.DATA / "schema" / name)


def _check(obj, schema: dict, path: str) -> list[str]:
    problems: list[str] = []
    t = schema.get("type")
    allowed = t if isinstance(t, list) else ([t] if t else [])
    if allowed:
        if obj is None:
            # An explicit null is fine only where the schema says so; otherwise the
            # caller skips nulls on optional fields before we get here.
            return [] if "null" in allowed else [f"{path}: expected {'/'.join(allowed)}, got null"]
        matched = [a for a in allowed if a in _TYPES and isinstance(obj, _TYPES[a])]
        if not matched:
            return [f"{path}: expected {'/'.join(allowed)}, got {type(obj).__name__}"]
        t = matched[0]
    else:
        return problems

    if t == "object":
        for key in schema.get("required", []):
            if key not in obj:
                problems.append(f"{path}: missing required field {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in obj and obj[key] is not None:
                problems += _check(obj[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            extra = set(obj) - set(schema.get("properties", {}))
            for key in sorted(extra):
                problems.append(f"{path}: unexpected field {key!r}")
    elif t == "array":
        if "minItems" in schema and len(obj) < schema["minItems"]:
            problems.append(f"{path}: needs >= {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(obj):
                problems += _check(item, schema["items"], f"{path}[{i}]")
    elif t in ("integer", "number"):
        if "minimum" in schema and obj < schema["minimum"]:
            problems.append(f"{path}: {obj} < minimum {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            problems.append(f"{path}: {obj} > maximum {schema['maximum']}")
    elif t == "string" and "enum" in schema and obj not in schema["enum"]:
        problems.append(f"{path}: {obj!r} not in {schema['enum']}")
    return problems


def validate_episodes(episodes: list[dict]) -> list[str]:
    schema = _load_schema("episode.schema.json")
    problems: list[str] = []
    for i, ep in enumerate(episodes):
        problems += _check(ep, schema, f"episodes[{i}]")
    return problems


def validate_signals(rows: list[dict], known_episodes: set[str]) -> list[str]:
    schema = _load_schema("signal.schema.json")
    problems: list[str] = []
    for i, row in enumerate(rows):
        problems += _check(row, schema, f"signals[{i}]")
        if row.get("episodeId") not in known_episodes:
            problems.append(f"signals[{i}]: unknown episodeId {row.get('episodeId')!r}")
    return problems


def validate_derived(derived: dict) -> list[str]:
    schema = _load_schema("derived.schema.json")
    return _check(derived, schema, "derived")
