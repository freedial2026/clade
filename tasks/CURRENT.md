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

## Next, in order

1. Confirm the full B/K load finished with `failed=0`; investigate any
   entries in the failure list.
2. `python -m boat_prediction.db.load_odds_archive` on the host — the
   odds pages are already transferred but nothing has loaded them yet.
3. Re-run P0-P2 against the real loaded data (this is the step the
   whole backlog has been waiting on; everything before it was
   validated on synthetic fixtures).
4. `motors`/`boats` tables: still on hold until a source with real
   service periods is found.
5. JMA weather: new table + migration + a batch driver over the
   already-transferred `jma/` archive.
6. fan-file: fixed-width record parser (doesn't exist yet) + a new
   point-in-time racer-stats table + loader.

Before any real use: re-run P0-P2 against the real data, confirm the P2
forward test is genuinely stable, then seek separate approval for any
promotion beyond paper operation. See tasks/HANDOFF.md.
