import json

import pytest

from sp_cli.webdav import (
    ConflictError,
    SyncFileClient,
    WebDavError,
    add_prefix,
    serialize,
    strip_prefix,
)


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeSession:
    """Stands in for requests.Session; records calls."""

    def __init__(self):
        self.auth = None
        self.get_responses: list[FakeResponse] = []
        self.put_responses: list[FakeResponse] = []
        self.put_calls: list[dict] = []
        self.get_calls: list[str] = []

    def get(self, url):
        self.get_calls.append(url)
        return self.get_responses.pop(0)

    def put(self, url, data=None, headers=None):
        self.put_calls.append({"url": url, "data": data, "headers": headers or {}})
        return self.put_responses.pop(0)


def _minimal_file() -> dict:
    return {
        "version": 2,
        "syncVersion": 1,
        "schemaVersion": 4,
        "vectorClock": {"X": 1},
        "lastModified": 0,
        "clientId": "X",
        "state": {},
        "recentOps": [{"id": "a", "sv": 1}],
        "oldestOpSyncVersion": 1,
    }


def _client(tmp_path, session):
    return SyncFileClient(
        "http://localhost:8091",
        "superproductivity",
        "user",
        "pw",
        str(tmp_path / "backups"),
        session=session,
    )


class TestPrefix:
    def test_roundtrip(self):
        data = {"version": 2, "x": "тест"}
        text = add_prefix(serialize(data))
        assert text.startswith("pf_2__")
        assert json.loads(strip_prefix(text)) == data

    def test_strip_rejects_compressed(self):
        with pytest.raises(WebDavError, match="compressed"):
            strip_prefix("pf_C2__xxx")

    def test_strip_rejects_encrypted(self):
        with pytest.raises(WebDavError, match="encrypted"):
            strip_prefix("pf_E2__xxx")

    def test_strip_rejects_missing_prefix(self):
        with pytest.raises(WebDavError):
            strip_prefix('{"version": 2}')

    def test_strip_rejects_wrong_version(self):
        with pytest.raises(WebDavError, match="version"):
            strip_prefix("pf_3__{}")

    def test_serialize_compact_and_unicode(self):
        out = serialize({"a": [1, 2], "b": "ё"})
        assert out == '{"a":[1,2],"b":"ё"}'


class TestGetPut:
    def test_get_captures_etag_and_parses(self, tmp_path):
        session = FakeSession()
        body = add_prefix(serialize(_minimal_file())).encode()
        session.get_responses.append(
            FakeResponse(200, body, {"ETag": '"abc123"'})
        )
        client = _client(tmp_path, session)
        d = client.get()
        assert d["version"] == 2
        assert client.etag == '"abc123"'
        assert client.original_bytes == body
        assert session.get_calls == [
            "http://localhost:8091/superproductivity/sync-data.json"
        ]

    def test_put_sends_if_match_and_prefix(self, tmp_path):
        session = FakeSession()
        data = _minimal_file()
        body = add_prefix(serialize(data)).encode()
        session.get_responses.append(FakeResponse(200, body, {"ETag": '"e1"'}))
        session.put_responses.append(FakeResponse(201, b"", {"ETag": '"e2"'}))
        client = _client(tmp_path, session)
        client.get()
        client.put(data)
        call = session.put_calls[0]
        assert call["headers"]["If-Match"] == '"e1"'
        assert call["data"].startswith(b"pf_2__")
        assert client.etag == '"e2"'

    def test_put_without_etag_refused(self, tmp_path):
        session = FakeSession()
        body = add_prefix(serialize(_minimal_file())).encode()
        session.get_responses.append(FakeResponse(200, body, {}))
        client = _client(tmp_path, session)
        client.get()
        with pytest.raises(WebDavError, match="ETag"):
            client.put(_minimal_file())
        assert session.put_calls == []  # nothing went over the wire

    def test_put_without_etag_allowed_with_override(self, tmp_path):
        session = FakeSession()
        body = add_prefix(serialize(_minimal_file())).encode()
        session.get_responses.append(FakeResponse(200, body, {}))
        session.put_responses.append(FakeResponse(204, b"", {}))
        client = _client(tmp_path, session)
        client.get()
        client.put(_minimal_file(), allow_unconditional=True)
        assert "If-Match" not in session.put_calls[0]["headers"]

    def test_412_raises_conflict(self, tmp_path):
        session = FakeSession()
        body = add_prefix(serialize(_minimal_file())).encode()
        session.get_responses.append(FakeResponse(200, body, {"ETag": '"e1"'}))
        session.put_responses.append(FakeResponse(412, b"", {}))
        client = _client(tmp_path, session)
        client.get()
        with pytest.raises(ConflictError):
            client.put(_minimal_file())

    def test_get_error_status(self, tmp_path):
        session = FakeSession()
        session.get_responses.append(FakeResponse(404, b"", {}))
        client = _client(tmp_path, session)
        with pytest.raises(WebDavError, match="not found"):
            client.get()


class TestBackups:
    def _do_put(self, client, session, data):
        session.put_responses.append(FakeResponse(204, b"", {}))
        client.put(data)

    def test_backup_contains_original_bytes(self, tmp_path):
        session = FakeSession()
        data = _minimal_file()
        body = add_prefix(serialize(data)).encode()
        session.get_responses.append(FakeResponse(200, body, {"ETag": '"e1"'}))
        client = _client(tmp_path, session)
        client.get()
        self._do_put(client, session, data)
        backups = list((tmp_path / "backups").glob("sync-data-*.json"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == body

    def test_backup_rotation_keeps_last_10(self, tmp_path):
        session = FakeSession()
        data = _minimal_file()
        body = add_prefix(serialize(data)).encode()
        client = _client(tmp_path, session)
        for _ in range(13):
            session.get_responses.append(FakeResponse(200, body, {"ETag": '"e1"'}))
            client.get()
            self._do_put(client, session, data)
        backups = list((tmp_path / "backups").glob("sync-data-*.json"))
        assert len(backups) == 10


class TestStoreRetry:
    def test_commit_retries_on_conflict(self, tmp_path, sample):
        from sp_cli import mutations as mut
        from sp_cli.model import make_task
        from sp_cli.store import SyncStore

        session = FakeSession()
        body = add_prefix(serialize(sample)).encode()
        # initial GET + one re-GET after 412
        session.get_responses = [
            FakeResponse(200, body, {"ETag": '"e1"'}),
            FakeResponse(200, body, {"ETag": '"e2"'}),
        ]
        session.put_responses = [
            FakeResponse(412, b"", {}),
            FakeResponse(204, b"", {"ETag": '"e3"'}),
        ]
        client = _client(tmp_path, session)
        store = SyncStore(client, "B_test01")
        d0 = client.get()

        def add(dd, b):
            mut.add_task(dd, b, make_task("Z" * 21, "retry me", "INBOX_PROJECT"))

        result = store.commit([add], initial=d0)
        assert len(session.put_calls) == 2
        assert "Z" * 21 in result["state"]["task"]["entities"]
        # second attempt re-applied on fresh state: exactly one occurrence
        assert result["state"]["task"]["ids"].count("Z" * 21) == 1
        assert result["syncVersion"] == sample["syncVersion"] + 1

    def test_commit_gives_up_after_3(self, tmp_path, sample):
        from sp_cli.store import SyncStore
        from sp_cli import mutations as mut
        from sp_cli.model import make_task

        session = FakeSession()
        body = add_prefix(serialize(sample)).encode()
        session.get_responses = [
            FakeResponse(200, body, {"ETag": f'"e{i}"'}) for i in range(3)
        ]
        session.put_responses = [FakeResponse(412, b"", {}) for _ in range(3)]
        client = _client(tmp_path, session)
        store = SyncStore(client, "B_test01")

        def add(dd, b):
            mut.add_task(dd, b, make_task("Y" * 21, "conflict", "INBOX_PROJECT"))

        with pytest.raises(ConflictError):
            store.commit([add])
        assert len(session.put_calls) == 3


class TestExplicitBackups:
    """save_backup with a custom directory and rotation (sp backup --dir/--keep)."""

    def _client_with_state(self, tmp_path):
        session = FakeSession()
        data = _minimal_file()
        body = add_prefix(serialize(data)).encode()
        session.get_responses.append(FakeResponse(200, body, {"ETag": '"e1"'}))
        client = _client(tmp_path, session)
        client.get()
        return client, body

    def test_custom_directory(self, tmp_path):
        client, body = self._client_with_state(tmp_path)
        target = tmp_path / "cron-backups"
        path = client.save_backup(target)
        assert path.parent == target
        assert path.read_bytes() == body
        # default backup_dir untouched
        assert not (tmp_path / "backups").exists()

    def test_custom_keep_rotates(self, tmp_path):
        client, _ = self._client_with_state(tmp_path)
        target = tmp_path / "cron-backups"
        for _ in range(5):
            client.save_backup(target, keep=3)
        assert len(list(target.glob("sync-data-*.json"))) == 3

    def test_keep_zero_keeps_everything(self, tmp_path):
        client, _ = self._client_with_state(tmp_path)
        target = tmp_path / "cron-backups"
        for _ in range(12):
            client.save_backup(target, keep=0)
        assert len(list(target.glob("sync-data-*.json"))) == 12

    def test_default_rotation_unchanged(self, tmp_path):
        client, _ = self._client_with_state(tmp_path)
        for _ in range(12):
            client.save_backup()
        assert len(list((tmp_path / "backups").glob("sync-data-*.json"))) == 10
