# Sci-or-Fi redesign — design notes

Status: draft for review. Branch `redesign`, baseline frozen at tag `baseline-v1` (= `dd7f4d7`).
P2 is done: full Star Trek (15 series + 14 films) and Doctor Who (3 eras + 3 films) are ingested,
scored and rendered, each augmented with its franchise wiki — Memory Alpha for Star Trek, the Tardis
Wiki for Doctor Who — so the source scope is now `tvmaze + wikipedia + <franchise wiki>`.

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
  Film     id, name, year, wikipedia   -- a kind: "film" work with one one-entry season
```

A **film** is a series-like work (`kind: "film"`) with a single one-entry season, so it rides the
same pipeline as an episode. Films have no TVmaze entry, so their metadata is pinned in
`franchise.json` (article title + release year) and their text is a Wikipedia plot section fetched
by title. The entity lists that feed the Fi coder are keyed **per series**.

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
- **P2 — full Star Trek + Doctor Who (done).** Every official Star Trek series and film, and every
  Doctor Who era and film, ingested and rendered. See "P2 status" below.
- **P3 — a stronger instrument.** Transcripts (the highest-value quality jump), ratings/reaction
  categories as an overlay, and the reserved judgement sub-signals under a real coding coder.
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

## P2 status — full Star Trek and Doctor Who, done

Coverage now spans two franchises: **Star Trek** (15 official series, 1,001 TV episodes + 14 films
including the 2025 *Section 31*) and **Doctor Who** (3 eras — 1963–1989, 2005–2022, 2023– — 869 TV
parts + 3 films including the 1996 television film). A film is a `kind: "film"` work with one
one-entry season, so it flows through the same pipeline.

**Structural findings from doing it.**

1. **TVmaze is TV-only — it has no film entries at all** (every Star Trek film title returns 404).
   Films therefore need a different source: Wikipedia plot sections, fetched by *pinned article
   title* rather than by search. A nice side-effect is that film plot sections are 380–835 words,
   several times richer than TVmaze summaries, so films are measured on better text than episodes.
2. **The Wikipedia search hint was a latent bug.** It was hardcoded to
   `"<title> Star Trek: The Original Series episode"`. On a film title that query resolves to
   *List of Star Trek films*, which has no Plot section — so "Star Trek Generations" silently
   missed. The hint is now passed per series (`"<series name> episode"`), and films skip search
   entirely.
3. **Doctor Who's specials are not dropped.** TVmaze folds them into the numbered seasons for all
   three eras (verified: zero season-0 episodes), so no specials are lost — and the 1996 television
   film is genuinely absent from the 1963 series and is added as a film instead.
4. **A franchise-level entity list does not scale, and pretending otherwise would bias an axis.**
   With one curated list (TOS), running the entity coder over 17 other series would (a) import TOS
   gods into other shows and (b) leave every uncoded series' Fi score silently halved, because an
   entity sub-signal that matches nothing still counts in the denominator. Entities are now scoped
   per series, and a series without a list is coded **lexicon-only**, with the page's "Fi coder"
   column stating which. The scale problem is real and unresolved: it is the main open decision on
   the Fi axis (see below).
5. **Classic Doctor Who is thin, and the era is worth naming explicitly.** TVmaze lists its
   25-minute *parts* (median summary ~31 words) rather than serials. Per-part lexicon densities are
   correspondingly noisy; Wikipedia plot sections are the primary measurement for that franchise.
   *(Resolved in P4: parts are now grouped into serials at ingest.)*
6. **A search always returns *something*, so resolution needs a guard.** Accepting the first hit
   that has a Plot section resolved Lower Decks' "Second Contact" to a *Next Generation* episode
   article and Doctor Who's "Rose" to "The Star Beast" — real, wrong plots feeding real scores. An
   article is now accepted only if its title *is* the episode's title (with or without a
   disambiguator), preferring a disambiguator that names the series; an unresolved episode is left
   unresolved rather than filled with a different episode's plot.
7. **Plot sections split into sub-sections were being truncated to nothing.** The section
   terminator matched a heading of *any* level, so "Emissary (Star Trek: Deep Space Nine)", whose
   level-2 Plot is split into `=== Part I ===` / `=== Part II ===`, yielded no text and was
   silently dropped. The section now runs to the next heading of the same level. This is why
   resolution is verified by the *shape of the article*, not just by a search hit.
8. **Wikipedia's episode-article coverage is uneven within a franchise, and that changes the
   measurement basis per series.** TOS/TAS/TNG/DS9/VOY/ENT/DIS have per-episode articles; SNW, LD
   and Prodigy have only season pages; classic Who has serial articles (so only part-one titles
   resolve); the modern Who eras have per-episode articles. Each work's text basis — episodes
   resolved and median words per source — is published in the table, so a series measured on
   30-word summaries is visible as such rather than averaged in silently.

**What this unblocks.** The axis-framing question (full 0–10 vs zoom-to-data) is decided and
superseded in P3: the full 0–10 frame stays, the zoom toggle stays as an opt-in, and the real fix
turned out to be a validity floor plus a separately calibrated saturation and an opt-in rank scale.

### Augmenting Wikipedia with the franchise wikis

Wikipedia's coverage of a franchise is *not* a property of the franchise — it is a property of
Wikipedia's editorial scope, and it collapses exactly where the streaming era begins (Discovery
6/65, Picard 0/30, Lower Decks 0/50, Prodigy 0/39, Strange New Worlds 3/40). A franchise wiki
covers its own franchise completely. So two MediaWiki sources are now bound per franchise:

| franchise | wiki | section used | typical length |
|---|---|---|---|
| Star Trek | Memory Alpha (`memory-alpha.fandom.com`) | `Summary` | 1,200–9,500 words |
| Doctor Who | Tardis Wiki (`tardis.fandom.com`) | `Plot` | 750–2,400 words |

Both are the same MediaWiki API, so one adapter serves Wikipedia and the two wikis; only the
endpoint, the search hint and the section name differ. The section-name priority
(`plot` → `plot summary` → `synopsis` → `summary` → `story`) matters twice over. Tardis carries both a
deliberately spoiler-light `Synopsis` and a detailed `Plot`, so `plot` must win. And Memory Alpha
names the plot `Summary` while also carrying a **real-world** `Story` section under *Background
information* — so `summary` must beat `story`. Getting that last ordering wrong attaches a
plausible-looking number to the wrong text, which is the failure mode this whole redesign exists to
remove. The damage was bounded by an accident of the code (a section that strips to nothing falls
through to the next candidate, and Memory Alpha's `Story` sections are usually empty stubs), so only
two works were actually mis-measured: *Star Trek Beyond* (2,086 plot words → 589 words about casting)
and Voyager's *Caretaker* (5,469 → 568 words about the writers on set). Both are corrected, and the
episode-level medians are unchanged for every series that was already reading `Summary`.

The effect is large and one-directional: for Star Trek, Lower Decks went from 0/50 episodes resolved
to 3/3 in sample with ~3,000 words each, and Strange New Worlds from 3/40 to 3/4 in sample with
~4,800 words. Text length rises from a ~31-word TVmaze median to a ~2,000-word wiki median, which
narrows the per-episode noise that was the stated limit of the P1 slice.

Two consequences worth stating plainly. First, **the source scope is now a per-franchise decision**,
not a global list: the rubric remains the registry of known sources and owns their ordering, while
each franchise declares which of them cover it (`config.effective_sources`). Scoring the Tardis Wiki
against Star Trek would only add a source with zero measurements. Second, **films go through the same
wikis**, but by article title rather than by search: a film's wiki article usually carries the film's
title (all 14 Star Trek films resolve on Memory Alpha that way, 600–9,500 words each), and the few
that do not are pinned per film per wiki in `franchise.json` (`wikiTitles` — the 1996 Doctor Who film
is Tardis' "Doctor Who (TV story)"). Direct lookup is what makes this safe: there is no search result
to mismatch. TVmaze still contributes nothing for films, since it has no film entries at all.

**Classic Doctor Who was the remaining hole — fixed, see P4.** Tardis and Wikipedia both carry the *serial* article
("An Unearthly Child (TV story)", ~2,400 words), not a page per 25-minute part, so of 700 classic
parts only ~10 resolve on Wikipedia and ~19 on Tardis, and the rest fall back to ~31-word TVmaze
summaries. The measured text basis makes this explicit on the page: DW63 reads
`tvmaze 683/700·31w · wikipedia 10/700·375w · tardis 19/700·1190w`.
Two fixes are available: extract the serial article's per-part sub-sections (it has
`An Unearthly Child (1)`, `The Cave of Skulls (2)`, … headings), or group the parts into serials and
analyse at story level — which is arguably the right unit for 1963–1989 anyway, since a serial is
one story told over several weeks. The sub-section route is constrained by TVmaze's own titling,
which is inconsistent: 117 of the 700 parts are titled `"<part title> (<serial>, Part N)"` (so the
part title can be matched against a sub-heading) but 583 are titled just `"<serial>, Part N"`, leaving
only the part *index* to go on. Grouping at story level needs no such guess and would take Doctor Who
from 869 part-level works, most on 31-word summaries, to ~170 story-level works, most on 1,000–2,400
words of Tardis plot.

## P3 status — the scale, normalised, done

The P2 chart was technically correct and read badly: **three quarters of a 0–10 axis was empty.**
Half of all works sat under 2.5 on Sci and under 0.5 on Fi, so the plotted points were a smear along
the bottom-left, and the points at exactly 0 were drawn half outside the plot area. Two of those are
a scale problem and one is a rendering bug; all three are fixed, and the diagnosis is worth keeping
because it was not the diagnosis anyone expected.

**The complaint "movies and series are evaluated differently" is true, and the mechanism is neither
length nor the app.** Correlation between a source text's word count and its density across every
source and axis is `r = −0.012 … +0.018`: a 9,500-word Memory Alpha film page and a 2,100-word
episode page are already on the same footing, because the measurement is per 1,000 words. What
differs is **which sources exist for each work**:

| source | mean Sci | median words | share scoring exactly 0 |
|---|---|---|---|
| TVmaze | 1.30 | 33 | **92%** |
| Wikipedia | 2.82 | 416 | 31% |
| Memory Alpha | 2.83 | 2,110 | 3% |

TVmaze's "summaries" are 33-word teasers; 92% of TVmaze measurements contain no rubric term at all,
and its 90th percentile on Fi is 0.00. **It is not measuring anything — it is averaging zero into
every series.** Films have no TVmaze entry whatsoever, so they escaped that drag, which is exactly
the asymmetry that was visible on screen: the film-versus-series Sci gap was **+1.92 with TVmaze in
scope and +0.77 without it**. Roughly 60% of the apparent film advantage was the scope artefact; the
remainder is real, and film plots are genuinely more technology-driven.

**Fix 1 — a validity floor (`rubric-v3`).** A density is only a measurement if there was enough
prose to measure. A source that supplies fewer than `measurement.minWords` (200) words still counts
as *covering* an episode — that is published in the text basis as `measured/covered·median words` —
but contributes no score. This is a rule about measurement validity, not a policy about sources, so
it drops TVmaze's teasers without a special case and would equally catch any other thin source.
Dropping TVmaze outright was the alternative; the floor is better because it is general and because
it leaves the coverage visible.

**Fix 2 — saturation reclaimed from the corpus.** A sub-signal's `saturation` is the density that
scores 10. `rubric-v1`/`v2` fixed it at the 95th percentile of the *TOS reference corpus* — 79
episodes — which was defensible when TOS was the only thing measured and is not defensible now.
Saturation is now the 95th percentile of that sub-signal's own value across every valid measurement
in the repository, so 10 means "top 5% of what has actually been measured". Calibration is a
separate, re-runnable stage (`python3 -m sciorfi calibrate`) that writes a named rubric file, never
something `score` does on the fly: a score must be a function of a named rubric, and re-calibrating
must be a reviewable event. Current values:

| sub-signal | was | measured p95 | n |
|---|---|---|---|
| `technical-lexicon-density` | 20.0 | 15.019 | 2,016 |
| `mythic-lexicon-density` | 9.0 | 4.854 | 2,016 |
| `numinous-entity` | 6.0 | 23.654 | 152 |

**Fix 3 — a rank scale, on request.** Measured scores are densities and densities bunch, so no
rescaling of the measured axis makes it fill the frame honestly. Each season therefore also carries
a **percentile rank** per source, averaged over the selected sources exactly as the measured score
is, and the sidebar can switch the chart to it. The rank is uniform by construction, which is the
point: it uses the full range and cancels the systematic level differences between sources. It is
opt-in and clearly labelled, because a rank says *where a work sits*, not *how much science it
contains* — the measured scale is still the default.

**Fix 4 — three honesty bugs found while looking.** (a) A bubble centred on 0 was drawn half outside
the plot area; the full-scale axes now start slightly below 0 and end slightly above 10 while the
ticks still read 0–10. (b) A season measured on a *single* episode had `ci95 = 0.0`, so the least
certain point on the chart was drawn as the smallest, most confident bubble — an exact inversion of
the truth. An interval is now `null` when there is only one measurement, drawn at a fixed large size
and reported as `n/a`, never `±0.00`. (c) Films were sized by the same interval; a film is one work,
not a season average, so films are now drawn at a fixed size.

**Fix 5 — the absence is published.** The floor makes the chart honest by *removing* works, and a
silently emptier chart would be worse than a crowded one. `derived.unmeasured` counts works with no
measurement at all, and the page states it under the chart. The result is the most important
number in this pass:

> **688 of 872 Doctor Who works (79%) have no measurement at all** — 682 of them classic Who —
> against 60 of 1,015 for Star Trek.

That is not damage from the floor; it is the floor *exposing* what P2's classic-Who section already
suspected: 700 parts rested on 31-word TVmaze teasers and nothing else. Star Trek, whose streaming
era is now covered by Memory Alpha at 1,200–9,500 words, is unaffected (6% unmeasured, mostly
*Star Trek: Scouts* which has no source text at all).

**Result (season level, full source scope).**

| | axis | median | p90 | above 4 | seasons |
|---|---|---|---|---|---|
| Star Trek | v2 Sci | 2.09 | 3.80 | 7% | 69 |
| Star Trek | **v3 Sci** | **3.66** | **5.29** | **42%** | 66 |
| Star Trek | v2 Fi | 0.69 | 1.66 | 1% | 69 |
| Star Trek | **v3 Fi** | **1.47** | **2.71** | **6%** | 66 |
| Doctor Who | v2 Sci | 0.52 | 1.43 | 2% | 44 |
| Doctor Who | **v3 Sci** | **1.19** | **2.91** | **9%** | 22 |
| Doctor Who | v2 Fi | 0.83 | 2.12 | 0% | 44 |
| Doctor Who | **v3 Fi** | **2.69** | **4.00** | **14%** | 22 |

The rank scale lands where a rank must: median ≈ 5, p90 ≈ 8, on both franchises. The measured scale
now uses its range without being rescaled per corpus. And the Fi axis is no longer a flat line at
zero — its median moved from 0.69 to 1.47 on Star Trek, mostly because the mythic-lexicon saturation
was recalibrated down (9.0 → 4.854) while the entity saturation was recalibrated up, correctly
trading a hand-guessed constant for a measured one.

## P4 status — classic Doctor Who at story level, done

P3's floor exposed the hole; this closes it. Classic Who (1963–1989) was broadcast as 700
twenty-five-minute **parts**, and TVmaze lists it that way — each part with the serial's summary, a
median 31 words. The sources that actually carry the era do the opposite: Tardis and Wikipedia both
have **one article per serial**, with the parts as sub-headings, 1,000–2,400 words of plot. So
measuring parts meant measuring a fifth of a story's text against a whole story's plot, and it put
700 of the corpus's 872 Doctor Who works on the thinnest text we have.

A series can now declare `grouping: "serial"`. At ingest the parts are collapsed into one work per
serial, numbered by broadcast order; a part with no `", Part N"` label (`Mission to the Unknown`, a
single-episode serial) becomes a one-part serial named after itself. TVmaze's titling is inconsistent
in exactly three ways and all three are handled: `"The Cave of Skulls (An Unearthly Child, Part
Two)"` (117 parts), `"The Savages, Part One"` (568), and `"The Trial of a Time Lord, Part Two (The
Mysterious Planet)"` (14, the one serial that names its parts in parentheses). All 700 parts parse
into **155 serials**, and the count of original transmission parts is carried per season rather than
lost (`partCount`), so the table can say "8 serials" and still know they were 42 parts.

Two smaller changes came with it. The TVmaze lookup in `signals` now matches by episode **id**
instead of by `(season, episode)` number, because a grouped series renumbers its works 1..n within
the season and would otherwise match the wrong parts. And the schema validator learned `type: ["x",
"null"]`, so `partCount` can be honestly absent for ungrouped series.

**Result — the coverage hole is closed, and Doctor Who is no longer mostly missing.**

| | P3 (parts) | **P4 (serials)** |
|---|---|---|
| Doctor Who works with no measurement | 688 of 872 (79%) | **9 of 327 (3%)** |
| classic Who works measured | 35 of 700 | **152 of 155** |
| classic Who text (Tardis) | 31-word teasers | **2,434-word median** |
| Doctor Who seasons on the chart | 22 | **42** |

| | axis | median | p90 | above 4 | seasons |
|---|---|---|---|---|---|
| Star Trek | Sci | 3.80 | 5.49 | 47% | 66 |
| Star Trek | Fi | 1.41 | 2.58 | 5% | 66 |
| Doctor Who | **Sci** | **1.34** | **2.86** | **2%** | 42 |
| Doctor Who | **Fi** | **2.12** | **3.90** | **10%** | 42 |

The recalibration moved with the corpus, which is the point of it being a stage rather than a
constant: `technical-lexicon-density` 15.019 → 14.472 and `mythic-lexicon-density` 4.854 → 5.097
(n 2,016 → 2,289); `numinous-entity` held at 23.654 because no new series is entity-coded.

Doctor Who's Sci axis stays low, and that is now a real finding rather than a gap: classic Who is
science-fiction television whose plots are written as adventure serials — the vocabulary is
"journey", "capture", "escape", not "tachyon" and "dilithium". The DW63 seasons that do score high
on Sci are the ones that literally put a scientist or a machine at the centre of the story, which is
what the axis is supposed to measure. Star Trek is the opposite: even its mythic episodes are
narrated in technical language, which is why it sits a full point higher on Sci with a much wider
spread.

## P5 status — Fi coded across both franchises, done

P4 fixed coverage; P5 fixes the other axis. Fi (myth, magic, reality-bending) is computed from two
coders that see different things. The **mythic lexicon** counts mythic vocabulary in plot prose — it
is dense and works on every work with text. The **entity coder** counts named mythic entities
(`Apollo`, `V'Ger`, `the Eye of Harmony`) — it exists because plot summaries *name* gods rather than
describing them mythically, so it recovers myth the lexicon cannot see. In `rubric-v2` that list
existed for TOS alone, which made Fi comparable inside TOS and not across anything else. (It was
explicitly *not* a second opinion: `r(lexicon, entity) = +0.34`, and 6% of measured rows score zero on
the lexicon while firing an entity.)

**Curating the lists.** The lists are hand-curated per series with LLM assistance, because "is this
named thing treated as a god in this franchise?" is a judgement about story, not a string-matching
task. The method was five parallel LLM passes over each franchise's episode articles with a fixed
spec — universe of discourse only (gods, prophets, cosmic powers, reality-altering artefacts and
places), never ordinary characters; every proposal needs a name, the work it belongs to, and evidence
counts (hits, works, texts, words) — followed by a merge and an explicit pruning pass. Proposals with
no hits, generic terms ("the Force", "the Other"), and ordinary named characters were dropped. The
result is **84 Star Trek entities across 14 works** (TOS 25, TNG 14, DS9 9, VOY 8, DIS 6, LD 6, TAS 4,
ENT 3, PIC 3, PRO 2, SNW 1, three films) and **59 Doctor Who entities across 4 works** (dw63 38, dw05
13, dw23 7, the 1996 film 1).

Two details matter for correctness. A term is matched as a whole word with plural/possessive
tolerance, and `caseSensitive` is set for names that are also common nouns — `the Oracle`, `the
Caretaker`, `the Prophets`, `the Beast`, `the Great One` — so "the caretaker of the colony" does not
count as a mythic entity. Each entity carries its evidence and sits next to a short `codebook` in
`data/franchises/<franchise>/entities.json`, so a reviewer can challenge any single name without
re-running the pipeline.

**Combining the two coders: `max`, not a mean.** Averaging a dense coder with a sparse recovery coder
punishes the recovery coder for being silent: its many zeros are "I have nothing to add", not "no
myth here". The Fi axis therefore takes `max(lexicon, entity)`; the rule is a rubric field
(`axes.fi.combine`), with the reasoning in `combineNotes`, so reverting to a weighted mean is a
one-line change in a versioned file. At the current lists the difference is large and one-directional:

| Fi, season level | mean (weighted) | **max** |
|---|---|---|
| Star Trek median / p90 | 1.07 / 2.56 | **1.84 / 3.81** |
| Doctor Who median / p90 | 1.62 / 3.76 | **2.79 / 5.10** |

The entity coder is not a list-length artefact: `r(entity-list length, mean Fi density) = +0.05`
across Star Trek's 14 entity-coded works (Doctor Who's `+0.58` rests on three points and is not
evidence).

**Result.** With entity lists everywhere and `max`, and with the entity saturation reclaimed from the
enlarged corpus (**23.654 → 12.285**, n = 2,233; technical 14.472 and mythic 5.097 unchanged, n =
2,289), the season-level picture is:

| | axis | median | p90 | above 4 | seasons |
|---|---|---|---|---|---|
| Star Trek | Sci | 3.80 | 5.32 | 47% | 66 |
| Star Trek | **Fi** | **1.84** | **3.81** | **6%** | 66 |
| Doctor Who | Sci | 1.34 | 2.86 | 2% | 42 |
| Doctor Who | **Fi** | **2.79** | **5.10** | **24%** | 42 |

Fi is no longer flat, and it now separates works the way the concept should. Doctor Who sits a full
point above Star Trek on Fi with a wider spread — a show whose premise is an ancient traveller with a
magic box is mythic in its bones — while Star Trek keeps the higher Sci axis. The films are a clean
sanity check on a single-work scale: **The Motion Picture** (V'Ger) scores Sci 9.99 / Fi 8.09, a
god-machine story told in technical language, and **The Final Frontier** (Sha Ka Ree) scores Sci 1.00
/ Fi 7.85, the franchise's least scientific and most explicitly theological film. The top Fi seasons
are the expected ones: DIS S2 (the Red Angel) 4.49, TOS S2 3.92, DS9 S7 3.89; the top Fi episodes
include TOS "Who Mourns for Adonais?", TNG "Rightful Heir", DS9 "The Reckoning", and classic Who's
"Image of the Fendahl" and "The Face of Evil", all at 10.00.

Fi remains a *text* measure, not a judgement coder: it counts how much the plot summary turns on myth,
and an episode whose myth is visual rather than narrated will still read low. That is the gap the
judgement coder in open decision 4 is meant to close.

## P6 status — classic Who split by Doctor, done

The last scope question from P4 was whether 1963–1989 should stay one `dw63` series or be split by
lead actor. It is now split. A season average over 26 years and seven leads answers "how mythic is
classic Who?" but not "which Who?", and the show's relationship to its own science visibly changes
with the actor and production team.

The split lives in `franchise.json`, not in code. The `dw63` entry keeps the TVmaze id, wiki hint and
entity list, and declares `split.eras`: seven entries, each with an `id`, `short`, `name`, `doctor`,
`years`, pinned `color` and a `from: [season, serial]` start. Each era runs until the next era's
start (half-open), and the container itself is metadata — it never appears in a snapshot or on the
chart. Boundaries are on the **serial**, not the season: season 4 splits at *The Tenth Planet* /
*The Power of the Daleks* and season 21 at *The Caves of Androzani* / *The Twin Dilemma*, so no story
is attributed to the wrong Doctor. An era that begins mid-season renumbers its own works from 1, so
every `(series, season)` still has a contiguous 1..n; the season label stays the broadcast number
(the Second Doctor's path starts at S4). `expectedSerials` per era is checked by `validate`, so a
changed upstream catalogue fails loudly instead of silently shifting a boundary.

Eras inherit three things so that splitting changes *nothing else*: the TVmaze catalogue, the wiki
search hint, and the curated `dw63` entity list (`entityGroup`). Fi therefore uses the same 38
entities for every classic Doctor — the coder did not change, only the grouping. Colours are pinned
per era and for `dw05`/`dw23` so the other series do not shift when the palette index moves.

| era | Doctor | years | serials | seasons | Sci median | Fi median |
|---|---|---|---|---|---|---|
| DW1 | William Hartnell | 1963–1966 | 29 | 4 | 1.35 | 1.81 |
| DW2 | Patrick Troughton | 1966–1969 | 21 | 3 | 1.31 | **1.19** |
| DW3 | Jon Pertwee | 1970–1974 | 24 | 5 | 1.45 | 3.39 |
| DW4 | Tom Baker | 1974–1981 | 42 | 7 | **2.36** | 3.00 |
| DW5 | Peter Davison | 1982–1984 | 19 | 3 | 2.01 | 3.61 |
| DW6 | Colin Baker | 1984–1986 | 8 | 2 | 1.03 | 0.89 |
| DW7 | Sylvester McCoy | 1987–1989 | 12 | 3 | **0.68** | **4.88** |

The split turns the aggregate into a reading of the show. McCoy's era is the most mythic and least
technical by a wide margin (Fi 4.88, Sci 0.68): the late 1980s stories are explicitly about gods,
ancient evil and the Doctor's own mythology (Fenric, the Gods of Ragnarok, the Caves of Androzani's
aftermath), not about machines. Pertwee and Davison sit high on Fi too. Troughton's "base under
siege" era is the least mythic (Fi 1.19), and Tom Baker's is the most technical (Sci 2.36) with
individual seasons that swing hard — S14 Fi 5.59 (*The Masque of Mandragora*, *The Hand of Fear*,
*The Deadly Assassin*) and S16 Fi 6.67 (the Key to Time arc) against S12 Fi 0.65.

Two cautions. Each era has only 2–7 seasons, so these medians are descriptive, not estimates with
useful precision; the per-season intervals on the page are the honest unit. And Colin Baker has only
two measured seasons (S23, *The Trial of a Time Lord*, is one 14-part serial and is unmeasured), so
DW6's 0.89 rests on thin evidence and is the one era where the number should not be quoted alone.

The franchise totals barely move — Doctor Who Fi median 2.79 → 2.69, Sci 1.34 → 1.32 — because the
split redistributes the same measurements. What changes is that the spread is now attributable: the
44 "seasons" on the page are work-seasons, and the two boundary seasons (S4, S21) legitimately appear
under both Doctors they contain.

## P7 status — the scale made usable and source-proof, done

Two complaints, both checked against the data before anything was changed: the plotted score moved
with *which sources resolved a work* (so series were not comparable), and the top of the 0–10 axis
was still unused (so the scale did not read as a scale). They are two different failures with two
different fixes, and both are now declared in `rubric-v4`.

**The source-mix confound is real, and the headline example proves it.** `Starfleet Academy` S1
scored Sci 2.95 — level with `Strange New Worlds` — but it was measured on 10 Wikipedia articles and
7 Memory Alpha summaries, and those two sources disagree about the *same episodes*:

| work | source | Sci | per-source rank | n |
|---|---|---|---|---|
| SFA S1 | memory-alpha | **4.13** | 5.83 | 7 |
| SFA S1 | wikipedia | **2.13** | 1.98 | 10 |
| SFA S1 | *combined* | 2.95 | 3.57 | 17 |
| SNW S1 | memory-alpha | 3.18 | 3.11 | 9 |

SNW is almost entirely Memory Alpha; SFA is half Wikipedia, and Wikipedia's terse, zero-heavy plot
sections pulled it down. It is not one series: across 96 multi-source Star Trek works the median
per-source spread is **0.83** and it runs to **5.66** (`Generations`: Memory Alpha 4.34, Wikipedia
10.00). A density is measured *per source*, and sources are not on one scale, so a raw mean across
sources silently rewards or punishes a work for its coverage.

**Fix 1 — `scale.standardise: "quantile-per-source"`.** Before a density is scored, its
within-source percentile is read back off the **franchise's pooled** density distribution, so every
source in a franchise shares one scale and a work's figure no longer depends on its source mix.
`calibrate` writes the 21-point quantile grid per (franchise, source, sub-signal) alongside each
sub-signal's saturation, so the score is still a pure function of the named rubric. It is
**per-franchise, not global**, and that is a deliberate design call: Tardis only ever measures Doctor
Who, so folding it into a Star-Trek-dominated pool would assert that Who is as technical as Trek —
inverting P5's finding. Standardising within a franchise removes the source effect and keeps the
franchise difference.

**Fix 2 — `scale.transform: "log"`.** Saturation is the p95 of a heavy-tailed density, so the
median of a sub-signal sits near a fifth of saturation and a *linear* map leaves the top of the axis
empty — an artifact that reads as "nothing is scientific". The map is now
`frac = min(1, ln(1 + d) / ln(1 + saturation))`: order-preserving, saturation still exactly 10 (the
top 5% stays the top 5%), but the body of the distribution spreads upward. Saturation stays a
**global** p95 on purpose — it is what keeps `Doctor Who`'s Sci below `Star Trek`'s.

**Result (season level, full source scope, v3 → v4).**

| | axis | median | p90 | above 5 | seasons |
|---|---|---|---|---|---|
| Star Trek | Sci | 3.92 → **6.18** | 5.49 → **7.86** | 18% → **74%** | 66 |
| Star Trek | Fi | 1.93 → **2.38** | 3.89 → **5.64** | 3% → **11%** | 66 |
| Doctor Who | Sci | 1.32 → **3.00** | 2.86 → **4.98** | 2% → **9%** | 44 |
| Doctor Who | Fi | 2.76 → **3.47** | 5.10 → **5.96** | 14% → **23%** | 44 |

The Sci axis now uses its range and the franchise difference survives (Who Sci 3.00 vs Trek 6.18).
**Fi deliberately stays low**: most episodes contain no myth at all, so a full Fi axis would claim
the median episode is half-mythic, which is false. Filling a scale is only right where the emptiness
is an artifact of the mapping, not where it is the finding.

**What this did *not* fix, and should not pretend to.** SFA still reads ~1 point under SNW
(4.82 vs 5.79). Standardisation cannot fix genuine disagreement between sources — only systematic
level differences. SFA's Memory Alpha articles are ~1,300 words against SNW's ~4,600, and its
Wikipedia coverage is 10 thin articles (574-word median, 40% containing no rubric term). That is a
**coverage/quality** gap, not a scaling one: the lever is transcripts (open decision 3) or a
per-source reliability weight, not another constant. The per-source spreads are now visible in the
`text basis` column, so the fragility is stated rather than hidden under a smoothed number.

## Open decisions

1. **Scaling the Fi coder — closed by P5.** Entity lists now exist for every series with text in both
   franchises, the Fi axis combines its two coders by `max`, and films are entity-coded where their
   story names a mythic entity. See P5 for the method and the mean-vs-max numbers. Nothing in the
   pipeline treats a series without a list as an error: it simply stays lexicon-only, and the page's
   "Fi coder" column says so.
2. **Franchise scope edges.** Currently excluded, and easy to add on request: Doctor Who spin-offs
   (Torchwood, The Sarah Jane Adventures, Class, K9) and animated webcasts (Dreamland, Shada, Real
   Time, Scream of the Shalka); Star Trek fan productions (Continues, Renegades, Odyssey).
3. **Transcript source**: OpenSubtitles via OPUS (ODC-BY) vs a franchise transcript wiki — this is
   the highest-value quality improvement available, and it mostly fixes the thin-text problem above.
4. **Judgement coder** for a later rubric: LLM-coded episode coding against a published codebook,
   human spot-checks, per-episode confidence. This is what turns the reserved sub-signals into
   scores.
5. **Classic Doctor Who — closed by P4.** Grouping the parts into serials restored 152 of 155 classic
   stories to measured status on 1,000–2,400 words of Tardis plot. The alternative (fetch the serial
   article for each part) was rejected because it would give every part of a serial the same text and
   so invent a per-part signal that does not exist.
6. **Splitting the classic era by Doctor — closed by P6.** `dw63` is now seven works (`dw1`–`dw7`),
   cut at serial boundaries so the two straddling seasons are attributed correctly, each with its own
   path on the chart. The judgement about what a "series" is settled on: one lead actor, because that
   is the unit in which the show's relationship to its own mythology visibly changes.

