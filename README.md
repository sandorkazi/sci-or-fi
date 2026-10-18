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
  calibrate.py      reclaims each sub-signal's saturation from the measured corpus
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
per source, `measured/covered·median words`: how many episodes the source resolved at all, and how
many of those supplied enough prose to be measured.

## The validity floor (`rubric-v3`)

A density is only a measurement if there was enough prose to measure. A source that supplies fewer
than `measurement.minWords` words (200) still counts as *covering* an episode, but contributes no
score. This exists because TVmaze's "summaries" have a 33-word median and 92% of them contain no
rubric term at all: their zero is the absence of text, not the absence of science, and averaging it
into every series pulled the whole chart toward zero. A source's zero-measurement coverage stays
visible in the *text basis* column rather than being deleted.

Removing works means the chart could quietly become emptier, so `derived.unmeasured` counts works
with no measurement at all and the page states the tally under the chart. It is currently the most
important number in the build: **688 of 872 Doctor Who works have no measurement** (682 of them
classic Who, which rested entirely on 31-word teasers), against 60 of 1,015 for Star Trek.

## Scoring and scales

Sci and Fi are scored from disjoint term sets, per episode per source, as a density
(hits per 1,000 words), then mapped to 0–10 with `frac = min(1, density / saturation)`.

- **Saturation is calibrated, not asserted.** `python3 -m sciorfi calibrate` recomputes each
  sub-signal's saturation as the 95th percentile of its own value across every valid measurement in
  the repository, and writes a named rubric file. It is a separate re-runnable stage, never something
  `score` does on the fly: a score must be a function of a named rubric, and re-calibrating must be a
  reviewable event.
- **Uncertainty is measured.** A season's 95% interval comes from the dispersion of its episode-level
  measurements and is what the bubble size encodes. A season measured on a single episode has no
  estimable interval: it is published as `n/a` and drawn at a fixed large size, never as `±0.00`. A
  film is one work rather than a season average, so films are drawn at a fixed size too.
- **Measured score, or rank.** Each season also carries a **percentile rank** per source, averaged
  over the selected sources exactly as the measured score is. The sidebar can switch the chart to it.
  The rank scale is uniform by construction, so it uses the full 0–10 range and cancels the
  systematic level differences between sources — but a rank says where a work *sits*, not how much
  science it contains. The measured scale is the default; rank is opt-in.

## Stages

| stage | reads | writes |
|---|---|---|
| `ingest` | source APIs | `data/snapshots/<franchise>/<date>.json` |
| `signals` | snapshot + sources | `data/signals/<franchise>/<date>.jsonl` |
| `score` | signals + rubric | `data/derived/<franchise>/<date>/<scope>-<rubric>.json` |
| `render` | derived | `output/` + root `index.html` |
| `calibrate` | signals | a new `rubric/<name>.json` with corpus-measured saturations |
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
