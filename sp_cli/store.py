"""SyncStore: load → mutate → commit with conflict retry."""

from __future__ import annotations

from typing import Callable

from sp_cli.ops import OpBuilder, finalize
from sp_cli.webdav import ConflictError, SyncFileClient

MAX_RETRIES = 3

MutationFn = Callable[[dict, OpBuilder], object]


class SyncStore:
    def __init__(self, client: SyncFileClient, client_id: str):
        self.client = client
        self.client_id = client_id

    def load(self) -> dict:
        return self.client.get()

    def commit(
        self, mutations: list[MutationFn], initial: dict | None = None
    ) -> dict:
        """Apply mutation closures to the (fresh) file state and PUT.

        On HTTP 412 the file is re-downloaded and the same mutations are
        re-applied to the fresh state; up to MAX_RETRIES attempts.
        """
        last_error: ConflictError | None = None
        for attempt in range(MAX_RETRIES):
            if attempt == 0 and initial is not None:
                d = initial
            else:
                d = self.client.get()
            builder = OpBuilder(d, self.client_id)
            for fn in mutations:
                fn(d, builder)
            if not builder.ops:
                return d
            finalize(d, builder.ops, self.client_id)
            try:
                self.client.put(d)
                return d
            except ConflictError as e:
                last_error = e
        raise last_error  # type: ignore[misc]
