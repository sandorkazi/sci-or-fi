# sci-or-fi

How much is science, how much is myth — measured per episode from named sources, aggregated to
seasons, and published as static pages via GitHub Pages.

The published page lets you **choose the sources** a finding rests on and watch the scores
recompute. Nothing on it is hand-assigned: every number traces back to a source, a snapshot date
and a rubric version.

```
make build                  # ingest -> signals -> score -> render -> validate
make FRANCHISE=doctor-who build
```

## Layout

```
sciorfi/            pipeline (standard library only — no virtualenv needed)
  config.py         paths, JSON I/O, rubric + entity loading
  http.py           cached HTTP (raw source text is cached outside the repo)
  sources.py        MediaWiki adapter (Wikipedia, Memory Alpha, Tardis) + TVmaze
  lexicon.py        coders: lexicon (term lists) and entity (curated names)
  pipeline.py       ingest -> signals -> score
  render.py         static HTML generation (Chart.js vendored, not CDN)
  validate*.py      schema, range, coverage, provenance and reproducibility gates
rubric/             versioned scoring rules and term lists
data/
  schema/           JSON Schema for episode / signal / derived score
  franchises/       one directory per franchise; franchise.json (+ entities.json)
  snapshots/        dated ingestion snapshots — facts and IDs only, never source prose
  signals/          per-episode measurements (the atomic unit of evidence)
  derived/          season scores per (snapshot, scope, rubric) — what gets published
  baselines/        frozen pre-redesign hand-scored values, for comparison
output/             generated pages + vendored Chart.js  (published)
docs/redesign.md    why this exists and what the measurements found
cache/              git-ignored: raw third-party text (licence-restricted)
```

## Works: series and films

The data model is **franchise → series → season → episode**, and a **film** is modelled as a
series-like work with `kind: "film"` and a single one-entry season, so films flow through the same
ingest → signals → score → render path. Films have no TVmaze entry, so their metadata is pinned in
`franchise.json` (exact Wikipedia article title + release year) and their text is fetched from the
wikis by exact article title (never by search — see `wikiTitles` for the handful whose wiki title
differs from the film name). On the chart, films share one neutral marker so they stay legible
against 15+ television series; the tooltip and the table still name each film.

Curated `entities.json` lists are **per series**. A series without one is coded lexicon-only on the
Fi axis, and the page's "Fi coder" column says so — a missing list is stated, never hidden behind an
entity term that silently contributes zero.

## Source resolution (why a measurement is trustworthy)

A search API always returns *something*, so "did this episode resolve to its own plot?" is checked,
not assumed. For each episode the adapter searches on the episode's base title (a `", Part I"` or
parenthetical suffix is stripped), then accepts an article **only if its title is the episode's
title**, with or without a disambiguator, preferring a disambiguator that names the series. If
nothing qualifies the episode is left unresolved and measured on its remaining source — an
unresolved episode is better than a confidently wrong plot. The page's *text basis* column reports,
per source, how many episodes resolved and the median word count behind a measurement.

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

1. `data/franchises/<id>/franchise.json` — series ids (TVmaze id, years, age rating), a `films`
   array (name, release year, exact Wikipedia article title), source bindings and licences.
2. `data/franchises/<id>/entities.json` — curated named entities **per series** (optional; a
   missing list means Fi is lexicon-only for that series, and the page says so).
3. `make FRANCHISE=<id> build`

Adding a whole new *source* means writing one adapter in `sciorfi/sources.py` and declaring it in
the rubric's `scope.sources` and the franchise's `sources` block. Adding another **MediaWiki** wiki
(e.g. another Fandom wiki) needs no code at all — declare it in the franchise's `sources` block with
an `api` URL and a search `hint`, and set the rubric's `scope.sources` to include it.

## Sources

| source | category | what it gives | covers |
|---|---|---|---|
| `tvmaze` | plot-summary | episode metadata + a short summary | all television, no films |
| `wikipedia` | plot-summary | Plot-section prose | uneven — thin for the streaming era |
| `memory-alpha` | plot-summary | `Summary`-section prose | **every** Star Trek episode |
| `tardis` | plot-summary | `Plot`-section prose | every Doctor Who story |

The rubric's `scope.sources` is the registry of known sources; each franchise declares which of
them cover it, and `config.effective_sources` computes the intersection. See
`docs/redesign.md` → "Augmenting Wikipedia with the franchise wikis".

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
