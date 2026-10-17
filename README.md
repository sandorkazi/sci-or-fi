# sci-or-fi

How much is science, how much is myth — measured per episode from named sources, aggregated to
seasons, and published as static pages via GitHub Pages.

The published page lets you **choose the sources** a finding rests on and watch the scores
recompute. Nothing on it is hand-assigned: every number traces back to a source, a snapshot date
and a rubric version.

```
python3 -m sciorfi build   # or: make build
```

## Layout

```
sciorfi/            pipeline (standard library only — no virtualenv needed)
  config.py         paths, JSON I/O, rubric + entity loading
  http.py           cached HTTP (raw source text is cached outside the repo)
  sources.py        one adapter per source: tvmaze, wikipedia
  lexicon.py        coders: lexicon (term lists) and entity (curated names)
  pipeline.py       ingest -> signals -> score
  render.py         static HTML generation (Chart.js vendored, not CDN)
  validate*.py      schema, range, coverage, provenance and reproducibility gates
rubric/             versioned scoring rules and term lists
data/
  schema/           JSON Schema for episode / signal / derived score
  franchises/       one directory per franchise; franchise.json + entities.json
  snapshots/        dated ingestion snapshots — facts and IDs only, never source prose
  signals/          per-episode measurements (the atomic unit of evidence)
  derived/          season scores per (snapshot, scope, rubric) — what gets published
  baselines/        frozen pre-redesign hand-scored values, for comparison
output/             generated pages + vendored Chart.js  (published)
docs/redesign.md    why this exists and what the measurements found
cache/              git-ignored: raw third-party text (licence-restricted)
```

## Stages

| stage | reads | writes |
|---|---|---|
| `ingest` | source APIs | `data/snapshots/<franchise>/<date>.json` |
| `signals` | snapshot + sources | `data/signals/<franchise>/<date>.jsonl` |
| `score` | signals + rubric | `data/derived/<franchise>/<date>/<scope>-<rubric>.json` |
| `render` | derived | `output/` + root `index.html` |
| `validate` | all of the above | exit code |

Each stage reads the previous stage's committed output, so changing a **rubric** or a **scope**
never re-hits a network source.

## Adding a franchise

1. `data/franchises/<id>/franchise.json` — series ids, years, source bindings, licences.
2. `data/franchises/<id>/entities.json` — curated named entities (optional; empty is fine).
3. `make FRANCHISE=<id> build`

Adding a whole new *source* means writing one adapter in `sciorfi/sources.py` and declaring it in
the rubric's `scope.sources` and the franchise's `sources` block.

## Provenance and licensing

The site is public but non-commercial. Wikimedia text is CC BY-SA and TVTropes is CC BY-NC-SA, so
the repository stores **derived numeric measurements, identifiers and provenance pointers — never
verbatim third-party prose**. Raw responses live in the git-ignored `cache/`, and the validator
fails the build if source prose appears in a snapshot. Attribution for every source in scope ships
in the page footer.

## Baseline

`git tag baseline-v1` (`dd7f4d7`) is the original single-file, hand-scored chart, frozen before this
redesign. The published page can overlay it so the difference between a holistic 1–10 integer and a
measured episode aggregate is visible.
