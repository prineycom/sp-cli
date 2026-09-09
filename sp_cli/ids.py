"""ID generation: nanoid (entities), UUIDv7 (ops), client ids."""

from __future__ import annotations

import secrets
import threading
import time

NANOID_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
CLIENT_ID_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)


def nanoid(size: int = 21) -> str:
    """nanoid with SP's default alphabet (A-Za-z0-9_-), 21 chars.

    The first char never is '-' so generated ids are safe to pass as CLI
    positional arguments (argparse would treat '-...' as an option).
    """
    first = NANOID_ALPHABET[secrets.randbelow(63)]  # excludes trailing '-'
    rest = "".join(NANOID_ALPHABET[secrets.randbelow(64)] for _ in range(size - 1))
    return first + rest


def new_client_id() -> str:
    """SP-style client id: B_ + 6 base62 chars."""
    return "B_" + "".join(
        CLIENT_ID_ALPHABET[secrets.randbelow(62)] for _ in range(6)
    )


_uuid7_lock = threading.Lock()
_uuid7_state = {"ts": 0, "seq": 0}


def uuid7() -> str:
    """RFC 9562 UUIDv7: 48-bit unix-ms timestamp, version 7, variant 10.

    Monotonic within the process: a 12-bit counter in rand_a is incremented
    when several ids are generated within the same millisecond.
    """
    with _uuid7_lock:
        ts = int(time.time() * 1000)
        if ts <= _uuid7_state["ts"]:
            ts = _uuid7_state["ts"]
            _uuid7_state["seq"] += 1
            if _uuid7_state["seq"] > 0xFFF:
                ts += 1
                _uuid7_state["ts"] = ts
                _uuid7_state["seq"] = secrets.randbits(11)
        else:
            _uuid7_state["ts"] = ts
            _uuid7_state["seq"] = secrets.randbits(11)
        rand_a = _uuid7_state["seq"] & 0xFFF
        rand_b = secrets.randbits(62)
        value = (
            (ts & 0xFFFFFFFFFFFF) << 80
            | 0x7 << 76
            | rand_a << 64
            | 0b10 << 62
            | rand_b
        )
        h = f"{value:032x}"
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
