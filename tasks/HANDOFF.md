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
3. **Completed: odds raw-HTML archive via retry-enabled fetch**
   (2026-08-01). A prior partial fetch to 2025-11-04 (14,580 pages)
   stopped with `TimeoutError`. Added exponential backoff (1s, 2s, then
   fail) to `odds_source.py`'s `_fetch()` function, allowing the
   idempotent `fetch_range()` to resume from the incomplete 2025-11-05
   (skipping already-downloaded files). The retry picks up 2025-07-29 →
   2026-07-28 from where it failed; currently running in background.

## P2 measured on real payouts: the card-feature edge does not survive the price (2026-08-01)

The standing claim (tasks/CURRENT.md item 4) was that P2 could only be a
market-comparison study, because the archived odds are stamped at the
deadline and so cannot inform a pre-close decision. **That is only true
of EV-screened selection.** A paper simulation needs a price only to
*settle*, and settlement does not need odds at all: boat racing is
pari-mutuel, and the K-file's 単勝 payout is exactly what a ¥100 bet
returned. `race_payouts` holds 1,130,675 of them across the full 21
years. So the decision can use pre-deadline card features only, and the
settlement can use a recorded payout — leakage-free, and available now
rather than after the cron series matures.

**Two floors were measured first, because an ROI without them means
nothing.**

- **Takeout = 26.41%**, from closing odds over the 11,050 races carrying
  a full six-lane quote (`market.overround()`: `Σ1/odds = 1/(1-t)`).
  Mean and median agree to four decimals, p05–p95 = 1.335–1.399.
- **Per-lane flat-bet return, whole archive**, from payouts alone:

  | lane | win rate | return |
  |---|---|---|
  | 1 | 46.32% | **0.8598** |
  | 2 | 16.06% | 0.7363 |
  | 3 | 13.62% | 0.7057 |
  | 4 | 11.72% | 0.6766 |
  | 5 | 7.25% | 0.5722 |
  | 6 | 5.04% | 0.4109 |

  A calibrated market would return `1 - t = 0.736` on *every* lane. Lane
  2 sits almost exactly there; lane 1 returns 12 points more and lane 6
  32 points less. That is a large, persistent **favourite–longshot
  bias**: lane 1 wins 46.3% but is priced at ≈39.6%.

  The two measurements cross-validate: `Σ return_L = 3.9615 = (1-t)·Σ(p/m)`
  gives `Σ(p/m) = 5.383`, and the six-lane flat portfolio's measured
  0.6603 equals `(1-t)/6 · 5.383 = 0.660` exactly. An earlier draft
  reported that 0.6603 *as* the takeout — wrong, and worth recording as
  the trap: the flat portfolio only equals `1-t` when the market is
  calibrated, and here it plainly is not.

**So the bar is not −26.4% (the takeout) but −14.0% (always back lane 1).**

**The run.** Same window and folds as P1 (2023-01-01…2026-07-29, 31
walk-forward folds, 142,884 races), `logistic_cards` refit per fold, bet
the argmax lane when its probability clears a threshold, settle at the
real 単勝 payout. 0 races lacked a payout.

| thresh | bets | % raced | hit% | ROI | lane-1 ROI, same races |
|---|---|---|---|---|---|
| 0.00 | 142,884 | 100.0% | 56.4% | 0.9099 | 0.9040 |
| 0.50 | 90,680 | 63.5% | 66.0% | 0.9138 | 0.9100 |
| 0.70 | 32,309 | 22.6% | 75.9% | 0.9229 | 0.9207 |
| 0.80 | 7,386 | 5.2% | 81.3% | 0.9235 | 0.9232 |
| 0.90 | 147 | 0.1% | 84.4% | 0.8878 | 0.8878 |

**The model beats "always lane 1" by 0.3–0.6 points of ROI — noise —
despite picking the winner 56.4% of the time against lane 1's ~47%.**
The accuracy is real and it converts entirely into shorter prices: mean
payout on the model's winning picks is ≈¥161 against ≈¥192 for lane 1.
Every race where the model correctly abandons lane 1 is a race the
market had already marked down. Selectivity helps a little (0.910 →
0.924) but plateaus around threshold 0.8 and reverses at 0.9 on 147
bets.

**This is the honest counterweight to P1's log-loss result.** +31.6%
over uniform and +11.5% over `lane_prior` are real, and they are
statistical skill the price has already absorbed. Nothing here says the
model is bad; it says the card features are public and the market reads
them at least as well.

**What this rules out and what it does not.**

- Ruled out as a path to positive ROI: more first-place accuracy from
  B-file card features. That axis is saturated — it is already being
  traded away at fair value.
- Not tested, and the only two mechanisms left with a plausible story:
  1. **Selection on price.** Every rule above chose a *lane* and ignored
     what it cost. ROI is a function of price, and no run has ever
     selected on one, because no pre-deadline price existed. The cron
     capture started 2026-08-01 and is the first data that would allow
     it. This is the single highest-value thing the daily job is
     accumulating.
  2. **直前情報** (exhibition time, tilt, 進入変更) — genuinely pre-race
     signal that is *not* printed on the card, so it is not obviously
     already in the price at capture time.
- Untested and separate: exotic bet types. Different takeout, far more
  combinations, and correspondingly noisier prices — but also far higher
  variance, so it needs the same discipline, not optimism.

Measured with two throwaway scripts run read-only against the host
(`measure_baselines.py`, `measure_selectivity.py`); nothing was written
to the database. Promoting them into `db/evaluate_p2.py` needs
`Dataset` to carry `race_ids`, which it does not today — the scratchpad
version reuses `dataset._ROW_SQL`/`_lane_features` to avoid duplicating
the feature logic while collecting them.

## Within-meeting form: no tuning drift, but it is contaminated by lane luck (2026-08-01)

Prompted by the question of whether the motor and propeller are modified
during a 節. `dataset.py`'s docstring justifies the within-meeting form
feature on the motor and boat being "drawn once per 節 and kept all
series" — true of the *individual*, but the crew tunes it (整備, propeller
adjustment, parts replacement), so the quantity being averaged might be
moving. A flat mean would then be the wrong estimator.

**Tested, and drift is not supported.**

1. Mean-of-earlier-days vs single-most-recent-day, 1,882,348
   racer-meeting-day pairs: the mean wins in **every one of 22 years**,
   0.3006 vs 0.1964. Not conclusive on its own — the mean averages ≥2
   days, so its noise advantage is confounded with recency.
2. Like-for-like, single day against single day at lags 1/2/3 (same
   rows, same sample size): drift would force monotonic decay. Instead
   lag 1 is the *lowest* of the three and lag 2 the peak — 0.1590 /
   0.1838 / 0.1750 pre-2011, 0.2098 / 0.2351 / 0.2235 from 2011.

**The lag-1 dip is lane rotation, not drift.** Lanes rotate across a 節,
so yesterday's lane is systematically unlike today's while two days ago
may line up better. Re-running the lag test on a lane-adjusted score
(score minus that lane's own mean) over 4,513,744 pairs:

| score | lag1 | lag2 | lag3 | lag1−lag2 |
|---|---|---|---|---|
| raw | 0.1394 | 0.1635 | 0.1567 | −0.0240 |
| lane-adjusted | 0.1442 | 0.1478 | 0.1415 | **−0.0037** |

Adjusting removes ~85% of the anomaly and leaves a **flat** profile
across lags — which is the direct answer: within-meeting form behaves as
a static quantity, so the flat mean is the right estimator and no
recency weighting is warranted.

**But the same test exposes a real defect in the feature.** The lane
effect is far larger than the signal: mean score by lane runs 0.7649
(lane 1) down to 0.4563 (lane 6), a spread of 0.31, against a total
within-meeting persistence of ~0.14. `meeting_form_score` averages raw
scores with **no lane adjustment**, so a racer who happened to draw
inside lanes earlier in the 節 scores high on "form" for reasons that
have nothing to do with how the boat is going.

Fixing it has a leakage constraint that must not be glossed over: the
lane baselines cannot be computed over the whole archive and then used
in a 2023 prediction. They must come from data strictly before the
prediction — either recomputed per training window, or frozen as
constants derived from an early slice (they move by ~0.01 across eras,
so a 2005–2010 slice is both stable and safely in the past for every
evaluation window used so far). Not yet implemented.

**Also settled: the propeller regime change is not visible here.**
持ちペラ (racer-owned propellers) ran 1988-05 to **2012-04**, when
venues began lending propellers with the motor. Within-meeting
persistence rises gradually from 2005 (0.228) to about 2015 (0.327) and
is flat after, with the largest single step at 2010→2011 — a year
*before* the rule change, and 2012→2013 is flat (0.3074 → 0.3088). So
the abolition left no mark on this metric and the 2011 step has no
identified cause. Note the naive story predicts the opposite sign
anyway: a racer's own propeller is also constant across a 節, so
persistence should have been *higher* under 持ちペラ, and it was lower.

Sources for the propeller dates:
[マクール](https://sp.macour.jp/columns/macour_timemachine/176266/),
[競艇大全](https://kyougikai2020.jp/mochipera/).

## Interactions, course aptitude and tuning skill, all measured (2026-08-01)

Three questions about what the model is not capturing, answered on real
data. One of them is the session's first *positive* result.

### Six-boat combination effects: additive, no exploitable interaction

`logistic_cards` is linear, so it already learns "lane 1 is worse when
lane 4 is strong" — what it cannot learn is a conditional effect, e.g.
lane 4's particular strength threatening lane 1 more than its general
strength implies. A gradient-boosted tree finds those without being told
where to look, so the linear-vs-GBDT gap measures how much the *combination*
of six boats matters beyond their sum.

`HistGradientBoostingClassifier` (already in the host's scikit-learn;
LightGBM/CatBoost are not installed and installing them on a shared host
to answer a question that does not need it would be the wrong trade),
same 31 folds and 142,884 races:

| model | mean log-loss | ROI @0.00 | ROI @0.70 |
|---|---|---|---|
| logistic | **1.21148** | 0.9089 | 0.9229 |
| histgbt | 1.22995 (−1.52%) | 0.9084 | 0.9207 |

**The tree loses on both metrics.** Untuned, so this is not "no
interaction exists" — but it is strong evidence that the card features'
signal is close to additive, consistent with everything else measured
today.

### Course aptitude: REAL, persistent, and concentrated at the extremes

Method: temporal split (2015–2020 vs 2021–2026), expectation removed
per **venue × lane × motor-bucket** (venue matters — racers race
disproportionately at their home venue and venues differ in how much the
inside lane dominates, so a lane effect pooled across venues would
manufacture persistence), then the racer's own main effect removed. What
is left is the racer × lane interaction. Persistence is the weighted
early-vs-late correlation; the racer main effect is carried as a control
because it is known real and calibrates the scale.

| | persistence |
|---|---|
| racer main effect (control) | 0.7843 |
| **racer × lane, all lanes** | **0.4933** |
| — lane 1 | 0.5760 |
| — lane 2 | 0.3586 |
| — lane 3 | 0.2360 |
| — lane 4 | 0.2254 |
| — lane 5 | 0.3066 |
| — lane 6 | 0.5649 |

**U-shaped, and the shape is the interpretation.** Lanes 1 and 6 demand
specialised technique (逃げ; まくり/大外), and aptitude there persists at
~0.57 — over 70% of the control. Lanes 3-4 are dominated by 展開, which
is a property of the *field*, not of the racer, and persistence collapses
to ~0.23 accordingly.

Not controlled: race-class composition. A racer in stronger events faces
better opponents, but that shifts all their lanes together and is absorbed
by the main effect, so it is unlikely to explain a lane-*specific*
residual.

**This settles the open fan-file scope question** (item 6 in
tasks/CURRENT.md). The per-course breakdown was described there as "most
of the per-course finish/irregular-count breakdown is unlikely to ever be
used as a feature". That is now measurably wrong: per-course ability is
the strongest genuine racer attribute found so far beyond overall skill,
and the fan-file is the only source that publishes it directly. Build the
per-course columns.

### Tuning skill (調整力): real but small, and only on bad motors

Identification is clean here: **motors are drawn by lottery at the start
of a 節**, so motor quality is exogenous to racer skill — no selection to
argue about.

A first version of this test was wrong and would have been reported as a
strong positive. It subtracted only the lane × motor-bucket expectation
and not the racer's own level, so its residual was mostly just racer
ability and "persisted" at 0.76 — indistinguishable from the control at
0.75, which is exactly what should have given it away. 調整力 is not "is
this racer good", it is "does this racer's edge *change* with motor
quality", i.e. the racer × motor-bucket interaction, which requires the
main effect removed.

Corrected:

| motor tercile | persistence | sd of interaction |
|---|---|---|
| worst third | **0.1400** | 0.0134 |
| middle third | 0.0515 | 0.0129 |
| best third | 0.0447 | 0.0131 |

**The gradient is the finding**: the interaction is ~3× more persistent
on the worst motors than the best — precisely the signature 調整力 would
produce, since a good tuner has room to improve a bad motor and nothing
to add to a good one. But the magnitude is small (sd ≈ 0.013 in score
units, roughly a fifth of raw racer-ability differences) and persistence
of 0.14 against a 0.78 control is weak. Worth one feature, not worth a
research programme — and clearly subordinate to course aptitude.

## 予選→優勝: the card rate ages, the motors do not converge (2026-08-01)

Re-opened after the earlier "no drift" conclusion was challenged on the
grounds that motors and propellers are worked on between 予選 and 決勝.
The challenge was right that the earlier test was insufficient, for two
reasons worth recording:

1. It used **calendar-day lags**, ignoring the 予選 → 準優 → 優勝
   structure the question is actually about.
2. More seriously, **finish position is relative to the field, so it is
   zero-sum.** If every crew improves its motor across the six days, no
   finish-based metric can see it *at all*. The earlier conclusion was
   therefore only ever about *differential* change, and did not say so.

**First test — the gap does narrow.** Motors classified into terciles by
the card's 2連率 on the racer's first day of that 節 (fixed there; the
card rate updates daily, so classifying per race would let the label
absorb the performance being measured), 予選 only so no selection
compression, residual = score − venue×lane mean:

| 節 day | bottom | middle | top | top−bottom |
|---|---|---|---|---|
| 1 | −0.0163 | −0.0036 | 0.0209 | **0.0372** |
| 2 | −0.0153 | −0.0037 | 0.0194 | 0.0348 |
| 3 | −0.0158 | −0.0036 | 0.0188 | 0.0345 |
| 4 | −0.0150 | −0.0052 | 0.0174 | **0.0324** |

−13% from day 1 to day 4. Two explanations fit: the motors converge
(tuning), or the pre-節 card rate simply ages.

**Second test separates them.** Rebuild the label from the motor's *own
results on days 1-2 of the same 節* — in-節, so it cannot go stale in the
way the card rate can — then hold it fixed and measure at growing
distance:

| 節 day | in-節 label gap | card label gap |
|---|---|---|
| 3 | 0.1585 | 0.0340 |
| 4 | 0.1572 (**−0.8%**) | 0.0321 (**−5.5%**) |

**The in-節 label does not decay; only the card rate does.** If the
motors were genuinely being changed, a label measured on days 1-2 would
lose power by day 4 too — the thing it describes would have moved. It is
flat. So: the card's 2連率 ages, and relative standing within a 節 is
essentially set by days 1-2.

Two things this does **not** say:

- It measures the racer+motor+boat package, not the motor alone. The
  in-節 label includes racer skill, which is constant across the 節. That
  does not affect the drift conclusion (racer skill does not drift
  either) but it does mean the 4.7× figure below is not motor-specific.
- **Uniform improvement across all crews remains invisible**, by
  construction. Only 展示タイム — an absolute, solo measurement — could
  see it, and the database holds 5,829 of them against 6,984,306 entries
  (0.08%). This is the sharpest argument yet for the 直前情報 capture.

**Actionable**: two days of in-節 results carry a **4.7× larger** gap
(0.1585 vs 0.0340) than the season motor rate does. `dataset.py` shrinks
`meeting_form_score` toward the season win rate with
`MEETING_FORM_SHRINKAGE_STARTS = 3.0`; that weight was chosen a priori,
and this measurement suggests it is too strong. Worth a sweep — not yet
run.

## 相性 (racer-vs-racer matchup): real but small, and finals are the worst place to look (2026-08-01)

Re-opened after the claim that pairwise matchups are unidentifiable was
challenged on the grounds that finals and big tournaments draw from a
small pool, so the same racers meet repeatedly. The challenge was right
that the original argument was bad — it counted pairs across the whole
population (1,600 racers → 1.3M pairs) and concluded "hopeless" without
looking at the actual encounter distribution.

**Measured, 2013 onward, head-to-head within a race:**

| scope | pairs | encounters | median | max | ≥15 | ≥30 |
|---|---|---|---|---|---|---|
| all races | 1,655,506 | 11,102,034 | 4 | 216 | **171,228** | 28,005 |
| 優勝戦/準優勝戦 only | 321,977 | 598,949 | **1** | 42 | 1,022 | 69 |

Both halves of the original position were wrong, in opposite directions:

- **The whole population is far richer than claimed** — 171,228 pairs
  have 15+ encounters. "Hopeless" was simply false.
- **But the elite/final subset is the poorest place to estimate it**, not
  the richest. A 節 produces one final, so pair encounters there are
  *scarcer* than in ordinary racing: median 1, and only 69 pairs reach 30.
  Restricting to big tournaments to get denser matchups does the opposite.

**Persistence test.** Head-to-head outcome, minus the base rate for that
lane pair, minus each racer's own strength — what survives is "A beats B
more than their individual strengths predict", i.e. the non-transitive
part. Temporal split 2013–2019 vs 2020–2026.

| | persistence |
|---|---|
| racer strength (control) | 0.7825 |
| pair residual, uncontrolled | 0.1778 |
| **pair residual, venue-controlled** | **0.1429** |

The uncontrolled figure could not be read as 相性: pairs that meet often
share a venue, venue aptitude is real (see the course-aptitude entry), so
a venue specialist beating a visitor would look exactly like matchup.
Recomputing every quantity **within venue** — base rate per
(venue, lane_a, lane_b), and each racer's strength per venue — removes
about a fifth of it. The rest survives.

**So 相性 is real and small.** Persistence 0.14 against a 0.78 control is
~18% of the scale of racer strength; with an observed residual sd of
0.0967, the persistent part implies roughly ±3.7 percentage points on a
head-to-head probability.

This does **not** contradict the GBDT result above. That tree had only
*card* features, and pairwise matchup is an identity-level effect no card
column can express. The two together say: the card features are additive,
and the non-additive part that does exist lives in racer identity, which
nothing in the current feature set can reach.

Practical reading: if 相性 is ever built, build it from **all races**,
never from the finals — and expect a marginal feature, not an edge. Given
that today's P2 result showed larger effects than this being fully priced,
it is not a promising place to spend next.

Remaining confound, not removed: pairs meet repeatedly *within one 節*
carrying the same motor and boat all week, so a good motor that week
inflates a pair's residual. That adds noise within a period rather than
across them, so it attenuates the correlation rather than manufacturing
it — 0.1429 is if anything conservative.

## 得意会場 is weak, and it fails in lane 1 and in seeded races (2026-08-01)

Proposed design: extract each racer's strong venue from per-venue
performance, then invert the question and find the conditions under which
that advantage stops holding. Two traps were designed out rather than
caveated — strength is fit on 2013–2019 and the advantage measured
entirely on 2020–2026, so regression to the mean has already happened;
and the condition set was fixed at five splits in advance, so this is not
a hunt.

**A. 得意会場 is real but much weaker than course aptitude.**

| attribute | persistence |
|---|---|
| racer main effect (control) | 0.7843 |
| racer × lane (course aptitude) | 0.4933 |
| **racer × venue (得意会場)** | **0.1592** |

Three times weaker than course aptitude, against the same control. The
reason is visible in the method: the baseline is the (venue, lane) mean,
so "this venue favours the inside" is already removed, and what remains
for venue aptitude is small. Much of the folk 得意会場 effect is the venue's
own lane bias, not the racer.

**B. Where the advantage breaks.** 6,723 (racer, venue) pairs with an
edge in period 1; their period-2 advantage is **+0.0070** overall
(n=430,551), and by condition:

| condition | bucket | n | advantage | vs overall |
|---|---|---|---|---|
| lane | **4-6** | 203,359 | +0.0158 | **+0.0088** |
| lane | 2-3 | 148,109 | +0.0031 | −0.0039 |
| lane | **1** | 79,083 | **−0.0084** | **−0.0154** |
| race | unseeded | 401,944 | +0.0109 | +0.0039 |
| race | **seeded (準優/優勝)** | 28,607 | **−0.0477** | **−0.0547** |
| wind | calm / moderate / windy | ~125k each | +0.0046 … +0.0096 | ±0.003 |
| rain | dry / rain | 239k / 132k | +0.0073 / +0.0076 | ±0.001 |
| temp | cold / mild / hot | ~127k each | +0.0063 … +0.0094 | ±0.003 |

Three readings:

1. **Venue knowledge is worth nothing in lane 1 and everything outside
   it.** The advantage inverts at lane 1 (−0.0084) and is strongest at
   lanes 4-6 (+0.0158). Physically consistent: 逃げ from the inside is
   the most automatic outcome in the sport, while making a まくり work
   from outside is where knowing the water, the current and the turn
   actually pays.
2. **Weather does nothing at all.** All three weather splits land within
   ±0.003 of the overall figure — a clean null on the proposal to use
   気象条件 to characterise a 得意会場. Two readings, and they are not
   exclusive: the effect may not exist, and the measurement is certainly
   coarse (JMA *daily* averages from the nearest *land* station, not
   race-time water surface). 直前情報 carries the real thing, which is one
   more argument for capturing it.
3. **The seeded number is large but partly confounded.** −0.0477 is a
   sign flip, not a shrinkage. But seeded fields compress every positive
   residual — the motor-tercile gap also fell (0.0293 → ~0.0199) in the
   same rounds — and the racer main effect subtracted here is computed
   over all races, so a strong racer under-performs it in a final by
   construction. The move here (+0.0109 → −0.0477) is much larger than
   the ~32% compression seen elsewhere, so something beyond compression
   is likely, but separating them needs a control group of
   overall-strong-but-not-venue-specialist racers. Not yet run.

If (3) survives that control it is directly actionable: discount 得意会場
in 準優/優勝戦. It would also rhyme with the P1 phase breakdown, where the
card-feature edge was smallest in exactly those seeded rounds.

## F持ち: the mechanism is real, and the market under-prices it (2026-08-01)

The first genuinely promising money-side finding. Practitioners hold that
a racer carrying a flying start in the current 級別審査期間 cannot risk a
second one, starts cautiously, and that this weakens the inside lane.
Nothing on the card states it as a number.

Everything needed was already in the database, from three directions that
only converged today: `race_result_entries.status = 'F'` (27,866 rows),
`actual_st_sec` (the realised start timing, so the claimed *mechanism* is
directly measurable rather than inferred), and the rating-period
boundaries, which were established this morning from the fan files and are
exactly the window in which an F counts.

**Design.** Each racer compared before vs after **their own first F of a
period**, within the same racer and period, so class and skill are fixed.
Control: racer-periods with no F, split at the midpoint of their own
racing, which absorbs any within-period time trend. The effect is the
difference of the differences.

| metric | F group | control | **DiD** |
|---|---|---|---|
| lane 1 mean ST | +0.0167 | −0.0028 | **+0.0195** |
| lane 1 win% | −6.71 pt | +0.58 pt | **−7.29 pt** |
| **lane 1 return** | −0.0765 | +0.0004 | **−0.0769** |
| lane 6 mean ST | +0.0309 | −0.0047 | +0.0356 |
| lane 6 win% | −0.84 pt | +0.22 pt | −1.06 pt |
| lane 6 return | −0.0671 | +0.0045 | −0.0715 |

All-lane ST: the F group goes 0.1605 → 0.1830 while the control goes
0.1713 → 0.1675. **They measurably slow down**, by about 0.02 s, which is
the size of gap that decides starts.

**The third row is the finding.** If the market priced this, the odds
would lengthen exactly enough to leave the return unchanged — that is
what pricing *means*. Instead backing an F-holding lane 1 returns 0.8482
against 0.9032 for a comparable non-F racer: **7.7 points of return that
the market does not take out.** Every other effect measured today was
either priced away or too small; this one is neither.

An F-holder underperforms in *every* lane, not only lane 1 — the caution
costs everywhere, and lane 1 simply has the most to lose.

**Caveats, neither of which the ST evidence supports away entirely:**

- Selection into "after": an F brings 斡旋停止, so later races may be at
  different venues and grades, and the racer's whole situation may have
  worsened. The DiD control absorbs calendar trend, not this.
- The F group's *before* lane-1 win rate is 55.42% against the control's
  51.93%, i.e. racers who commit an F are the aggressive, faster-starting
  ones, so some reversion is expected. But reversion does not make a
  racer's measured ST 0.02 s slower; that is a behavioural change, and it
  is the mechanism the theory predicts.

**Immediate follow-up, not yet run — and this is the buyable side.** The
7.3 points of win probability that leave lane 1 have to arrive somewhere,
and lane 6 does not gain them either (it also falls). So they go to lanes
2-5. If the market under-adjusts lane 1, it very likely under-adjusts
those too, which would make **the other boats in an F-holder's race
underpriced**. That is a positive-EV direction rather than an avoidance
rule, and it is the first candidate of the whole session that could
survive a payout test. Test it next.

### F持ち, the tradeable version — and a correction to the entry above

The entry above reported the difference-in-differences figure (lane-1
return −0.0769) as "7.7 points of return the market does not take out".
**That overstates what a bettor can capture, and the distinction matters
enough to record.**

The DiD compares a racer to *their own* pre-F self, so it measures the
size of the behavioural change. Nobody can bet against that counterfactual.
What a bettor actually faces is the market price of an F-holding lane 1,
and F-holders are *better than average* racers to begin with (pre-F lane-1
win rate 55.42% against the control's 51.93%), so their quality partly
offsets their caution. Cross-sectionally, which is the tradeable
comparison:

| lane | return, lane-1 F | return, no F | diff |
|---|---|---|---|
| 1 | 0.8822 | 0.9019 | **−0.0198** |
| 2 | **0.7848** | 0.7530 | **+0.0318** |
| 3 | 0.7457 | 0.7335 | +0.0122 |
| 4 | 0.7321 | 0.6935 | **+0.0386** |
| 5 | 0.6162 | 0.5882 | +0.0280 |
| 6 | 0.4027 | 0.3877 | +0.0150 |

**All five outer lanes move in the pre-specified direction**, so the
under-adjustment is real and spreads across the whole race, not just the
inside. An F-holding lane 1 occurs in 14.1% of races (104,314), so a rule
built on it is executable rather than theoretical.

But the size is ~2-4 points, not 7.7, and the best outer lane reaches
0.7848 against a break-even of 1.0000.

### Where the session actually landed

| rule | return |
|---|---|
| flat six lanes | 0.6603 |
| always lane 1 | 0.8598 |
| model, confidence ≥ 0.8 | 0.9235 |
| avoid an F-holding lane 1 | ≈ +0.02 |
| **break-even** | **1.0000** |

**The dominant fact is the 26.41% takeout.** Every effect measured today
moves the return by 1-8 points; the gap is 26. That is an order of
magnitude, not a tuning problem, and it is why "the model is accurate"
kept failing to become "the model is profitable".

Three routes remain, and only these:

1. **Selection on pre-deadline price** — the only untested one. Every
   rule evaluated so far chose a *lane* and ignored its cost. The cron
   capture began 2026-08-01.
2. **Bet types with more market noise** (3連単 and other exotics) — more
   combinations, coarser crowd pricing, but variance rises with it, so it
   needs the same payout discipline rather than optimism.
3. **直前情報** — public, but absent from the card, so the question is
   whether the price absorbs it fully in the minutes before close, which
   is again a price question rather than an accuracy one.

## Collection prioritised: predictions and 直前情報 now captured daily (2026-08-01)

Direction taken: weight effort toward accumulating data, collect anything
plausibly needed, and stop collecting later if validation says it is not.
The asymmetry justifies it — a day not captured can never be recaptured,
while an analysis can be redone whenever. Two gaps were closed.

### Prospective predictions (`race_predictions`, `db/predict_daily.py`)

The forward half of the P2 test. `capture_odds` had been recording
pre-deadline quotes since this morning; nothing recorded what the model
believed at the same moment, and reconstructing that later would make the
record a backtest wearing a forward test's clothes.

Deployed and run for today: **105 of 156 races** predicted (51 already
past their deadline), 630 rows, 0 races dropped for a missing feature.
Model frozen as `logistic_cards_20260731`, trained 2023-01-01..2026-07-31
and registered with a verified artifact checksum — `predict_daily` cannot
fit anything, which is asserted in the tests by passing a model with no
`fit` method.

Leakage invariants, all measured on the real rows: 0 predictions after a
deadline, 0 features available after their prediction, 0 races without
exactly six lanes, margin 1-481 minutes before the deadline.

A check flagged 4 races whose probabilities did not sum to 1. Chased
down: the maximum deviation is 0.000002, which is `Numeric(8,6)` rounding
six values (bound 3e-6). Not a defect — the check's tolerance was tighter
than the column's own precision.

**Probabilities are stored; decisions are not.** No rule measured today
is positive-EV, so committing to one would freeze a policy this project
does not believe in and invalidate the accumulated record every time the
policy changed. Storing the inputs leaves the decision rule free.

### 直前情報 (`before_info_entries`, `race_surface_conditions`)

The one genuinely pre-race source never collected, and today's
measurements pointed at it three separate times.

First live capture: **8 races due, 5 stored** (30 boat rows, 5 weather
rows), 3 fetched before their exhibition run and therefore deliberately
not written — the next scheduled run retries them, which is the whole
reason a blank row is never written. Captured 5-16 minutes before the
deadline. All weather rows carried the safe `"NR時点"` form.

**3 of 30 boat rows already show 進入変更** (start course ≠ lane). That is
10%, and it is exactly what nothing else in the schema can see: the
per-course statistics loaded this morning are keyed by *course*, the card
gives only the *lane*, and joining them on lane is wrong for those rows.

### Schedule now running on .21

| JST | job | requests/day |
|---|---|---|
| 06:30 | `ingest_daily card` | ~1 |
| **06:45** | **`predict_daily`** | **0 (DB only)** |
| every 2 min (even), 08-21 | `capture_odds` | ~300 |
| **every 2 min (odd), 08-21** | **`capture_beforeinfo`** | **~150** |
| 02:00 | `ingest_daily results` | ~1 |

The new capture runs on odd minutes so it interleaves with the odds
capture rather than firing alongside it; each job paces its own requests
3 s apart, so the combined worst case is well under one request per
second.

**What to revisit rather than assume**: the frozen model will go stale,
and no retrain policy is set. Retraining is safe for the record (the
`model_version` on every row makes the boundary visible) but should be a
decision, not a drift. Also still outstanding: a `db/load_fan_archive.py`
CLI, and nothing yet reads either the per-course stats or 直前情報.

## Within-race ST z-score: the first feature today to survive the payout test (2026-08-01)

Idea taken from an external write-up
(https://aijikan.com/2026/04/19/boatrace-ai-claude-code-roi/) which names
the *exhibition* ST z-score as the decisive addition to its LightGBM
model. The transform is right for a reason this project had already
arrived at from the other direction: in a six-class problem a quantity
shared by all six boats cannot change who wins, and the absolute ST level
in a race is dominated by shared terms — the venue, the day's water, how
hard the field is pushing. Z-scoring within the race removes those and
leaves the only discriminating part.

Exhibition ST does not exist historically (0 rows), so the same idea was
tested with what does: `race_result_entries.actual_st_sec`, 6.87 M
realised start timings, aggregated to (racer, **day**) before windowing so
that a result earlier the *same* day can never enter — `results_available_at`
puts a result at midnight after its race, and making that structural
beats making it a condition. A racer with no history falls back to the
field mean (z = 0) with a companion indicator, rather than dropping the
race; that affects 0.55% of lanes.

Same 31 folds and 142,884 races as every other run today:

| | log-loss | ROI @0.00 | ROI @0.50 | ROI @0.70 |
|---|---|---|---|---|
| baseline | 1.21143 | 0.9091 | 0.9155 | 0.9223 |
| **+ ST z-score** | **1.20233** (+0.751%) | **0.9147** | **0.9206** | **0.9254** |

**All four improve.** That is the finding, and it is what nothing else
today managed: the card features improved log-loss and lost the whole
gain to the price, and the gradient-boosted tree improved neither.

Read it at its real size, though: +0.5 ROI points against a 26.41%
takeout. The best figure anywhere is now 0.9254, and break-even is
1.0000. This is the same order as every other effect measured today, not
an escape from it.

**A bug nearly turned this into a null result.** The first run filtered
normal finishes with `rre.status IS NULL`; a normal finish carries
`'01'`..`'06'` and is never NULL, so the aggregate was empty, every
z-score was 0, and the comparison would have read as "the feature does
nothing". It was caught only because the run printed its coverage —
"races with any ST history: 0" — rather than only its result. Corrected
to `finish_position IS NOT NULL`, which also excludes the F rows whose
stored ST is not on the same scale.

**Why this raises the value of the 直前情報 backfill.** What was tested is
a *proxy*: a racer's ST tendency over their last ten racing days. The
article's feature is the exhibition ST — measured that day, in those
conditions, minutes before the race. The proxy already pays, so the
direct measurement plausibly pays more, and it is now being captured
daily. Backfilling the archive window is the way to test it on more than
one day of data.

**Not measured**: per-fold consistency. The aggregate covers 31 folds and
142,884 races, but the "wins in N of 31 folds" check that was applied to
`logistic_cards` was not applied here, so the claim is an average, not a
demonstrated per-fold win.

## Three external write-ups checked against the data (2026-08-01)

| source | claim | validation | bet type | takeout discussed |
|---|---|---|---|---|
| aijikan.com | ROI **96.3%** | backtest only, split undisclosed | 単勝 | yes (25%) |
| ai-moneygame.com | 回収率 **120.4-131.4%** | backtest + two anecdotes | 3連複 | **no** |
| note.com/codenai_san | **4x** (¥10k → ¥40k) | live, ~8 bets | 3連単/3連複 | **no** |

**Only the first reports a number consistent with everything measured
here — and it is a loss.** 96.3% is below the 100% break-even; the
article's framing against a 75% random baseline makes it read as a
success. It also settles on "履歴平均オッズ" rather than the actual price,
which biases upward for a reason this project measured directly today:
the model's accuracy converts into *shorter* prices (mean winning payout
¥161 against ¥192 for lane 1), and an average-odds settlement hides that
conversion entirely.

The second does not reconcile arithmetically. Case 1 gives 1,248 races,
hit rate 38.7%, average odds 4.2x — that is 0.387 x 4.2 = **1.625**,
against a claimed 1.314. The gap implies ~1.24 combinations staked per
race, which is never stated, so the headline cannot be checked from the
numbers given. 3連複 has 20 combinations, so a 38.7% hit rate requires
multiple tickets and the denominator matters entirely.

The third carries no information about ROI at all: ~8 bets on a bet type
whose favourite pays 7.36x means a single hit swings the result by
several hundred percent. Its NDCG@3 of 0.9006 is quoted without a
baseline, and in a six-boat field where lane 1 wins ~52% a trivial
lane-ordering baseline already scores high — the same trap as this
project's own "31% better than uniform", which turned out to be entirely
priced.

### Is any exotic bet type a softer market? Measured: no.

The claims all move to exotics, and that was *not* something these
measurements had refuted — more combinations plausibly means a crowd
pricing a bigger space with the same attention. Tested two ways from
recorded payouts alone, 2015 onward, ~634,000 races per bet type.

**Flat bet on every combination** (returns `(1-t) x mean(p/m)`):

| bet type | combos | flat return | p99/median payout |
|---|---|---|---|
| 単勝 | 6 | **0.6840** | 14.4x |
| ２連単 | 30 | 0.6180 | 21.3x |
| ２連複 | 15 | 0.6200 | 15.6x |
| ３連単 | 120 | 0.6086 | **28.6x** |
| ３連複 | 20 | 0.6207 | 14.2x |

**Backing the k-th favourite every race** (from `popularity_rank`, which
the K-file does not record for 単勝):

| bet type | rank 1 | rank 2 | rank 3 | rank 5 | rank 10 |
|---|---|---|---|---|---|
| ２連単 | 0.7485 | 0.7733 | 0.7847 | 0.7766 | 0.7008 |
| ２連複 | 0.7593 | 0.8153 | **0.8194** | 0.7350 | 0.5684 |
| ３連単 | 0.7605 | 0.7692 | 0.7663 | 0.7796 | 0.7656 |
| ３連複 | 0.7603 | 0.7757 | 0.7898 | 0.7674 | 0.6690 |

Two conclusions:

1. **No exotic favourite beats 単勝's lane 1 at 0.86.** The best figure
   anywhere is 0.8194 (2連複, 3rd favourite). 3連複 specifically — the bet
   type the 120% article recommends — returns 0.7603 to its favourite,
   has the *same* payout dispersion as 単勝 (14.2x vs 14.4x), and a worse
   flat portfolio. Its recommendation is not supported on any axis
   measurable here.
2. **Exotic markets are flat across ranks 1-5** (0.75-0.79), so the
   popular end is priced about as efficiently as 単勝's is. The
   favourite-longshot distortion lives in the deep longshots (rank 10
   falls to 0.57-0.70), which is the worst place to try to collect it.

The flat-portfolio numbers should not be read as takeouts: the nominal
rate is regulated at ~25% and the excess is the same favourite-longshot
bias measured this morning. Exotics look worse there largely because most
of their combinations *are* longshots, which is a property of spreading
money evenly, not of the market a selective bettor faces. The
favourite-backing table is the fairer comparison, and it points the same
way.

**Net effect on the plan**: exotics are not the escape hatch. The
remaining route is unchanged and is still selection on pre-deadline
price, which is now accumulating.

## 直前情報 backfilled, and the article's feature is the weakest part of it (2026-08-01)

Backfilled from the Open API mirror: **1,094,253 boat rows over 182,375
races, 2023-05-01 to 2026-08-01**, 99.99% carrying an exhibition time and
98.4% a start-exhibition ST. **8.16% of boat rows show 進入変更** — the
course actually taken differs from the lane — which is exactly the
fraction for which joining `racer_period_course_stats` on lane number is
wrong, and which nothing else in the schema can see.

Also completed: `race_results.winning_method` backfilled across the whole
archive — **1,152,001 updated, 11,930 races with no technique stated
(cancelled or void), 0 not found, 0 failures.**

### The result

Same discipline as everything today: log-loss *and* return settled at
real 単勝 payouts. 26 folds, 175,025 races, 99.9% with 直前情報.

| variant | log-loss | vs base | folds won | ROI @0.00 | @0.50 | @0.70 |
|---|---|---|---|---|---|---|
| baseline | 1.21163 | — | — | 0.9098 | 0.9137 | 0.9214 |
| exhibition ST z-score | 1.20973 | +0.157% | 25/26 | 0.9091 | 0.9142 | 0.9243 |
| **full 直前情報 block** | **1.19427** | **+1.433%** | **26/26** | **0.9163** | **0.9191** | **0.9295** |

**The external write-up named the exhibition ST z-score as its decisive
feature. On this data it is the weakest thing in the block**: +0.157% of
log-loss and a ROI that is flat to slightly *negative* at threshold 0.

**And the proxy beat the real thing.** The stand-in built earlier today —
a racer's ST averaged over their last ten racing days, z-scored within
the race — returned +0.751% and +0.0056 ROI, roughly five times the
log-loss gain of the actual exhibition ST. The likely reason is plain
once stated: the exhibition ST is *one* start on *one* day, while the
proxy averages ten. The direct measurement is fresher and much noisier,
and the noise wins.

That is worth keeping as a general caution: "measured today, on this
water, minutes before the race" sounds strictly better than a lagged
average, and here it is not.

**Where the value actually is**: the full block (ST z-score, exhibition
*time* z-score, tilt, 進入変更 flag) is nearly ten times better than the
ST z-score alone and wins **26 of 26 folds**. So the work is being done
by 展示タイム, tilt or 進入 — not by the start. 展示タイム is the natural
candidate: it measures boat speed over a timed solo run, which is the
"what would this boat do alone" quantity nothing else in the schema
observes. **Not decomposed** — which component carries it is untested.

**Best result of the session: ROI 0.9295** at threshold 0.70, against
0.9214 baseline and 0.9254 for the earlier proxy. Break-even remains
1.0000, so this is 7 points short. The 26.41% takeout still dominates
every effect found today; this is the largest of them, not an escape.

### Decomposed: 展示タイム carries it, and the components are additive

Each component added to the baseline alone — not ablated from the top,
which would confound with whatever the others absorb. Prior expectation
was recorded before the run: 展示タイム should carry it, being the only
*absolute* measurement of boat speed in the schema.

| component | log-loss | folds won | ROI @0.00 | ROI @0.70 |
|---|---|---|---|---|
| **展示タイム z-score** | **+0.764%** | **26/26** | **0.9148** | 0.9237 |
| **進入変更 flag** | +0.391% | **26/26** | 0.9114 | **0.9256** |
| 展示ST z-score | +0.155% | 25/26 | 0.9092 | 0.9238 |
| tilt | +0.148% | 22/26 | 0.9088 | 0.9220 |
| full block | +1.434% | 26/26 | 0.9162 | **0.9285** |
| baseline | — | — | 0.9075 | 0.9231 |

**The expectation held**: 展示タイム is the largest single component, more
than half the full block's gain, and it wins every fold.

**The components are almost perfectly additive** — 0.764 + 0.391 + 0.155
+ 0.148 = 1.458 against the full block's 1.434. They carry nearly
independent information, which is consistent with the other additivity
result today (the gradient-boosted tree losing to the linear model).

**進入変更 is the interesting second.** It fires on only 8.16% of boat
rows, yet at threshold 0.70 it produces a *higher* ROI (0.9256) than
展示タイム does (0.9237) despite half the log-loss gain. A rare signal
that is very informative when present will do that: it matters most
exactly where the model is being selective.

**Tilt is not established.** +0.148% and only 22 of 26 folds, and at
threshold 0.70 its ROI is *below* baseline.

**Noise floor, measured rather than assumed.** The same baseline, on the
same folds and data, returned 0.9231 in this run and 0.9214 in the
previous one (log-loss 1.21177 vs 1.21163). So **ROI differences below
about 0.002 are run-to-run variation** and must not be read as effects —
which is exactly the size of the tilt result.

Best figure stands at ROI **0.9285-0.9295** for the full block at
threshold 0.70, against a break-even of 1.0000.

## Conditions: the market compresses, and calm water is a lane game (2026-08-01)

Two measurements, both made possible by the 直前情報 backfill, which
supplies *race-time surface* conditions where the JMA data already loaded
is a daily average from the nearest land station -- and showed no effect
at all, plausibly for being too coarse.

Race 1 is excluded throughout. The mirrored surface reading carries no
observation label, and for race 1 the official page shows a wall clock
which, fetched after the fact, is the day's *last* reading — confirmed on
a real page timestamped six hours after race 1's deadline. Including it
would be scoring tomorrow's weather against today's result.

### Does the market price the conditions?

The question is not whether wind and waves change results — they plainly
do — but whether the crowd moves the price by the right amount. In a
pari-mutuel pool the return is `(1-t) · p/m`, so **the win rate may vary
across conditions all it likes; if pricing is correct the return will
not.**

| conditions | races | lane-1 win% | **lane-1 return** | lane-6 return | flat six |
|---|---|---|---|---|---|
| stable (wind ≤2, wave ≤2) | 73,430 | 56.39% | **0.9181** | 0.3509 | 0.6570 |
| middling | 64,835 | 53.42% | 0.9034 | 0.3952 | 0.6940 |
| unstable (wind ≥5 or wave ≥6) | 26,656 | 49.71% | **0.8667** | 0.3990 | 0.7129 |

**It does not.** The return moves 5.1 points, monotonically, on all three
cuts (wind alone, wave alone, combined) — twenty-five times the measured
noise floor of ~0.002. Lane 1 is underbet in every bucket (`p/m` = 1.248
stable, 1.178 unstable) but *more* underbet in calm water: **the market
compresses, moving the price less than the probability moves.** Lane 6
mirrors it (0.3509 → 0.3990), and the flat six-lane portfolio is
correspondingly better calibrated in rough water than in calm.

### What each factor is actually worth

Same scale for all of them — win-probability points. Lane is measured
across lanes; **every other factor is measured within lane and then
averaged**, because the lane effect is large enough to leak into and
inflate everything else if it is not held fixed.

| factor | stable | middling | unstable |
|---|---|---|---|
| **lane 1 vs lane 6** | **53.4%** | 50.2% | **46.2%** |
| racer win rate, top vs bottom third | 13.1% | 13.6% | **13.9%** |
| racer class A1 vs B2 | 10.5% | 11.1% | **12.0%** |
| **exhibition time, fastest vs slowest third** | **3.1%** | 2.8% | 2.7% |
| **motor 2連率, top vs bottom third** | **2.4%** | 2.5% | 2.1% |

1. **Lane dominates by a factor of four.** 53.4 points against 13.1 for
   the best racer measure.
2. **Calm water is a lane game; rough water gives skill room.** Lane
   falls 53.4 → 46.2 while the racer measures rise. The folk version of
   this is old; this is it on one scale.
3. **Motor is small — 2.1-2.5 points**, about a fifth of the racer effect
   and a twentieth of lane, despite the attention 2連率 gets. It also
   explains the earlier 調整力 result: if the motor *main* effect is this
   size, an interaction on top of it must be smaller still.
4. **Exhibition time beats the season motor rate** (3.1 vs 2.4 in calm
   water), and is strongest exactly where raw speed shows through. That
   is the same conclusion the feature decomposition reached from the
   other direction.

Caveat: these are marginal effects within lane, not partial effects net
of each other. Racer class and racer win rate overlap heavily. Motor
against racer is a fair comparison, since motors are drawn by lottery.

### The tension this creates

**Room and reward point in opposite directions.** Calm water is where the
market misprices lane 1 most (0.9181) but also where a model has least to
add, since lane explains almost everything. Rough water is where skill
assessment has the most room (racer 13.9 against lane 46.2) but where
backing the favourite pays worst (0.8667).

Any price-selection rule built later has to face that directly rather
than assume the two line up.

**Not yet measured**: whether "stable conditions" and "high model
confidence" are largely the same races. They probably overlap heavily —
the model should be more confident in calm water — so the two filters may
not stack, and assuming they do would overstate any combined rule.

## E30 fuel: exhibition times slowed 0.020 s; nothing else survives noise (2026-08-02)

BOAT RACE振興会 rolled bioethanol-30 fuel out venue by venue from
2026-04-09, **in step with each venue's motor replacement**, and stated
that "競技内容への影響は確認されず、走行性能に問題もなかった" from prior
testing. Staggered adoption makes that checkable: six venues switched on
known April dates, the rest had not by 2026-08-01.

Ethanol carries about two-thirds the energy of petrol, so the direction
was stated before looking: **exhibition times should slow.**

| metric | E30 change | control change | **DiD** |
|---|---|---|---|
| mean exhibition time | +0.0031 | −0.0170 | **+0.0201 s** |
| lane-1 win rate | +0.33 pt | −0.65 pt | +0.99 pt |
| lane-1 return | +0.0305 | +0.0194 | +0.0111 |

**Only the first survives.** With 26,460 exhibition rows per arm and a
~0.15 s spread, the DiD's SE is ≈0.0018 — so +0.0201 is about 11 SE.
The other two are not established: lane-1 win rate has SE ≈1.6 pt against
a 0.99 pt effect, and the return SE is ≈0.05 against 0.011. Both are
inside noise, on ~4,000 races per arm.

So boats are measurably slower and **nothing can yet be said about lane
advantage or market pricing.** Against the promoter's claim: a 0.020 s
slowdown is not a "走行性能の問題", so this is not a contradiction — but
"影響は確認されず" is not literally true at measurement precision.

**The fuel effect cannot be separated from the fleet effect, ever.** The
rollout is tied to motor replacement at every venue by design, so the
0.020 s is the combined effect of new fuel plus new motors and no design
can attribute it further. Recorded in `venue_regimes.py`.

### Two venue-copy claims checked

Published prediction copy, checked for factual accuracy rather than for
edge — today's pattern is that widely-read information is priced, so a
correct claim is not a profitable one.

**"江戸川の69号機がエース機として君臨、66号機が急上昇" — substantially
correct.** Over the fleet in use since 2025-05, 60 motors with ≥100
starts: **69 ranks 3rd (22.8% win) and 66 ranks 5th (21.6%)**, against a
fleet median of 16.2% and a floor of 10.1%. "君臨" overstates it — 55
(23.7%) and 56 (23.4%) are ahead — and at ~270 starts the SE is ~2.4 pt,
so the **top ten are statistically indistinguishable**.

**A trap inside that check, worth keeping.** The raw win-rate range across
the fleet is 10.1–23.7%, i.e. 13.6 points, which reads as an enormous
motor effect and appears to contradict the 2.4-point figure measured for
motor 2連率 terciles. It does not: subtracting binomial noise
(SE ≈ 2.4 pt at n≈270) leaves a true spread of roughly 1.8 pt sd. **The
raw range overstates motor quality about fivefold**, and the two
measurements agree once that is removed.

**"びわこでE30後に舟足が二段化" — not supported.** The claim predicts a
widening spread, which the earlier E30 measurement had not looked at: it
measured the *mean* exhibition time and never the dispersion. Tested as a
DiD on the within-race standard deviation of exhibition times:

| group | before | after | change |
|---|---|---|---|
| E30 | 0.07147 | 0.05854 | −0.0129 |
| control | 0.06998 | 0.06300 | −0.0070 |
| **DiD** | | | **−0.00595** (SE ≈ 0.00767) |

The spread **narrowed** rather than widened, and the effect is inside one
SE. So: the mean fell, the dispersion did not move.

In fairness the claim names 行き足 and 直線足 explicitly as things
展示タイム does not capture — which also means **it cannot be falsified
with public data**, and that is worth noticing about the claim rather
than about the fuel.

Not checked, for want of data: the 平和島/児島 renovation wind claim needs
construction dates (a `venue_regimes` row would make it testable), and
the "3-4 months in, motor differences emerge" claim is testable as
dispersion against fleet age but was not run.

## The 直前情報 model is deployed and predicting daily (2026-08-03)

The block measured on 2026-08-01 was a finding in a script. It is now a
second frozen model, running alongside the card model on the same races.

### What was built

`dataset.include_before_info` adds four columns per lane -- 展示タイム
z-scored within the race, 展示ST likewise, tilt, 進入変更 -- to *both*
`build_dataset` and `build_prediction_rows`, assembled from shared SQL
fragments. The fragments are the point: a feature computed one way at fit
time and another at predict time raises nothing, it just makes the model
score numbers that do not mean what it was fit on. A test asserts the two
paths return an identical row for the same race.

**Optional rather than always-on.** `before_info_entries` begins
2023-05-01, so one always-on feature set would have to throw away two and
a half years of training data or carry an is-it-there indicator that
makes the morning prediction pay for a feature it never has. Two models,
each predicting what it can.

**Registry activations are now scoped to a role** (`default` = card,
`preview` = 直前情報). Neither is a candidate for the other's slot, cron
names a role rather than a version id that goes stale on the next
retrain, and entries written before roles existed read as `default` --
so the activation already on .21 was unaffected. Verified: after
registering the preview model, `default` still resolves to
`logistic_cards_20260731`.

**Which feature set to build is read from the registry entry**, not
passed as a flag. A flag would be a second place to state the same fact.

### Deployed on .21

```
logistic_cards_preview_20260802  role=preview  91 features
  window 2023-05-01..2026-08-02, 180,216 races
  races_considered=180442 races_used=180216 dropped_no_single_winner=30
  dropped_missing_feature=0 dropped_late_feature=0
  dropped_missing_before_info=196
```

**Coverage is not the constraint it might have been**: 180,243 of 180,442
finished races since 2023-05-01 carry a complete block (99.89%), and the
30 further losses are the usual dead heats and void races. The generated
PostgreSQL was checked against the live database before deploying --
valid, `bi_too_late = 0`, 進入変更 on 6.4% of a day's lane rows against
the 8.16% measured over the archive.

Dry run at 13:25 JST on 2026-08-03, rolled back:

```
races_in_dataset=60 races_predicted=3 skipped_deadline_passed=55
skipped_not_yet_due=2 rows_written=18
[races_considered=144 races_used=60 dropped_missing_before_info=84]
```

That shape is the design working: of 144 races, 60 had their 直前情報 in
at that moment, 84 had not run their exhibition yet, and only the 3 whose
deadline was within ten minutes were predicted.

### The schedule

One crontab line, odd minutes 08-21, after `capture_beforeinfo`. A race
is normally predicted in the same cycle it was captured, but nothing
depends on that ordering -- a race missed this cycle is picked up two
minutes later, and the ten-minute lead absorbs it. DB only, no request.

**Each race is predicted once**, unlike the morning run's "a later run
adds a later-stamped row". Odds move, so a second odds reading is a
second fact; 直前情報 does not change once published, so a second
prediction would be the same arithmetic on the same inputs -- fifteen
identical rows per race over the window.

**Ten minutes out matches the earlier of `capture_odds`' leads**, so a
probability and a price now exist at roughly the same moment for the same
race. That is what item 2 of the plan (selection on price) needs and
could not have without this: the pair can be compared without
interpolating between odds readings.

### What this does and does not establish

It starts a record; it proves nothing yet. The +1.43% is a walk-forward
figure on backfilled 直前情報 whose `available_at` is the deadline, not
the fetch. The forward record accumulating from today is on *live*
captures stamped when they were actually read, and it is the first
evidence about the block that is not a backtest.

The comparison that matters is now available and was not before: the same
race carries a card-only probability from 06:45 and a 直前情報 probability
from minutes before the deadline, settling against the same result. What
the block is worth forward is a query away rather than another experiment.

Still true, and unchanged by any of this: the best ROI measured anywhere
is 0.9295 against a break-even of 1.0000, and the 26.41% takeout
dominates every effect found so far.

## 1番人気は式別を問わず 0.71-0.79。歪みは人気の逆側にあった (2026-08-03)

Question asked directly: for the lowest-odds ticket in each bet type,
how often does it win and what does it pay? Measured rather than
reasoned about, because the follow-up question -- "if it averaged 2x and
hit over 50%, that would profit" -- is arithmetically correct and the
only way to answer it is with the actual pair of numbers.

### 1番人気の的中率と払戻倍率

`race_payouts.popularity_rank` covers 連単/連複/拡連複 across the whole
archive; it is **NULL for 単勝 and 複勝 in every one of the 1.15 M rows**,
so those two come from `odds_snapshots` closing prices and cover only the
80-day window (11,050 races, 2025-07-29..10-17).

| 式別 | レース数 | 的中率 | 平均倍率 | 中央 | 最高 | 回収率 |
|---|---|---|---|---|---|---|
| ３連単 | 1,151,431 | 9.30% | 7.95 | 7.20 | 41.6 | 0.7394 |
| ２連単 | 1,152,282 | 21.73% | 3.43 | 3.10 | 15.1 | 0.7457 |
| ３連複 | 1,150,243 | 25.03% | 3.03 | 2.90 | 10.2 | 0.7588 |
| ２連複 | 1,151,890 | 31.15% | 2.44 | 2.30 | 8.5 | 0.7586 |
| 拡連複 | 1,150,329 | 52.68% | 1.35 | 1.30 | 6.7 | **0.7115** |
| 単勝 | 11,050 | 54.50% | 1.44 | 1.30 | — | **0.7868** |
| 複勝 | 11,087 | 70.67% | 1.11 | 1.00 | — | 0.7842 |

Re-running the 連単/連複 rows restricted to the same 80-day window moved
the hit rates by only +0.6 to +1.8 pt, so that window is not peculiar and
the 単勝 figure can be read alongside the archive ones.

複勝 carries a caveat: 2,151 of 11,087 races have several boats tied at
the lowest 複勝 odds (the quote is coarse, 1.0-1.1), and the tie was
broken by lane number, which biases the pick toward lane 1. Read it as
roughly "lane 1's top-two rate". 単勝 has only 173 such ties.

**ワイドが最も悪い (0.7115) にもかかわらず的中率は最高 (52.68%)**, which is
the cleanest single demonstration available that 当たりやすさ and 儲かるか
are unrelated quantities.

### なぜ「2倍で50%超」が存在しないのか

損益分岐に必要な的中率は `1 / 平均倍率`. Every bet type falls short of it
by almost exactly the same fraction:

| 式別 | 必要的中率 | 実際 | 不足 |
|---|---|---|---|
| ３連単 | 12.58% | 9.30% | −26% |
| ２連単 | 29.13% | 21.73% | −25% |
| ３連複 | 32.99% | 25.03% | −24% |
| ２連複 | 41.06% | 31.15% | −24% |
| 拡連複 | 74.03% | 52.68% | −29% |

Directly, from the 80-day 単勝 odds over all six boats (not just the
favourite) -- the table that answers the question as asked:

| オッズ帯 | 平均オッズ | 理論勝率 1/odds | 実際の勝率 | 回収率 |
|---|---|---|---|---|
| 〜1.5 | 1.18 | 84.47% | 66.09% | 0.7740 |
| 1.5-2.0 | 1.69 | 59.33% | 47.24% | **0.7860** |
| 2.0-3.0 | 2.43 | 41.14% | **31.85%** | 0.7612 |
| 3.0-5.0 | 3.93 | 25.47% | 18.98% | 0.7231 |
| 5-10 | 7.23 | 13.84% | 9.64% | 0.6659 |
| 10-20 | 14.07 | 7.11% | 4.49% | 0.5981 |
| 20〜 | 44.98 | 2.22% | 1.85% | 0.6625 |

**A 2x ticket wins about 32%, not 50%.** The odds are the crowd's
probability estimate, and the realised rate sits below the implied one in
every band -- by 22% at the short end and 37% at 10-20x, the familiar
favourite-longshot shape.

### 会場・R・節日で「人気通り」率は動く。回収率は動かない

Asked specifically to hold weather constant and look for venues or
positions in the series where the favourite simply holds, on the theory
that机力 (motor) would dominate there.

**会場** (2連単1番人気, full archive): 人気通り率 spans 8.81 pt, 大村
26.25% down to 平和島 17.44%. 回収率 spans only 7.2 pt (0.7037 鳴門 ..
0.7756 蒲郡) and **does not follow it** -- 戸田 is second-worst on hit
rate (17.68%) yet returns 0.7604, above 大村's 0.7625 peer group. The
correlation across the 24 venues is **+0.3932**, just under the n=24
significance threshold of 0.404.

**レース番号**: R12 25.35% vs R3 18.28% (7 pt), 回収率 flat at
0.723-0.763.

**節の第N日** -- the one that matches the hypothesis:

| 第N日 | レース数 | 人気通り% | 回収率 |
|---|---|---|---|
| 1 | 211,656 | 20.31 | 0.7275 |
| 2 | 211,749 | 21.44 | 0.7513 |
| 3 | 211,640 | 21.29 | 0.7454 |
| 4 | 211,119 | 22.11 | 0.7431 |
| 5 | 172,510 | 22.92 | 0.7513 |
| 6 | 126,736 | 22.83 | 0.7625 |
| 7 | 6,949 | 25.18 | 0.7707 |

**Monotone, and the return moves with it** -- 0.7275 to 0.7625 is 3.5 pt
against a ±0.7 pt interval, so it is a real effect and not noise. This is
consistent with "the machine becomes known as the series runs".

It does **not** isolate the motor, though. Everything learnable is
learned over a 節 -- the racer's condition, the water, how the 進入 is
settling -- so the gradient cannot distinguish 機力 from any other
factor that resolves with repetition. And the motor's own size was
already measured: 2連率 terciles differ by only **2.4 pt** of win rate,
with the raw fleet range (10.1-23.7%) overstating motor quality about
fivefold.

**水面** (直前情報, 2023-05以降, R1除く): 安定 (風≤2 波≤2) 24.86% /
0.7581, 中間 23.63% / 0.7537, 荒天 (風≥5 or 波≥6) 22.04% / 0.7204.

### 条件を積んでも 0.8173 が上限

安定水面 × 節4日目以降 × 人気通り率上位3場 (徳山・大村・蒲郡),
2023-05以降: **5,516 races, 29.62%, 0.8173 ±0.0356**, and it reproduces
across halves (0.8266 前半 / 0.8080 後半). The best conditional cell
found anywhere in this session, and still 18 points short of 1.0000.

**The venue edge in that cell is a period effect, not a weather effect,**
which is worth recording because the opposite is the natural reading:

| 会場 | 2023-05より前(全天候) | 2023-05以降・それ以外 | 2023-05以降・安定 |
|---|---|---|---|
| 徳山 | 0.7546 | 0.8034 | 0.8421 |
| 蒲郡 | 0.7742 | 0.7663 | 0.8179 |
| 大村 | 0.7579 | **0.8540** | 0.8159 |

All three rise after 2023-05 in *both* weather groups, and 大村's rough
group beats its calm group. So "静水面だから人気通りに来て、だから儲かる"
is not supported; what the recent period is doing was not established.

### 歪みは人気の逆側にあった

The only thing measured this session that cleared 1.0000 was the
opposite of the search:

| | レース数 | 1号艇勝率 | 回収率 | 95%区間 |
|---|---|---|---|---|
| 1号艇が1番人気 | 8,361 | 61.42% | 0.8509 | [0.8352, 0.8666] |
| **1号艇が1番人気でない** | 2,688 | 31.81% | **1.1072** | [1.0338, 1.1807] |

By 1号艇's own 単勝 odds band the gradient is monotone: 0.7902 (~1.3),
0.8638, 0.8910, 0.9490, 1.0606 (3-5x), **1.3979 (5x~)**. Robustness: both
halves of the window agree at odds≥3.0 (1.2194 and 1.0943), and dropping
the five largest payouts of 112 hits in the 5x+ band still leaves 1.2559.

Also measured for comparison, same window: **1号艇ベタ 0.9078** against
**1番人気ベタ 0.7868** -- backing the inside lane blindly beats backing
the favourite by 12 points.

**Read it as a hypothesis, not a finding.** 1,840 races at odds≥3.0, an
80-day window, and the odds-band cut was chosen after seeing the data.
The prices are *closing* odds, which no bettor can act on. And the sign
is opposite to the exotic markets, where the deep longshots are overbet
(2連単 by 人気: 0.7457 / 0.7744 / 0.7824 / 0.7817 / 0.7736 / 0.7599 for
ranks 1-6 -- essentially flat, no rank worth singling out).

**Why it is the more promising direction anyway**: the takeout is fixed,
so a profit can only come from mispricing, and 1番人気 is the ticket the
most people examined. Looking for conditions where the favourite holds is
looking where a distortion is least likely to have survived.

This is exactly what item 2 (selection on price) exists to test, and as
of today both halves of the evidence it needs are accumulating: the
2-minute pre-deadline odds since 2026-08-01, and the 直前情報 model's
probabilities since 2026-08-03.

**Note on an earlier figure.** This session recomputed 1号艇単勝ベタ over
2015 onward: **634,921 races, 53.39%, mean 1.69, return 0.9032.** The
"単勝's lane 1 at 0.86" quoted in the exotics section above does not
reproduce at that definition. The discrepancy was not chased down;
whoever revisits it should re-derive the 0.86 rather than trust it.

## 内枠のミスプライスは「格」ではなく「価格」の次元にある (2026-08-03)

Three items were run in the order asked: capture the second pool first,
then the two measurements that existing data could already answer.

### 3. 2連単/2連複 を捕捉開始（デプロイ済み）

`odds2tf` renders both pools on one page, so 2連複 is free. The grid is
read *by column* -- the header carries the six first-place boats and each
body row holds six `(second boat, odds)` pairs -- and the first lane is
taken from the header rather than assumed to be `column + 1`, because a
欠場 would otherwise shift every price onto the wrong boat with nothing
downstream able to notice.

Both pages for a race share **one** `observed_at`, read before either
fetch. Two stamps 3 s apart would leave every cross-pool comparison with
a 3 s window for the market to move in, which is exactly the quantity
being measured.

Live on .21 from 2026-08-03 14:16 JST; first race captured 30 exacta and
15 quinella rows at a single stamp. Volume goes from ~450 requests a day
to ~900, about 1.3 a minute averaged over the racing window. (The
proposal said "+150/day", which was wrong by 3x -- it is +450, three lead
times times ~150 races. The "double the current" characterisation was
right.)

### 1. 仮説は否定された

The proposed mechanism was: the crowd over-discounts the inside lane's
*positional* advantage when the lane-1 racer is visibly weak. The
descriptive evidence looked strong -- lane 1 goes unpopular almost
entirely on its own grade (級別 3.162 → 2.186, 勝率 5.958 → 4.637) while
the opposition barely changes (5.246 → 5.145, marginally *weaker*).

**It does not survive.** Stratified by `grade_gap` (lane 1's 全国勝率
minus the mean of the other five), lane 1's return is flat:

| 格差 | n | 市場の確率 | 実際の勝率 | 回収率 |
|---|---|---|---|---|
| < −1.5 | 506 | 20.63% | 21.15% | 0.9079 |
| −1.5..0 | 3,379 | 35.01% | 39.83% | 0.9201 |
| 0..1.5 | 5,338 | 50.66% | 59.44% | 0.9055 |
| ≥ 1.5 | 1,826 | 60.97% | 74.70% | 0.9247 |

The rising 乖離 in percentage points (0.5 → 13.7) is a base-rate
artifact, not a widening error: 13.7 pt on a 61% base is a *smaller*
relative mistake than 4.8 pt on 35%, and the return column -- which is
what a bettor collects -- does not move.

### The pattern is in the price dimension, and it is sharper

Cross-tabulating by odds band and lane instead:

| オッズ帯 | | n | 勝率 | 理論勝率 | 回収率 |
|---|---|---|---|---|---|
| ~3 | 1号艇 | 10,044 | 59.22% | 64.09% | 0.8619 |
| ~3 | 2-6号艇 | 5,383 | 29.65% | 46.77% | 0.6057 |
| 3-5 | **1号艇** | 1,279 | **28.85%** | 26.93% | **1.0606** |
| 3-5 | 2-6号艇 | 7,973 | 17.40% | 25.25% | 0.6689 |
| 5-10 | **1号艇** | 483 | **21.33%** | 15.59% | **1.3741** |
| 5-10 | 2-6号艇 | 16,607 | 9.30% | 13.79% | 0.6453 |
| 10~ | 1号艇 | 78 | 11.54% | 6.61% | 1.5449 |
| 10~ | 2-6号艇 | 28,583 | 3.26% | 3.54% | 0.6251 |

**Above about 3x, lane 1 wins more often than its price implies while
every other boat wins less.** At 5-10x the implied rate is 15.59% and the
realised one is 21.33%; for the other five lanes at the same price it is
13.79% implied against 9.30% realised. So this is not the
favourite-longshot bias -- lane 1 runs *opposite* to the field.

The reading: the market applies too small an inside-lane premium at long
prices. It prices a dear lane 1 like an ordinary longshot, and it is not
one.

The gradient survives the grade control, which is what says the two
dimensions are not the same variable in disguise:

| 1号艇の格 | ~2 | 2-3 | 3-5 | 5~ |
|---|---|---|---|---|
| 格下 | 0.7347 | 0.8730 | 1.0012 | 1.3317 |
| 格上 | 0.8630 | 1.0445 | 1.2715 | 1.9610 |

**Read the size honestly.** 80 days, and the odds-band cut was chosen
after seeing the data. n collapses along the gradient: 3-5x is 1,279
bets (CI [0.968, 1.153], includes 1.0), 5-10x is 483 (CI [1.132, 1.616],
excludes it), 10x+ is 78 with a ±1.12 interval and carries no
information. The 格上 × 5x~ cell is 59 bets.

**There is no out-of-sample test available in existing data.** 単勝 odds
exist for these 80 days and nowhere else; the 21-year archive has
payouts, which give the winner's odds only. The forward capture is the
test, which is the same conclusion the price-selection item already had.

### 4. オーバーラウンドの幅は丸めではなく下限1.00で、利用できない

Σ(1/odds) averages 1.3589 (26.41% implied takeout, matching the known
figure) but ranges 1.0403-1.4293. The spread is **not** generic 0.1
quantisation:

| Σ(1/odds) | レース数 | 含意控除率 | 最短オッズ平均 | 最長オッズ平均 |
|---|---|---|---|---|
| <1.32 | 452 | 20.10% | **1.00** | 96.81 |
| ≥1.32 | 10,598 | ~26.4% | 1.58 | 39.08 |

Every low-overround race contains a boat quoted at the **1.00 floor**.
A pari-mutuel would price a 90%-probability boat at 0.736/0.90 = 0.82;
the floor lifts it to 1.00, which removes about 0.22 from the race's
implied total. That is the whole of the effect.

It is not actionable, and the reason is structural rather than
statistical:

| 戦略 | 舟券数 | 勝率 | 回収率 |
|---|---|---|---|
| 下限1.00の本命を買う | 1,329 | 72.23% | **0.7223** |
| 同レースの残り5艇を一律に買う | 6,645 | 5.55% | 0.7871 ±0.1104 |

A 1.00 quote pays the stake back, so the best possible outcome is a
return of exactly 1.0 and the expected return **is** the win rate --
below 1 by construction, whatever the takeout says. The other five in
those races return the market average. The earlier 0.9544 figure was
flat-betting all six and was carried by rare hits at up to 150x; it does
not survive splitting.

Noted in passing: `race_payouts` 単勝 has a minimum payout of **70 yen**,
not 100, so a winning ticket can still return 0.7. 104,773 rows pay
exactly 100 (元返し). Neither was chased down.

### 差し引き

Item 4 is closed. Item 1's stated mechanism is refuted but left a
sharper and better-supported pattern in its place, and one that the
already-running capture is positioned to test forward. Item 2 (odds
movement, and the market's reaction to 直前情報 across the 60-minute
bracket) waits on accumulation, as planned.

## 期待値で選ぶ — 一度も試していなかった唯一の選択規則 (2026-08-03)

Asked whether any other route to a positive return exists. Checking the
record first turned up the answer to a different question: **every ROI
figure this project has produced selected bets by the model's own
probability**, never by price. "Back the argmax lane when it clears 0.70"
finds likely winners; it has been measured repeatedly (0.910 → 0.924,
plateauing, reversing at 0.90) and it never approached 1.0000, because
the accuracy converts into shorter prices. `expected_value.py` existed
since P2-T003 and had never been pointed at real rows.

`db/evaluate_p2.py` (new) does it. `ev_i = p_i * o_i` is directly the
expected return per unit staked, `Dataset` gained `race_ids` so a
prediction can be settled at its own payout, and three rules run on the
same races: `confidence` (the historical rule, for reference), `ev_best`,
`ev_all`.

Train 2023-01-01..2025-07-28 (143,299 races), test 2025-07-29..10-17
(11,049 priced races) -- the only window with odds. **The `confidence`
row reproduces the known figure (56.27% hit, ROI 0.9176), which is what
says the settlement bookkeeping is right.**

### 生の結果は信用できない

| rule | thresh | bets | hit | ROI |
|---|---|---|---|---|
| confidence | 0.80 | 562 | 82.38% | 0.9270 |
| ev_best | 1.0 | 8,368 | 20.69% | 1.1600 |
| ev_best | 1.5 | 3,180 | 12.55% | 1.5025 |
| ev_all | 1.5 | 4,436 | 12.24% | 1.5500 |

ROI 1.55 against a 26.4% takeout is not believable, and it is not what
it looks like. Four checks were run before reporting any of it:

1. **Control** -- feed the same code the *market's own* implied
   probabilities. `p_market * o` is identically 1/overround, so no bet
   can clear EV 1.0. Result: max market EV seen 0.9613, zero bets, and
   flat-betting all six returned **0.6747**, matching the known flat
   portfolio figure. The harness settles correctly.
2. **Odds attribution** -- for a winning bet, 単勝 payout must equal the
   odds by definition. **10,871 of 11,049 agree** (the 178 are rounding
   and refund cases). The prices are on the right boats.
3. **Calibration** -- the model is well calibrated on the test set
   (bin 0.0: 0.0436 predicted vs 0.0446 actual; bin 0.7: 0.7447 vs
   0.7360). It is not producing nonsense probabilities in aggregate.
4. **The tail** -- and here it breaks. Dropping the **20 largest payouts
   of 4,472 bets (0.45%)** takes ROI from 1.5309 to **1.1071**. The
   100x+ band shows ROI 3.18 on **8 hits out of 368 bets**.

So the headline is carried by a handful of enormous longshot hits. The
mechanism is plain once stated: a model probability of 1.5% on a 100x
boat clears EV 1.5, and the calibration bins are far too coarse (0-10%
holds 37,876 observations) to validate the model anywhere near 1-3%.

### 残るものは、朝見つけた1号艇の効果

Decomposed by lane, with the top ten payouts trimmed:

| EV≥ | | bets | hits | ROI | 上位10除く |
|---|---|---|---|---|---|
| 1.0 | 1号艇 | 3,477 | 1,586 | 1.1557 | **1.1190** |
| 1.0 | 2-6号艇 | 10,625 | 1,009 | 1.1110 | **0.9862** |
| 1.2 | 1号艇 | 1,772 | 709 | 1.3210 | **1.2497** |
| 1.2 | 2-6号艇 | 6,776 | 569 | 1.2573 | 1.0618 |
| 1.5 | 1号艇 | 765 | 271 | 1.5557 | **1.3984** |
| 1.5 | 2-6号艇 | 3,669 | 276 | 1.5456 | 1.1849 |

**Lane 1 survives trimming; the other five largely do not.** Capping the
odds at 30x barely moves the lane-1 rows (1.1471 / 1.3044 / 1.5178), so
it is not a tail effect there either, and the hit counts are large
(271-1,586) rather than a handful.

This is the *same* phenomenon found this morning -- lane 1 is underpriced
above about 3x -- but now expressed as a decision rule rather than a
stratification, and with a threshold that trades bet count against edge.

`TRIM_TOP_PAYOUTS` and `trimmed_roi` were added to the module afterwards
so this check runs by default. A rule that reports only ROI hides exactly
the failure that nearly got reported here.

### 何が言えて、何が言えないか

Says: **selecting on price rather than confidence is the mechanism that
was missing**, and it had never been tried. Every previous ROI number in
this project answered a different question.

Does **not** say there is a profitable strategy:

- The odds are archived **closing** prices. Nobody can bet at them. This
  is not a detail -- it is the whole gap between this and a strategy.
- One 80-day window, and it is the only window with odds, so there is no
  out-of-sample test available in existing data.
- Several thresholds were tried. The lane-1 result is monotone across
  all of them, which is reassuring, but it was still chosen after
  looking.
- The durable part reduces to a single, already-known effect. Nothing
  here found a *second* source of edge.

The forward capture is the test, and it is now collecting everything it
needs: pre-deadline odds at 60/10/2 minutes, the 直前情報 model's
probabilities, and since today the 2連単/2連複 pool.

## 未使用テーブルの棚卸しと、選手の数値化 (2026-08-03)

### どのテーブルがモデルに届いているか

Features reach a model through exactly one path -- `dataset.py`'s SQL --
so "is this table used" has a precise answer. It reads `races`,
`race_entries`, `race_results`, `race_result_entries`,
`before_info_entries`. `evaluate_p2` additionally reads `odds_snapshots`
and `race_payouts` for settlement. That is all.

**Stored, populated, and never read by any model:**

| テーブル | 行数 | 未使用の理由 |
|---|---|---|
| `racer_period_course_stats` | 241,224 | コースで鍵付け、進入を観測する手段が無かった |
| `racer_period_stats` | 40,204 | 同上（親） |
| `race_surface_conditions` | 182,722 | 直前情報ブロックから意図的に除外 |
| `weather_observations` | 189,096 | 陸上最寄り局の日別平均、会場適性に効果ゼロと既測 |
| `race_results.winning_method` | 1,152,001 | バックフィル済み、用途未定 |
| `exhibition_entries` | 7,628 | **ロード順バグ**で698万エントリ中7,628行のみ |

`exhibition_entries` is worth a note it has not had: its data is
results-time so it can never describe *this* race, but a feature about
*past* races is available at prediction time. Fixing the loader would
extend 展示タイム back to 2005 -- the only route past
`before_info_entries`' 2023-05 start.

### 選手のコース別能力は実在し、番組表には無い

`(racer, period, course)` holds a **mean of 16.8 starts, median 17**;
only 4.6% of rows have 30 or more. At course 1's 46.8% base that is a
~12 pt standard error, at course 6's 2.0% about 3.4 pt. The published
per-course rate is therefore mostly noise, and shrinkage is not
optional.

Empirical-Bayes constants, by the beta-binomial method of moments:

| course | 基準勝率 | k（縮約） | 信号割合 |
|---|---|---|---|
| 1 | 53.43% | 7.7 | 68.0% |
| 2 | 14.82% | 27.0 | 39.8% |
| 3 | 12.77% | 28.0 | 38.8% |
| 4 | 11.27% | 31.0 | 35.9% |
| 5 | 6.05% | 45.6 | 27.4% |
| 6 | 2.03% | 48.0 | 26.2% |

**Shrinkage barely moves a correlation** (one period → next: raw 0.6760
vs shrunk 0.6717 at course 1; it only wins at courses 5-6, +0.0199 at
course 6). That is expected -- correlation is scale-free and the start
counts cluster tightly around 17, so shrinkage is nearly affine here. It
is still required for the *level*: an unshrunk 0/5 hands a probability
model a literal zero.

**The finding that matters.** Regressing the shrunk per-course rate on
the racer's own 全国勝率 and testing whether the *residual* persists to
the next period:

| course | 全国勝率のみ r | コース残差の持続 r |
|---|---|---|
| 1 | 0.7004 | **0.3072** |
| 2 | 0.5246 | 0.1493 |
| 3 | 0.5157 | 0.1479 |
| 4 | 0.4319 | 0.2031 |
| 5 | 0.3930 | 0.1450 |
| 6 | 0.2779 | 0.1648 |

n ≈ 34,000 per course. **Per-course ability survives removing overall
skill, at 0.15-0.31.** "イン屋" is a real and measurable thing, and the
B-file's 全国勝率 does not encode it. Whether the market has it priced
is a separate question -- the fan file is public -- and untested.

## レース単位の集計値：合計・平均は効かない、配置が効く (2026-08-03)

634,942 races, 2015 onward, six-lane finished races, favourite = 2連単
`popularity_rank = 1`.

**枠番の合計は6艇カードでは常に21、平均は常に3.5** -- no information, so
the lane axis was replaced with `inside_edge` = lane 1's 全国勝率 minus
the mean of the other five. 選手勝率の合計 and 平均 differ only by a
factor of 6 and are reported as the sum.

| 指標 | 分位 | 的中率 | 回収率 |
|---|---|---|---|
| 選手勝率の合計 | 低/中/高 | 23.91 / 22.58 / 24.82% | 0.7532 / 0.7383 / 0.7540 |
| モーター2連率の合計 | 低/中/高 | 23.96 / 23.62 / 23.72% | 0.7494 / 0.7457 / 0.7504 |
| レース内の選手sd | 小/中/大 | 23.01 / 23.45 / 24.85% | 0.7400 / 0.7442 / 0.7613 |
| **1号艇の相対的強さ** | 低/中/高 | **17.21 / 23.78 / 30.31%** | 0.7135 / 0.7566 / 0.7754 |

**Field level is inert.** The racer sum is *non-monotone* (the middle
tercile is the worst) and the motor sum spans 0.34 pt of hit rate --
consistent with the 2.4 pt motor-tercile effect already measured. Six
boats all getting better changes nothing about which one wins; it is the
zero-sum structure showing through.

**Placement is not.** Whether the strong racer sits inside moves the hit
rate 13 points, an order of magnitude more than every level metric
combined. But the return moves only 6.2 points and every cell stays far
under 1.0.

節日 reproduces (22.20% → 27.05% over 第1日..第7日; 初日 0.7243 / 中日
0.7511 / 最終日 0.7636). Crossed with `inside_edge` the two are
independent -- the 17/24/30% ordering holds inside every day group and
the day gradient holds inside every tercile. Best cell 最終日 ×
inside-high: 0.7807 ±0.0117 over 43,615 races.

## 上位選手は、拮抗した相手と組むと割高になる (2026-08-03)

Asked whether a top racer paired with weak opponents differs from one
paired with equals. 634,921 races; the top racer is the highest 全国勝率
on the card, backed at 単勝 and settled at the real payout (only the
winner's payout is needed, so the whole archive is usable).

Uncontrolled, by the gap between the top racer and the mean of the other
five (+0.93 / +1.59 / +2.43): 28.22 / 33.82 / 41.49% win, ROI 0.7652 /
0.8053 / **0.8353**.

**Controlled for the top racer's lane -- monotone in all six:**

| トップの枠 | 実力差小 | 実力差中 | 実力差大 |
|---|---|---|---|
| 1 | 68.39% / 0.9022 | 72.02% / 0.9133 | 77.20% / **0.9186** |
| 2 | 20.67% / 0.7744 | 29.73% / 0.8214 | 39.75% / 0.8288 |
| 3 | 17.98% / 0.7876 | 25.18% / 0.7922 | 34.32% / 0.8233 |
| 4 | 14.80% / 0.7561 | 21.18% / 0.7919 | 30.30% / 0.8198 |
| 5 | 9.41% / 0.7290 | 14.34% / 0.7816 | 21.99% / 0.8134 |
| 6 | 5.33% / **0.5037** | 9.58% / 0.6357 | 17.39% / 0.7420 |

The effect's *size* depends entirely on the lane: +1.6 points of return
for a lane-1 top racer, **+23.8 for a lane-6 one**. A strong racer on the
outside against weak opposition is the worst cell measured anywhere in
this project (0.5037) -- the market does not discount them nearly enough
for a 5.33% win rate.

By A1 count -- how many equals the top racer faces:

| A1人数 | n | トップ勝率 | 回収率 |
|---|---|---|---|
| 0 | 138,950 | 33.27% | **0.8432** |
| 1 | 272,391 | 37.21% | 0.7997 |
| 3 | 35,601 | 35.13% | 0.7904 |
| 6 | 39,648 | 24.22% | **0.6723** |

**An all-A1 race is the worst composition to back the top racer in.**
Restricted to a lane-1 top racer the same direction holds (A1 0-1:
0.9120, A1 2-3: 0.9189, A1 4+: 0.8849).

### なぜこれが今までと違うのか

**This is the first exception to "the hit rate moves and the return does
not."** Venue, race number, series day, and every field-level aggregate
moved the hit rate while leaving the return flat -- the signature of a
correctly-priced market. The strength *gap* moves both, monotonically,
in all six lanes.

Read the direction carefully, though: the usable information is on the
**avoid** side, not the back side. The best cell is 0.9186 and still 8
points short of break-even, while 0.5037 and 0.6723 are far *below* the
0.75 market average. And the counter-intuitive part is the one to keep:
a top racer among equals is worse value than one among weaker
opponents, because their win rate falls to 24% while the price does not
widen to match.

All of this is computed from the card, which is public, so it is not
private information -- it is a pricing error the crowd leaves in place.

## コース別成績を実装：確信度規則には毒、EV規則には薬 (2026-08-03)

`racer_period_course_stats` now has a reader. Joined on the course
actually taken (`start_exhibition_course` from 直前情報, falling back to
the lane), point-in-time against `available_at`, and empirical-Bayes
shrunk with the per-course constants measured earlier the same day.

Two per-lane columns (`course_win_shrunk`, `course_starts`) and two
race-level ones (`field_a1_count`, `field_win_rate_sd`). Deliberately
**not** added: a "gap to the field" column. A multinomial logit already
holds all six `national_win_rate` columns, so any linear combination of
them -- which that gap is -- it already has, and it gets a per-lane
coefficient so it can already express the lane-dependence the
2026-08-03 measurement found. Only shapes it cannot form are worth a
column: a count over a categorical, and a spread.

### 情報としては本物、金にはならない

Train 2023-05-01..2025-07-28 (124,835 races), test on the odds window
(11,997 races), backing the argmax lane at real 単勝 payouts:

| 変種 | log-loss | 改善 | 的中率 | 回収率 |
|---|---|---|---|---|
| カードのみ | 1.21317 | — | 56.44% | **0.9227** |
| +直前情報 | 1.19781 | +1.266% | 56.54% | **0.9227** |
| +コース別成績 | 1.20621 | **+0.574%** | 56.46% | 0.9145 |
| +両方 | 1.19493 | **+1.503%** | 56.46% | 0.9117 |

**The block carries real information** -- +0.574% alone, about 45% of
what 直前情報 gives, which is consistent with the residual persistence
(0.15-0.31) measured before building it. Combined they are *sub*-additive
(1.503% against 1.840% summed) because both read 進入.

**And it costs 0.8-1.1 points of return.** The hit rate does not move
(56.44 → 56.46), so the model picks the same winners and they pay less.
That is above the 0.002 noise floor by 4x. Fifth time this pattern has
appeared, and the first time the effect is *negative* rather than flat.

### ところが EV 規則では逆に効く

Same features through `evaluate_p2` (both blocks on):

| rule | thresh | bets | hit | ROI | 上位10除く |
|---|---|---|---|---|---|
| confidence | 0.00 | 11,049 | 56.54% | 0.9118 | 0.9041 |
| confidence | 0.80 | 772 | 83.29% | 0.9412 | 0.9240 |
| ev_best | 1.20 | 5,191 | 18.42% | 1.3720 | 1.1362 |
| ev_best | 1.50 | 2,819 | 15.01% | **1.6879** | **1.2541** |
| ev_all | 1.20 | 7,609 | 17.05% | 1.3694 | 1.1962 |
| ev_all | 1.50 | 3,890 | 14.34% | 1.6793 | **1.3499** |

Against the card-only run earlier the same day, `ev_best` at threshold
1.5 goes **1.5025 → 1.6879**, and the tail-trimmed figure -- the one that
survived scrutiny -- goes from 1.1849/1.3984 (other lanes / lane 1) to
**1.2541** overall.

**The two rules move in opposite directions on the same features, and
that is coherent rather than contradictory.** Confidence selection asks
"who wins" and a better answer just walks you toward shorter prices --
the mechanism this project has measured five times. EV selection asks
"who is underpriced", and a better probability makes that comparison
sharper. Sharper probabilities are worth nothing to the first question
and something to the second.

### まだ戦略ではない

Unchanged, and it is the whole gap: these are **closing** odds nobody can
bet at, one 80-day window that is the only one with odds so there is no
out-of-sample, and thresholds chosen after looking. The earlier
decomposition also showed most of the durable part is the lane-1 effect
rather than a second independent source of edge; that has not been
re-decomposed with the new features.

What it does establish is narrower and still useful: **feature work is
not dead, it was being scored against the wrong rule.** Every previous
"this feature is priced in" verdict was reached by measuring it with
confidence selection.

### 再分解：新特徴量は1号艇効果の精緻化ではなかった (2026-08-03)

The open question from the previous entry. The card-only decomposition
had found the EV rule's durable part was lane 1 alone -- the other five
fell to 0.9812 once the ten largest payouts were removed, i.e. below
break-even. Re-run with both blocks:

| EV≥1.0, 上位10配当を除く | カードのみ | +両ブロック |
|---|---|---|
| 1号艇 | 1.1226 | 1.1496 |
| **2-6号艇** | **0.9812** | **1.0648** |
| 1号艇（30倍以下） | 1.1205 | 1.1474 |
| **2-6号艇（30倍以下）** | 1.0131 | **1.1048** |

| EV≥1.2, 上位10除く | カードのみ | +両ブロック |
|---|---|---|
| 1号艇 | 1.2562 | 1.3001 |
| 2-6号艇 | 1.0615 | **1.1531** |
| 2-6号艇（30倍以下） | 1.0781 | **1.2091** |

| EV≥1.5, 上位10除く | カードのみ | +両ブロック |
|---|---|---|
| 1号艇 | 1.3597 | **1.5279** |
| 2-6号艇 | 1.1820 | **1.2531** |
| 2-6号艇（30倍以下） | 1.2274 | **1.3626** |

**The outside lanes clear break-even for the first time.** And they do it
*more* strongly under the 30x odds cap (1.1048 against 1.0648 uncapped
at EV≥1.0), so it is not the long-shot tail carrying it -- the opposite
of what the raw untrimmed figures did in the previous session.

**Fewer bets, more hits.** At EV≥1.0 the outside-lane selections fall
10,751 → 9,729 (−9.5%) while hits rise 1,004 → 1,059 (+5.5%). Betting
less and connecting more is what a genuinely sharper probability looks
like, as opposed to a threshold that merely moved.

**The mechanism is consistent with where the feature should help.** Lane
1 almost always runs course 1, so its per-course record carries little
that the lane already implies; lanes 4-6 are where 進入変更 happens and
where per-course ability varies most between racers -- the residual
persistence measured earlier is 0.2031 at course 4, higher than courses
2, 3 and 5. The effect landed on the outside, which is where the feature
had something to say.

So the previous conclusion is superseded: **the durable part is no
longer lane 1 alone.** Per-course ability and 直前情報 together pick up a
distortion the market leaves on the outside lanes that card features
could not reach.

Unchanged and still decisive: these are **closing** odds nobody can bet
at, the 80-day window is the only one with odds so there is no
out-of-sample test, and the thresholds and the odds cap were chosen
after seeing the data. The trimmed figure is also **not an operational
expectation** -- it is the return after deleting the ten best results,
constructed to test tail-dependence, not to be earned.
