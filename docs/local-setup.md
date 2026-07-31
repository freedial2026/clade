# Local environment setup

## Prerequisites

- Python 3.12+
- No database or external service is required to run the test suite —
  everything in `src/boat_prediction/` is validated against synthetic
  fixtures only (see `tasks/HANDOFF.md` for what's real vs. synthetic).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -e .
```

Optional extras, install only when a task requires them:

```bash
pip install -e ".[app]"   # FastAPI/SQLAlchemy/Alembic, for future API/DB work
pip install -e ".[ml]"    # scikit-learn/LightGBM/CatBoost, for P1-T004-style comparisons
pip install -e ".[dev]"   # ruff, used by `make quality` / `python scripts/quality_gate.py`
```

## Environment variables

Copy `.env.example` to `.env` and adjust values locally. `.env` is
git-ignored; never commit it. Variables read by `boat_prediction.config`:

- `APP_ENV` — defaults to `local` when unset.
- `DATABASE_URL` — unset by default; only required once a task connects to a
  real database (PostgreSQL per `docs/PROJECT_PROFILE.md`).

## Local PostgreSQL (for `src/boat_prediction/db/`)

`docker-compose.yml` at the repo root brings up a disposable local
Postgres 17 with fixed, non-secret dev credentials (published on
`127.0.0.1` only) — needed for loading the B-file/K-file archives into
the `races`/`race_entries`/... schema:

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg2://boat:boat@localhost:5433/boat_prediction
alembic upgrade head
```

Notes:

- Port `5433` (not the Postgres default `5432`) so this never collides
  with a Postgres you already run locally.
- `alembic -x dialect=postgresql upgrade head --sql` renders the
  PostgreSQL DDL without any running database — useful for reviewing a
  migration before applying it, or in an environment with no Docker.
- The database is rebuildable from scratch: every row it holds is
  derived from the raw archives under `data/raw/boatrace/` (gitignored,
  not committed) via `src/boat_prediction/db/loader.py`, so
  `docker compose down -v` is always safe to run locally.
- `src/boat_prediction/db/models.py`'s module docstring documents where
  this schema deliberately deviates from
  `docs/domain/.../implementation_guide.md` §7.2 and why (e.g. no
  `motors`/`boats` tables yet — no source publishes their service
  periods).

## Validation commands

```bash
make validate   # template structure check
make test       # unit tests (304+ as of the last full run)
make quality    # validate + compile check + ruff (if `[dev]` installed) + tests
python -m boat_prediction   # skeleton CLI: prints version and env summary
```

## Notes

- No production or paid resource is created by running any of the above.
- Application code lives under `src/boat_prediction/` (P0 data audit, P1
  first-place probability, P2 market/paper-simulation, P3 entry/exacta —
  see `tasks/P0-T001.md` through `tasks/P3-T004.md`).
- **Before any real/production use**: everything is built and tested
  against synthetic data only. See `tasks/HANDOFF.md` for the required
  steps (real data acquisition, re-running P0-P2, confirming a stable
  forward test) before this code is used with real races or money.
