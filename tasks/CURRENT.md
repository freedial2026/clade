# Current task

- Active task: deploy the schema + archive load to the 192.168.11.21
  Debian host (the project's actual runtime target; this Windows PC is
  the development/preparation machine only).
- Status: B/K-file 21-year load **complete** on 192.168.11.21
  (2026-07-31 20:24, `failed=5`, all five since re-loaded clean), fixes
  deployed, and `race_meetings` rebuilt. Next is the odds archive load.
- Last handoff: first application of this schema to a **live
  PostgreSQL** (previously only in-memory SQLite). See the
  "192.168.11.21 deployment" entry at the end of tasks/HANDOFF.md.

## Runtime target: 192.168.11.21 (`boat.internal`)

Verified state as of 2026-07-31:

- Debian 13 (trixie), user `ash`, passwordless sudo, 815 GB free.
- PostgreSQL 17.10, listening on `127.0.0.1:5432` only (not exposed
  to the LAN). Docker is **not** installed and is not needed — Postgres
  runs natively, so the repo's `docker-compose.yml` is for local
  development on a workstation only.
- The host is shared: it also serves LAN DNS (it is what resolves
  `boat.internal` to itself), MySQL/MariaDB, and web on 80/443.
  Treat this project as one tenant among several.
- Checkout lives at `~ash/boat-prediction` with its own `.venv`
  (Python 3.13.5). `/opt/boat-prediction` also exists but is not
  readable by `ash` and is not what we deploy to.

### Database authentication

Peer authentication over the Unix socket — **no password is stored
anywhere**:

```
DATABASE_URL=postgresql+psycopg2://@/boat_prediction?host=/var/run/postgresql
```

Role `ash` owns the `boat_prediction` database and its `public` schema.
A pre-existing `boat_prediction` role remains but is unused (its
password never matched `.env`); dropping it is safe cleanup, not yet
done. The previous password-bearing `.env` was backed up to
`.env.bak.20260731112151` (mode 600) and should be deleted once the
peer-auth setup is confirmed stable.

### Required extras on the host

`pip install -e ".[app,official-data]"` — `psycopg2-binary` for the DB
and `pylhasa` for `.lzh` extraction. Missing either produces a
`ModuleNotFoundError` only at load time, not at migration time.

## Done on 192.168.11.21

- `alembic upgrade head` → revision `3997a65d30a7`, 11 tables created.
- `load_archive --dry-run` for 2005-01: 62 files, **0 failures**.
- `load_archive` (real) for 2005-01: row counts match `LoadStats`
  exactly (races 4,793 / entries 28,758 / results 4,782 /
  result_entries 28,692 / payouts 43,752 / racers 1,431 / venues 24).
- Raw archive transferred from the Windows PC: 32,711 files / 1.2 GB
  under `data/raw/boatrace/` (B, K, odds, fan, jma, venue), covering
  `200501`–`202607`.
- Full 21-year B/K load launched under `nohup`, logging to
  `~ash/boat-prediction/logs/load_archive_full.log`. Estimated ~11 h at
  the measured 2 m 35 s per month. Resumable and idempotent via the
  ledger at `data/manifests/db_load_ledger.json`, so an interrupted run
  can simply be re-issued.

## JMA weather: schema + loader done locally (2026-07-31), not yet applied to .21

Built while the B/K load ran in the background, entirely on the Windows
PC against SQLite/in-memory fixtures — no connection to .21 was needed
for this:

- `db/models.py`: new `WeatherObservation` table (`weather_observations`),
  keyed by `(venue_id, weather_date)`. `loader.weather_available_at`
  reuses the same day-after-midnight-JST conservative bound as
  `results_available_at`, for the same reason (the source states no
  per-observation publish time).
- `loader.load_weather_month` — idempotent per venue-month, mirrors
  `load_b_file_day`'s replace-then-reinsert pattern.
- `db/load_jma_archive.py` — resumable ledger-driven CLI, iterating
  `(year, month) x venue` rather than by date since
  `jma_weather_source.fetch_all`'s on-disk layout is one file per
  venue per month.
- Alembic revision `9e24c5ea64e2` (on top of `3997a65d30a7`), generated
  against a scratch SQLite baseline (no live DB was available on this
  PC) and cross-checked with `alembic -x dialect=postgresql ... --sql`.
  Forward/rollback both verified.
- 17 new tests (`test_db_models.py`, `test_db_loader.py`,
  `test_load_jma_archive.py`); 512/512 total, quality gate green.

Not yet done: `alembic upgrade head` and `load_jma_archive` have not
run against .21 — do that after the B/K load finishes, so it isn't
competing with it for the same PostgreSQL instance.

**2026-07-31, mid-load: 2 failures appeared in the B/K run** (log line
`2008-08: ... failed=2`, out of 2,554 files loaded so far). The run
does not stop on a per-file failure by design (see `load_archive.py`'s
docstring) and continues.

**Cause found and fixed the same day, on this PC, without touching .21.**
Exactly four archives in the 2005-2026 range hold the *same day* twice
under names differing only in case (`K090406.TXT` + `k090406.txt`): a
re-issue, not two days. `official_source.extract_k_file_text` required
exactly one member and raised on them, losing four days of results:
`k080706`, `k080713` (both 2008-07, and the only two before 2008-08 —
they are the reported `failed=2`), `k090406`, `k090708`. **The full run
should therefore end with `failed=4`, not `failed=0`.**

`_select_archive_member` now resolves a same-name duplicate by
preferring the larger member, which in all four cases is the modern
layout matching the neighbouring days; archives holding genuinely
different files are still rejected. In `k080706` venue 05 race 9 the
older copy yields `exhibition_time=0.0` for a 欠場 (K0) row where the
modern one correctly yields `None`, so the choice is not cosmetic.
Verified by re-extracting and parsing every archive: **K 7,863/7,863
and B 7,862/7,862, 0 failures.**

After redeploying to .21, re-running `load_archive` for those four days
is enough — the ledger makes it idempotent.

**A 5th failure exists, and it is a different bug.** The host log shows
`failed` reaching 5 at 2011-05, one more than the four duplicate-member
archives. Reproduced locally against SQLite (2011-01..05; the month
alone does not trigger it, so it is state-dependent):

```
K/201104/k110424.lzh: venue 01 race 1 on 2011-04-24 has no entries,
no payouts and no cancellation flag, which indicates a parse defect
```

Venue 01's section that day carries the 1R-12R payout table with every
row blank, no race detail block at all, and **no 中止 marker** — a day
that did not run, written without saying so. The parser builds 12 empty
races from the payout table rows and `load_k_file_day`'s parse-defect
guard rejects the file.

`load_k_file_day` now treats a venue-day whose races are *all* empty as
cancelled, while a single empty race inside an otherwise populated day
still raises — that shape really would be a parse defect. Exactly one
venue-day in the 97,079 in the archive has this shape, so the tolerance
is as narrow as the evidence.

**So the full 21-year load ends with `failed=5`, and all five are now
explained and fixed.**

## fan-file parser done locally (2026-07-31), table/loader not yet built

`src/boat_prediction/fan_stats_parser.py` (new): parses the fixed-width
モーターボートファン手帳 racer records `fan_file_source.py` downloads.
Layout came from the official spec page
(`boatrace.jp/owpc/pc/extra/data/layout.html`) and was cross-validated
against all 1,644 real records in `fan2604.lzh` — computed field-length
total matches the observed record length exactly, and every field lands
on domain-plausible values (year/period echo the file's own period on
every row, class is always A1/A2/B1/B2, ability index clusters around
50.00, course-1 stats are systematically stronger than other courses,
period_from/to match the file's stated window). Full 2014-2026 archive
swept: 40,204 records, 0 parse failures.

**Finding: the file format changed in 2014.** Every file from
`fan1404.lzh` onward is a 403-character record; every file from
`fan0110.lzh` through `fan1310.lzh` (2001-2013) is a different,
400-character record not yet reverse-engineered. The parser raises
`FanStatsParseError` with a clear message on that legacy length rather
than guessing — 2001-2013 fan-file data is currently unparseable, not
silently wrong.

Two fields share a 4-character width but different decimal scales,
found empirically: `win_rate` (a 0-9-ish weighted score) is `raw/100`;
`place_rate` (a genuine 0-100% stat) is `raw/10`. See the module
docstring for the full evidence trail.

14 new tests (`test_fan_stats_parser.py`), fixtures built
programmatically from `_FIELD_LAYOUT` rather than real downloaded text.
526/526 total, quality gate green.

Not yet done: no DB table exists for this data yet, and none of
`fan_file_source.py`/`fan_stats_parser.py` is wired into any loader.
Deliberately out of scope for this pass — `ParsedFanRecord` alone is
~30 scalar fields plus 6 courses x (4 summary + 14 position-breakdown)
fields, and deciding how much of that to materialize as DB columns
(most of the per-course finish/irregular-count breakdown is unlikely to
ever be used as a feature) is a real design call, not mechanical work
like the JMA weather table was.

## 直前情報 IS archived — earlier conclusion was wrong (2026-07-31)

`tasks/HANDOFF.md`, `db/models.py` and `jma_weather_source.py` all
recorded that BOATRACE's 直前情報 (exhibition time, tilt, parts
replacement, start exhibition, surface weather) "is not archived
anywhere officially, so it would need to be captured live going
forward". **That is false.** `beforeinfo?rno=..&jcd=..&hd=..` serves
past dates: verified against venue 04 on both 2026-07-30 and
2025-07-31, returning fully populated data. The original mistake was
probing a venue on a date it did not race, which returns an empty page
shell indistinguishable from "no retention" — the same failure mode
nearly repeated during this investigation.

This is the single most valuable correction so far: exhibition time,
tilt and the start-exhibition course order are the strongest genuinely
pre-race signals available, and 進入変更 (course entry differing from
lane assignment) is observable pre-race only here — exactly what
`entry_course.py` (P3-T001) models.

`src/boat_prediction/beforeinfo_source.py` (new) fetches and parses
these pages; 23 tests. Structure was read off two real pages (one
completed historical race, one same-day race before its exhibition run).

**Leakage trap found and encoded.** The surface-weather block carries
its own label in two forms:

- `"NR時点"` — observed at race N; safe for race N+1 onward.
- `"HH:MM現在"` — race 1 only (no previous race to reference). Fetched
  from the archive this is the **day's latest** reading: on a real page,
  race 1 (deadline 11:53) reported `17:43現在`, minutes after the final
  race closed at 17:40. Using it would feed race 1 six hours of future
  weather.

`SurfaceWeather.is_safe_for_race()` returns True only for the first
form with `N < race_number`. Per-boat exhibition values carry no such
caveat — confirmed race-specific across races 1/6/12 of one venue-day.

### Retention probed (2026-07-31)

Coverage does **not** start on a clean date — it was rolled out per
venue during 2016. Racing-day/venue pairs were taken from the local
K-file archive so no probe could repeat the "asked about a venue that
did not race" mistake:

| Date | Venues with 直前情報 |
|---|---|
| 2016-05-01 … 2016-06-20 | none |
| 2016-06-26 | **2 of 13** (only venues 17, 23) |
| 2016-07-15 | 11 of 11 |
| 2016-08-01 / 08-15 / 09-14 / 12-15 / 2017-04-01 | all, every date |

So: **full coverage from 2016-07-15 at the latest**, partial through
late June 2016, nothing before. That is ~9 months *earlier* than
`odds_source.EARLIEST_RETAINED_DATE` (2017-04-01) — the odds constant
must not be reused for this source. A loader must tolerate per-venue
gaps in the June-July 2016 window rather than treating them as
failures.

### Scale problem — full backfill is not proportionate

A racing day is ~13 venues x 12 races ~= 150 pages. From 2016-07-15 to
now is ~3,670 days, so a complete backfill is **~550,000 requests**, or
about **19 days of continuous fetching** at the module's 3 s delay.
That is squarely the "large-volume access" the site's policy prohibits,
and it is not proportionate to the value.

Calibration: the existing odds archive under `data/raw/boatrace/odds/`
is only **80 days** (2025-07-29 … 2025-10-16, 11,951 files) — the full
2017-2026 odds range was never fetched either, presumably for the same
reason.

Recommended staging (not yet approved or run):

1. **Match the odds window** — 2025-07-29 … 2025-10-16, ~12,000 pages,
   ~10 h at 3 s. This is the only window where odds *and* 直前情報 would
   both exist, so it is what makes an end-to-end P2 test on real data
   possible at all. Comparable in size to the odds fetch already done.
2. **Daily incremental capture going forward** — ~150 pages/day, small
   and sustainable, and the thing that actually matters for "races run
   every day".
3. **Extend backwards for P1 only if step 1 shows the features earn
   their keep.** P1 (first-place probability) needs 直前情報 + K-file
   results but no odds, so a wider window helps there independently.

Not yet done: no archive download run; no DB table or loader. Do not
backfill into `exhibition_entries` — see deviation 5 in
`db/models.py`'s docstring.

## 節 structure measured; `race_phase.py` added (2026-07-31)

Full sweep of the K-file archive (7,863 files, 97,079 venue-days,
17,870 節) to settle how a 節 is actually shaped:

- **Essentially every 節 ends in a final.** 99.76% of 節 containing no
  cancelled or empty day carry one; the rest are 順延 artefacts of the
  sweep's own day-grouping, not missing finals.
- 節 length: 6日 54.8%, 5日 21.4%, 4日 17.8%, 7日 4.6%.
- The final is 12R 96.7% of the time, 11R 2.3%.
- **準優 is not universal**: present in 96.9% of 6-day 節 but only
  **50.7% of 4-day 節**, which use 選抜戦 instead. Series shape must not
  be assumed from 第N日 alone.
- 3 semifinals is the norm (86.1%).

`src/boat_prediction/race_phase.py` (new) classifies `races.race_class`
into trial / qualifier / semifinal / final / selection / general /
unknown. It exists because `== "優勝戦"` misses 1% of finals: they are
named after the event (王将位決定戦, 海の王者決定戦, ファイナル) or
truncated by the fixed-width label field to a bare 優 (`サントリー優`).
The traps it encodes — 準優勝戦 contains 優勝, 準優 ends in 優,
順位決定戦 (475 races) is not a final, 準々優勝戦/準優進出戦 qualify
*into* a round — all come from the 1,675 distinct labels in that sweep.

Why it matters: lanes are assigned by 点率 standing in 準優/優勝戦 and
arbitrarily in 予選, so boat 1 does not mean the same thing in both.
`is_standing_seeded()` marks that split.

Deliberately title-only: "last race of the last day" would be a far
better signal but is future knowledge for any race inside the 節 —
the same reason `RaceMeeting.meeting_end_date` is NULL by design.
Ambiguous labels return `unknown` rather than a guess (8.0% of races,
mostly venue marketing names like ドリーム戦/ランチタイム).

Not yet done: nothing consumes `race_phase` yet. It is a pure function
over an existing column, so wiring it into features needs no migration.

## RaceMeeting key derivation is wrong for 3% of 節 (2026-07-31)

Investigated on the **B-file** archive, which is what `db/loader.py`
actually reads (7,862 files, 97,116 venue-days, 17,852 節).

`_get_or_create_meeting` derives `meeting_start_date = race_date -
(series_day - 1)` ([loader.py:268](../src/boat_prediction/db/loader.py)).
That assumes `series_day` advances by exactly one per calendar day.
**It does not.** The B-file is a race card published before the day, so
a 順延 (postponed) day still ships a full card, and the day counter
repeats — or skips, or goes backwards:

```
venue 09, クイーンカップ 2005-09     venue 24, 2005-09
  09-06 第1日   -> key 09-06          09-04 第4日 -> key 09-01
  09-07 第1日   -> key 09-07          09-05 第5日 -> key 09-01
  09-08 第3日   -> key 09-06          09-06 第5日 -> key 09-02
  09-09 第3日   -> key 09-07          09-07 第5日 -> key 09-03
  09-10 第4日   -> key 09-07
```

Measured effect: **541 節 (3.03%) split across 2-4 `RaceMeeting` rows**,
producing 18,450 rows for 17,852 節. Every split traces to a repeated,
skipped or non-monotonic `series_day`. No case was found where two
genuinely different 節 collapse into one row, so the failure mode is
fragmentation only, never conflation.

Why it matters: any "earlier in this 節" feature (motor/boat drawn for
the series, racer form within it) silently truncates at the 順延
boundary for those 節. Nothing consumes `meeting_id` yet, so the defect
is latent — but it must be fixed before P0 features use it.

**Fixed locally (2026-07-31); not yet applied to .21.**
`db/meeting_resolution.py` (new) holds the rule, shared by the loader
and the repair script so they cannot drift: reuse the venue's most
recent meeting when `series_day != 1` and the gap since its last loaded
race day is <= 3 days; otherwise open a new one keyed
`race_date - (series_day - 1)`, moved forward past any start date the
venue already uses. Only already-loaded days are consulted, so no future
knowledge enters. The archive replay reproduced all 17,852 節 with 0
collisions.

Two accepted caveats: it is order-dependent (loading a month in
isolation re-opens a meeting mid-節, as today), and the 0.21% of 節
whose 第1日 is missing attach to the previous meeting.

`db/rebuild_meetings.py` (new) repairs already-loaded data without
re-parsing anything — `races` already carries `venue_id`, `race_date`
and `series_day`, and `race_meetings` is referenced only by
`races.meeting_id`. Dry-run by default; `--apply` snapshots
`(races.id, races.meeting_id)` into `races_meeting_id_backup` and works
in one transaction.

Rehearsed on a local SQLite DB built from the real 2005-08..10 files,
loaded through the *old* rule to reproduce the host's shape: 226
meetings -> 220, 6 節 split, 177 races re-pointed, dry-run figures
identical to `--apply`, 0 empty meetings left, re-run reports nothing
to do.

Applying it to .21 is still a data change on the host and needs separate
approval. Do it only when no load is in flight.

## 21-year load and repair completed on .21 (2026-07-31)

The load finished at 20:24 JST after 8 h 51 m:

```
done: loaded_files=15658 skipped_missing=39 skipped_already_loaded=62 failed=5
LoadStats(races=1158622 entries=6951732 results=1146247
          result_entries=6877482 payouts=11249134
          venues_data_pending=152 venues_cancelled=2 races_cancelled=11775)
```

`failed=5` exactly as predicted, and the five files were the four
duplicate-member archives plus `k110424`. Re-running `load_archive` for
those five days after deploying the fixes gave `failed=0` on every one,
with row counts matching the local verification (180/204/144/96 races;
`k110424` recorded venue 01's 12 races as cancelled).

**The host is not a git deployment** — it was set up by copying files, so
`db/`, the parsers, `alembic/` and `alembic.ini` were all *untracked* on
an old checkout (`f85ce09`). `git reset --hard` is refused by
`.claude/hooks/command_guard.py`, so the reconciliation used
`git stash push -u` (twice, to cover `tests/`) followed by
`git merge --ff-only`. The stashes are still there as `stash@{0}` and
`stash@{1}`. The host now tracks `origin/main` and future deployments
are a plain `git pull`. 597 tests pass there.

Rebuild result, matching the archive replay exactly:

```
applied: venue_days=96980 meetings_before=18450 meetings_after=17852
         meetings_deleted=598 races_repointed=17976 series_split_before=541
```

Verified afterwards: 17,852 meetings, 0 empty meetings, 0 meeting
spanning more than 10 days, 216 races still without a meeting (the
card-less days, unchanged), and the backup table holding all 1,163,631
rows. Of the three known split 節, venue 03 (2007-07-11..18) and venue
24 (2005-09-01..07) are now single meetings; venue 09's 2005-09 節
remains two, because its B-file carries 第1日 twice and 第1日 always
opens a new meeting — the deliberate trade-off that keeps back-to-back
節 apart.

Rollback material, all still on the host:

- `~/boat-prediction-backup-20260731.tar.gz` (670K, pre-deploy tree)
- `~/backup_race_meetings_20260731.sql` (4.0M, `pg_dump -t race_meetings`)
- `races_meeting_id_backup` table (1,163,631 rows) — drop it once the
  rebuild is confirmed good, and note that `rebuild_meetings --apply`
  refuses to run again while it exists.

## Daily capture running on .21 since 2026-07-31

The archived odds are one observation per race stamped with the
deadline, so nothing in them supports a decision made *before* betting
closes. That cannot be fixed retroactively — the pre-deadline series was
never published — so it is being built forward from here.

Three cron jobs, all through `scripts/cron_job.sh` (sources `.env`, uses
the venv, logs to `logs/cron-YYYYMM.log`, rotating monthly):

| JST | Job | Why then |
|---|---|---|
| 06:30 | `ingest_daily card` | Before the day's first deadline; the earliest in the archive is 08:32. Without today's card there is no `scheduled_deadline_at` and capture does nothing. |
| every 2 min, 08-21 | `capture_odds` | Captures each race 10 and 2 minutes before its deadline. A run with nothing due makes no request. |
| 02:00 | `ingest_daily results` | After the last race settles. Defaults to yesterday. |

Volume: ~300 requests per racing day, 3 s apart, never parallel — the
same order as the archive fetch already done, and far below the site's
large-volume threshold. Adding lead times multiplies it.

Idempotency has no state outside the database: a capture round counts as
done if a snapshot exists at or after that round's window opens, so
overlapping runs, retries and restarts cannot double-record.

`odds_snapshots.is_closing` now distinguishes the two sources — `true`
for the 213,729 archived closing rows, `false` for live pre-deadline
readings, whose `available_at` is genuinely earlier than the deadline.

Watch for: the log grows one file per month with no pruning, and a day
with no racing exits 1 on the 404 (visible in the log, not silent).

## P0 re-run against real data: 100.00, train_or_predict (2026-07-31)

`db/quality_audit.py` (new) measures the five axes `quality.py` scores.
Nothing measured them before — P0 was validated on fixtures where the
answer was known in advance. 19 checks, each reporting examined and
defective counts so a score can be pointed at a query. An axis score is
its weight times the **mean** pass rate of its checks, not a row-weighted
average: a check over 7 M entry rows would otherwise drown out one over
17,860 meetings.

First run scored 99.94 and flagged three things. Two were the data
telling the truth; one was a real defect.

**Real:** 2,309 odds snapshots stored `0.00`. The page renders `0.0` for
a boat with no quote and the parser took it literally. Odds include the
stake, so 1.00 is the floor — and 1.00 is exactly the smallest real value
among the 213,729 rows. A stored 0.0 is worse than a wrong number:
market normalisation divides by it, so a missing quote becomes an
*infinite* implied probability. Values below the floor now parse as
absent; re-loading the archive left 211,420 snapshots and 2,672 skipped
missing values.

**Not defects:** 16 races have two boats on `finish_position=1` — 同着,
verified on a real row (lanes 1 and 2, both status `01`). 132 races end
with every boat carrying a status code (mostly `F`) and none a placing —
a void race. The check demanded "exactly one winner" and called both
wrong; it now asks a question with a right answer (a race that produced
placings must have a first) and skips races that produced none.

**Missing data, since fixed:** 168 races on 2021-12-21 had no card and
no deadline — that day's B-file was absent from the transferred archive.
It was still downloadable; `ingest_daily card --date 2021-12-21` loaded
168 races and 1,008 entries.

Second run: **100.00 / 100, every check passing.** The point_in_time
axis in particular is clean across 6,983,370 entry rows — no card
feature is available after its deadline.

## P1 re-run against real data (2026-07-31)

`db/dataset.py` and `db/evaluate_p1.py` (new) turn database rows into
`(X, y, dates)` and run the existing walk-forward machinery over them.
Window 2023-01-01..2026-07-29, expanding train, one-month test,
`min_train_months=12` → 31 folds, **198,264 races**.

Dataset losses are negligible and all explained: 32 of 198,296 races
dropped, every one a dead heat or a void race. **Zero** dropped for a
missing feature and **zero** for a feature available after its deadline.

| model | mean log-loss | vs uniform |
|---|---|---|
| `uniform` (1/6) | 1.79176 | — (ln 6 = 1.79176, so the harness is right) |
| `lane_prior` | 1.36814 | +23.64% |
| `logistic_cards` | **1.22502** | **+31.63%** |

`logistic_cards` beats `lane_prior` by 10.46% and does so in **31 of 31
folds** — consistent enough not to be noise. So the card features carry
real information beyond "inside lanes win", which is the question P1
existed to answer.

Two things worth keeping in mind about that number:

- It is log-loss, not money. Nothing here says the edge survives the
  market; that is P2's question and the archived odds cannot answer it
  (they are stamped at the deadline).
- `logistic_cards` is a linear model on 54 raw card columns with no
  calibration step yet. `calibration.py` exists and is unused by this
  runner.

`_AlignedProba` in the runner fixes a hazard that would not have raised
an error: log-loss reads each row by the true class's position in the
class list, while scikit-learn orders its columns by the classes seen in
training, so a fold whose training window contained no win by some lane
would have scored every later lane against the wrong probability.

scikit-learn 1.9.0 / numpy 2.5.1 were installed into the host venv (the
`ml` extra was already declared in `pyproject.toml`). The run takes
~4 min for 31 folds.

## P1 improved with within-meeting form + phase; calibration tried and did not help (2026-08-01)

Answering "should the first half of a 節 be treated as validation
evidence, with the final etc. as the decision point?" (raised after the
first P1 result): built the features to test it rather than guessing.

**db/dataset.py** gained two things:

- Within-meeting form: the motor/boat are drawn once per 節 and a racer
  keeps them all series, so how that racer has placed *earlier in this
  meeting* is direct evidence about the actual equipment -- something
  season-long B-file rates can only reflect with a lag. Tracked by
  `racer_id` (not lane, since 準優/優勝戦 reseed by standing), computed
  with one windowed SQL query per call rather than a per-race subquery.
  Shrunk toward the racer's own season win rate (weight = 3 starts,
  `MEETING_FORM_SHRINKAGE_STARTS`) so 第1日 still gets a defined feature.
  A DNF scores as the worst outcome (0.0), not missing.
- `race_phase.is_standing_seeded()`, appended once per race (not once
  per lane) so a multinomial model can learn its own per-lane
  coefficient for it -- letting the model discover "lane 1 matters more
  in a seeded round" itself rather than that being hand-coded.

Re-running the same 2023-01-01..2026-07-29 window (198,264 races, 31
folds): `logistic_cards` mean log-loss **1.22502 → 1.21134** (+1.12%),
now **+11.46%** over `lane_prior` (was +10.46%), still winning **31/31**
folds.

**db/evaluate_phase.py** (new) answers the actual question asked --
where does the edge live:

| phase | n | logistic_cards vs lane_prior |
|---|---|---|
| trial (予選) | 68,427 | **-12.47%** |
| qualifier | 392 | -12.25% |
| semifinal (準優勝戦) | 5,985 | **-8.77%** |
| final (優勝戦) | 2,186 | **-5.68%** |
| selection | 14,110 | -8.15% |
| general | 29,839 | -10.56% |
| unknown | 21,945 | -12.47% |

**The edge is smallest in exactly the standing-seeded rounds** (final,
then semifinal) and largest in 予選 -- consistent with the selection-bias
hypothesis raised when this was discussed: racers who reach 準優/優勝戦
are pre-filtered to be strong, so there is less variance left for card
features to explain. This is the opposite of "save the model for the
final" -- if anything, the trial races are where this feature set earns
its keep. `final`'s CI ([1.0128, 1.1074] for `logistic_cards`) is
noticeably wider than `trial`'s given ~70 test races/fold, so treat the
final's exact number as directional rather than precise, but the
same-direction drop across *both* seeded rounds (final and semifinal)
against both unseeded rounds is not a single sparse-group artifact.

**db/evaluate_calibration.py** (new): `BinnedCalibrator` fit on
predictions the classifier never trained on (a held-out month between
`core_train` and `test`, not the model's own training rows -- fitting on
in-sample predictions would calibrate away overfitting rather than
measure it). Result over 30 folds: mean log-loss **1.21112 → 1.21452**
(worse), mean ECE **0.01517 → 0.01817** (worse).

**Calibration did not help, and the reason is informative rather than a
bug**: raw ECE is already ~0.015-0.02 -- the multinomial logistic
regression's own confidence is close to calibrated out of the box (a
well-regularized model trained directly on log-loss often is). A 10-bin
histogram recalibrator fit on one month (~4,000-5,000 races, ~400-500
per bin) adds sampling noise to an already-small miscalibration rather
than correcting a real one. Not applied to production; worth revisiting
only if a future model (e.g. a tree ensemble) shows materially worse raw
ECE, or with more calib_valid data / fewer bins / isotonic regression if
this matters later.

## Next, in order

1. ~~Full B/K load, fix deployment, five-day re-load, meeting
   rebuild.~~ **Done 2026-07-31** — see "21-year load and repair
   completed" below.
2. `python -m boat_prediction.db.load_odds_archive` on the host — the
   odds pages are already transferred but nothing has loaded them yet.
3. `alembic upgrade head` (picks up `9e24c5ea64e2`) then
   `python -m boat_prediction.db.load_jma_archive` on the host — the
   jma/ pages are already transferred but nothing has loaded them yet.
4. **P0 and P1 are done** on real data, including the phase breakdown
   and a (negative-result) calibration check (see above). P2 is next
   and is the one with a data problem, not a code problem: the archived
   odds are stamped at the deadline, so they can score calibration
   against the market but cannot support a decision made before betting
   closed. The pre-deadline series started accumulating on 2026-08-01
   via cron. Until it is long enough, a P2 run can only be a
   market-comparison study, not a paper simulation anyone should
   believe.
5. `motors`/`boats` tables: still on hold until a source with real
   service periods is found.
6. Decide and build the fan-file DB table + loader (parser is done;
   see above for the scope-of-columns decision that's still open).

Before any real use: re-run P0-P2 against the real data, confirm the P2
forward test is genuinely stable, then seek separate approval for any
promotion beyond paper operation. See tasks/HANDOFF.md.
