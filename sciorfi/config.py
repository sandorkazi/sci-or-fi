"""Paths, JSON I/O and rubric loading for the sci-or-fi pipeline."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / "cache"
OUTPUT = ROOT / "output"
RUBRIC_DIR = ROOT / "rubric"


# --------------------------------------------------------------------------- io
def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj, *, indent: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def read_jsonl(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def canonical(obj) -> str:
    """Stable string form used for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj, *, length: int = 12) -> str:
    payload = obj if isinstance(obj, str) else canonical(obj)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def today() -> str:
    return _dt.date.today().isoformat()


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ repository
def franchises() -> list[str]:
    base = DATA / "franchises"
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "franchise.json").exists())


def franchise_dir(franchise_id: str) -> Path:
    return DATA / "franchises" / franchise_id


def franchise_meta(franchise_id: str) -> dict:
    path = franchise_dir(franchise_id) / "franchise.json"
    if not path.exists():
        raise SystemExit(f"unknown franchise: {franchise_id} (expected {path})")
    return read_json(path)


def franchise_entities(franchise_id: str) -> list[dict]:
    """Curated named entities used by the ``entity`` coder (may be empty)."""
    path = franchise_dir(franchise_id) / "entities.json"
    if not path.exists():
        return []
    return read_json(path).get("entities", [])


def effective_sources(franchise_id: str, rubric: dict) -> list[str]:
    """The sources in scope for a franchise.

    The rubric is the registry of known sources and owns their ordering; the
    franchise declares which of them cover it (Star Trek uses Memory Alpha,
    Doctor Who uses the Tardis Wiki). Scoring the other wiki would only add a
    source with zero measurements.
    """
    declared = franchise_meta(franchise_id).get("sources", {})
    chosen = [sid for sid in rubric["scope"]["sources"] if sid in declared]
    return chosen or list(declared)


def snapshot_path(franchise_id: str, date: str) -> Path:
    return DATA / "snapshots" / franchise_id / f"{date}.json"


def signals_path(franchise_id: str, date: str) -> Path:
    return DATA / "signals" / franchise_id / f"{date}.jsonl"


def derived_dir(franchise_id: str, date: str) -> Path:
    return DATA / "derived" / franchise_id / date


# ----------------------------------------------------------------------- rubric
def load_rubric(version: str = "rubric-v1") -> dict:
    path = RUBRIC_DIR / f"{version}.json"
    if not path.exists():
        raise SystemExit(f"unknown rubric: {version} (expected {path})")
    rubric = read_json(path)
    if rubric.get("version") != version:
        raise SystemExit(f"{path}: version field {rubric.get('version')!r} != {version!r}")
    return rubric
