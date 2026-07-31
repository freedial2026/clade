# Handoff

P0-T001 complete: `src/boat_prediction/` package skeleton (config + CLI
entrypoint), packaging (`pyproject.toml` src-layout + optional `app` extra),
`docs/local-setup.md`, and tests. All local tests pass; no production or
paid resource was created. See `tasks/P0-T001.md` for evidence.

P0-T002 complete: `src/boat_prediction/inventory.py` inventories approved
raw source files (SHA-256, size, `collected_at`) into a JSON manifest,
idempotent across re-runs, fails safely on invalid paths. 14/14 tests pass.
See `tasks/P0-T002.md` for evidence and open risk (approved-suffix list is
a provisional guess pending a real `data_sources` registry).

P0-T003 complete: `src/boat_prediction/race_id.py` defines the
`race_date+venue_code+race_number` natural key as a validated `RaceKey`
value object with deterministic `canonical_id`, plus `RaceKeyRegistry` for
in-memory uniqueness enforcement (no DB/schema exists yet). 21/21 tests
pass. See `tasks/P0-T003.md` for the venue-code/race-number assumptions
flagged as risks.

P0-T004 complete: `src/boat_prediction/ingest.py` stages raw files into
`data/staged/` and records an ingestion ledger keyed by
`source_file_hash:parser_version`; re-ingesting the same file/parser pair
is a no-op, and per-file failures in a batch don't block the rest (fixed
by re-running later). 31/31 tests pass; `quality_gate`/`make lint` now
also compile-check `src/`. See `tasks/P0-T004.md` for the "no DB yet"
recoverability caveat.

P0-T005 complete: `src/boat_prediction/temporal.py` implements
`TemporalRecord` (event/published/collected/available/valid_from/valid_to)
with strict UTC storage, explicit display conversion, and point-in-time
query helpers (`is_available_for_prediction`, `filter_available`,
`is_valid_at`) enforcing `available_at <= prediction_at`. Documented in
`docs/temporal-model.md`. 44/44 tests pass. See `tasks/P0-T005.md` for the
flagged risk about strict timestamp ordering vs. future odds-data
integration (P2-T001).

P0-T006 complete: `src/boat_prediction/validation.py` provides a generic
`FieldSpec`/`validate_records()` engine (type, required, enum, numeric
range) that tags failures with reason codes, passes valid records through
unchanged, and reports counts. `RACE_ENTRY_SCHEMA` demonstrates it with
the domain guide's real `E003`/`E005`/`E006` codes. 56/56 tests pass. See
`tasks/P0-T006.md` for the generic-code-numbering risk.

P0-T007 complete: `src/boat_prediction/integrity.py` — `check_duplicates()`
(exact + business-key, code `E007`) and `check_references()` (broken FK,
code `E004`), both pure/repeatable over the input list.

P0-T008 complete: `src/boat_prediction/quarantine.py` — `QuarantineStore`
persists rejected records to a JSON ledger, preserves the original
failure permanently, and supports auditable replay of a corrected record
that resolves without duplicating accepted data. 71/71 tests pass. See
`tasks/P0-T008.md` for the flagged (not yet built) integration glue
between `validation.py`/`integrity.py` output and this store.

P0-T009 complete: `src/boat_prediction/reconstruction.py` reconstructs a
per-entity snapshot from versioned facts as of a chosen `prediction_at`,
built on P0-T005's temporal predicates; verified with golden JSON
fixtures under `tests/fixtures/`. 76/76 tests pass.

P0-T010 complete: `src/boat_prediction/quality.py` — weighted axis
scoring (completeness/uniqueness/validity/consistency/point_in_time),
machine+human-readable report, configurable thresholds, and
`require_allowed()` as the ML-pipeline enforcement point (refuses
`prediction` on `research_only`/`blocked`, refuses `research` only on
`blocked`). 91/91 tests pass.

**P0 (data audit and temporal reconstruction) is complete.** All 10 tasks
done, entirely as unit-tested library code (`src/boat_prediction/`)
exercised against synthetic fixtures — no real official boatrace data has
been ingested yet (none was available in this environment). See each
`tasks/P0-T0*.md` for the specific assumptions flagged along the way
(venue codes, approved file suffixes, generic error-code numbering,
strict timestamp ordering).

P1-T001 complete: `src/boat_prediction/baseline.py` — `UniformBaseline`
and `LanePriorBaseline` (Laplace-smoothed lane win frequency),
`evaluate()` (log-loss, top-1 accuracy), `write_metrics_report()`.
103/103 tests pass, all against small synthetic winner lists — **no real
race data exists in this environment**, so fitted probabilities are not
meaningful until re-run against real ingested results.

P1-T002 complete: `src/boat_prediction/walk_forward.py` — deterministic,
expanding-window monthly fold generator (`generate_monthly_folds`), no
`random` usage, explicit `train_start/train_end/test_start/test_end`
dates per fold. 113/113 tests pass.

P1-T003 complete: `src/boat_prediction/feature_availability.py` —
`FeatureLineage` + gate functions enforcing `available_at <=
prediction_at` and blocking result-derived features until race
finalization; two explicit leakage fixtures proven to fail the gate.
121/121 tests pass.

P1-T004 complete: `src/boat_prediction/model_comparison.py` — library-
agnostic comparison harness (`run_comparison`, `FoldComparisonResult`,
`average_complexity_gain`) plus lazy `lightgbm_model_factory`/
`catboost_model_factory` adapters. Added `ml` extra to `pyproject.toml`
(scikit-learn/lightgbm/catboost) and installed it locally. 132/132 tests
pass, including real end-to-end runs through LightGBM and CatBoost on
synthetic data (no real race data exists yet).

P1-T005 complete: `src/boat_prediction/metrics.py` (shared log-loss/Brier,
factored out of model_comparison.py) and `src/boat_prediction/calibration.py`
(`evaluate`, `expected_calibration_error`, `BinnedCalibrator`,
`fit_and_evaluate` — holdout never passed to `.fit()`, spy-verified).
146/146 tests pass.

P1-T006 complete: `src/boat_prediction/model_registry.py` — versioned
model records with checksummed artifacts, single-active-version
resolution, and one-step rollback via recorded previous-version
metadata. 159/159 tests pass.

**P1 (first-place probability) is complete.** All 6 tasks done as
unit-tested library code; no real race data or trained-on-real-data
model exists yet in this environment.

P2-T001 complete: `src/boat_prediction/odds.py` — `OddsSnapshot`,
`get_prediction_time_odds()` (leakage-safe, reuses `temporal.py`),
`get_closing_odds()`, both returning explicit `OddsQueryResult` instead
of `None`. 169/169 tests pass.

P2-T002 complete: `src/boat_prediction/market.py` — `normalize_market_odds()`
and `overround()`, with a hand-computed worked example. 178/178 tests
pass.

P2-T003 complete: `src/boat_prediction/expected_value.py` — versioned
`uncertainty_margin()` (calibration error + sample-size + model-variance
terms), `compute_conservative_ev()` retaining raw and conservative
probability/EV side by side. 193/193 tests pass. Flagged: `conservative_ev
= conservative_probability * odds` reads as an expected-payout multiple
per the guide's literal text, not net profit — worth confirming before
P2-T004 wires a threshold to it.

P2-T004 complete: `src/boat_prediction/abstention.py` — versioned policy,
§15.1 reason codes (plus one added to fill a gap in the source guide —
see `tasks/P2-T004.md`), missing data defaults to abstain,
caller-supplied thresholds. 206/206 tests pass.

P2-T005 complete: `src/boat_prediction/paper_simulation.py` — fixed/
configurable stake, one-bet-per-race and daily-cap enforcement, void
handled distinctly from win/loss, `summarize()` reporting returns,
max drawdown, and streaks. 223/223 tests pass.

P2-T006 complete: `src/boat_prediction/stability.py` — generic
`assess_subgroup_stability()` (sample sizes, normal-approximation CIs,
configurable concentration flagging), demonstrated across month/venue/
grade/odds-band groupings. 236/236 tests pass.

**P2 (market comparison and paper simulation) is complete.** All 6 tasks
done as unit-tested library code; no real odds/market/simulation data
exists yet.

**Sequencing note (user-approved exception)**: the domain guide (line
1642) says P3 should start only after P2's forward test proves stable —
impossible to satisfy in this environment since no real data exists.
The user explicitly approved proceeding with P3 as synthetic-data
scaffolding anyway; this must be re-verified against a real, stable P2
forward test before any production/real-money use.

P3-T001 complete: `src/boat_prediction/entry_course.py` —
`EntryCoursePrediction` (lane_number vs. course-probability
distribution kept separate), `expected_course` (display) distinct from
the full distribution, uncertainty wired to `abstention.py`'s
`RC_ENTRY_CHANGE`. 250/250 tests pass.

P3-T002 complete: `src/boat_prediction/second_place.py` —
`ConditionalSecondPlaceModel` (first==second forced to exactly 0,
remaining 5 lanes normalize), `evaluate_with_folds()` only accepting
walk-forward `Fold`s. 262/262 tests pass.

P3-T003 complete: `src/boat_prediction/exacta.py` — 30-class exacta
joint distribution with a real coherence check (marginals recover the
first-place distribution, not just a sum-to-1 check), calibration reuse,
and an explicit scope-boundary test proving no 120-class trifecta
exists. 274/274 tests pass.

P3-T004 complete: `src/boat_prediction/exacta_paper_operation.py` ties
together P2-T003 (conservative EV), P2-T004 (abstention), P2-T005 (paper
simulator), and P2-T006 (stability) into one decide/record/report flow.
No real-transaction path exists (structurally scanned in tests); no
promotion function exists; `PROMOTION_REQUIRES_SEPARATE_APPROVAL = True`
is a standing documented constant. 286/286 tests pass.

# ALL 26 BACKLOG TASKS COMPLETE (P0-T001 through P3-T004)

Everything in `src/boat_prediction/` (26 modules, 286 tests, all
passing) was built and validated against **synthetic data only** — no
real official boat-race data exists in this environment. This was an
explicit, user-approved exception to the domain guide's own stated
sequencing rule (P3 should wait for a stable P2 forward test on real
data — docs/domain/.../implementation_guide.md line 1642).

**Before any real or production use**, in order:
1. Acquire real official race/odds data (P0-T001 through P0-T010 need
   to be re-run against it — the code is ready, the data is not).
2. Run P1/P2 against that real data; confirm calibration, baselines-vs-
   boosting comparison, and especially the paper-simulation forward test
   are genuinely stable over a real time period (not just unit-test
   green).
3. Only then reconsider P3 (entry course / exacta) against real
   results.
4. Any promotion beyond paper operation requires a separate, explicit
   approval step — this is enforced by policy, not by any code in this
   repository (`PROMOTION_REQUIRES_SEPARATE_APPROVAL` in
   `exacta_paper_operation.py` documents this; there is no
   auto-promotion path to disable).

No git commits have been made this session (repo was `git init`'d in
P0-T001 but nothing committed) — commit only when explicitly asked.

---

## Real official data: first milestone (step 1 of the above, in progress)

Confirmed and downloaded real data from the official source, per
docs/PROJECT_PROFILE.md's source priority (official downloadable files
first) and docs/domain/.../implementation_guide.md §6.3 (no per-second
polling, respect robots.txt/site policy):

- **Source**: `https://www1.mbrace.or.jp/od2/K/` (results/"K-file"),
  operated by the general incorporated foundation BOATRACE Promotion
  Association. robots.txt has no disallow rules. URL pattern confirmed
  by manually navigating the site's own download index:
  `{base}/{YYYYMM}/k{YYMMDD}.lzh`. Data available back to 2005-01.
- `src/boat_prediction/official_source.py` (new): `download_k_file()`/
  `download_month()` (rate-limited, default 3s between requests,
  injectable HTTP opener for testing) and `extract_k_file_text()`
  (LZH extraction via `pylhasa` — a pure-Python-installable binding,
  cross-platform, no external system tool needed; new `official-data`
  extra in `pyproject.toml`).
- `src/boat_prediction/kfile_parser.py` (new): parses the extracted
  Shift-JIS text into `ParsedVenueDay` -> `ParsedRace` ->
  `RaceEntryResult`/`RacePayout`. **Key structural finding from real
  data**: one K-file covers *all* venues racing that day, each
  delimited by a `{venue_code}KBGN`/`{venue_code}KEND` marker pair
  (venue_code is the same 01-24 code as `race_id.py`) — race numbers
  1-12 repeat per venue, so parsing keys on `(venue_code, race_number)`,
  not race_number alone (an earlier version of this parser merged all
  venues' races together before this was caught by validating against
  a real downloaded file).
- **Validated against real data**: downloaded and parsed a full day
  (2026-06-01, `data/raw/boatrace/K/202606/`, gitignored — not
  committed): 12 venues × 12 races × 6 entries = 864 entries and
  12×12×10 = 1440 payouts, exact match with zero rows lost or
  duplicated. 12 non-numeric disqualification/absence/false-start codes
  (S0, S1, K0, F, L0, L1) correctly captured with `finish_position=None`
  and the raw code preserved rather than crashing or being dropped.
- 327/327 tests pass (11 new parser tests use a small hand-written
  synthetic excerpt mimicking the real structure, not committed real
  data, to avoid redistributing the official body's copyrighted
  content in this repository).

**Not yet done**: wiring this into `ingest.py`/`inventory.py`'s
staging pipeline, mapping parsed records into P0-T003's `RaceKey`/
racer/venue tables, and downloading more than the one pilot month
needed for full P1/P2/P3 re-validation (step 2 above). The B-file
(race card / pre-race entries) has not been located or parsed yet —
only K-file (results) so far.

## Two more official data sources: racer period data + venue data

Added at user request (source: `https://www.boatrace.jp/owpc/pc/extra/data/layout.html`
and `https://www.boatrace.jp/owpc/pc/extra/data/stadium/index.html`).
Both hosted on `www.boatrace.jp` (same body as the K-file source, but a
different host than `www1.mbrace.or.jp`) — robots.txt permissive there
too, but `owpc/pc/extra/policy.html` explicitly prohibits large-volume
access and reproduction/redistribution beyond private use, so both
downloads were run only after explicit user approval in-session
(per `.claude/rules/01-approval-policy.md`'s "terms-of-service-sensitive
collection, scraping" rule), rate-limited at the same 3s/request default.

- `src/boat_prediction/fan_file_source.py` (new): downloads the
  "モーターボートファン手帳" racer period-performance files
  (`fan{code}.lzh`, fixed-width Shift-JIS records: registration number,
  name, win rate, per-course stats, hometown/branch — layout documented
  in `layout.html`). `list_fan_file_urls()` discovers the current file
  list from the live index page rather than computing a year→code
  formula, so it stays correct if the site's numbering convention ever
  changes. **Executed**: downloaded all 50 files currently listed
  (2002–2026, two per year) to `data/raw/boatrace/fan/` (gitignored,
  ~8.7 MB total, not committed). Not yet extracted/parsed into records
  — only the raw archives were fetched.
- `src/boat_prediction/venue_data_source.py` (new): scrapes and parses
  `https://www.boatrace.jp/owpc/pc/data/stadium?jcd={01..24}` — per
  venue basic info (address, motor, water quality, tidal range, course
  record) plus three statistics tables (course-based finish-rate +
  winning-technique breakdown, lane→course acquisition rate, and
  4 seasonal finish-rate tables), using `beautifulsoup4` (new dep in
  the `official-data` extra). Unlike the K-file/fan-file sources this
  is a **live rolling-statistics page** (e.g. "last 3 months," "spring
  season"), not a dated historical export — re-fetching later will
  return different numbers, not a stable archive. **Executed**: fetched
  and parsed all 24 venues to `data/raw/boatrace/venue/` (raw HTML) and
  `data/raw/boatrace/venue/parsed/*.json` (gitignored, not committed).
- 355/355 tests pass (19 new: 10 for `fan_file_source`, 9 for
  `venue_data_source`), using hand-written synthetic HTML fixtures for
  the parser tests (not real downloaded content, same reasoning as the
  K-file parser tests — avoids redistributing the official body's
  copyrighted data in this repository).

**Not yet done**: parsing the fan-file fixed-width records themselves
(only the archives were downloaded); wiring venue data into any
feature-engineering path; a scheduled/repeatable re-fetch for the
venue stats (they're time-varying, so a single snapshot goes stale).

## Full official K-file archive + supplementary JMA weather data

- Ran the K-file (race results) downloader across its full available
  range, 2005-01-01 through 2026-07-29 (~7,880 days), idempotent/
  resumable, to `data/raw/boatrace/K/{YYYYMM}/k{YYMMDD}.lzh`
  (gitignored). One-off script, not part of the package (ad hoc full-
  history pull, not a reusable library entry point).
- `src/boat_prediction/jma_weather_source.py` (new): supplementary
  weather data from the Japan Meteorological Agency
  (`data.jma.go.jp/stats/etrn/`), mapped per BOATRACE venue to its
  nearest observation station (`VENUE_STATIONS`, built by manually
  matching each venue's city/ward against that prefecture's station
  list — see module docstring for the exact list and which entries are
  an exact place-name match vs. nearest neighbor). Public open-
  government data (公共データ利用規約 1.0 — reuse incl. commercial
  allowed with attribution), confirmed to cover back to at least
  2005-01. **Scope confirmed during research**: covers air
  temperature, precipitation, wind, sunshine (humidity only at some
  stations/years). Does **not** cover water temperature or wave height
  at a venue's own racecourse — no official archive exists for those;
  they would need live capture going forward via BOATRACE's own
  pre-race "直前情報".
- **Executed**: fetching all 24 venues × 259 months (2005-01→2026-07)
  to `data/raw/boatrace/jma/{venue_code}/{YYYYMM}.html` (gitignored),
  idempotent (`skip_existing`, resumable like the K-file script).
- 359/359 tests pass (13 new for `jma_weather_source`), synthetic HTML
  fixture per the established pattern.

**Not yet done**: parsing the raw JMA HTML into a joined per-race-day
weather feature table; tide data for coastal/tidal venues (JMA's tide
tables only go back to 2011, separate investigation, not yet built);
live capture of water temperature/wave height/exhibition-time-at-race
(the still-unresolved gap noted above).

## B-file (番組表 / race card) — the leakage-safe pre-race source

Schema cross-check against `docs/domain/.../implementation_guide.md`'s
table definitions showed B-file is the only source that can legitimately
fill `race_entries.listed_national_win_rate` /
`listed_local_win_rate` / `listed_motor_second_rate` /
`listed_boat_second_rate`, `motors`/`boats` numbers, and (critically)
`races.scheduled_deadline_at` — the only *time-of-day* anchor found in
any data source so far, needed to make `available_at <= prediction_at`
actually checkable rather than date-only.

- `src/boat_prediction/b_file_source.py` (new): downloads B-files.
  Identical URL shape/host to `official_source.py`'s K-file
  (`od2/B/{YYYYMM}/b{YYMMDD}.lzh` vs `od2/K/.../k...`), confirmed by
  probing `od2/B/dindex.html` (200) and one sample file. Reuses
  `official_source.extract_k_file_text` for LZH extraction (generic
  Shift-JIS single-member decoding despite the name). 6 new tests.
- **Executed**: launched the full 2005-01-01→2026-07-29 download
  (mirrors the K-file full-archive script), idempotent/resumable, to
  `data/raw/boatrace/B/{YYYYMM}/b{YYMMDD}.lzh` (gitignored).
- 365/365 tests pass.

**Not yet done**: wiring parsed B-file entries into
`race_entries`/`races` per the schema above.

## B-file parser

`src/boat_prediction/bfile_parser.py` (new): parses B-file text into
`ParsedVenueDayCard` -> `ParsedRaceCard` -> `RaceEntryCard`. Same
`{code}BBGN`/`{code}BEND` venue-section structure as the K-file
parser, but entry rows are **fixed-column**, not space-tokenized (the
racer name directly abuts the surrounding fields with no delimiter).
Column boundaries were derived and verified against all 864 real entry
rows in the 2026-06-01 sample (0 parse failures): lane, registration
number, name, age, branch, weight, class, then 8 rate/number fields
(national/local win rate and second-rate, motor number+rate, boat
number+rate) at fixed positions 0-58, followed by a 14-character
trailing region.

**Key finding from real data**: that trailing region (current-series
per-heat results + an early-start-tendency indicator) does **not**
have a fixed internal split — a real row from a series with makeup
heats showed the results portion at 7 characters instead of 6, which
would silently corrupt a naive fixed 6/8 sub-split. Kept as one raw
`trailing_info_raw` string instead of guessing the boundary; the 8
rate/number fields (the actual `race_entries.listed_*` targets) are
unaffected by this since they sit earlier in the row.

The race header line (e.g. "１Ｒ 予選 Ｈ１８００ｍ 電話投票締切予定１７：４１")
uses full-width characters and can have an internal space in its class
label (e.g. "予選　　　　進入固定") — both handled (NFKC-normalize before
regex; the label capture is `.+?` not `\S+?`). This line is the only
source of a real time-of-day (`scheduled_deadline_time`), needed to
make `available_at <= prediction_at` checkable rather than date-only.

13/13 new tests (7 parser + prior 6 downloader) pass against a
hand-written synthetic excerpt (not real downloaded content, same
reasoning as `test_kfile_parser.py`). 372/372 tests pass overall.

**Not yet done**: wiring `ParsedVenueDayCard`/`ParsedRaceCard`/
`RaceEntryCard` into `race_entries`/`races` per the schema; parsing
the meeting-level metadata (date, day-N-of-series, meeting title) that
precedes each venue's first race header (not needed for the
entries/deadline data this parser targets, but present in the file).

## Odds source + odds-deviation detection

`src/boat_prediction/odds_source.py` (new): fetches closing odds
(締切時オッズ) per race, plus the daily index to discover which venues
raced. **Retention boundary established by probing: 2017-04-01** — every
probed date through 2017-03-01 returns no odds across 8 venues,
2017-04-01 onward returns data. That is exactly the Japanese
fiscal-year start, so it is likely a fiscal-year retention policy;
whether it is fixed or *rolling* could not be determined from one point
in time, and if rolling, FY2017 would age out on 2027-04-01. Validated
against real pages (2026-06-01 Omura R1: 6/6 lanes, win + place). The
1-year fetch (2025-07-29 → 2026-07-28) is running; ends at yesterday
deliberately, since the same URL renders *live* odds before a deadline
and those must not be stored as closing odds (`is_closing` records
whether the 締切時オッズ marker was actually present).

**Critical limitation, and why it shapes everything downstream**: only
ONE odds observation per race is retained. So `odds_snapshots` can only
ever get a single `observed_at` per race from history, the guide's
`OD_ODDS_STALE` / `OD_ODDS_SHARP_CHANGE` reasons cannot be evaluated
retroactively at all, and a leakage-safe backtest using these odds must
set `prediction_at` to the deadline (they do not exist before it).

That last point rules out pre-race EV screening on historical data:
there are no morning odds, so any morning EV would have to borrow
closing odds it could not have known. `odds_deviation.py` is the
leakage-free alternative that was built instead.

`src/boat_prediction/odds_deviation.py` (new): compares odds predicted
from pre-race features against actual closing odds and emits abstention
reasons.

- Metric is `log(actual / predicted)`, not a difference — odds are
  multiplicative, so 20.0-vs-10.0 must register the same surprise as
  2.0-vs-1.0. Sign is retained (positive = market priced the boat
  longer than expected) while thresholding uses the absolute value.
- **Deliberately emits only abstention reasons, never a buy signal**
  (structurally asserted in tests). Predicted odds and model win
  probability would both be functions of the same feature vector, so
  their disagreement measures model choice, not market inefficiency —
  an EV built from predicted odds is not a market comparison. Only real
  observed odds belong in `expected_value.py`. A sharp divergence is
  evidence the market holds information this project's features lack,
  which is a reason to stand down.
- Lanes where either side is missing are reported in `missing_lanes`
  and abstain, never silently treated as zero deviation.
- `target_lane` judges only the intended bet, so one wild outsider
  elsewhere in the field does not veto the race.

`abstention.py` changed (minimal, behavior-preserving): added
`OD_ODDS_UNEXPECTED_VS_MODEL` (reason codes now have a single home —
`odds_deviation.py` re-exports it) and an `extra_reason_codes` merge
parameter on `evaluate_abstention`, validated against `REASON_CODES` so
a typo cannot create a decision with an unrecognized reason, and
deduplicated rather than appended blindly. `OD_ODDS_SHARP_CHANGE` was
*not* reused: it describes odds moving over time, which is a different
signal and unobtainable historically.

411/411 tests pass; 20 new for odds_deviation (including the
end-to-end merge through `evaluate_abstention`) and 15 for odds_source.

**Not yet done**: the odds-prediction model itself (features → predicted
closing odds). Blocked on data overlap, not on design: B-file has only
reached 2017-06 while odds start 2025-07, so no race yet has both. The
B-file archive needs ~2.5h more to reach the odds period.

## K-file archive complete + parser validated across 21 years

Full K-file archive downloaded: **7,833 files, 2005-01-01 → 2026-07-29**.
17 dates failed, all a single contiguous block (2011-03-15 → 2011-03-31)
— nationwide racing was suspended after the Tōhoku earthquake, so those
files genuinely do not exist. 30 skipped were the earlier pilot month.

The parser had only ever been checked against one 2026 day, so it was
validated across the whole archive (132 files sampled, ~6 per year,
2005-2026). Findings:

- **Zero parse errors across 21 years** — the format is stable; no
  era-specific handling is needed.
- Every race with entries has **exactly 6** — no partial-field drift.
- Non-numeric finish codes observed across the archive: `S1`, `F`, `S0`,
  `S2`, `K0`, `K1`, `L0`, `L1` (and one stray `00`), all captured with
  `finish_position=None` and the raw code preserved.
- **Bug found and fixed**: whole venue-days appear 中止 (called off,
  typically weather) — the races exist only as 中止 lines in the payout
  block with no entry rows. The parser recorded them as races with zero
  entries, which is **indistinguishable from a parse failure**, and would
  have been counted as a data-quality defect and/or fed to training as a
  race with no result. `ParsedRace.is_cancelled` now flags them
  (defaults `False`, so existing callers are unaffected; the flag is
  re-stamped via `dataclasses.replace` at venue-flush time, which keeps
  the shared entries/payouts lists so nothing already parsed is lost).
  Verified against two real cancelled venue-days (2013-04-06 Wakamatsu,
  2018-08-08 Heiwajima) with control venues in the same files confirmed
  unflagged.
- Re-validated after the fix: **0 unexplained races** — every race in the
  sample is either a normal 6-entry race (19,645) or a flagged
  cancellation (46).

416/416 tests pass (5 new for cancellation handling).

**Downstream requirement this creates**: training-set construction must
exclude `is_cancelled` races, and quality scoring must not count them as
missing data.

## B-file archive complete + parser validated across 21 years

The B-file download finished: **7,862 files, 2005-01 → 2026-07**, with
2011-03 holding only 14 files — the same Tōhoku earthquake suspension
block already seen in the K-file archive, so the two agree. Coverage now
overlaps the odds period (2025-07 onward), which unblocks the
odds-prediction model that was previously waiting on data overlap.

The B-file parser had only ever been checked against one 2026 day —
exactly the situation that hid the K-file cancellation bug — and it is
worse-exposed than the K-file parser because it is **fixed-column**
rather than space-tokenized, so any era drift in column positions would
corrupt fields silently. Two sweeps were run.

**Sweep 1 (132 files, ~6/year, 2005-2026)** — checked for the specific
silent-failure mode: `_parse_entry_line` returns `None` on a bad row and
`parse_b_file_text` skips it with no error, so drift would show up as
missing entries, not as an exception.

- **Zero parse errors, zero extract errors, and zero silently-dropped
  entry rows** across 116,490 candidate rows.
- **Every one of the 19,415 races has exactly 6 entries** — the column
  layout is stable for all 21 years; no era-specific handling needed.
- Entry row length is *not* always 73: 89,436 rows are 73 chars and
  ~27,000 are 58-71. That is the already-known variable trailing region
  (`trailing_info_raw`), now quantified — the 8 rate/number fields sit
  at 0-58 and are unaffected, and short rows are `ljust`-padded, so this
  is not a defect. It does confirm the earlier decision not to guess a
  sub-split inside that region.

**Two defects found and fixed:**

1. **Card-less venue sections were indistinguishable from a parse
   failure** (same defect class as the K-file cancellation bug). A venue
   section can legitimately carry no race for two *different* reasons,
   and conflating them would be wrong:
   - `ParsedVenueDayCard.data_pending` — the section holds only
     「この場のデータ更新は、いましばらくお待ちください。」, i.e. the card was
     not finalized when the file was published. **152 occurrences**,
     heavily clustered in 2011-03/04 (post-earthquake schedule
     disruption). This one matters beyond bookkeeping: B-file exists in
     this project to be the leakage-safe pre-race source, so "no card
     had been published yet" *is* `available_at` information, and the
     archived copy we hold may predate the real card.
   - `ParsedVenueDayCard.is_cancelled` — the section holds a complete
     番組表 header followed by 「開催は中止となりました。」, i.e. the meeting
     was called off and no card will ever exist. **2 occurrences**
     (both 2006-09-17, venues 20 and 23).
   - `is_explained_without_races` combines them, so a card-less venue
     with neither flag can be treated as a parse defect. Both flags
     default `False`, so existing callers are unaffected.

2. **`race_class_label` fragmented one class into several.** The B-file
   pads the class field with spaces for column alignment, so "予選",
   "予 選" and "予  選" all occur and compare unequal — 87 distinct raw
   labels collapse to 75 in a 3-file sample. Grouping on the raw value
   would fragment `stability.py`'s per-class subgroup sample sizes and
   would create spurious categories in any categorical encoding of race
   class. Added `ParsedRaceCard.race_class`, a derived property that
   strips all whitespace (Japanese marks no word boundary here, so the
   padding carries no information). Compound labels stay distinct
   ("予 選    進入固定" -> "予選進入固定" != "予選"). It is a property, not a
   stored field, so it cannot desynchronize from the raw label — but
   note `dataclasses.asdict` will not include it.

**Sweep 2 (all 7,862 files, after the fix)** — 0 extract/parse errors
over **97,116 venue sections and 1,163,415 races**, with every card-less
venue accounted for: 96,962 have races, 152 are `data_pending`, 2 are
`is_cancelled`, and **0 are unexplained** (the three add up exactly). No
flagged venue wrongly carries races. Both fixes were also confirmed directly against the real
files (2021-02-02 venue 02, 2024-01-05 venue 10, 2006-09-17 venues
20/23), with control venues in the same files unflagged.

429/429 tests pass (13 new), ruff clean.

**Downstream requirement this creates**: B-file consumers must skip
`data_pending` and `is_cancelled` venues rather than counting them as
missing data, and must group race class on `race_class`, not
`race_class_label`.

## B-file meeting metadata (day-N-of-series, meeting date, tournament title)

Parsed the meeting-level metadata block that precedes each venue's first
race header — the one piece explicitly flagged as "not yet done" in the
prior B-file parser entry above. Schema motivation: motor/boat condition
and racer form both drift across a multi-day meeting, so which day of
the series a race falls on (節第N日) is a real feature, and this is the
only place in any BOATRACE source found so far that carries it.

- `ParsedVenueDayCard` gained three new fields, all reading from that
  venue's own "day banner" line (e.g. `"第１日　　２０２６年　６月　１日　　
  ボートレース大　村"`, NFKC-normalized to ASCII digits/spaces first, same
  technique already used for the race header line): `series_day` (int),
  `meeting_date` (`datetime.date`, a same-day cross-check against the
  filename rather than a substitute for it), and `meeting_title`
  (`str | None` — `None` for a plain unnamed race day, distinct from an
  empty string, which never occurs). All three default `None` so
  existing callers are unaffected.
- A state-machine bug was caught before it shipped: the first
  implementation attempt made the metadata-collection block swallow
  *every* line while a race was not yet underway, including the first
  race header itself — it would have silently dropped race 1 of every
  venue. Caught by running the existing 20-test suite immediately after
  writing the change (all races in `SAMPLE_B_FILE_TEXT` disappeared),
  fixed by only swallowing lines once the `番組表` marker has actually
  been seen, letting non-matching lines fall through to the race-header
  check otherwise.
- Confirmed present even for `is_cancelled` venues (the day banner is
  written before the cancellation notice) — a cancelled meeting's
  series_day/date/title are real data, not an artifact of an unfinished
  card. Never present for `data_pending` venues (nothing is written
  yet).

**Validated in two independent ways:**

1. A standalone line-by-line scan (not reusing `bfile_parser.py`, to
   avoid validating the code against itself) over all 7,862 files: the
   day-banner line matched for every one of 96,964 non-`data_pending`
   venue sections (0 missed), the banner's own date agreed with the
   file's own filename-derived date every time (0 mismatches), and the
   title was 0 or 1 lines every time (94,925 with a title, 2,039
   without, never wrapping to a second line).
2. The real `parse_b_file_text()` run across the same full archive:
   96,962 card-bearing venues, all with `series_day`/`meeting_date`
   populated (0 missing), 154 correctly card-less
   (`is_explained_without_races`), 0 parse errors, and every race still
   carrying exactly 6 entries (confirms the new metadata state machine
   introduced no regression in race/entry parsing).

`series_day` ranges 1-8 across the archive (most meetings run 4-6 days,
tapering off — 7-8 are rare, matches real-world scheduling).

436/436 tests pass (7 new for meeting metadata), ruff clean.

**Not yet done**: wiring `series_day`/`meeting_date`/`meeting_title`
(or the rest of `ParsedVenueDayCard`/`ParsedRaceCard`/`RaceEntryCard`)
into `race_entries`/`races` per the schema — still the same open item
noted in the original B-file parser entry above.

## Relational schema + B/K-file loaders (closes the item directly above)

User-approved plan (2026-07-31): PostgreSQL via a new local
`docker-compose.yml`, full 21-year (2005-2026) initial load once a
database is actually running. Nothing in this entry required real
network access or the terms-sensitive sources, so no additional
approval was needed under `.claude/rules/01-approval-policy.md`.

- `docker-compose.yml` (new, repo root): disposable local Postgres 17,
  fixed non-secret dev credentials, published on `127.0.0.1:5433` only
  (not the Postgres default port, so it never collides with one a
  developer already runs). Documented in `docs/local-setup.md`.
- `src/boat_prediction/db/` (new package): `ids.py` (UUID v7, since the
  stdlib's `uuid.uuid7` needs Python 3.14 and this project targets
  3.12+), `models.py` (SQLAlchemy 2.0 declarative schema), `session.py`
  (engine/session factory reading `DATABASE_URL`), `loader.py` (parsed
  dataclasses -> rows), `load_archive.py` (CLI walking the downloaded
  archive).
- `alembic.ini` + `alembic/` (new): initial migration
  (`alembic/versions/3997a65d30a7_initial_schema.py`), autogenerated
  then hand-verified to round-trip with zero drift and to render valid
  PostgreSQL DDL via `alembic -x dialect=postgresql upgrade head --sql`
  (checked without any live database, since none is running in this
  environment).

**`models.py` deliberately deviates from the implementation guide's
§7.2 table listing in six places** (full reasoning in that module's
docstring, summarized here):

1. No `motors`/`boats` tables yet — no source located so far publishes
   the `service_period_start`/`end` the guide's own unique constraint
   depends on, and fleets are known to be replaced over 21 years, so
   creating them now would assert a false identity. Motor/boat numbers
   and rates stay point-in-time on `race_entries` instead (what the
   B-file actually states).
2. `races.venue_id` is denormalized so the guide's own
   `UNIQUE(race_date, venue_id, race_number)` — i.e. `race_id.RaceKey`,
   the P0 natural key — is expressible at all.
3. `race_payouts` is new (not in the guide): the K-file publishes real
   payouts and `paper_simulation.py` needs real returns, not synthetic
   ones.
4. Point-in-time racer attributes (class, branch, weight, win rates)
   live on `race_entries`, not on the `racers` identity table — reading
   them off a mutable dimension row would feed a 2007 race the racer's
   2026 attributes, which rule 08 forbids.
5. `exhibition_entries` loaded from the K-file carries *results-time*
   `available_at`, not pre-race availability — this project only learns
   the exhibition time from the post-race file, so claiming otherwise
   would be a leak. `feature_availability.py`'s gate correctly refuses
   these values for a pre-race `prediction_at` as a result; a live 直前情報
   capture is what would change that (still not built).
6. `race_meetings.meeting_end_date` stays NULL at load time — knowing
   when a series ends needs later days of that same series, which is
   future knowledge for any race inside it.

**`loader.py`'s temporal decisions are the leakage argument for this
whole schema**, so they're deliberately conservative rather than
precise:

- `card_available_at` = midnight JST of the race day. The B-file
  archive records which day a card covers but not when it was
  published (cards are in fact published the day before), so midnight
  of the race day is a safe *later* bound — available_at can only ever
  be wrong in the direction of "later than it really was," never
  earlier, and it's still early enough for every pre-deadline
  `prediction_at` that day.
- `results_available_at` = midnight JST of the *next* day. The K-file
  carries no per-race confirmation timestamp, so the day boundary is
  the only bound derivable from the data. The cost: legitimate
  same-day features (using race 1's result to help predict race 12)
  are unavailable until per-race confirmation times exist.
- `scheduled_deadline_at` combines the race date with the B-file
  header's `HH:MM` — the only real time-of-day anchor in any source
  found so far, converted from JST to UTC via `temporal.to_utc`.

Both `load_b_file_day`/`load_k_file_day` are idempotent per
`(race_date, venue_code)` (full replace of that day's `race_entries` /
`race_result` on re-load, via ORM-level deletes so cascades run
identically on SQLite-in-tests and real PostgreSQL) and independent of
load order: whichever of B-file/K-file is loaded first, loading the
other retroactively links `race_result_entries.race_entry_id` and
`exhibition_entries.race_entry_id` by lane number
(`_relink_result_entries`). `data_pending`/`is_cancelled` venues (both
already flagged by the parsers) are skipped and counted rather than
raising; a card-less venue with *neither* flag, or a race with no
entries/payouts/cancellation flag, raises `LoaderError` — the same
"treat an unexplained gap as a parse defect, not as missing data"
principle the parsers themselves established.

`load_archive.py` walks a date range over
`data/raw/boatrace/{B,K}/{YYYYMM}/{b,k}{YYMMDD}.lzh`, tracking a JSON
ledger keyed by `(kind, source_file_hash)` (`ingest.py`'s dedup pattern)
so a re-run skips files it already loaded — necessary at this
archive's real size (~15,700 files across 21 years; correctness does
not depend on the ledger, since the DB writes are idempotent on their
own, but re-parsing everything on every resume would not be practical).
One file's parse/load failure is recorded and does not stop the run,
matching `ingest_directory`'s partial-failure recovery. `--dry-run`
parses and would-load every file, rolling back each one, without
touching the ledger or the database (a bug in an early draft rolled
back the whole session per file, which also undid the once-per-run
`ensure_reference_data()` seed; fixed by committing that seed
unconditionally before the per-file try/rollback loop, caught by a test
covering more than one date in a dry run).

482/482 tests pass (46 new: 12 for `db/models.py`/`ids.py`, 26 for
`db/loader.py`, 8 for `db/load_archive.py`), all against in-memory
SQLite with `PRAGMA foreign_keys=ON` — correct as far as portable
SQLAlchemy behavior goes, but the real target is PostgreSQL
(`docs/PROJECT_PROFILE.md`) and this has not yet been run against a
live Postgres instance in this environment. ruff clean on every new/
modified file (pre-existing lint debt in 18 untouched test files is
unrelated to this change and was left alone, matching rule 00.2's
"work on one bounded task at a time").

**Not yet done, in order**:
1. Actually start `docker compose up -d db` and run `alembic upgrade
   head` against it (not done in this environment — no Docker
   available here; needs to happen on a machine that has it).
2. Run `load_archive.py` over the real archive once the database
   exists. At ~15,700 files with real LZH extraction and parsing, this
   will take substantially longer than the download itself and should
   be resumed via the ledger rather than re-run from scratch if
   interrupted.
3. `motors`/`boats` tables, once/if a source with real service periods
   is found.
4. JMA weather and fan-file loaders (see the two queued items in the
   entry directly below — `odds_snapshots` itself is now done).
5. Only after 1-2: re-run P1/P2 against real loaded data and confirm a
   genuinely stable forward test, per the standing instruction at the
   top of this file.

## odds_snapshots loader

Closes the `odds_snapshots` half of the "not yet done" item above.
`odds_source.py` already fetched real HTML and could already parse it
(`parse_win_place_odds`), and the `OddsSnapshot` table already existed
in the schema (deliberately, per its own docstring) — so this was the
most ready of the three remaining sources (odds / JMA weather /
fan-file), and the only one done this session; JMA weather and fan-file
are queued below as separate follow-on tasks rather than attempted
together, matching this file's one-deliverable-per-entry pattern.

- `src/boat_prediction/db/loader.py` gained `load_odds_day()` (plus
  `OddsLoadStats` and a `SOURCE_ODDS` reference-data entry, seeded the
  same way as the B/K-file sources via `ensure_reference_data()`). No
  migration was needed — the table was already there.
- **Design decisions**, since none of this was mechanical:
  1. `odds_snapshots.odds` is a single `Numeric(8,2)` column but 複勝
     (place) odds are a low-high range. Stored as two rows,
     `bet_type="place_low"`/`"place_high"`, rather than widening the
     schema for one source.
  2. Only `is_closing=True` pages are loaded — a live/in-progress page
     renders current, not final, odds under the same URL
     (`odds_source.py`'s own docstring), and this source only ever
     retains one observation per race, so a non-closing page must never
     be mistaken for the closing one.
  3. `observed_at`/`available_at` = the race's `scheduled_deadline_at`
     (set by a prior B-file load). The odds page carries no timestamp of
     its own, and the deadline is the only leakage-safe anchor available
     — consistent with `odds_source.py`'s existing point that a
     leakage-safe backtest using this source must set `prediction_at` to
     the deadline, not earlier.
  4. The target race must already exist with a known deadline (i.e. the
     day's B-file was already loaded); if not, the odds page is skipped
     and counted (`skipped_race_not_found`/`skipped_no_deadline`), never
     used to invent a bare `Race` row — consistent with `LoaderError`'s
     documented "don't invent or lose data" rule elsewhere in this
     module. Neither condition raises; both are ordinary, expected
     skips, since odds and B-file loading can legitimately happen out of
     order.
  5. A scratched/missing lane (`None` odds) writes no row for that
     value, matching `odds_deviation.py`'s existing "missing lanes are
     reported, never treated as zero" stance.
- `src/boat_prediction/db/load_odds_archive.py` (new): the archive
  walker, mirroring `load_archive.py`'s ledger (`data/manifests/
  odds_load_ledger.json`) + `--dry-run`/`--force` + injectable-
  session-factory pattern, adapted for the odds archive's different
  on-disk layout (`data/raw/boatrace/odds/{YYYYMMDD}/{venue}_{race}.html`,
  discovered by globbing each day's directory rather than computing one
  fixed filename per date, the way B/K-file paths are computed).
- 495/495 tests pass (13 new: `tests/test_odds_loader.py`,
  `tests/test_load_odds_archive.py`), synthetic `RaceOdds`/`WinPlaceOdds`
  fixtures built directly, same reasoning as the B/K-file loader tests.
  ruff clean on all new/modified files.
- **Smoke-tested against real data**: dry-ran `load_odds_archive` over
  one real downloaded day (2025-07-29, 192 real HTML pages already on
  disk from the earlier partial fetch) against a throwaway in-memory
  SQLite DB with no B-file loaded. Zero parse/loader errors across all
  192 real pages; every one correctly skipped as `race_not_found`
  (expected, since no B-file card exists in that throwaway DB) rather
  than silently mis-loaded — confirms the real parse → load path is
  sound, independent of the "not yet run against real PostgreSQL"
  caveat that still applies (no Docker in this environment).

**Not yet done (queued as the next two follow-on tasks, deliberately not
attempted this session)**:

1. **JMA weather**: `jma_weather_source.parse_daily_month_html` already
   turns one month's HTML into `DailyWeather` records and is unit-tested,
   but nothing runs it over the full downloaded archive (24 venues x 259
   months) or persists the result. Needs: a new `VenueDailyWeather`-style
   table + Alembic migration (autogenerate, following the existing
   single-migration style with no hand-edits), plus a batch driver
   analogous to `load_odds_archive.py`.
2. **fan-file** (racer period stats): no parser exists yet for the
   fixed-width Shift-JIS records at all (only `extract_fan_file_text`,
   which returns one flat decoded string) — the column layout needs to
   be derived from `layout.html` first, the same way the B-file's fixed
   columns were derived and verified against real data. Needs its own
   new *point-in-time* table (must not write onto the `racers` identity
   table — see deviation 4 in `models.py`'s docstring, the same
   principle that keeps class/weight/win-rate off `racers` today) plus a
   loader.
3. Completing the odds raw-HTML archive itself: only 68 of the ~365
   days in the originally planned 2025-07-29 → 2026-07-28 range are
   downloaded so far (the fetch stopped partway through an earlier
   session). This is a separate data-acquisition task against the same
   already-approved terms-sensitive source (`.claude/rules/
   01-approval-policy.md`'s scraping approval already covers it), not a
   schema/loader task, and wasn't attempted here.
