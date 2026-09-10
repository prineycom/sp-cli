"""Local live timer state.

The running timer is device-local: SP's `currentTaskId` has no op
representation, so nothing about *starting* a timer is ever synced. Only the
accrued time is (one KT op per day at stop, see mutations.track_time).

State lives in a single JSON file (`~/.local/share/sp-cli/timer.json`,
override via SP_CLI_TIMER): `{"task_id", "title", "started_at"}` — written
atomically (tmp file + rename).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from sp_cli.config import timer_path

MIN_TRACK_MS = 60_000


class TimerError(Exception):
    pass


def _path(path: str | os.PathLike | None = None) -> Path:
    return Path(path) if path is not None else timer_path()


def read_timer(path: str | os.PathLike | None = None) -> dict | None:
    """Running timer, or None when no timer file exists."""
    p = _path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise TimerError(f"cannot read timer file {p}: {e}") from None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("task_id"), str)
        or not isinstance(data.get("started_at"), int)
    ):
        raise TimerError(f"corrupt timer file {p}; delete it to reset")
    return data


def write_timer(
    task_id: str,
    title: str,
    started_at: int,
    path: str | os.PathLike | None = None,
) -> Path:
    """Atomically replace the timer file."""
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    payload = {"task_id": task_id, "title": title, "started_at": int(started_at)}
    try:
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise TimerError(f"cannot write timer file {p}: {e}") from None
    return p


def clear_timer(path: str | os.PathLike | None = None) -> bool:
    """Remove the timer file. True if there was one."""
    p = _path(path)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        raise TimerError(f"cannot remove timer file {p}: {e}") from None


def _midnight_after(ms: int) -> int:
    day = datetime.datetime.fromtimestamp(ms / 1000).date()
    nxt = datetime.datetime.combine(
        day + datetime.timedelta(days=1), datetime.time.min
    )
    return int(nxt.timestamp() * 1000)


def split_by_day(started_at: int, stopped_at: int) -> list[tuple[str, int]]:
    """Split an interval into per-local-day (YYYY-MM-DD, duration_ms) chunks.

    A timer running across midnight yields one chunk per day, so the stop
    emits one KT op per day (all in a single batch).
    """
    segments: list[tuple[str, int]] = []
    cur = int(started_at)
    end = int(stopped_at)
    while cur < end:
        day = datetime.datetime.fromtimestamp(cur / 1000).date().isoformat()
        chunk_end = min(_midnight_after(cur), end)
        segments.append((day, chunk_end - cur))
        cur = chunk_end
    return segments
