# Current task

- Active task: deploy the schema + archive load to the 192.168.11.21
  Debian host (the project's actual runtime target; this Windows PC is
  the development/preparation machine only).
- Status: in progress — B/K-file 21-year load running on 192.168.11.21.
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
docstring) and continues. Not yet investigated — do this as step 1
below once the run finishes and the full failure list is in the log.

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

## Next, in order

1. Confirm the full B/K load finished with `failed=0`, and investigate
   the 2 failures already seen in the 2008-08 batch (see above).
2. `python -m boat_prediction.db.load_odds_archive` on the host — the
   odds pages are already transferred but nothing has loaded them yet.
3. `alembic upgrade head` (picks up `9e24c5ea64e2`) then
   `python -m boat_prediction.db.load_jma_archive` on the host — the
   jma/ pages are already transferred but nothing has loaded them yet.
4. Re-run P0-P2 against the real loaded data (this is the step the
   whole backlog has been waiting on; everything before it was
   validated on synthetic fixtures).
5. `motors`/`boats` tables: still on hold until a source with real
   service periods is found.
6. Decide and build the fan-file DB table + loader (parser is done;
   see above for the scope-of-columns decision that's still open).

Before any real use: re-run P0-P2 against the real data, confirm the P2
forward test is genuinely stable, then seek separate approval for any
promotion beyond paper operation. See tasks/HANDOFF.md.
