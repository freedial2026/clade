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


def _select_archive_member(entries: list, lzh_path: Path):
    """Pick the one member to parse from an opened archive.

    Almost every archive holds exactly one member. Four in the 2005-2026
    K-file range (`k080706`, `k080713`, `k090406`, `k090708`) instead
    hold the *same day* twice under names differing only in case
    (`K090406.TXT` and `k090406.txt`) — a re-issue, not two different
    days. Rejecting those cost four days of results, so they are
    resolved here instead.

    The two copies differ only in text layout: the larger member uses
    the same modern layout as the neighbouring single-member days
    ("一般戦", "晴れ"), the smaller an older one ("一　　般", "晴"). Parsed
    output is identical for three of the four; in `k080706` venue 05
    race 9 the older copy yields `exhibition_time=0.0` for a 欠場 (K0)
    row where the modern one correctly yields `None` — a fabricated
    zero that would reach features as a real exhibition time. So the
    larger member is preferred, with the name as a stable tie-break.

    Members whose names are not the same file are still rejected: that
    is a genuinely ambiguous archive, not a known re-issue.
    """
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise OfficialSourceError(f"archive contains no file member: {lzh_path}")

    names = {str(entry.safe_path).lower() for entry in entries}
    if len(names) != 1:
        raise OfficialSourceError(
            f"expected one file in {lzh_path}, found {len(entries)} distinct members: "
            f"{sorted(names)}"
        )
    return max(entries, key=lambda entry: (entry.size, str(entry.safe_path)))


def extract_k_file_text(lzh_path: Path) -> str:
    """Extract a downloaded K-file .lzh and decode its text member as
    Shift-JIS (the encoding used by this data source).

    See `_select_archive_member` for the handful of archives that carry
    the same day twice."""
    import pylhasa

    if not lzh_path.is_file():
        raise OfficialSourceError(f"lzh_path is not an existing file: {lzh_path}")

    try:
        archive = pylhasa.open(str(lzh_path))
        entries = [entry for entry in archive if not entry.is_dir]
    except Exception as exc:
        raise OfficialSourceError(f"failed to open archive {lzh_path}: {exc}") from exc

    return _select_archive_member(entries, lzh_path).read().decode("shift_jis", errors="replace")
