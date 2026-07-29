"""Minimal FastAPI service.

Implements the health/readiness endpoints from
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§17.1 (`GET /health`, `GET /ready`). Requires the `app` extra
(`pip install -e ".[app]"` for fastapi/uvicorn/sqlalchemy).

`/health` is a liveness check (process is up), no dependency checks.
`/ready` additionally verifies the configured database is reachable, so
a reverse proxy/load balancer can use it before routing real traffic.
The failure reason is deliberately generic — the underlying exception
is never echoed back, since a DB error message can contain the
connection string (including its password).
"""

from __future__ import annotations

from fastapi import FastAPI, Response

from . import __version__
from .config import load_settings

app = FastAPI(title="boat-prediction")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/ready")
def ready(response: Response) -> dict:
    settings = load_settings()
    if settings.database_url is None:
        response.status_code = 503
        return {"status": "not_ready", "reason": "DATABASE_URL not configured"}

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.database_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:  # noqa: BLE001 -- a readiness probe must not crash on any driver error
        response.status_code = 503
        return {"status": "not_ready", "reason": "database unreachable"}

    return {"status": "ok", "version": __version__}
