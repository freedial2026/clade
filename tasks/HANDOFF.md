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
