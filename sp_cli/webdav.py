"""WebDAV transport for sync-data.json: GET/PUT, pf_2__ prefix, ETag, backups."""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import requests

PREFIX = "pf_2__"
_PREFIX_RE = re.compile(r"^pf_(C?)(E?)(\d+)__")
BACKUP_KEEP = 10


class WebDavError(Exception):
    pass


class ConflictError(WebDavError):
    """HTTP 412 — concurrent write detected via If-Match."""


def strip_prefix(text: str) -> str:
    m = _PREFIX_RE.match(text)
    if not m:
        raise WebDavError("sync-data.json: missing pf_ prefix (unexpected format)")
    compressed, encrypted, version = m.group(1), m.group(2), m.group(3)
    if compressed:
        raise WebDavError("sync-data.json is compressed (pf_C); not supported")
    if encrypted:
        raise WebDavError("sync-data.json is encrypted (pf_E); not supported")
    if version != "2":
        raise WebDavError(f"sync-data.json: unsupported model version {version}")
    return text[m.end() :]


def add_prefix(text: str) -> str:
    return PREFIX + text


def serialize(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class SyncFileClient:
    def __init__(
        self,
        url: str,
        folder: str,
        user: str,
        password: str,
        backup_dir: str,
        session: requests.Session | None = None,
    ):
        self.session = session or requests.Session()
        self.session.auth = (user, password)
        self.file_url = f"{url.rstrip('/')}/{folder.strip('/')}/sync-data.json"
        self.backup_dir = Path(backup_dir)
        self.etag: str | None = None
        self.original_bytes: bytes | None = None

    def get(self) -> dict:
        try:
            r = self.session.get(self.file_url)
        except requests.RequestException as e:
            raise WebDavError(f"GET {self.file_url}: {e}") from e
        if r.status_code == 404:
            raise WebDavError(f"not found: {self.file_url}")
        if r.status_code >= 400:
            raise WebDavError(f"GET {self.file_url}: HTTP {r.status_code}")
        self.etag = r.headers.get("ETag")
        self.original_bytes = r.content
        text = r.content.decode("utf-8")
        payload = strip_prefix(text)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise WebDavError(f"sync-data.json: invalid JSON: {e}") from e
        if data.get("version") != 2:
            raise WebDavError(
                f"sync-data.json: unexpected file version {data.get('version')}"
            )
        return data

    def put(self, data: dict, allow_unconditional: bool = False) -> None:
        if not self.etag and not allow_unconditional:
            raise WebDavError(
                f"PUT {self.file_url}: refusing to write without an ETag from "
                "the previous GET (no If-Match means a concurrent write could "
                "be silently overwritten); pass allow_unconditional=True to "
                "override"
            )
        self._write_backup()
        body = add_prefix(serialize(data)).encode("utf-8")
        headers = {"Content-Type": "application/octet-stream"}
        if self.etag:
            headers["If-Match"] = self.etag
        try:
            r = self.session.put(self.file_url, data=body, headers=headers)
        except requests.RequestException as e:
            raise WebDavError(f"PUT {self.file_url}: {e}") from e
        if r.status_code == 412:
            raise ConflictError(f"PUT {self.file_url}: HTTP 412 (concurrent write)")
        if r.status_code >= 400:
            raise WebDavError(f"PUT {self.file_url}: HTTP {r.status_code}")
        self.etag = r.headers.get("ETag")

    def save_backup(self) -> Path | None:
        """Write a backup of the last downloaded bytes; returns the path."""
        if self.original_bytes is None:
            return None
        return self._write_backup()

    def _write_backup(self) -> Path | None:
        if self.original_bytes is None:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.backup_dir / f"sync-data-{stamp}.json"
        path.write_bytes(self.original_bytes)
        backups = sorted(self.backup_dir.glob("sync-data-*.json"))
        for old in backups[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        return path
