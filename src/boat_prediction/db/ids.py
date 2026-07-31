"""UUID v7 generation.

docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§7.1 specifies UUID v7 for internal ids. The stdlib gained `uuid.uuid7`
only in Python 3.14 and this project targets 3.12+, so it is implemented
here (RFC 9562 §5.7): 48-bit big-endian Unix timestamp in milliseconds,
then version/variant bits, then random.

Why v7 rather than v4 for this schema specifically: `race_entries` will
hold roughly 7 million rows loaded in race-date order, and a v7 key is
time-ordered, so index inserts stay near the right edge of the B-tree
instead of dirtying random pages across the whole index. It also means
`ORDER BY id` approximates load order, which is convenient for resuming
a partial load.

Monotonicity within a millisecond is not guaranteed (the sub-millisecond
bits are random, per the "fixed bit-length dedicated counter" being
optional in RFC 9562 §6.2). Ids from the same millisecond may therefore
sort arbitrarily among themselves. Nothing in this project derives
ordering from ids -- `available_at`/`race_date` do that -- so this is a
deliberate simplification, not an oversight.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7(*, timestamp_ms: int | None = None) -> uuid.UUID:
    """Return a UUID v7.

    `timestamp_ms` is injectable so tests can assert the time-ordering
    property deterministically rather than by sleeping.
    """
    if timestamp_ms is None:
        timestamp_ms = time.time_ns() // 1_000_000
    if not 0 <= timestamp_ms < 1 << 48:
        raise ValueError(f"timestamp_ms does not fit in 48 bits: {timestamp_ms}")

    value = bytearray(os.urandom(16))
    value[0:6] = timestamp_ms.to_bytes(6, "big")
    # version 7 in the high nibble of octet 6, variant 0b10 in octet 8
    value[6] = 0x70 | (value[6] & 0x0F)
    value[8] = 0x80 | (value[8] & 0x3F)
    return uuid.UUID(bytes=bytes(value))
