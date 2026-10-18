"""Command line entry point: ``python3 -m sciorfi <stage>``."""
from __future__ import annotations

import argparse
import sys

from . import config, pipeline, validate

STAGES = ("ingest", "signals", "score", "render", "validate", "calibrate", "all")
DEFAULT_RUBRIC = "rubric-v3"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m sciorfi", description=__doc__)
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--franchise", default="star-trek", help="franchise id (default: star-trek)")
    p.add_argument("--date", default=None, help="snapshot date YYYY-MM-DD (default: today)")
    p.add_argument("--rubric", default=DEFAULT_RUBRIC)
    p.add_argument("--refresh", action="store_true", help="bypass the on-disk HTTP cache")
    p.add_argument("--sources", default=None, help="comma-separated source scope override")
    p.add_argument("--out", default=None, help="calibrate: rubric file to write (default: rubric/<rubric>-calibrated.json)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress must show in redirected logs
    date = args.date or config.today()
    rubric = config.load_rubric(args.rubric)
    sources = args.sources.split(",") if args.sources else None

    if args.stage == "calibrate":
        from . import calibrate as calib

        dest = args.out or f"rubric/{args.rubric}-calibrated.json"
        path = calib.calibrate_file(args.rubric, config.ROOT / dest, version=args.rubric)
        updated = config.read_json(path)
        for sub_id, info in updated["calibration"]["subsignals"].items():
            print(f"calibrate: {sub_id:26} {info['was']} -> {info['p95']}  (n={info['n']})")
        print(f"calibrate: {path.relative_to(config.ROOT)}")
        return 0

    if args.stage in ("ingest", "all"):
        snap = pipeline.build_snapshot(args.franchise, date, refresh=args.refresh)
        print(f"ingest  : {snap['episodeCount']} episodes -> {config.snapshot_path(args.franchise, date).relative_to(config.ROOT)}")

    if args.stage in ("signals", "all"):
        rows = pipeline.extract_signals(
            args.franchise, date, rubric, refresh=args.refresh,
            on_progress=lambda s, found, total: print(f"signals : {s} plots {found}/{total}"),
        )
        by_source: dict[str, int] = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        detail = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
        print(f"signals : {len(rows)} rows -> {config.signals_path(args.franchise, date).relative_to(config.ROOT)} ({detail})")

    if args.stage in ("score", "all"):
        derived = pipeline.score_scope(args.franchise, date, rubric, scope_sources=sources)
        name = pipeline.write_derived(args.franchise, date, derived)
        print(f"score   : scope={','.join(derived['scope']['sources'])} -> data/derived/{args.franchise}/{date}/{name}")

    if args.stage in ("render", "all"):
        from . import render

        for path in render.render_all(args.franchise, date, rubric):
            print(f"render  : {path.relative_to(config.ROOT)}")

    if args.stage in ("validate", "all"):
        problems = validate.run(args.franchise, date, rubric)
        if problems:
            print("validate: FAILED")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("validate: ok")

    return 0


if __name__ == "__main__":
    sys.exit(main())
