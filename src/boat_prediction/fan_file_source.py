"""Official data source: BOATRACE racer profile/period data ("fan" files).

Source confirmed by fetching the official download index
(https://www.boatrace.jp/owpc/pc/extra/data/download.html), operated by
the general incorporated foundation BOATRACE Promotion Association (the
same body as `official_source.py`'s K-file source). Its `layout.html`
page (https://www.boatrace.jp/owpc/pc/extra/data/layout.html) documents
the fixed-width record layout of these files ("モーターボートファン手帳"
racer data: registration number, name, win rate, per-course stats,
finish-position counts, hometown/branch, etc.) but is not itself a data
file.

robots.txt on this host (`www.boatrace.jp`) has no disallow rules.
`https://www.boatrace.jp/owpc/pc/extra/policy.html` prohibits
"large-volume access/transmission that interferes with site operation"
and unauthorized reproduction/redistribution beyond private use. Per
docs/domain/.../implementation_guide.md §6.3 and that policy, this
module:

- only downloads the publicly linked, unauthenticated `.lzh` URLs found
  on the index page itself (no URL guessing/formula extrapolation)
- defaults to a multi-second delay between requests in `download_all`
- does not retry aggressively or parallelize requests

There are only two files per year (first half/second half), so the
total corpus is small (~50 files as of 2026).

Requires the `official-data` extra (`pylhasa`, for LZH extraction),
shared with `official_source.py`.
"""

from __future__ import annotations

import re
import time
import urllib.request
from pathlib import Path

FAN_INDEX_URL = "https://www.boatrace.jp/owpc/pc/extra/data/download.html"
FAN_FILE_BASE_URL = "https://www.boatrace.jp/static_extra/pc_static/download/data/kibetsu"
_FAN_LINK_PATTERN = re.compile(r"/static_extra/pc_static/download/data/kibetsu/(fan\d{4}\.lzh)")
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0


class FanFileSourceError(ValueError):
    """Raised for invalid input or a failed download/extraction."""


def list_fan_file_urls(*, opener: object | None = None) -> list[str]:
    """Fetch the official index page and return every fan-file URL
    linked from it, in the order they appear (newest year first, as
    published). Discovers URLs from the live page rather than
    computing a year->code formula, so it stays correct if the site's
    numbering convention ever changes."""
    opener = opener or urllib.request
    request = opener.Request(FAN_INDEX_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise FanFileSourceError(f"failed to fetch {FAN_INDEX_URL}: {exc}") from exc

    filenames = dict.fromkeys(_FAN_LINK_PATTERN.findall(html))  # de-dup, preserve order
    if not filenames:
        raise FanFileSourceError(
            f"no fan-file links found on {FAN_INDEX_URL}; page structure may have changed"
        )
    return [f"{FAN_FILE_BASE_URL}/{name}" for name in filenames]


def download_fan_file(url: str, dest_dir: Path, *, opener: object | None = None) -> Path:
    """Download one fan-file URL to dest_dir, named after its URL basename."""
    opener = opener or urllib.request
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / url.rsplit("/", 1)[-1]

    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            data = response.read()
    except Exception as exc:
        raise FanFileSourceError(f"failed to download {url}: {exc}") from exc

    dest_path.write_bytes(data)
    return dest_path


def download_all(
    dest_dir: Path,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
) -> list[Path]:
    """Download every fan file currently linked from the official index,
    waiting `delay_seconds` between requests (deliberately not
    per-second polling, per docs/domain/.../implementation_guide.md
    §6.3 and the site's own "large-volume access" prohibition). Makes
    one extra request up front to fetch the index page itself."""
    if delay_seconds < 1.0:
        raise FanFileSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")

    urls = list_fan_file_urls(opener=opener)
    paths = []
    for i, url in enumerate(urls):
        paths.append(download_fan_file(url, dest_dir, opener=opener))
        if i < len(urls) - 1:
            sleep(delay_seconds)
    return paths


def extract_fan_file_text(lzh_path: Path) -> str:
    """Extract a downloaded fan-file .lzh and decode its text member(s)
    as Shift-JIS (the encoding used by this data source), concatenating
    if the archive holds more than one file."""
    import pylhasa

    if not lzh_path.is_file():
        raise FanFileSourceError(f"lzh_path is not an existing file: {lzh_path}")

    try:
        archive = pylhasa.open(str(lzh_path))
        entries = [entry for entry in archive if not entry.is_dir]
    except Exception as exc:
        raise FanFileSourceError(f"failed to open archive {lzh_path}: {exc}") from exc

    if not entries:
        raise FanFileSourceError(f"no files found in archive {lzh_path}")

    return "".join(entry.read().decode("shift_jis", errors="replace") for entry in entries)
