"""Official data source: BOATRACE results (K-file) download and extraction.

Source confirmed by manual navigation of the official download index
(https://www1.mbrace.or.jp/od2/K/dindex.html -> dmenu.html -> mday.html),
operated by the general incorporated foundation BOATRACE Promotion
Association. robots.txt on this host has no disallow rules. Per
docs/PROJECT_PROFILE.md's source priority ("official downloadable
files" first) and the "取得禁止事項" in
docs/domain/.../implementation_guide.md §6.3 (no per-second polling,
respect robots.txt/site policy, no CAPTCHA bypass, no private-API
reverse engineering, no auth bypass, no load testing), this module:

- only downloads the publicly documented, unauthenticated K-file URLs
- defaults to a multi-second delay between requests in `download_month`
- does not retry aggressively or parallelize requests

Requires the `official-data` extra (`pylhasa`, for LZH extraction —
verified locally and cross-platform, no external system tool needed).
"""

from __future__ import annotations

import calendar
import time
import urllib.request
from datetime import date
from pathlib import Path

K_FILE_BASE_URL = "https://www1.mbrace.or.jp/od2/K"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0


class OfficialSourceError(ValueError):
    """Raised for invalid input or a failed download/extraction."""


def k_file_url(target_date: date) -> str:
    yymmdd = target_date.strftime("%y%m%d")
    yyyymm = target_date.strftime("%Y%m")
    return f"{K_FILE_BASE_URL}/{yyyymm}/k{yymmdd}.lzh"


def download_k_file(
    target_date: date,
    dest_dir: Path,
    *,
    opener: object | None = None,
) -> Path:
    """Download one day's K-file to dest_dir. `opener` (default
    `urllib.request`) must expose `Request` and `urlopen`, so tests can
    inject a fake instead of hitting the network."""
    opener = opener or urllib.request
    url = k_file_url(target_date)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"k{target_date.strftime('%y%m%d')}.lzh"

    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            data = response.read()
    except Exception as exc:
        raise OfficialSourceError(f"failed to download {url}: {exc}") from exc

    dest_path.write_bytes(data)
    return dest_path


def download_month(
    year: int,
    month: int,
    dest_dir: Path,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
) -> list[Path]:
    """Download every day of the given month, waiting `delay_seconds`
    between requests (deliberately not per-second polling, per
    docs/domain/.../implementation_guide.md §6.3)."""
    if delay_seconds < 1.0:
        raise OfficialSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")

    _, days_in_month = calendar.monthrange(year, month)
    paths = []
    for day in range(1, days_in_month + 1):
        paths.append(download_k_file(date(year, month, day), dest_dir, opener=opener))
        if day < days_in_month:
            sleep(delay_seconds)
    return paths


def extract_k_file_text(lzh_path: Path) -> str:
    """Extract a downloaded K-file .lzh and decode its single text
    member as Shift-JIS (the encoding used by this data source)."""
    import pylhasa

    if not lzh_path.is_file():
        raise OfficialSourceError(f"lzh_path is not an existing file: {lzh_path}")

    try:
        archive = pylhasa.open(str(lzh_path))
        entries = [entry for entry in archive if not entry.is_dir]
    except Exception as exc:
        raise OfficialSourceError(f"failed to open archive {lzh_path}: {exc}") from exc

    if len(entries) != 1:
        raise OfficialSourceError(
            f"expected exactly one file in {lzh_path}, found {len(entries)}"
        )

    return entries[0].read().decode("shift_jis", errors="replace")
