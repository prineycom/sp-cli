import datetime

import pytest

from conftest import assert_doctor_clean
from sp_cli import mutations as mut
from sp_cli.model import make_note, make_project, make_tag, make_task, today_str
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
        mut.note_update(sample, b, "1" * 21, {"isPinnedToToday": True})
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
