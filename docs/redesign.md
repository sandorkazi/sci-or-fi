# Sci-or-Fi redesign — design notes

Status: draft for review. Branch `redesign`, baseline frozen at tag `baseline-v1` (= `dd7f4d7`).

## Why the current version is being replaced

`output/star-trek.html` is one 621-line, 32 KB self-contained page containing, in a single
`<script>` block, the entire analysis: 14 series / 53 seasons of hand-assigned integer scores
(`sci`, `fi`), hand-asserted uncertainties (`sciUnc`, `fiUnc`), free-text justifications, the
rubric, and the "notable outliers" narrative — fused with ~150 lines of CSS and a Chart.js config.

Three concrete failures drive the redesign:

1. **Data, method and presentation are fused.** A score change means editing a 32 KB HTML file.
   Nothing can be reused, validated, or recomputed.
2. **The prose can silently drift from the numbers.** The uncertainty explanation is repeated in
   three places, and the outlier list is hand-written prose making claims about the data that
   nothing verifies.
3. **The scoring method is not defensible.** Whole-season holistic integers on 1–10 with a strong
   central tendency: the distribution piles up on 4–7 and, because a single holistic judgement
   tends to read "balanced", `sci ≈ fi` for many seasons — points land on the diagonal. The
   uncertainty is asserted by hand rather than measured. One number per season cannot be audited,
   cannot be reproduced, and cannot be re-derived from a different set of sources.

The redesign's central goal: **make every number traceable to a source, and let the reader choose
which sources a finding rests on.**

## Architecture — five separated layers

```
src/
  ingest/     source adapters, one per source, each behind a "scope" id
  resolve/    cross-source entity reconciliation -> stable internal IDs
  signals/    per-source-category, per-episode typed signals (with confidence)
  score/      versioned rubric applied to a chosen scope -> scores + measured uncertainty
  render/     static HTML generation (vendored Chart.js)
  validate/   schema, ranges, drift, reproducibility
data/
  schema/         JSON Schema for franchise / episode / signal / derived score
  franchises/     one directory per franchise (canonical, hand-curated)
  snapshots/      dated, immutable ingestion output
  derived/        computed scores, keyed by snapshot + rubric + scope
rubric/           rubric-v1.md, rubric-v2.md ... (codebook + weights, versioned)
output/           generated single-file HTML, published via GitHub Pages
```

A build runs `ingest -> resolve -> signals -> score -> render`, with `validate` gating each
hand-off. Pipeline order is fixed; each stage is independently runnable from the previous stage's
on-disk output, so you never re-hit a network source just to change the rubric.

## Data model

Hierarchy is **franchise → series → season → episode**, so many franchises coexist and one large
dataset is fine. `year` is a first-class field at every level.

```
Franchise  id, name, years, wikis[], sourceBindings{}
  Series   id, name, years, seasonCount, sourceIds{ wikidata, tmdb, imdb, tvmaze }
    Season number, year, episodeCount
      Episode  id, number, title, airDate, year, summaryRefs{}
```

Stable IDs use the Wikidata QID as the anchor (e.g. `Q1071` Star Trek: TOS) with IMDb `tconst`,
TMDB id and TVmaze id as cross-references. An episode id is `<seriesId>:S<season>E<episode>`.

Signals are stored per episode, per source category, each carrying its own confidence and
provenance pointer — never verbatim third-party text (see licensing):

```json
{
  "episodeId": "star-trek:tos:S1E14",
  "year": 1966,
  "signals": [
    { "axis": "sci", "sub": "resolution-mechanism", "value": 1,
      "category": "plot-summary", "source": "memory-alpha", "confidence": 0.7 },
    { "axis": "fi", "sub": "numinous-entity", "value": 1,
      "category": "plot-summary", "source": "memory-alpha", "confidence": 0.6 },
    { "axis": "sci", "sub": "technical-lexicon-density", "value": 0.42,
      "category": "dialogue", "source": "subtitles", "confidence": 0.9 }
  ]
}
```

## Source taxonomy, aggregated by category

Sources are grouped into categories because they answer different questions and carry different
reliability. A *scope* is a selection of categories (optionally narrowed to individual sources,
series, or a since-date), and scores are always computed for a declared scope.

| # | Category | Question it answers | Candidate sources | Notes |
|---|---|---|---|---|
| 1 | Metadata / canonical order | what exists, when, in what order | Wikidata (SPARQL, CC0), TMDB (attribution, non-commercial), TVmaze (free API), IMDb bulk TSV | IMDb free TSV availability/terms have shifted — verify before depending on it |
| 2 | Plot summary / narrative text | what the episode is about | Wikipedia (CC BY-SA), franchise wikis (Memory Alpha/Beta; verify per-wiki licence), TMDB overview, TVmaze summary | feeds rubric coding of the resolution mechanism |
| 3 | Verbatim dialogue / transcript | how the episode talks | subtitle corpora (OpenSubtitles via OPUS, ODC-BY), franchise transcript wikis | enables measured signals: technical lexicon density, technobabble rate, science-vs-magic vocabulary |
| 4 | Audience reaction / ratings | how viewers responded | IMDb episode ratings, Trakt (free, rate-limited), Reddit episode threads, RT audience score | used as *validation* and overlay, not as a direct score input |
| 5 | Professional reception | how critics responded | RT critics, Metacritic | validation only; licensing is grey, keep to derived values |
| 6 | Tropes / narrative structure | which narrative devices are present | TVTropes (CC BY-NC-SA — derived counts + attribution only, never verbatim text), Wikidata narrative properties | direct Sci/Fi signal, but the licence is why we store counts not text |
| 7 | Production context | sampling and reliability | episode counts, season length, budget, showrunner (Wikidata/IMDb) | drives *measured* uncertainty via sample size |

**Licensing constraint (important, the site is public):** the published site is non-commercial but
public. Wikimedia text is CC BY-SA and TVTropes is CC BY-NC-SA (share-alike). Therefore the repo
stores **derived numeric signals, IDs, and provenance pointers — never verbatim third-party
prose**. Raw licensed responses are cached outside the repo (git-ignored) and only normalised
derived values are committed. Attribution for every source category ships in the site footer.

## Fixing the scoring method

Replace "one holistic integer per season" with **episode-level, signal-based scoring and
aggregate up**. This is what removes the diagonal clustering and makes uncertainty real.

**Sci axis sub-signals** (science/engineering rigour):
- resolution mechanism — is the central problem solved by an in-world technical method?
- methodological framing — is there on-screen hypothesis / measurement / iteration?
- internal consistency — does the science respect established in-universe rules?
- technical lexicon density — measured from transcript, per 1k words.
- counter-signal — pseudoscientific handwave as the actual resolution.

**Fi axis sub-signals** (myth / magic / reality-bending):
- numinous or god-tier entity present and plot-relevant
- prophecy / destiny driving the plot
- explicitly magic-equivalent action
- reality- or format-bending (holodeck-as-real, musical, animation, multiverse)
- literal afterlife / religion as fact
- absurdist tonal break

Each sub-signal is coded per episode on an explicit codebook, either by rubric application to
source text or by measurement. Consequences:

- **Season scores become continuous** (`6.3`, not `6`), because they are means over episodes.
- **Uncertainty becomes measured, not asserted**: within-season dispersion of episode signals
  gives the spread, and sample size (episode count, post-knowledge-window airing) gives the
  confidence interval. `sciUnc` / `fiUnc` disappear as hand-set fields.
- **The diagonal problem disappears by construction**: axes are scored from disjoint sub-signal
  sets, so nothing pulls `sci` toward `fi`.
- **Portability**: the same rubric applies to any franchise; only the adapters and the
  franchise-specific wiki binding change.

## Scopes and versioning

Everything a chart depends on is explicit and pinnable:

```
score = f( franchise, snapshotDate, scope, rubricVersion )
```

- **Snapshot by date** — each ingestion run writes `data/snapshots/<franchise>/<YYYY-MM-DD>.json`.
  Snapshots are immutable; re-running the same day overwrites only with an explicit `--force`.
- **Timeline fields** — `year` on episode, season and series, so any view can be filtered or
  animated over air date, independent of ingestion date.
- **Derived outputs** — `data/derived/<franchise>/<snapshotDate>/<scopeHash>-<rubricVersion>.json`,
  so a published chart is reproducible from committed inputs.
- **URL-pinnable** — a chart can be addressed as
  `?franchise=star-trek&snapshot=2026-10-18&scope=metadata,plot-summary&rubric=v2`.

## Phasing

- **P0 — baseline (done).** Local `.git` was corrupt (empty object store, index pointing at
  missing blobs). The remote already held commit `dd7f4d7`; the working tree was verified
  byte-identical to `origin/main`, the repo was restored from `origin/main`, and `baseline-v1`
  was tagged. No work was lost.
- **P1 — one franchise, one series, thin vertical slice (done).** Schemas, adapters, the episode
  signal path, scoring and render, end to end on Star Trek: TOS. Wikidata was dropped from this
  slice: its Query Service was rate-limiting to 1 request/minute during an active outage, which is
  itself a design lesson — adapters must degrade gracefully and snapshots must be committed so the
  pipeline reproduces without the network. See "P1 status" below.
- **P2 — full Star Trek + a stronger instrument.** All Star Trek series, transcript and ratings
  categories, scope switching, sharper visuals.
- **P3 — second franchise.** Add a non-Star-Trek franchise to prove the framework is generic and
  the franchise hierarchy works with more than one branch.
- **P4 — validation and publish.** Drift checks, attribution footer, merge to `main` so Pages
  deploys.

## P1 status — thin vertical slice, done

Built and run end to end on Star Trek: TOS: 79 episodes, 2 sources, 316 measurements, rubric-v2.

**What works.** Dependency-free Python pipeline (`ingest → signals → score → render → validate`);
dated snapshots that carry facts and identifiers only; signals as the atomic unit of evidence;
scoring as a *mapping* over stored measurements, so re-scoring under a different rubric needs no
re-extraction; a published page that recomputes live as you toggle the source scope; and
validator gates for schema, ranges, episode-number contiguity, coverage, a licensing check, and
determinism.

**Measured result (rubric-v2, scope = tvmaze + wikipedia):**

| season | years | eps | Sci | Fi |
|---|---|---|---|---|
| TOS S1 | 1966–1967 | 29 | 2.80 ± 0.78 | 1.42 ± 0.62 |
| TOS S2 | 1967–1968 | 26 | 2.83 ± 0.90 | 1.58 ± 0.74 |
| TOS S3 | 1968–1969 | 24 | 1.93 ± 0.76 | 1.37 ± 0.66 |

Scores are continuous, uncertainty is measured from the episode distribution, and the axes come
from disjoint inputs so nothing pulls `sci` toward `fi`. Against baseline-v1's `6/6`, `6/6`, `4/6`,
three things stand out.

**Finding 1 — the Fi lexicon was blind, and that is now visible.** `rubric-v1` (lexicon only) scored
Fi at 0.83 / 1.30 / 0.94 with a median episode density of exactly **0**. Plot summaries do not
describe gods with mythic vocabulary; they *name* them ("Apollo", "Trelane"). Scraping vocabulary
alone therefore cannot see the Fi axis. `rubric-v2` adds a curated entity coder, which lifts Fi to
1.42 / 1.58 / 1.37 — and, because the entity list is a versioned, diffable file rather than a
number, the judgement is reviewable. Sci is unchanged between v1 and v2, as it should be.

**Finding 2 — at season level, TOS is statistically indistinguishable on this instrument.** The
95% intervals overlap for every pair of seasons on both axes. Baseline-v1's `6/6/6/6/4/6` asserted
season-level differences that this evidence does not support. That is the redesign working as
intended: it refuses to over-claim.

**Finding 3 — the answer depends on which source you trust.** With TVmaze summaries alone, S3 Sci is
**1.09 ± 1.20**; with Wikipedia plot sections alone it is **2.77 ± 0.95**. Same episodes, same
rubric, different source, materially different conclusion — and the confidence intervals widen when
only one source is in scope. Source-choice sensitivity is not a footnote here; it is the finding.

**Known limits of this slice.** Median source text is only ~115 words per summary, so per-episode
measurements are noisy and the seasons are close together — the next quality jump is transcripts
(thousands of words per episode) rather than more rubric tuning. And a lexicon measures
*vocabulary*, which is a proxy for, not a definition of, rigour: the judgement sub-signals
(`resolution-mechanism`, `internal-consistency`, …) remain reserved for `rubric-v3` with a real
coding coder.

## Open decisions

1. **Next franchise:** Doctor Who (chosen) — needs a `franchise.json`, an entity list, and a source
   binding; no pipeline changes.
2. **Transcript source** for P2: OpenSubtitles via OPUS (ODC-BY) vs a franchise transcript wiki —
   this is the highest-value quality improvement available.
3. **Judgement coder** for `rubric-v3`: LLM-coded episode coding against a published codebook, human
   spot-checks, per-episode confidence. This is what turns the reserved sub-signals into scores.

