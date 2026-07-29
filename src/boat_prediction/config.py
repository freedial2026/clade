from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str | None

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ
    return Settings(
        app_env=source.get("APP_ENV", "local"),
        database_url=source.get("DATABASE_URL"),
    )
