import datetime

import pytest

from conftest import assert_doctor_clean
from sp_cli import mutations as mut
from sp_cli.model import (
    make_board,
    make_issue_provider,
    make_note,
    make_panel,
    make_project,
    make_simple_counter,
    make_tag,
    make_task,
    today_str,
)
from sp_cli.ops import OpBuilder

CLI = "B_test01"


@pytest.fixture
def b(sample):
    return OpBuilder(sample, CLI)


def _last_op(b):
    return b.ops[-1]


def _task(d, tid):
    return d["state"]["task"]["entities"][tid]


def _today_order(d):
    return d["state"]["tag"]["entities"]["TODAY"]["taskIds"]


class TestAddTask:
    def test_add_basic(self, sample, b):
        task = make_task("T" * 21, "hello", "INBOX_PROJECT")
        mut.add_task(sample, b, task)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("HA", "CRT", "TASK", "T" * 21)
        p = op["p"]["actionPayload"]
        assert p["task"]["title"] == "hello"
        assert p["workContextId"] == "INBOX_PROJECT"
        assert p["workContextType"] == "PROJECT"
        assert p["isAddToBacklog"] is False
        assert p["isAddToBottom"] is True
        assert op["p"]["entityChanges"] == []

        assert "T" * 21 in sample["state"]["task"]["ids"]
        proj = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        assert proj["taskIds"][-1] == "T" * 21  # appended to bottom
        assert_doctor_clean(sample)

    def test_add_due_today_enters_today_order(self, sample, b):
        task = make_task("A" * 21, "due today", "INBOX_PROJECT", due_day=today_str())
        mut.add_task(sample, b, task)
        assert _today_order(sample)[0] == "A" * 21
        assert_doctor_clean(sample)

    def test_add_due_with_time_today_enters_today_order(self, sample, b):
        ts = int(
            datetime.datetime.now()
            .replace(hour=23, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        task = make_task("D" * 21, "timed today", "INBOX_PROJECT", due_with_time=ts)
        mut.add_task(sample, b, task)
        assert _today_order(sample)[0] == "D" * 21
        assert_doctor_clean(sample)

    def test_add_due_with_time_other_day_not_in_today_order(self, sample, b):
        task = make_task(
            "E" * 21, "timed later", "INBOX_PROJECT", due_with_time=1800000000000
        )
        mut.add_task(sample, b, task)
        assert "E" * 21 not in _today_order(sample)
        assert_doctor_clean(sample)

    def test_add_with_tags_syncs_membership(self, sample, b):
        tag = make_tag("G" * 21, "mytag")
        mut.tag_add(sample, b, tag)
        task = make_task("B" * 21, "tagged", "INBOX_PROJECT", tag_ids=["G" * 21])
        mut.add_task(sample, b, task)
        assert "B" * 21 in sample["state"]["tag"]["entities"]["G" * 21]["taskIds"]
        assert_doctor_clean(sample)

    def test_today_tag_rejected(self, sample, b):
        task = make_task("C" * 21, "bad", "INBOX_PROJECT", tag_ids=["TODAY"])
        with pytest.raises(mut.MutationError):
            mut.add_task(sample, b, task)


class TestUpdateTask:
    def test_complete_and_reopen(self, sample, b, add_task_entity):
        t = add_task_entity(title="to complete")
        mut.complete_task(sample, b, t["id"])
        op = _last_op(b)
        assert (op["a"], op["o"]) == ("HU", "UPD")
        changes = op["p"]["actionPayload"]["task"]["changes"]
        assert changes["isDone"] is True
        assert isinstance(changes["doneOn"], int)
        assert _task(sample, t["id"])["isDone"] is True

        mut.reopen_task(sample, b, t["id"])
        changes = _last_op(b)["p"]["actionPayload"]["task"]["changes"]
        assert changes == {"isDone": False, "doneOn": None}
        assert _task(sample, t["id"])["isDone"] is False
        assert _task(sample, t["id"])["doneOn"] is None
        assert_doctor_clean(sample)

    def test_due_day_clears_due_with_time_xor(self, sample, b, add_task_entity):
        t = add_task_entity(title="xor", due_with_time=1800000000000, remind_at=1800000000000)
        mut.update_task(sample, b, t["id"], {"dueDay": "2030-01-01"})
        changes = _last_op(b)["p"]["actionPayload"]["task"]["changes"]
        assert changes["dueDay"] == "2030-01-01"
        assert changes["dueWithTime"] is None
        assert changes["remindAt"] is None
        task = _task(sample, t["id"])
        assert task["dueDay"] == "2030-01-01"
        assert task["dueWithTime"] is None
        assert task["remindAt"] is None
        assert_doctor_clean(sample)

    def test_due_with_time_clears_due_day(self, sample, b, add_task_entity):
        t = add_task_entity(title="xor2", due_day="2030-01-01")
        mut.update_task(sample, b, t["id"], {"dueWithTime": 1800000000000})
        changes = _last_op(b)["p"]["actionPayload"]["task"]["changes"]
        assert changes["dueDay"] is None
        task = _task(sample, t["id"])
        assert task["dueDay"] is None
        assert task["dueWithTime"] == 1800000000000
        assert_doctor_clean(sample)

    def test_modified_set_in_state_not_payload(self, sample, b, add_task_entity):
        t = add_task_entity(title="mod")
        mut.update_task(sample, b, t["id"], {"title": "new"})
        changes = _last_op(b)["p"]["actionPayload"]["task"]["changes"]
        assert "modified" not in changes
        assert isinstance(_task(sample, t["id"])["modified"], int)

    def test_tag_ids_change_syncs_tag_task_ids(self, sample, b, add_task_entity):
        tag1 = make_tag("1" * 21, "one")
        tag2 = make_tag("2" * 21, "two")
        mut.tag_add(sample, b, tag1)
        mut.tag_add(sample, b, tag2)
        t = add_task_entity(title="retag")
        mut.update_task(sample, b, t["id"], {"tagIds": ["1" * 21]})
        assert t["id"] in sample["state"]["tag"]["entities"]["1" * 21]["taskIds"]
        mut.update_task(sample, b, t["id"], {"tagIds": ["2" * 21]})
        assert t["id"] not in sample["state"]["tag"]["entities"]["1" * 21]["taskIds"]
        assert t["id"] in sample["state"]["tag"]["entities"]["2" * 21]["taskIds"]
        assert_doctor_clean(sample)


class TestDelete:
    def test_delete_cascade(self, sample, b, add_task_entity):
        parent = add_task_entity(title="parent", due_day=today_str())
        sub = make_task("S" * 21, "sub", "INBOX_PROJECT")
        mut.add_subtask(sample, b, parent["id"], sub)
        mut.plan_today(sample, b, [parent["id"]])
        state = sample["state"]
        state.setdefault("planner", {"days": {}})["days"]["2030-05-05"] = [parent["id"]]

        mut.delete_task(sample, b, parent["id"])
        op = _last_op(b)
        assert (op["a"], op["o"], op["d"]) == ("HD", "DEL", parent["id"])
        snap = op["p"]["actionPayload"]["task"]
        assert snap["id"] == parent["id"]
        assert [s["id"] for s in snap["subTasks"]] == ["S" * 21]

        assert parent["id"] not in state["task"]["entities"]
        assert "S" * 21 not in state["task"]["entities"]
        assert parent["id"] not in state["project"]["entities"]["INBOX_PROJECT"]["taskIds"]
        assert parent["id"] not in _today_order(sample)
        assert "2030-05-05" not in state["planner"]["days"]
        assert_doctor_clean(sample)

    def test_bulk_delete(self, sample, b, add_task_entity):
        t1 = add_task_entity(title="one")
        t2 = add_task_entity(title="two")
        mut.delete_tasks(sample, b, [t1["id"], t2["id"]])
        op = _last_op(b)
        assert (op["a"], op["o"]) == ("HDM", "DEL")
        assert op["ds"] == [t1["id"], t2["id"]]
        assert op["p"]["actionPayload"] == {"taskIds": [t1["id"], t2["id"]]}
        assert t1["id"] not in sample["state"]["task"]["entities"]
        assert t2["id"] not in sample["state"]["task"]["entities"]
        assert_doctor_clean(sample)

    def test_bulk_delete_flattens_subtask_ids(self, sample, b, add_task_entity):
        t1 = add_task_entity(title="parent one")
        sub1 = make_task("S" * 21, "sub1", "INBOX_PROJECT")
        sub2 = make_task("Z" * 21, "sub2", "INBOX_PROJECT")
        mut.add_subtask(sample, b, t1["id"], sub1)
        mut.add_subtask(sample, b, t1["id"], sub2)
        t2 = add_task_entity(title="two")

        mut.delete_tasks(sample, b, [t1["id"], t2["id"]])
        op = _last_op(b)
        flat = [t1["id"], "S" * 21, "Z" * 21, t2["id"]]
        assert op["ds"] == flat
        assert op["p"]["actionPayload"] == {"taskIds": flat}
        for tid in flat:
            assert tid not in sample["state"]["task"]["entities"]
        assert_doctor_clean(sample)


class TestSubtask:
    def test_add_subtask(self, sample, b, add_task_entity):
        parent = add_task_entity(title="parent")
        sub = make_task("S" * 21, "sub", "OTHER_PROJECT_IGNORED")
        mut.add_subtask(sample, b, parent["id"], sub)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("TA", "CRT", "TASK", "S" * 21)
        p = op["p"]["actionPayload"]
        assert p["parentId"] == parent["id"]
        assert p["task"]["parentId"] == parent["id"]
        assert p["task"]["projectId"] == parent["projectId"]  # inherited

        state = sample["state"]
        assert "S" * 21 in state["task"]["entities"]
        assert parent["subTaskIds"] == ["S" * 21]
        assert "S" * 21 not in state["project"]["entities"]["INBOX_PROJECT"]["taskIds"]
        assert_doctor_clean(sample)


class TestMoveToProject:
    def test_move(self, sample, b, add_task_entity):
        proj = make_project("P" * 21, "Target")
        mut.project_add(sample, b, proj)
        parent = add_task_entity(title="movable")
        sub = make_task("S" * 21, "sub", parent["projectId"])
        mut.add_subtask(sample, b, parent["id"], sub)

        mut.move_to_project(sample, b, parent["id"], "P" * 21)
        op = _last_op(b)
        assert (op["a"], op["o"]) == ("HMP", "UPD")
        p = op["p"]["actionPayload"]
        assert p["targetProjectId"] == "P" * 21
        assert p["task"]["subTasks"][0]["id"] == "S" * 21

        state = sample["state"]
        assert _task(sample, parent["id"])["projectId"] == "P" * 21
        assert _task(sample, "S" * 21)["projectId"] == "P" * 21
        assert parent["id"] not in state["project"]["entities"]["INBOX_PROJECT"]["taskIds"]
        assert parent["id"] in state["project"]["entities"]["P" * 21]["taskIds"]
        assert_doctor_clean(sample)


class TestReorder:
    def test_reorder(self, sample, b):
        proj = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        current = list(proj["taskIds"])
        new_order = list(reversed(current))
        mut.reorder_project(sample, b, "INBOX_PROJECT", new_order)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "PU", "UPD", "PROJECT", "INBOX_PROJECT",
        )
        assert op["p"]["actionPayload"] == {
            "project": {"id": "INBOX_PROJECT", "changes": {"taskIds": new_order}}
        }
        assert proj["taskIds"] == new_order
        assert_doctor_clean(sample)

    def test_reorder_rejects_non_permutation(self, sample, b):
        with pytest.raises(mut.MutationError):
            mut.reorder_project(sample, b, "INBOX_PROJECT", ["nope"])


class TestPlanning:
    def test_plan_today(self, sample, b, add_task_entity):
        t1 = add_task_entity(title="p1", due_with_time=1800000000000)
        t2 = add_task_entity(title="p2")
        sample["state"].setdefault("planner", {"days": {}})["days"]["2030-05-05"] = [t2["id"]]

        mut.plan_today(sample, b, [t1["id"], t2["id"]])
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("HPT", "UPD", "TASK", t1["id"])
        assert op["ds"] == [t1["id"], t2["id"]]
        assert op["p"]["actionPayload"] == {
            "taskIds": [t1["id"], t2["id"]],
            "today": today_str(),
        }
        for t in (t1, t2):
            task = _task(sample, t["id"])
            assert task["dueDay"] == today_str()
            assert task["dueWithTime"] is None
            assert task["remindAt"] is None
        assert _today_order(sample)[:2] == [t1["id"], t2["id"]]
        assert "2030-05-05" not in sample["state"]["planner"]["days"]
        assert_doctor_clean(sample)

    def test_plan_today_keeps_due_with_time_when_already_today(
        self, sample, b, add_task_entity
    ):
        ts = int(
            datetime.datetime.now()
            .replace(hour=23, minute=30, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        t = add_task_entity(title="timed today", due_with_time=ts, remind_at=ts)
        mut.plan_today(sample, b, [t["id"]])
        task = _task(sample, t["id"])
        assert task["dueWithTime"] == ts  # kept: already points to today
        assert task["remindAt"] == ts
        assert task.get("dueDay") is None  # XOR preserved: no dueDay set
        assert _today_order(sample)[0] == t["id"]
        assert_doctor_clean(sample)

    def test_plan_today_without_today_tag_is_tolerated(
        self, sample, b, add_task_entity
    ):
        t = add_task_entity(title="no today tag")
        del sample["state"]["tag"]["entities"]["TODAY"]
        sample["state"]["tag"]["ids"].remove("TODAY")
        mut.plan_today(sample, b, [t["id"]])  # must not raise
        assert _task(sample, t["id"])["dueDay"] == today_str()

    def test_remove_from_today(self, sample, b, add_task_entity):
        t = add_task_entity(title="rm", due_day=today_str())
        mut.plan_today(sample, b, [t["id"]])
        mut.remove_from_today(sample, b, t["id"])
        op = _last_op(b)
        assert (op["a"], op["o"], op["d"]) == ("HSX", "UPD", t["id"])
        assert op["p"]["actionPayload"] == {"id": t["id"], "today": today_str()}
        task = _task(sample, t["id"])
        assert task["dueDay"] is None
        assert task["dueWithTime"] is None
        assert task["remindAt"] is None
        assert t["id"] not in _today_order(sample)
        assert_doctor_clean(sample)

    def test_plan_for_future_day(self, sample, b, add_task_entity):
        t = add_task_entity(title="future")
        day = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        mut.plan_for_day(sample, b, t["id"], day)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("LP", "UPD", "PLANNER", t["id"])
        p = op["p"]["actionPayload"]
        assert p["day"] == day
        assert p["isAddToTop"] is False
        assert p["task"]["id"] == t["id"]
        task = _task(sample, t["id"])
        assert task["dueDay"] == day
        assert sample["state"]["planner"]["days"][day] == [t["id"]]
        assert t["id"] not in _today_order(sample)
        assert_doctor_clean(sample)

    def test_plan_for_today_goes_to_today_order(self, sample, b, add_task_entity):
        t = add_task_entity(title="ptoday")
        mut.plan_for_day(sample, b, t["id"], today_str())
        assert _today_order(sample)[0] == t["id"]
        assert t["id"] not in sample["state"]["planner"]["days"].get(today_str(), [])
        assert_doctor_clean(sample)

    def test_plan_for_past_day_rejected(self, sample, b, add_task_entity):
        t = add_task_entity(title="past")
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        n_ops = len(b.ops)
        with pytest.raises(mut.MutationError, match="past day"):
            mut.plan_for_day(sample, b, t["id"], yesterday)
        assert len(b.ops) == n_ops  # no op emitted
        assert_doctor_clean(sample)


class TestSchedule:
    def test_schedule_today(self, sample, b, add_task_entity):
        t = add_task_entity(title="sched", due_day="2030-01-01")
        now = datetime.datetime.now()
        ts = int(now.replace(hour=23, minute=0).timestamp() * 1000)
        mut.schedule_task(sample, b, t["id"], ts, remind_at=ts)
        op = _last_op(b)
        assert (op["a"], op["o"]) == ("HS", "UPD")
        p = op["p"]["actionPayload"]
        assert p["dueWithTime"] == ts
        assert p["remindAt"] == ts
        assert p["isMoveToBacklog"] is False
        assert p["task"]["dueDay"] == "2030-01-01"  # snapshot BEFORE change
        task = _task(sample, t["id"])
        assert task["dueWithTime"] == ts
        assert task["dueDay"] is None
        assert _today_order(sample)[0] == t["id"]
        assert_doctor_clean(sample)

    def test_reschedule_uses_hsr(self, sample, b, add_task_entity):
        t = add_task_entity(title="resched", due_with_time=1800000000000)
        mut.schedule_task(sample, b, t["id"], 1800000360000)
        op = _last_op(b)
        assert op["a"] == "HSR"
        assert "remindAt" not in op["p"]["actionPayload"]
        assert_doctor_clean(sample)


class TestDeadline:
    def test_deadline_day(self, sample, b, add_task_entity):
        t = add_task_entity(title="dl")
        mut.set_deadline(sample, b, t["id"], deadline_day="2030-06-01")
        op = _last_op(b)
        assert (op["a"], op["o"], op["d"]) == ("HDL", "UPD", t["id"])
        assert op["p"]["actionPayload"] == {
            "taskId": t["id"],
            "deadlineDay": "2030-06-01",
        }
        task = _task(sample, t["id"])
        assert task["deadlineDay"] == "2030-06-01"
        assert task["deadlineWithTime"] is None

    def test_deadline_with_time_xor(self, sample, b, add_task_entity):
        t = add_task_entity(title="dl2")
        mut.set_deadline(sample, b, t["id"], deadline_day="2030-06-01")
        mut.set_deadline(
            sample, b, t["id"], deadline_with_time=1900000000000,
            deadline_remind_at=1899999000000,
        )
        p = _last_op(b)["p"]["actionPayload"]
        assert p["deadlineWithTime"] == 1900000000000
        assert p["deadlineRemindAt"] == 1899999000000
        assert "deadlineDay" not in p
        task = _task(sample, t["id"])
        assert task["deadlineDay"] is None
        assert task["deadlineWithTime"] == 1900000000000

    def test_deadline_requires_exactly_one(self, sample, b, add_task_entity):
        t = add_task_entity(title="dl3")
        with pytest.raises(mut.MutationError):
            mut.set_deadline(sample, b, t["id"])


class TestTrackTime:
    def test_track(self, sample, b, add_task_entity):
        t = add_task_entity(title="tracked")
        mut.track_time(sample, b, t["id"], "2026-09-09", 1800000)
        mut.track_time(sample, b, t["id"], "2026-09-09", 600000)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("KT", "UPD", "TASK", t["id"])
        expected = {"taskId": t["id"], "date": "2026-09-09", "duration": 600000}
        assert op["p"]["actionPayload"] == expected
        assert op["p"]["entityChanges"] == [
            {
                "entityType": "TASK",
                "entityId": t["id"],
                "opType": "UPD",
                "changes": expected,
            }
        ]
        task = _task(sample, t["id"])
        assert task["timeSpentOnDay"]["2026-09-09"] == 2400000
        assert task["timeSpent"] == 2400000
        assert_doctor_clean(sample)

    def test_track_rejects_negative(self, sample, b, add_task_entity):
        t = add_task_entity(title="neg")
        with pytest.raises(mut.MutationError):
            mut.track_time(sample, b, t["id"], "2026-09-09", -5)


class TestRepeat:
    def test_repeat_add(self, sample, b, add_task_entity):
        t = add_task_entity(title="daily thing")
        mut.repeat_add(
            sample, b, t["id"], "R" * 21, repeat_cycle="DAILY",
            start_time="09:00", remind_at="AtStart",
        )
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "RA", "CRT", "TASK_REPEAT_CFG", "R" * 21,
        )
        p = op["p"]["actionPayload"]
        assert p["taskId"] == t["id"]
        assert p["startTime"] == "09:00"
        assert p["remindAt"] == "AtStart"
        cfg = p["taskRepeatCfg"]
        assert cfg["repeatCycle"] == "DAILY"
        assert cfg["quickSetting"] == "DAILY"
        assert cfg["repeatEvery"] == 1
        assert cfg["isPaused"] is False
        assert cfg["tagIds"] == []
        assert cfg["order"] == 0
        assert cfg["skipOverdue"] is False
        assert cfg["lastTaskCreationDay"] == today_str()
        assert cfg["title"] == "daily thing"
        assert "notes" not in cfg
        assert cfg["monday"] is True and cfg["saturday"] is False

        state = sample["state"]
        assert "R" * 21 in state["taskRepeatCfg"]["ids"]
        assert _task(sample, t["id"])["repeatCfgId"] == "R" * 21
        assert_doctor_clean(sample)

    def test_repeat_weekly_days(self, sample, b, add_task_entity):
        t = add_task_entity(title="weekly")
        mut.repeat_add(
            sample, b, t["id"], "W" * 21, repeat_cycle="WEEKLY",
            days=["monday", "thursday"],
        )
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["W" * 21]
        assert cfg["monday"] is True
        assert cfg["thursday"] is True
        assert cfg["tuesday"] is False
        assert cfg["quickSetting"] == "CUSTOM"


class TestProjectsTags:
    def test_project_add(self, sample, b):
        proj = make_project("P" * 21, "Work", "#ff0000")
        mut.project_add(sample, b, proj)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("PA", "CRT", "PROJECT", "P" * 21)
        payload_project = op["p"]["actionPayload"]["project"]
        assert payload_project["theme"]["primary"] == "#ff0000"
        assert payload_project["advancedCfg"]["worklogExportSettings"]["groupBy"] == "DATE"
        assert "P" * 21 in sample["state"]["project"]["ids"]
        assert_doctor_clean(sample)

    def test_project_update(self, sample, b):
        proj = make_project("P" * 21, "Work")
        mut.project_add(sample, b, proj)
        mut.project_update(sample, b, "P" * 21, {"title": "Renamed"})
        op = _last_op(b)
        assert op["p"]["actionPayload"] == {
            "project": {"id": "P" * 21, "changes": {"title": "Renamed"}}
        }
        assert sample["state"]["project"]["entities"]["P" * 21]["title"] == "Renamed"

    def test_tag_add_update(self, sample, b):
        tag = make_tag("G" * 21, "urgentish", "#00ff00")
        mut.tag_add(sample, b, tag)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("GA", "CRT", "TAG", "G" * 21)
        assert op["p"]["actionPayload"]["tag"]["color"] == "#00ff00"
        mut.tag_update(sample, b, "G" * 21, {"title": "renamed"})
        op = _last_op(b)
        assert op["p"]["actionPayload"] == {
            "tag": {"id": "G" * 21, "changes": {"title": "renamed"}}
        }
        assert_doctor_clean(sample)


class TestTagTask:
    def test_hgt_add(self, sample, b, add_task_entity):
        mut.tag_add(sample, b, make_tag("G" * 21, "t1"))
        t = add_task_entity(title="taggee")
        assert mut.add_tag_to_task(sample, b, t["id"], "G" * 21) is True
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("HGT", "UPD", "TASK", t["id"])
        assert op["ds"] == [t["id"], "G" * 21]
        assert op["p"]["actionPayload"] == {"tagId": "G" * 21, "taskId": t["id"]}
        assert "G" * 21 in _task(sample, t["id"])["tagIds"]
        assert t["id"] in sample["state"]["tag"]["entities"]["G" * 21]["taskIds"]
        assert_doctor_clean(sample)

    def test_idempotent_add_emits_no_op(self, sample, b, add_task_entity):
        mut.tag_add(sample, b, make_tag("G" * 21, "t1"))
        t = add_task_entity(title="taggee")
        mut.add_tag_to_task(sample, b, t["id"], "G" * 21)
        n_ops = len(b.ops)
        assert mut.add_tag_to_task(sample, b, t["id"], "G" * 21) is False
        assert len(b.ops) == n_ops
        assert _task(sample, t["id"])["tagIds"].count("G" * 21) == 1

    def test_tag_task_add_and_remove(self, sample, b, add_task_entity):
        mut.tag_add(sample, b, make_tag("1" * 21, "one"))
        mut.tag_add(sample, b, make_tag("2" * 21, "two"))
        t = add_task_entity(title="both")
        mut.add_tag_to_task(sample, b, t["id"], "1" * 21)
        n_ops = len(b.ops)
        mut.tag_task(sample, b, t["id"], add=["2" * 21], remove=["1" * 21])
        new_ops = b.ops[n_ops:]
        assert [o["a"] for o in new_ops] == ["HGT", "HU"]
        assert new_ops[1]["p"]["actionPayload"]["task"]["changes"]["tagIds"] == ["2" * 21]
        assert _task(sample, t["id"])["tagIds"] == ["2" * 21]
        assert t["id"] not in sample["state"]["tag"]["entities"]["1" * 21]["taskIds"]
        assert_doctor_clean(sample)


class TestArchive:
    def test_archive_done(self, sample, b, add_task_entity):
        done = add_task_entity(title="done one")
        sub = make_task("S" * 21, "done sub", "INBOX_PROJECT")
        mut.add_subtask(sample, b, done["id"], sub)
        mut.complete_task(sample, b, done["id"])
        keep = add_task_entity(title="keep me")

        archived = mut.archive_done(sample, b)
        assert archived == [done["id"]]
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("HX", "UPD", "TASK", done["id"])
        assert op["ds"] == [done["id"]]
        snap = op["p"]["actionPayload"]["tasks"][0]
        assert snap["id"] == done["id"]
        assert [s["id"] for s in snap["subTasks"]] == ["S" * 21]

        state = sample["state"]
        assert done["id"] not in state["task"]["entities"]
        assert "S" * 21 not in state["task"]["entities"]
        assert keep["id"] in state["task"]["entities"]
        arch = sample["archiveYoung"]["task"]
        assert done["id"] in arch["ids"]
        assert "S" * 21 in arch["ids"]
        # flattened: archived entities carry no 'subTasks' key at all
        assert "subTasks" not in arch["entities"][done["id"]]
        assert "subTasks" not in arch["entities"]["S" * 21]
        assert arch["entities"][done["id"]]["subTaskIds"] == ["S" * 21]
        assert arch["entities"]["S" * 21]["title"] == "done sub"
        assert "timeTracking" in sample["archiveYoung"]
        assert_doctor_clean(sample)

    def test_archive_nothing_done_emits_no_op(self, sample, b):
        n_ops = len(b.ops)
        assert mut.archive_done(sample, b) == []
        assert len(b.ops) == n_ops


class TestNotes:
    @staticmethod
    def _note_reg(d):
        return d["state"]["note"]

    @staticmethod
    def _project(d, pid="INBOX_PROJECT"):
        return d["state"]["project"]["entities"][pid]

    def test_add_basic(self, sample, b):
        note = make_note("N" * 21, "hello note")
        mut.note_add(sample, b, note)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("NA", "CRT", "NOTE", "N" * 21)
        p = op["p"]["actionPayload"]
        assert set(p) == {"note"}
        assert p["note"]["content"] == "hello note"
        assert p["note"]["projectId"] is None
        assert p["note"]["isPinnedToToday"] is False
        assert op["p"]["entityChanges"] == []

        reg = self._note_reg(sample)
        assert reg["ids"] == ["N" * 21]
        assert reg["entities"]["N" * 21]["content"] == "hello note"
        assert reg["todayOrder"] == []
        assert_doctor_clean(sample)

    def test_add_prepends_ids(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "first"))
        mut.note_add(sample, b, make_note("2" * 21, "second"))
        assert self._note_reg(sample)["ids"] == ["2" * 21, "1" * 21]
        assert_doctor_clean(sample)

    def test_add_pinned_prepends_today_order(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        mut.note_add(sample, b, make_note("2" * 21, "b", is_pinned_to_today=True))
        assert self._note_reg(sample)["todayOrder"] == ["2" * 21, "1" * 21]
        assert_doctor_clean(sample)

    def test_add_with_project_prepends_note_ids(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", project_id="INBOX_PROJECT"))
        mut.note_add(sample, b, make_note("2" * 21, "b", project_id="INBOX_PROJECT"))
        assert self._project(sample)["noteIds"] == ["2" * 21, "1" * 21]
        assert_doctor_clean(sample)

    def test_add_unknown_project_raises(self, sample, b):
        with pytest.raises(mut.MutationError):
            mut.note_add(sample, b, make_note("N" * 21, "x", project_id="nope"))

    def test_add_duplicate_id_raises(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "a"))
        with pytest.raises(mut.MutationError):
            mut.note_add(sample, b, make_note("N" * 21, "b"))

    def test_update_content_patch(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "old"))
        mut.note_update(sample, b, "N" * 21, {"content": "new"})
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("NU", "UPD", "NOTE", "N" * 21)
        changes = op["p"]["actionPayload"]["note"]["changes"]
        assert changes["content"] == "new"
        assert "modified" in changes
        assert "isPinnedToToday" not in changes
        assert self._note_reg(sample)["entities"]["N" * 21]["content"] == "new"
        assert self._note_reg(sample)["todayOrder"] == []
        assert_doctor_clean(sample)

    def test_update_content_leaves_today_order_untouched(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        mut.note_add(sample, b, make_note("2" * 21, "b", is_pinned_to_today=True))
        mut.note_update(sample, b, "1" * 21, {"content": "edited"})
        assert self._note_reg(sample)["todayOrder"] == ["2" * 21, "1" * 21]
        assert_doctor_clean(sample)

    def test_update_pin_prepends(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        mut.note_add(sample, b, make_note("2" * 21, "b"))
        mut.note_update(sample, b, "2" * 21, {"isPinnedToToday": True})
        assert self._note_reg(sample)["todayOrder"] == ["2" * 21, "1" * 21]
        assert_doctor_clean(sample)

    def test_update_unpin_filters(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        mut.note_update(sample, b, "1" * 21, {"isPinnedToToday": False})
        assert self._note_reg(sample)["todayOrder"] == []
        assert self._note_reg(sample)["entities"]["1" * 21]["isPinnedToToday"] is False
        assert_doctor_clean(sample)

    def test_update_pin_twice_does_not_duplicate(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        n_ops = len(b.ops)
        mut.note_update(sample, b, "1" * 21, {"isPinnedToToday": True})
        # A no-op pin must not reach the wire at all: SP's reducer prepends
        # without dedupe, so the phone would end up with the id twice.
        assert len(b.ops) == n_ops
        assert self._note_reg(sample)["todayOrder"] == ["1" * 21]
        assert_doctor_clean(sample)

    def test_update_no_op_unpin_emits_nothing(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a"))
        n_ops = len(b.ops)
        mut.note_update(sample, b, "1" * 21, {"isPinnedToToday": False})
        assert len(b.ops) == n_ops
        assert self._note_reg(sample)["todayOrder"] == []

    def test_update_drops_redundant_pin_but_keeps_other_changes(self, sample, b):
        mut.note_add(sample, b, make_note("1" * 21, "a", is_pinned_to_today=True))
        mut.note_update(
            sample, b, "1" * 21, {"isPinnedToToday": True, "content": "b"}
        )
        changes = _last_op(b)["p"]["actionPayload"]["note"]["changes"]
        assert "isPinnedToToday" not in changes
        assert changes["content"] == "b"
        assert self._note_reg(sample)["todayOrder"] == ["1" * 21]
        assert_doctor_clean(sample)

    def test_update_missing_note_raises(self, sample, b):
        with pytest.raises(mut.MutationError):
            mut.note_update(sample, b, "nope", {"content": "x"})

    def test_delete_payload_and_lists(self, sample, b):
        mut.note_add(
            sample,
            b,
            make_note(
                "N" * 21, "bye", project_id="INBOX_PROJECT", is_pinned_to_today=True
            ),
        )
        mut.note_delete(sample, b, "N" * 21)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("ND", "DEL", "NOTE", "N" * 21)
        p = op["p"]["actionPayload"]
        assert p == {
            "id": "N" * 21,
            "projectId": "INBOX_PROJECT",
            "isPinnedToToday": True,
        }
        reg = self._note_reg(sample)
        assert reg["ids"] == []
        assert reg["entities"] == {}
        assert reg["todayOrder"] == []
        assert self._project(sample)["noteIds"] == []
        assert_doctor_clean(sample)

    def test_delete_payload_reflects_current_state(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "x"))
        mut.note_update(sample, b, "N" * 21, {"isPinnedToToday": True})
        mut.note_move(sample, b, "N" * 21, "INBOX_PROJECT")
        mut.note_delete(sample, b, "N" * 21)
        p = _last_op(b)["p"]["actionPayload"]
        assert p["projectId"] == "INBOX_PROJECT"
        assert p["isPinnedToToday"] is True
        assert_doctor_clean(sample)

    def test_delete_coerces_dangling_project_id_to_null(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "x", project_id="INBOX_PROJECT"))
        # Simulate a note whose project vanished (SP's ND replay would
        # dereference the missing project unguarded).
        sample["state"]["note"]["entities"]["N" * 21]["projectId"] = "GONE"
        sample["state"]["project"]["entities"]["INBOX_PROJECT"]["noteIds"] = []
        mut.note_delete(sample, b, "N" * 21)
        assert _last_op(b)["p"]["actionPayload"]["projectId"] is None
        assert sample["state"]["note"]["ids"] == []

    def test_move_with_dangling_source_project_refuses(self, sample, b):
        other = make_project("P" * 21, "Other")
        mut.project_add(sample, b, other)
        mut.note_add(sample, b, make_note("N" * 21, "x"))
        sample["state"]["note"]["entities"]["N" * 21]["projectId"] = "GONE"
        n_ops = len(b.ops)
        with pytest.raises(mut.MutationError, match="doctor"):
            mut.note_move(sample, b, "N" * 21, "P" * 21)
        assert len(b.ops) == n_ops

    def test_move_payload_carries_old_project(self, sample, b):
        other = make_project("P" * 21, "Other")
        mut.project_add(sample, b, other)
        mut.note_add(sample, b, make_note("N" * 21, "x", project_id="INBOX_PROJECT"))
        mut.note_move(sample, b, "N" * 21, "P" * 21)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("NM", "UPD", "NOTE", "N" * 21)
        p = op["p"]["actionPayload"]
        assert p["note"]["projectId"] == "INBOX_PROJECT"  # OLD project id
        assert p["targetProjectId"] == "P" * 21
        assert self._project(sample)["noteIds"] == []
        assert self._project(sample, "P" * 21)["noteIds"] == ["N" * 21]
        assert self._note_reg(sample)["entities"]["N" * 21]["projectId"] == "P" * 21
        assert_doctor_clean(sample)

    def test_move_appends_to_target(self, sample, b):
        other = make_project("P" * 21, "Other")
        mut.project_add(sample, b, other)
        mut.note_add(sample, b, make_note("1" * 21, "a", project_id="P" * 21))
        mut.note_add(sample, b, make_note("2" * 21, "b"))
        mut.note_move(sample, b, "2" * 21, "P" * 21)
        assert self._project(sample, "P" * 21)["noteIds"] == ["1" * 21, "2" * 21]
        assert_doctor_clean(sample)

    def test_move_to_same_project_raises(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "x", project_id="INBOX_PROJECT"))
        n_ops = len(b.ops)
        with pytest.raises(mut.MutationError):
            mut.note_move(sample, b, "N" * 21, "INBOX_PROJECT")
        assert len(b.ops) == n_ops

    def test_move_unknown_project_raises(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "x"))
        with pytest.raises(mut.MutationError):
            mut.note_move(sample, b, "N" * 21, "nope")

    def test_full_lifecycle_keeps_state_clean(self, sample, b):
        mut.note_add(sample, b, make_note("N" * 21, "draft", is_pinned_to_today=True))
        mut.note_update(sample, b, "N" * 21, {"content": "draft\nmore"})
        mut.note_move(sample, b, "N" * 21, "INBOX_PROJECT")
        mut.note_update(sample, b, "N" * 21, {"isPinnedToToday": False})
        mut.note_delete(sample, b, "N" * 21)
        assert [o["a"] for o in b.ops] == ["NA", "NU", "NM", "NU", "ND"]
        assert sample["state"]["note"]["ids"] == []
        assert_doctor_clean(sample)


class TestBoards:
    @staticmethod
    def _cfgs(d):
        return d["state"]["boards"]["boardCfgs"]

    @staticmethod
    def _board(d, board_id):
        return next(b for b in d["state"]["boards"]["boardCfgs"] if b["id"] == board_id)

    def test_add_board(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint", cols=3))
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("BA", "CRT", "BOARD", "B" * 21)
        p = op["p"]["actionPayload"]
        assert set(p) == {"board"}
        assert p["board"] == {"id": "B" * 21, "title": "Sprint", "cols": 3, "panels": []}
        assert op["p"]["entityChanges"] == []
        # boardCfgs is a plain ARRAY, appended to (no ids/entities registry).
        assert isinstance(self._cfgs(sample), list)
        assert self._cfgs(sample)[-1]["id"] == "B" * 21
        assert_doctor_clean(sample)

    def test_add_duplicate_board_rejected(self, sample, b):
        with pytest.raises(mut.MutationError):
            mut.board_add(sample, b, make_board("EISENHOWER_MATRIX", "dup"))

    def test_update_board(self, sample, b):
        mut.board_update(sample, b, "KANBAN_DEFAULT", {"title": "Kanban", "cols": 4})
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "BU",
            "UPD",
            "BOARD",
            "KANBAN_DEFAULT",
        )
        assert op["p"]["actionPayload"] == {
            "id": "KANBAN_DEFAULT",
            "updates": {"title": "Kanban", "cols": 4},
        }
        board = self._board(sample, "KANBAN_DEFAULT")
        assert (board["title"], board["cols"]) == ("Kanban", 4)
        assert board["panels"]  # shallow merge leaves panels alone
        assert_doctor_clean(sample)

    def test_update_unknown_board_raises(self, sample, b):
        with pytest.raises(mut.MutationError):
            mut.board_update(sample, b, "nope", {"title": "x"})

    def test_delete_board(self, sample, b):
        mut.board_delete(sample, b, "KANBAN_DEFAULT")
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "BD",
            "DEL",
            "BOARD",
            "KANBAN_DEFAULT",
        )
        assert op["p"]["actionPayload"] == {"id": "KANBAN_DEFAULT"}
        assert [c["id"] for c in self._cfgs(sample)] == ["EISENHOWER_MATRIX"]
        assert_doctor_clean(sample)

    def test_boards_state_key_created_when_missing(self, sample, b):
        del sample["state"]["boards"]
        mut.board_add(sample, b, make_board("B" * 21, "fresh"))
        assert sample["state"]["boards"]["boardCfgs"][0]["id"] == "B" * 21

    # ------------------------------------------------------------ panels

    def test_panel_add_emits_bu_with_full_panels_array(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        mut.panel_add(sample, b, "B" * 21, make_panel("P" * 21, "Doing"))
        op = _last_op(b)
        # Panels are ONLY editable through BU (BP is a dead reducer).
        assert (op["a"], op["o"], op["e"], op["d"]) == ("BU", "UPD", "BOARD", "B" * 21)
        panels = op["p"]["actionPayload"]["updates"]["panels"]
        assert len(panels) == 1
        assert panels[0] == {
            "id": "P" * 21,
            "title": "Doing",
            "taskIds": [],
            "includedTagIds": [],
            "excludedTagIds": [],
            "taskDoneState": 1,
            "scheduledState": 1,
            "backlogState": 1,
            "isParentTasksOnly": False,
            "projectIds": [""],
        }
        assert self._board(sample, "B" * 21)["panels"] == panels
        assert_doctor_clean(sample)

    def test_panel_add_keeps_existing_panels(self, sample, b):
        before = [p["id"] for p in self._board(sample, "KANBAN_DEFAULT")["panels"]]
        mut.panel_add(sample, b, "KANBAN_DEFAULT", make_panel("P" * 21, "Extra"))
        panels = _last_op(b)["p"]["actionPayload"]["updates"]["panels"]
        assert [p["id"] for p in panels] == before + ["P" * 21]

    def test_panel_add_enum_values_are_numbers(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        panel = make_panel(
            "P" * 21,
            "Filtered",
            taskDoneState=2,
            scheduledState=3,
            backlogState=3,
            isParentTasksOnly=True,
            sortBy="dueDate",
            sortDir="desc",
        )
        mut.panel_add(sample, b, "B" * 21, panel)
        written = _last_op(b)["p"]["actionPayload"]["updates"]["panels"][0]
        assert written["taskDoneState"] == 2
        assert written["scheduledState"] == 3
        assert written["backlogState"] == 3
        assert written["isParentTasksOnly"] is True
        assert (written["sortBy"], written["sortDir"]) == ("dueDate", "desc")

    def test_panel_sanitization_on_write(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        dirty = {
            "id": "P" * 21,
            "title": "Dirty",
            "projectId": "LEGACY",  # legacy key
            "sortByDue": True,  # legacy key
            "projectIds": ["", "INBOX_PROJECT"],  # '' is exclusive
            "sortBy": "nonsense",
            "sortDir": None,
            "includedTagsMatch": None,
            "taskDoneState": None,
            "isParentTasksOnly": None,
        }
        mut.panel_add(sample, b, "B" * 21, dirty)
        written = _last_op(b)["p"]["actionPayload"]["updates"]["panels"][0]
        assert written["projectIds"] == [""]
        assert "projectId" not in written and "sortByDue" not in written
        assert "sortBy" not in written and "sortDir" not in written
        assert "includedTagsMatch" not in written
        assert written["taskDoneState"] == 1
        assert written["isParentTasksOnly"] is False

    def test_legacy_sibling_panel_survives_a_panel_add(self, sample, b):
        """A whole-array BU must MIGRATE the untouched siblings, not gut them."""
        board = self._board(sample, "KANBAN_DEFAULT")
        board["panels"][0].pop("projectIds")
        board["panels"][0]["projectId"] = "WORK"
        board["panels"][1]["sortByDue"] = "desc"
        mut.panel_add(sample, b, "KANBAN_DEFAULT", make_panel("P" * 21, "Extra"))
        panels = _last_op(b)["p"]["actionPayload"]["updates"]["panels"]
        assert panels[0]["projectIds"] == ["WORK"]
        assert "projectId" not in panels[0]
        assert (panels[1]["sortBy"], panels[1]["sortDir"]) == ("dueDate", "desc")
        assert "sortByDue" not in panels[1]
        assert panels[-1]["id"] == "P" * 21
        assert_doctor_clean(sample)

    def test_legacy_project_id_loses_to_a_real_project_ids(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        mut.panel_add(
            sample,
            b,
            "B" * 21,
            {
                "id": "P" * 21,
                "title": "P",
                "projectId": "LEGACY",
                "projectIds": ["INBOX_PROJECT"],
            },
        )
        written = _last_op(b)["p"]["actionPayload"]["updates"]["panels"][0]
        assert written["projectIds"] == ["INBOX_PROJECT"]

    def test_unknown_future_keys_are_preserved(self, sample, b):
        board = self._board(sample, "KANBAN_DEFAULT")
        board["panels"][0]["someFutureFlag"] = {"nested": [1, 2]}
        mut.panel_update(sample, b, "IN_PROGRESS", {"title": "Doing"})
        panels = _last_op(b)["p"]["actionPayload"]["updates"]["panels"]
        assert panels[0]["someFutureFlag"] == {"nested": [1, 2]}

    def test_panel_without_id_rejected(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        with pytest.raises(mut.MutationError, match="panel id must be"):
            mut.panel_add(sample, b, "B" * 21, {"title": "no id"})

    def test_panel_project_ids_deduped(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        mut.panel_add(
            sample,
            b,
            "B" * 21,
            make_panel("P" * 21, "P", projectIds=["INBOX_PROJECT", "INBOX_PROJECT"]),
        )
        written = _last_op(b)["p"]["actionPayload"]["updates"]["panels"][0]
        assert written["projectIds"] == ["INBOX_PROJECT"]

    def test_panel_today_tag_rejected(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        with pytest.raises(mut.MutationError, match="TODAY"):
            mut.panel_add(
                sample, b, "B" * 21, make_panel("P" * 21, "P", includedTagIds=["TODAY"])
            )

    def test_panel_id_must_be_globally_unique(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        with pytest.raises(mut.MutationError, match="already used"):
            mut.panel_add(sample, b, "B" * 21, make_panel("TODO", "clash"))

    def test_panel_update_rewrites_panels(self, sample, b):
        mut.panel_update(sample, b, "TODO", {"title": "To do!"})
        op = _last_op(b)
        assert op["a"] == "BU" and op["d"] == "KANBAN_DEFAULT"
        panels = op["p"]["actionPayload"]["updates"]["panels"]
        assert panels[0]["title"] == "To do!"
        assert len(panels) == len(self._board(sample, "KANBAN_DEFAULT")["panels"])
        assert self._board(sample, "KANBAN_DEFAULT")["panels"][0]["title"] == "To do!"

    def test_panel_remove(self, sample, b):
        before = [p["id"] for p in self._board(sample, "KANBAN_DEFAULT")["panels"]]
        mut.panel_remove(sample, b, "TODO")
        panels = _last_op(b)["p"]["actionPayload"]["updates"]["panels"]
        assert [p["id"] for p in panels] == [i for i in before if i != "TODO"]
        assert_doctor_clean(sample)

    def test_panel_unknown_id_raises(self, sample, b):
        with pytest.raises(mut.MutationError, match="panel not found"):
            mut.panel_remove(sample, b, "ghost")

    # ------------------------------------------------------------ BT / BS

    def test_panel_task_order(self, sample, b, add_task_entity):
        t1 = add_task_entity(title="one")["id"]
        t2 = add_task_entity(title="two")["id"]
        mut.panel_task_order(sample, b, "TODO", [t2, t1])
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == ("BT", "UPD", "BOARD", "TODO")
        assert op["p"]["actionPayload"] == {"panelId": "TODO", "taskIds": [t2, t1]}
        board = self._board(sample, "KANBAN_DEFAULT")
        assert board["panels"][0]["taskIds"] == [t2, t1]
        # Only that panel's taskIds change.
        assert all(p["taskIds"] == [] for p in board["panels"][1:])
        assert_doctor_clean(sample)

    def test_panel_task_order_omits_ds(self, sample, b, add_task_entity):
        tid = add_task_entity()["id"]
        mut.panel_task_order(sample, b, "TODO", [tid])
        assert "ds" not in _last_op(b)

    def test_panel_task_order_on_sorted_panel_rejected(
        self, sample, b, add_task_entity
    ):
        tid = add_task_entity()["id"]
        mut.panel_update(sample, b, "TODO", {"sortBy": "title", "sortDir": "asc"})
        with pytest.raises(mut.MutationError, match="sorted panels ignore"):
            mut.panel_task_order(sample, b, "TODO", [tid])

    def test_panel_task_order_after_sort_manual(self, sample, b, add_task_entity):
        tid = add_task_entity()["id"]
        mut.panel_update(sample, b, "TODO", {"sortBy": "title", "sortDir": "asc"})
        mut.panel_update(sample, b, "TODO", {"sortBy": None, "sortDir": None})
        panel = self._board(sample, "KANBAN_DEFAULT")["panels"][0]
        assert "sortBy" not in panel and "sortDir" not in panel
        mut.panel_task_order(sample, b, "TODO", [tid])
        assert _last_op(b)["a"] == "BT"

    def test_panel_task_order_unknown_task(self, sample, b):
        with pytest.raises(mut.MutationError, match="task not found"):
            mut.panel_task_order(sample, b, "TODO", ["ghost"])

    def test_boards_sort(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Third"))
        mut.boards_sort(sample, b, ["KANBAN_DEFAULT"])
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"]) == ("BS", "MOV", "BOARD")
        assert op["d"] == "KANBAN_DEFAULT"
        assert op["ds"] == ["KANBAN_DEFAULT"]
        assert op["p"]["actionPayload"] == {"ids": ["KANBAN_DEFAULT"]}
        # Unlisted boards keep their tail position, in their old order.
        assert [c["id"] for c in self._cfgs(sample)] == [
            "KANBAN_DEFAULT",
            "EISENHOWER_MATRIX",
            "B" * 21,
        ]

    def test_boards_sort_unknown_id(self, sample, b):
        with pytest.raises(mut.MutationError, match="board not found"):
            mut.boards_sort(sample, b, ["ghost"])

    def test_boards_sort_duplicate_ids(self, sample, b):
        with pytest.raises(mut.MutationError, match="duplicate"):
            mut.boards_sort(sample, b, ["KANBAN_DEFAULT", "KANBAN_DEFAULT"])

    def test_boards_sort_empty_list(self, sample, b):
        with pytest.raises(mut.MutationError, match="no board ids"):
            mut.boards_sort(sample, b, [])

    def test_boards_sort_tolerates_id_less_entry(self, sample, b):
        self._cfgs(sample).append({"title": "junk from a foreign writer"})
        mut.boards_sort(sample, b, ["KANBAN_DEFAULT"])
        ids = [c.get("id") for c in self._cfgs(sample)]
        assert ids == ["KANBAN_DEFAULT", "EISENHOWER_MATRIX", None]

    def test_non_bulk_board_ops_omit_ds(self, sample, b):
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        mut.panel_add(sample, b, "B" * 21, make_panel("P" * 21, "Doing"))
        mut.board_update(sample, b, "B" * 21, {"cols": 3})
        mut.board_delete(sample, b, "B" * 21)
        assert [o["a"] for o in b.ops] == ["BA", "BU", "BU", "BD"]
        assert all("ds" not in o for o in b.ops)

    def test_bp_is_never_emitted(self, sample, b, add_task_entity):
        tid = add_task_entity()["id"]
        mut.board_add(sample, b, make_board("B" * 21, "Sprint"))
        mut.panel_add(sample, b, "B" * 21, make_panel("P" * 21, "Doing"))
        mut.panel_update(sample, b, "P" * 21, {"title": "Doing!"})
        mut.panel_task_order(sample, b, "P" * 21, [tid])
        mut.panel_remove(sample, b, "P" * 21)
        mut.board_update(sample, b, "B" * 21, {"cols": 3})
        mut.boards_sort(sample, b, ["B" * 21])
        mut.board_delete(sample, b, "B" * 21)
        actions = [op["a"] for op in b.ops]
        assert "BP" not in actions
        assert set(actions) <= {"BA", "BU", "BD", "BT", "BS"}
        assert all(op["e"] == "BOARD" for op in b.ops)


class TestSimpleCounters:
    @staticmethod
    def _reg(d):
        return d["state"]["simpleCounter"]

    @staticmethod
    def _counter(d, cid):
        return d["state"]["simpleCounter"]["entities"][cid]

    def test_add_full_default_object(self, sample, b):
        counter = make_simple_counter("C" * 21, "Pushups")
        mut.counter_add(sample, b, counter)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "SA",
            "CRT",
            "SIMPLE_COUNTER",
            "C" * 21,
        )
        assert op["p"]["actionPayload"] == {
            "simpleCounter": {
                "id": "C" * 21,
                "title": "Pushups",
                "isEnabled": True,
                "icon": None,
                "type": "ClickCounter",
                "countOnDay": {},
                "isOn": False,
                "isTrackStreaks": True,
                "streakMinValue": 1,
                "streakMode": "specific-days",
                "streakWeekDays": {
                    "0": False,
                    "1": True,
                    "2": True,
                    "3": True,
                    "4": True,
                    "5": True,
                    "6": False,
                },
            }
        }
        assert op["p"]["entityChanges"] == []
        assert self._reg(sample)["ids"][-1] == "C" * 21
        assert_doctor_clean(sample)

    def test_add_forces_is_on_false(self, sample, b):
        counter = make_simple_counter("C" * 21, "x")
        counter["isOn"] = True
        mut.counter_add(sample, b, counter)
        payload = _last_op(b)["p"]["actionPayload"]["simpleCounter"]
        assert payload["isOn"] is False
        assert self._counter(sample, "C" * 21)["isOn"] is False

    def test_add_countdown_carries_duration(self, sample, b):
        counter = make_simple_counter(
            "C" * 21,
            "Stretch",
            counter_type="RepeatedCountdownReminder",
            countdown_duration=1800000,
        )
        mut.counter_add(sample, b, counter)
        payload = _last_op(b)["p"]["actionPayload"]["simpleCounter"]
        assert payload["countdownDuration"] == 1800000

    def test_add_duplicate_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="already exists"):
            mut.counter_add(sample, b, make_simple_counter("COFFEE_COUNTER", "dup"))

    def test_update(self, sample, b):
        mut.counter_update(
            sample, b, "COFFEE_COUNTER", {"title": "Coffee", "isEnabled": True}
        )
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "SU",
            "UPD",
            "SIMPLE_COUNTER",
            "COFFEE_COUNTER",
        )
        assert op["p"]["actionPayload"] == {
            "simpleCounter": {
                "id": "COFFEE_COUNTER",
                "changes": {"title": "Coffee", "isEnabled": True},
            }
        }
        counter = self._counter(sample, "COFFEE_COUNTER")
        assert (counter["title"], counter["isEnabled"]) == ("Coffee", True)
        assert_doctor_clean(sample)

    def test_update_is_on_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="device-local"):
            mut.counter_update(sample, b, "COFFEE_COUNTER", {"isOn": True})
        assert b.ops == []

    def test_update_count_on_day_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="countOnDay"):
            mut.counter_update(sample, b, "COFFEE_COUNTER", {"countOnDay": {}})

    def test_update_unknown_raises(self, sample, b):
        with pytest.raises(mut.MutationError, match="counter not found"):
            mut.counter_update(sample, b, "nope", {"title": "x"})

    def test_delete(self, sample, b):
        mut.counter_delete(sample, b, "COFFEE_COUNTER")
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "SD",
            "DEL",
            "SIMPLE_COUNTER",
            "COFFEE_COUNTER",
        )
        assert op["p"]["actionPayload"] == {"id": "COFFEE_COUNTER"}
        assert "COFFEE_COUNTER" not in self._reg(sample)["ids"]
        assert "COFFEE_COUNTER" not in self._reg(sample)["entities"]
        assert_doctor_clean(sample)

    def test_set_today_emits_st(self, sample, b):
        today = today_str()
        mut.counter_set(sample, b, "COFFEE_COUNTER", 3)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "ST",
            "UPD",
            "SIMPLE_COUNTER",
            "COFFEE_COUNTER",
        )
        assert op["p"]["actionPayload"] == {
            "id": "COFFEE_COUNTER",
            "newVal": 3,
            "today": today,
        }
        assert self._counter(sample, "COFFEE_COUNTER")["countOnDay"] == {today: 3}

    def test_set_other_date_emits_sfd(self, sample, b):
        mut.counter_set(sample, b, "COFFEE_COUNTER", 5, "2020-01-02")
        op = _last_op(b)
        assert op["a"] == "SFD"
        assert op["p"]["actionPayload"] == {
            "id": "COFFEE_COUNTER",
            "date": "2020-01-02",
            "newVal": 5,
        }
        assert self._counter(sample, "COFFEE_COUNTER")["countOnDay"]["2020-01-02"] == 5

    def test_set_clamps_to_zero(self, sample, b):
        mut.counter_set(sample, b, "COFFEE_COUNTER", -7)
        assert _last_op(b)["p"]["actionPayload"]["newVal"] == 0
        assert self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today_str()] == 0

    def test_set_rejects_non_integer(self, sample, b):
        with pytest.raises(mut.MutationError, match="integer"):
            mut.counter_set(sample, b, "COFFEE_COUNTER", 1.5)

    def test_inc_emits_absolute_value(self, sample, b):
        today = today_str()
        self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today] = 4
        assert mut.counter_inc(sample, b, "COFFEE_COUNTER", 2) == 6
        op = _last_op(b)
        assert op["a"] == "ST"
        assert op["p"]["actionPayload"]["newVal"] == 6
        assert self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today] == 6

    def test_inc_from_missing_day_starts_at_zero(self, sample, b):
        assert mut.counter_inc(sample, b, "COFFEE_COUNTER", 1, "2020-03-04") == 1
        op = _last_op(b)
        assert op["a"] == "SFD"
        assert op["p"]["actionPayload"]["newVal"] == 1

    def test_inc_negative_clamps_at_zero(self, sample, b):
        today = today_str()
        self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today] = 1
        assert mut.counter_inc(sample, b, "COFFEE_COUNTER", -5) == 0
        assert _last_op(b)["p"]["actionPayload"]["newVal"] == 0

    def test_log_time_emits_sc_delta(self, sample, b):
        mut.counter_log_time(sample, b, "STANDING_DESK_ID", "2024-05-01", 60000)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "SC",
            "UPD",
            "SIMPLE_COUNTER",
            "STANDING_DESK_ID",
        )
        assert op["p"]["actionPayload"] == {
            "id": "STANDING_DESK_ID",
            "date": "2024-05-01",
            "duration": 60000,
        }
        assert self._counter(sample, "STANDING_DESK_ID")["countOnDay"] == {
            "2024-05-01": 60000
        }

    def test_log_time_accumulates_locally(self, sample, b):
        mut.counter_log_time(sample, b, "STANDING_DESK_ID", "2024-05-01", 60000)
        total = mut.counter_log_time(sample, b, "STANDING_DESK_ID", "2024-05-01", 30000)
        assert total == 90000
        # each op still carries only its own DELTA
        assert [op["p"]["actionPayload"]["duration"] for op in b.ops] == [60000, 30000]
        assert (
            self._counter(sample, "STANDING_DESK_ID")["countOnDay"]["2024-05-01"] == 90000
        )

    def test_log_time_negative_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="non-negative"):
            mut.counter_log_time(sample, b, "STANDING_DESK_ID", "2024-05-01", -1)
        assert b.ops == []

    def test_log_time_on_non_stopwatch_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="not a StopWatch"):
            mut.counter_log_time(sample, b, "COFFEE_COUNTER", "2024-05-01", 60000)
        assert b.ops == []

    @pytest.mark.parametrize("date", ["2024-13-01", "20240501", "today", "", None])
    def test_log_time_invalid_date_rejected(self, sample, b, date):
        with pytest.raises(mut.MutationError):
            mut.counter_log_time(sample, b, "STANDING_DESK_ID", date, 60000)
        assert b.ops == []

    def test_order_emits_sm(self, sample, b):
        ids = ["COFFEE_COUNTER", "STRETCHING_COUNTER", "STANDING_DESK_ID"]
        mut.counter_order(sample, b, ids)
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"]) == ("SM", "MOV", "SIMPLE_COUNTER")
        assert op["d"] == "COFFEE_COUNTER"
        assert op["ds"] == ids
        assert op["p"]["actionPayload"] == {"ids": ids}
        assert self._reg(sample)["ids"] == ids
        assert_doctor_clean(sample)

    def test_order_partial_list_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="permutation"):
            mut.counter_order(sample, b, ["COFFEE_COUNTER"])

    def test_order_empty_list_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="no counter ids"):
            mut.counter_order(sample, b, [])
        assert b.ops == []

    def test_order_duplicates_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="duplicate"):
            mut.counter_order(sample, b, ["COFFEE_COUNTER", "COFFEE_COUNTER"])

    def test_only_persistent_actions_are_emitted(self, sample, b):
        mut.counter_add(sample, b, make_simple_counter("C" * 21, "New"))
        mut.counter_update(sample, b, "C" * 21, {"title": "New!"})
        mut.counter_set(sample, b, "C" * 21, 2)
        mut.counter_inc(sample, b, "C" * 21, 1)
        mut.counter_set(sample, b, "C" * 21, 1, "2020-01-01")
        mut.counter_log_time(sample, b, "STANDING_DESK_ID", today_str(), 1000)
        mut.counter_order(
            sample,
            b,
            ["C" * 21, "COFFEE_COUNTER", "STRETCHING_COUNTER", "STANDING_DESK_ID"],
        )
        mut.counter_delete(sample, b, "C" * 21)
        actions = [op["a"] for op in b.ops]
        assert set(actions) <= {"SA", "SU", "SD", "ST", "SFD", "SM", "SC"}
        for forbidden in ("SI", "SX", "SG", "SO", "SF", "SN", "SUS", "SDM", "SUA"):
            assert forbidden not in actions
        assert all(op["e"] == "SIMPLE_COUNTER" for op in b.ops)


class TestMetrics:
    """Metric id IS the day; EU is self-creating, EL is additive."""

    def _reg(self, d):
        return d["state"]["metric"]

    def test_set_on_missing_day_creates_entity_with_defaults(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"impactOfWork": 3})
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "EU",
            "UPD",
            "METRIC",
            "2026-09-09",
        )
        assert op["p"]["actionPayload"] == {
            "metric": {"id": "2026-09-09", "changes": {"impactOfWork": 3}}
        }
        metric = self._reg(sample)["entities"]["2026-09-09"]
        assert metric == {
            "id": "2026-09-09",
            "focusSessions": [],
            "remindTomorrow": False,
            "reflections": [],
            "impactOfWork": 3,
        }
        assert self._reg(sample)["ids"] == ["2026-09-09"]
        assert_doctor_clean(sample)

    def test_set_on_existing_day_patches(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"impactOfWork": 3})
        mut.metric_update(sample, b, "2026-09-09", {"energyCheckin": 2})
        metric = self._reg(sample)["entities"]["2026-09-09"]
        assert metric["impactOfWork"] == 3 and metric["energyCheckin"] == 2
        assert self._reg(sample)["ids"] == ["2026-09-09"]  # not re-added
        assert_doctor_clean(sample)

    def test_empty_changes_emit_nothing(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {})
        assert b.ops == []
        assert self._reg(sample)["entities"] == {}

    def test_id_in_changes_is_ignored(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"id": "nope", "notes": "x"})
        changes = _last_op(b)["p"]["actionPayload"]["metric"]["changes"]
        assert changes == {"notes": "x"}

    def test_reflections_append_keeps_previous(self, sample, b):
        mut.metric_update(
            sample, b, "2026-09-09", {"reflections": [{"text": "a", "created": 1}]}
        )
        current = self._reg(sample)["entities"]["2026-09-09"]["reflections"]
        mut.metric_update(
            sample,
            b,
            "2026-09-09",
            {"reflections": list(current) + [{"text": "b", "created": 2}]},
        )
        texts = [
            r["text"] for r in self._reg(sample)["entities"]["2026-09-09"]["reflections"]
        ]
        assert texts == ["a", "b"]
        assert_doctor_clean(sample)

    @pytest.mark.parametrize(
        "field", ["mood", "productivity", "obstructions", "improvements"]
    )
    def test_dead_fields_rejected(self, sample, b, field):
        with pytest.raises(mut.MutationError, match="no longer exists"):
            mut.metric_update(sample, b, "2026-09-09", {field: 5})
        assert b.ops == []

    def test_unknown_field_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="unknown field"):
            mut.metric_update(sample, b, "2026-09-09", {"whatever": 1})

    def test_focus_sessions_cannot_be_patched(self, sample, b):
        with pytest.raises(mut.MutationError, match="metric_log_focus"):
            mut.metric_update(sample, b, "2026-09-09", {"focusSessions": [1000]})

    @pytest.mark.parametrize(
        "changes,match",
        [
            ({"impactOfWork": 0}, "1-4"),
            ({"impactOfWork": 5}, "1-4"),
            ({"energyCheckin": 4}, "1-3"),
            ({"completedTasks": -1}, ">= 0"),
            ({"notes": 5}, "notes"),
            ({"remindTomorrow": "yes"}, "boolean"),
            ({"reflections": "hi"}, "array"),
            ({"reflections": [{"created": 1}]}, "text"),
        ],
    )
    def test_invalid_values_rejected(self, sample, b, changes, match):
        with pytest.raises(mut.MutationError, match=match):
            mut.metric_update(sample, b, "2026-09-09", changes)
        assert b.ops == []

    def test_null_ratings_allowed(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"impactOfWork": None})
        assert self._reg(sample)["entities"]["2026-09-09"]["impactOfWork"] is None

    @pytest.mark.parametrize("day", ["2026-9-9", "09-09-2026", "today", ""])
    def test_invalid_day_rejected(self, sample, b, day):
        with pytest.raises(mut.MutationError, match="metric set"):
            mut.metric_update(sample, b, day, {"impactOfWork": 1})

    def test_focus_appends_and_self_creates(self, sample, b):
        assert mut.metric_log_focus(sample, b, "2026-09-09", 1500000) == 1
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "EL",
            "UPD",
            "METRIC",
            "2026-09-09",
        )
        assert op["p"]["actionPayload"] == {"day": "2026-09-09", "duration": 1500000}
        assert mut.metric_log_focus(sample, b, "2026-09-09", 600000) == 2
        metric = self._reg(sample)["entities"]["2026-09-09"]
        assert metric["focusSessions"] == [1500000, 600000]
        assert self._reg(sample)["ids"] == ["2026-09-09"]
        assert_doctor_clean(sample)

    @pytest.mark.parametrize("duration", [0, -1, 1.5, True])
    def test_focus_non_positive_rejected(self, sample, b, duration):
        with pytest.raises(mut.MutationError, match="positive"):
            mut.metric_log_focus(sample, b, "2026-09-09", duration)
        assert b.ops == []

    def test_delete(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"impactOfWork": 2})
        mut.metric_delete(sample, b, "2026-09-09")
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "ED",
            "DEL",
            "METRIC",
            "2026-09-09",
        )
        assert op["p"]["actionPayload"] == {"id": "2026-09-09"}
        assert self._reg(sample)["ids"] == []
        assert self._reg(sample)["entities"] == {}
        assert_doctor_clean(sample)

    def test_delete_missing_day_rejected(self, sample, b):
        with pytest.raises(mut.MutationError, match="not found"):
            mut.metric_delete(sample, b, "2026-09-09")

    def test_only_safe_actions_are_emitted(self, sample, b):
        mut.metric_update(sample, b, "2026-09-09", {"impactOfWork": 4})
        mut.metric_log_focus(sample, b, "2026-09-09", 1000)
        mut.metric_delete(sample, b, "2026-09-09")
        actions = [op["a"] for op in b.ops]
        assert actions == ["EU", "EL", "ED"]
        # EX is a FULL REPLACE (a partial payload wipes fields) — never emitted.
        assert "EX" not in actions and "EA" not in actions
        assert all(op["e"] == "METRIC" for op in b.ops)


class TestIssueProviders:
    @staticmethod
    def _reg(d):
        return d["state"]["issueProvider"]

    @staticmethod
    def _ical(pid="P" * 21, **overrides):
        return make_issue_provider(pid, "ICAL", **overrides)

    @staticmethod
    def _archive(d, key, task):
        blob = d.setdefault(key, {"task": {"ids": [], "entities": {}}})
        reg = blob.setdefault("task", {"ids": [], "entities": {}})
        reg.setdefault("ids", []).append(task["id"])
        reg.setdefault("entities", {})[task["id"]] = task
        return task

    @staticmethod
    def _linked_task(tid, provider_id):
        task = make_task(tid, "cal event", "INBOX_PROJECT")
        task.update(
            {
                "issueId": "ev1",
                "issueProviderId": provider_id,
                "issueType": "ICAL",
                "issueWasUpdated": False,
                "issueLastUpdated": 1,
                "issueAttachmentNr": 0,
                "issueTimeTracked": 0,
                "issuePoints": None,
            }
        )
        return task

    def test_add_emits_full_cfg(self, sample, b):
        provider = self._ical(icalUrl="https://cal/x.ics")
        assert mut.provider_add(sample, b, provider) == "P" * 21
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "IA",
            "CRT",
            "ISSUE_PROVIDER",
            "P" * 21,
        )
        payload = op["p"]["actionPayload"]["issueProvider"]
        # Full cfg: COMMON + the ICAL defaults, flattened on the object.
        assert set(payload) == {
            "id",
            "issueProviderKey",
            "isEnabled",
            "isAutoPoll",
            "isAutoAddToBacklog",
            "isIntegratedAddTaskBar",
            "defaultProjectId",
            "pinnedSearch",
            "pollingMode",
            "defaultTagIds",
            "defaultNote",
            "icalUrl",
            "isAutoImportForCurrentDay",
            "isReferenceCalendar",
            "checkUpdatesEvery",
            "showBannerBeforeThreshold",
            "isDisabledForWebApp",
            "filterIncludeRegex",
            "filterExcludeRegex",
        }
        assert payload["isEnabled"] is True
        assert payload["checkUpdatesEvery"] == 7200000
        assert payload["showBannerBeforeThreshold"] == 7200000
        assert self._reg(sample)["ids"] == ["P" * 21]
        assert_doctor_clean(sample)

    def test_add_caldav_full_cfg(self, sample, b):
        provider = make_issue_provider(
            "C" * 21,
            "CALDAV",
            caldavUrl="https://dav/",
            resourceName="cal",
            username="u",
            password="pw",
        )
        mut.provider_add(sample, b, provider)
        payload = _last_op(b)["p"]["actionPayload"]["issueProvider"]
        assert payload["issueProviderKey"] == "CALDAV"
        assert payload["password"] == "pw"
        assert payload["categoryFilter"] is None
        assert payload["twoWaySync"] == {
            "isDone": "pullOnly",
            "title": "pullOnly",
            "notes": "off",
        }

    def test_add_duplicate_rejected(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        with pytest.raises(mut.MutationError, match="already exists"):
            mut.provider_add(sample, b, self._ical())

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="unsupported issue provider key"):
            make_issue_provider("X" * 21, "JIRA")

    def test_update_emits_iu(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        mut.provider_update(sample, b, "P" * 21, {"isEnabled": False})
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "IU",
            "UPD",
            "ISSUE_PROVIDER",
            "P" * 21,
        )
        assert op["p"]["actionPayload"] == {
            "issueProvider": {"id": "P" * 21, "changes": {"isEnabled": False}}
        }
        assert self._reg(sample)["entities"]["P" * 21]["isEnabled"] is False

    def test_update_key_is_immutable(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        with pytest.raises(mut.MutationError, match="immutable"):
            mut.provider_update(sample, b, "P" * 21, {"issueProviderKey": "CALDAV"})

    def test_update_missing_provider(self, sample, b):
        with pytest.raises(mut.MutationError, match="not found"):
            mut.provider_update(sample, b, "nope", {"isEnabled": True})

    def test_order_listed_first_tail_kept(self, sample, b):
        for pid in ("A" * 21, "B" * 21, "C" * 21):
            mut.provider_add(sample, b, self._ical(pid))
        mut.provider_order(sample, b, ["C" * 21])
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"]) == ("IS", "MOV", "ISSUE_PROVIDER")
        assert op["d"] == "C" * 21
        assert op["ds"] == ["C" * 21]
        assert op["p"]["actionPayload"] == {"ids": ["C" * 21]}
        assert self._reg(sample)["ids"] == ["C" * 21, "A" * 21, "B" * 21]
        assert_doctor_clean(sample)

    def test_order_rejects_duplicates_and_unknown(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        with pytest.raises(mut.MutationError, match="duplicate"):
            mut.provider_order(sample, b, ["P" * 21, "P" * 21])
        with pytest.raises(mut.MutationError, match="not found"):
            mut.provider_order(sample, b, ["Z" * 21])
        with pytest.raises(mut.MutationError, match="no provider ids"):
            mut.provider_order(sample, b, [])

    def test_delete_unlinks_live_and_archived_tasks(self, sample, b):
        pid = "P" * 21
        mut.provider_add(sample, b, self._ical(pid))
        live = self._linked_task("L" * 21, pid)
        sample["state"]["task"]["ids"].append(live["id"])
        sample["state"]["task"]["entities"][live["id"]] = live
        sample["state"]["project"]["entities"]["INBOX_PROJECT"]["taskIds"].append(
            live["id"]
        )
        young = self._archive(
            sample, "archiveYoung", self._linked_task("Y" * 21, pid)
        )
        old = self._archive(sample, "archiveOld", self._linked_task("O" * 21, pid))
        other = self._archive(
            sample, "archiveOld", self._linked_task("N" * 21, "other-provider")
        )

        unlinked = mut.provider_delete(sample, b, pid)
        assert sorted(unlinked) == sorted([live["id"], young["id"], old["id"]])
        op = _last_op(b)
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "HID",
            "DEL",
            "ISSUE_PROVIDER",
            pid,
        )
        assert op["p"]["actionPayload"]["issueProviderId"] == pid
        assert sorted(op["p"]["actionPayload"]["taskIdsToUnlink"]) == sorted(unlinked)

        fields = (
            "issueId",
            "issueProviderId",
            "issueType",
            "issueWasUpdated",
            "issueLastUpdated",
            "issueAttachmentNr",
            "issueTimeTracked",
            "issuePoints",
        )
        for task in (live, young, old):
            assert not [f for f in fields if f in task]
        # A task of a different provider keeps every field.
        assert all(f in other for f in fields)
        assert self._reg(sample)["ids"] == []
        assert self._reg(sample)["entities"] == {}
        assert_doctor_clean(sample)

    def test_delete_without_linked_tasks(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        assert mut.provider_delete(sample, b, "P" * 21) == []
        assert _last_op(b)["p"]["actionPayload"]["taskIdsToUnlink"] == []

    def test_delete_missing_provider(self, sample, b):
        with pytest.raises(mut.MutationError, match="not found"):
            mut.provider_delete(sample, b, "nope")

    def test_only_safe_actions_are_emitted(self, sample, b):
        mut.provider_add(sample, b, self._ical())
        mut.provider_update(sample, b, "P" * 21, {"isEnabled": False})
        mut.provider_order(sample, b, ["P" * 21])
        mut.provider_delete(sample, b, "P" * 21)
        assert [op["a"] for op in b.ops] == ["IA", "IU", "IS", "HID"]
        assert all(op["e"] == "ISSUE_PROVIDER" for op in b.ops)
