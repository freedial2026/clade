"""Official data source: BOATRACE race card ("B-file", 番組表) download.

Source confirmed identically to `official_source.py`'s K-file (results):
same host (`https://www1.mbrace.or.jp/od2/`), same
`{base}/{letter}/{YYYYMM}/{letter_lower}{YYMMDD}.lzh` URL shape, just the
`B` letter instead of `K` (confirmed by probing `od2/B/dindex.html` ->
200 and a sample `od2/B/202606/b260601.lzh` -> 200, then downloading and
inspecting that sample: it is a per-race, per-lane entry list -- racer
name/registration number, national/local win rate, motor/boat number
and their 2-rates, current-series results -- published *before* the
race, unlike K-file which is published after).

This is the primary source for leakage-safe pre-race features: using
K-file's entry fields directly as P1 training features would violate
`available_at <= prediction_at` (K-file is a post-race publication).

Same rate-limiting rationale as `official_source.py`: default 3s
between requests, no aggressive retry.

Requires the `official-data` extra (`pylhasa`, for LZH extraction --
reuses `official_source.extract_k_file_text`, which despite its name is
generic Shift-JIS single-member LZH decoding, not K-file-specific).
"""

from __future__ import annotations

import calendar
import time
import urllib.request
from datetime import date
from pathlib import Path

B_FILE_BASE_URL = "https://www1.mbrace.or.jp/od2/B"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0


class BFileSourceError(ValueError):
    """Raised for invalid input or a failed download."""


def b_file_url(target_date: date) -> str:
    yymmdd = target_date.strftime("%y%m%d")
    yyyymm = target_date.strftime("%Y%m")
    return f"{B_FILE_BASE_URL}/{yyyymm}/b{yymmdd}.lzh"


def download_b_file(
    target_date: date,
    dest_dir: Path,
    *,
    opener: object | None = None,
) -> Path:
    """Download one day's B-file to dest_dir. `opener` (default
    `urllib.request`) must expose `Request` and `urlopen`, so tests can
    inject a fake instead of hitting the network."""
    opener = opener or urllib.request
    url = b_file_url(target_date)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"b{target_date.strftime('%y%m%d')}.lzh"

    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            data = response.read()
    except Exception as exc:
        raise BFileSourceError(f"failed to download {url}: {exc}") from exc

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
        raise BFileSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")

    _, days_in_month = calendar.monthrange(year, month)
    paths = []
    for day in range(1, days_in_month + 1):
        paths.append(download_b_file(date(year, month, day), dest_dir, opener=opener))
        if day < days_in_month:
            sleep(delay_seconds)
    return paths
