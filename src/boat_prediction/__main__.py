from __future__ import annotations

from . import __version__
from .config import load_settings


def main() -> int:
    settings = load_settings()
    print(f"boat-prediction {__version__}")
    print(f"app_env={settings.app_env}")
    print(f"database_url_configured={settings.database_url is not None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
