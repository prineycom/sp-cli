import re

from sp_cli.ids import nanoid, new_client_id, uuid7
from sp_cli.ops import MAX_RECENT_OPS, OpBuilder, finalize, merge_clocks

CLI = "B_test01"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _op(b, n=1):
    ops = []
    for i in range(n):
        ops.append(b.op("HU", "UPD", "TASK", f"t{i}", {"x": i}))
    return ops


class TestIds:
    def test_uuid7_format(self):
        for _ in range(50):
            u = uuid7()
            assert UUID_RE.match(u), u

    def test_uuid7_monotonic_and_unique(self):
        ids = [uuid7() for _ in range(500)]
        assert ids == sorted(ids)
        assert len(set(ids)) == 500

    def test_nanoid(self):
        nid = nanoid()
        assert len(nid) == 21
        assert re.fullmatch(r"[A-Za-z0-9_-]{21}", nid)

    def test_nanoid_never_starts_with_dash(self):
        assert all(not nanoid().startswith("-") for _ in range(300))

    def test_client_id(self):
        cid = new_client_id()
        assert re.fullmatch(r"B_[A-Za-z0-9]{6}", cid)


class TestOpEnvelope:
    def test_fields(self, sample):
        b = OpBuilder(sample, CLI, now=1234567890123)
        op = b.op("HA", "CRT", "TASK", "abc", {"task": {}}, ds=["abc", "def"])
        assert op["a"] == "HA"
        assert op["o"] == "CRT"
        assert op["e"] == "TASK"
        assert op["d"] == "abc"
        assert op["ds"] == ["abc", "def"]
        assert op["c"] == CLI
        assert op["s"] == 4
        assert op["t"] == 1234567890123
        assert UUID_RE.match(op["id"])
        assert op["p"] == {"actionPayload": {"task": {}}, "entityChanges": []}

    def test_no_ds_when_single(self, sample):
        b = OpBuilder(sample, CLI)
        op = b.op("HU", "UPD", "TASK", "abc", {})
        assert "ds" not in op

    def test_entity_changes(self, sample):
        b = OpBuilder(sample, CLI)
        ec = [{"entityType": "TASK", "entityId": "x", "opType": "UPD", "changes": {}}]
        op = b.op("KT", "UPD", "TASK", "x", {}, entity_changes=ec)
        assert op["p"]["entityChanges"] == ec


class TestVectorClock:
    def test_merge_and_cumulative_increment(self, sample):
        # sample file clock: {'I_jHradR': 5}
        b = OpBuilder(sample, CLI)
        op1, op2, op3 = _op(b, 3)
        assert op1["v"] == {"I_jHradR": 5, CLI: 1}
        assert op2["v"] == {"I_jHradR": 5, CLI: 2}
        assert op3["v"] == {"I_jHradR": 5, CLI: 3}
        # ops carry independent clock dicts
        assert op1["v"] is not op2["v"]

    def test_base_continues_from_existing_counter(self, sample):
        sample["vectorClock"][CLI] = 7
        b = OpBuilder(sample, CLI)
        (op,) = _op(b, 1)
        assert op["v"][CLI] == 8
        assert op["v"]["I_jHradR"] == 5

    def test_merge_clocks(self):
        assert merge_clocks({"a": 2, "b": 5}, {"b": 3, "c": 1}) == {
            "a": 2,
            "b": 5,
            "c": 1,
        }


class TestFinalize:
    def test_finalize_basics(self, sample):
        old_sync = sample["syncVersion"]
        old_ops_count = len(sample["recentOps"])
        b = OpBuilder(sample, CLI, now=1111)
        ops = _op(b, 2)
        finalize(sample, ops, CLI, now=2222)

        assert sample["syncVersion"] == old_sync + 1
        for op in ops:
            assert op["sv"] == old_sync + 1
        assert len(sample["recentOps"]) == old_ops_count + 2
        assert sample["recentOps"][-2:] == ops
        assert sample["oldestOpSyncVersion"] == sample["recentOps"][0]["sv"]
        assert sample["lastModified"] == 2222
        assert sample["clientId"] == CLI
        assert sample["version"] == 2
        assert sample["schemaVersion"] == 4

    def test_finalize_clock_merge_preserves_foreign_keys(self, sample):
        sample["vectorClock"]["OTHER_CLIENT"] = 42
        b = OpBuilder(sample, CLI)
        ops = _op(b, 3)
        finalize(sample, ops, CLI)
        clock = sample["vectorClock"]
        assert clock["OTHER_CLIENT"] == 42
        assert clock["I_jHradR"] == 5
        assert clock[CLI] == 3

    def test_trim_to_2000(self, sample):
        filler = [
            {"id": f"f{i}", "a": "HU", "o": "UPD", "e": "TASK", "d": "x",
             "p": {"actionPayload": {}, "entityChanges": []},
             "c": "I_jHradR", "s": 4, "t": 0, "v": {}, "sv": 1}
            for i in range(2100)
        ]
        sample["recentOps"] = filler
        b = OpBuilder(sample, CLI)
        ops = _op(b, 5)
        finalize(sample, ops, CLI)
        assert len(sample["recentOps"]) == MAX_RECENT_OPS
        assert sample["recentOps"][-5:] == ops
        assert sample["oldestOpSyncVersion"] == sample["recentOps"][0]["sv"]

    def test_recent_ops_never_empty(self, sample):
        b = OpBuilder(sample, CLI)
        ops = _op(b, 1)
        finalize(sample, ops, CLI)
        assert len(sample["recentOps"]) >= 1

    def test_empty_batch_rejected(self, sample):
        import pytest

        with pytest.raises(ValueError):
            finalize(sample, [], CLI)
