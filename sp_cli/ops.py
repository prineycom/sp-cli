"""Operation-log layer: op envelopes, vector clock, file finalization."""

from __future__ import annotations

from sp_cli.ids import uuid7
from sp_cli.model import now_ms

OP_SCHEMA_VERSION = 4
MAX_RECENT_OPS = 2000
MAX_CLOCK_ENTRIES = 20


class OpBuilder:
    """Builds the ops of a single write batch over a loaded sync file.

    The client's counter is cumulative across the batch: op i carries
    v = merge(file_vector_clock, {client_id: base + i}).
    """

    def __init__(self, d: dict, client_id: str, now: int | None = None):
        self.file = d
        self.client_id = client_id
        self._file_clock = dict(d.get("vectorClock") or {})
        self._base = int(self._file_clock.get(client_id, 0))
        self._count = 0
        self.now = now
        self.ops: list[dict] = []

    def op(
        self,
        a: str,
        o: str,
        e: str,
        entity_id: str,
        payload: dict,
        ds: list[str] | None = None,
        entity_changes: list[dict] | None = None,
    ) -> dict:
        self._count += 1
        clock = dict(self._file_clock)
        own = self._base + self._count
        clock[self.client_id] = max(clock.get(self.client_id, 0), own)
        op = {
            "id": uuid7(),
            "a": a,
            "o": o,
            "e": e,
            "d": entity_id,
        }
        if ds is not None:
            op["ds"] = list(ds)
        op["p"] = {
            "actionPayload": payload,
            "entityChanges": list(entity_changes or []),
        }
        op["c"] = self.client_id
        op["s"] = OP_SCHEMA_VERSION
        op["t"] = self.now if self.now is not None else now_ms()
        op["v"] = clock
        self.ops.append(op)
        return op


def merge_clocks(a: dict, b: dict) -> dict:
    merged = dict(a)
    for k, v in b.items():
        merged[k] = max(int(merged.get(k, 0)), int(v))
    return merged


def finalize(d: dict, ops: list[dict], client_id: str, now: int | None = None) -> None:
    """Stamp the batch into the file: syncVersion+1, sv, trim, clocks, metadata.

    Missing syncVersion/vectorClock/recentOps keys default to 0/{}/[]
    (tolerated only when absent — present values are never regressed).
    """
    if not ops:
        raise ValueError("finalize: empty batch")
    new_sync_version = int(d.get("syncVersion") or 0) + 1
    for op in ops:
        op["sv"] = new_sync_version
    recent = (list(d.get("recentOps") or []) + ops)[-MAX_RECENT_OPS:]
    if not recent:
        raise ValueError("finalize: recentOps would be empty")
    d["recentOps"] = recent
    d["oldestOpSyncVersion"] = recent[0]["sv"]
    merged = merge_clocks(dict(d.get("vectorClock") or {}), ops[-1]["v"])
    if len(merged) > MAX_CLOCK_ENTRIES:
        # Cap: keep our own client id + the highest-counter other entries.
        others = sorted(
            ((k, int(v)) for k, v in merged.items() if k != client_id),
            key=lambda kv: (-kv[1], kv[0]),
        )
        keep = MAX_CLOCK_ENTRIES - (1 if client_id in merged else 0)
        capped = {k: v for k, v in others[:keep]}
        if client_id in merged:
            capped[client_id] = merged[client_id]
        merged = capped
    d["vectorClock"] = merged
    d["syncVersion"] = new_sync_version
    d["lastModified"] = now if now is not None else now_ms()
    d["clientId"] = client_id
    d["version"] = 2
    d["schemaVersion"] = 4
