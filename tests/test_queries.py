import datetime

import pytest

from sp_cli import queries as q
from sp_cli.model import today_str
from sp_cli.render import format_duration, parse_duration


def _ts_today(hour=12):
    dt = datetime.datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


class TestResolution:
    def test_resolve_task_by_prefix(self, sample):
        full = sample["state"]["task"]["ids"][0]
        assert q.resolve_task(sample, full[:6]) == full
        assert q.resolve_task(sample, full) == full

    def test_resolve_task_not_found(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_task(sample, "zzzzzzzz")

    def test_resolve_task_ambiguous(self, sample, add_task_entity):
        add_task_entity(task_id="prefixAAAAAAAAAAAAAA1")
        add_task_entity(task_id="prefixAAAAAAAAAAAAAA2")
        with pytest.raises(q.AmbiguousIdError) as ei:
            q.resolve_task(sample, "prefix")
        assert len(ei.value.candidates) == 2

    def test_resolve_task_with_leading_dash_stripped(self, sample, add_task_entity):
        add_task_entity(task_id="-dashedAAAAAAAAAAAAAA")
        assert q.resolve_task(sample, "dashed") == "-dashedAAAAAAAAAAAAAA"

    def test_resolve_project_by_title(self, sample):
        assert q.resolve_project(sample, "inbox") == "INBOX_PROJECT"
        assert q.resolve_project(sample, "INBOX_PROJECT") == "INBOX_PROJECT"

    def test_resolve_tag_by_title(self, sample):
        assert q.resolve_tag(sample, "today") == "TODAY"


class TestTodayMembership:
    def test_due_day_today(self, sample, add_task_entity):
        t = add_task_entity(title="a", due_day=today_str())
        assert q.is_today_member(t)

    def test_due_with_time_today_wins(self, sample, add_task_entity):
        t = add_task_entity(title="b", due_with_time=_ts_today())
        assert q.is_today_member(t)

    def test_due_with_time_other_day_overrides_due_day(self, sample):
        task = {"dueDay": today_str(), "dueWithTime": 1800000000000}
        assert not q.is_today_member(task)

    def test_not_member(self, sample, add_task_entity):
        t = add_task_entity(title="c", due_day="2030-01-01")
        assert not q.is_today_member(t)

    def test_today_list_order_and_extras(self, sample, add_task_entity):
        t1 = add_task_entity(title="ordered", due_day=today_str())
        t2 = add_task_entity(title="extra", due_day=today_str())
        # only t1 is in the TODAY ordering
        sample["state"]["tag"]["entities"]["TODAY"]["taskIds"] = [t1["id"]]
        listed = [t["id"] for t in q.today_list(sample)]
        assert listed == [t1["id"], t2["id"]]

    def test_today_list_ignores_stale_order_entries(self, sample, add_task_entity):
        t = add_task_entity(title="not today", due_day="2030-01-01")
        sample["state"]["tag"]["entities"]["TODAY"]["taskIds"] = [t["id"]]
        assert q.today_list(sample) == []


class TestOverdue:
    def test_overdue_due_day(self, sample, add_task_entity):
        t = add_task_entity(title="old", due_day="2020-01-01")
        assert q.is_overdue(t)

    def test_overdue_due_with_time(self, sample, add_task_entity):
        t = add_task_entity(title="old2", due_with_time=1500000000000)
        assert q.is_overdue(t)

    def test_done_never_overdue(self, sample, add_task_entity):
        t = add_task_entity(title="done", due_day="2020-01-01")
        t["isDone"] = True
        assert not q.is_overdue(t)

    def test_today_not_overdue(self, sample, add_task_entity):
        t = add_task_entity(title="today", due_day=today_str())
        assert not q.is_overdue(t)

    def test_scheduled_earlier_today_not_overdue(self, sample, add_task_entity):
        # dueWithTime earlier today: today-member, NOT overdue.
        earlier = int(
            datetime.datetime.now()
            .replace(hour=0, minute=1, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        t = add_task_entity(title="earlier today", due_with_time=earlier)
        assert q.is_today_member(t)
        assert not q.is_overdue(t)

    def test_scheduled_yesterday_overdue(self, sample, add_task_entity):
        yesterday = int(
            (datetime.datetime.now() - datetime.timedelta(days=1))
            .replace(hour=12, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        t = add_task_entity(title="yesterday", due_with_time=yesterday)
        assert q.is_overdue(t)


class TestListFilters:
    def test_default_excludes_done(self, sample, add_task_entity):
        t = add_task_entity(title="donezo")
        t["isDone"] = True
        ids = [x["id"] for x in q.list_tasks(sample)]
        assert t["id"] not in ids
        ids = [x["id"] for x in q.list_tasks(sample, include_done=True)]
        assert t["id"] in ids
        ids = [x["id"] for x in q.list_tasks(sample, only_done=True)]
        assert ids == [t["id"]]

    def test_search_title_and_notes(self, sample, add_task_entity):
        t = add_task_entity(title="Alpha", notes="the SECRET word")
        assert t["id"] in [x["id"] for x in q.list_tasks(sample, search="secret")]
        assert t["id"] in [x["id"] for x in q.list_tasks(sample, search="alp")]
        assert t["id"] not in [x["id"] for x in q.list_tasks(sample, search="nomatch")]

    def test_unscheduled(self, sample, add_task_entity):
        t1 = add_task_entity(title="free")
        t2 = add_task_entity(title="planned", due_day=today_str())
        ids = [x["id"] for x in q.list_tasks(sample, unscheduled=True)]
        assert t1["id"] in ids
        assert t2["id"] not in ids

    def test_parents_only(self, sample, add_task_entity):
        parent = add_task_entity(title="parent")
        sub = add_task_entity(title="sub", parent_id=parent["id"])
        parent["subTaskIds"].append(sub["id"])
        ids = [x["id"] for x in q.list_tasks(sample, parents_only=True)]
        assert sub["id"] not in ids
        assert parent["id"] in ids


class TestAgenda:
    def test_sections(self, sample, add_task_entity):
        over = add_task_entity(title="overdue", due_day="2020-01-01")
        today_t = add_task_entity(title="today", due_day=today_str())
        sched = add_task_entity(title="timed", due_with_time=_ts_today(23))
        dl = add_task_entity(title="deadline")
        dl["deadlineDay"] = today_str()
        far_dl = add_task_entity(title="far deadline")
        far_dl["deadlineDay"] = "2035-01-01"

        a = q.agenda(sample)
        assert over["id"] in [t["id"] for t in a["overdue"]]
        today_ids = [t["id"] for t in a["today"]]
        assert today_t["id"] in today_ids
        assert sched["id"] in [t["id"] for t in a["scheduled_today"]]
        dl_ids = [t["id"] for t in a["deadlines"]]
        assert dl["id"] in dl_ids
        assert far_dl["id"] not in dl_ids  # beyond 7 days


class TestWorklog:
    def test_aggregates_live_and_archive(self, sample, add_task_entity):
        t = add_task_entity(title="live")
        t["timeSpentOnDay"] = {"2026-09-01": 3600000}
        t["timeSpent"] = 3600000
        t["timeEstimate"] = 7200000
        sample["archiveYoung"] = {
            "task": {
                "ids": ["arch1"],
                "entities": {
                    "arch1": {
                        "id": "arch1",
                        "projectId": "INBOX_PROJECT",
                        "timeSpentOnDay": {"2026-09-01": 1800000, "2026-08-01": 600000},
                    }
                },
            },
            "timeTracking": {"project": {}, "tag": {}},
            "lastTimeTrackingFlush": 0,
        }
        log = q.worklog(sample)
        assert log["days"]["2026-09-01"] == 5400000
        assert log["days"]["2026-08-01"] == 600000
        assert log["projects"]["INBOX_PROJECT"] == 6000000
        assert log["estimate"]["estimated"] == 7200000
        assert log["estimate"]["spent"] == 3600000

    def test_date_range_filter(self, sample, add_task_entity):
        t = add_task_entity(title="ranged")
        t["timeSpentOnDay"] = {"2026-09-01": 100, "2026-09-05": 200, "2026-09-09": 300}
        log = q.worklog(sample, date_from="2026-09-02", date_to="2026-09-08")
        assert log["days"] == {"2026-09-05": 200}


class TestDoctor:
    def test_clean_sample(self, sample):
        assert q.doctor(sample) == []

    def test_detects_ids_entities_desync(self, sample):
        sample["state"]["task"]["ids"].append("ghost")
        assert any("ghost" in p for p in q.doctor(sample))

    def test_detects_today_in_tag_ids(self, sample, add_task_entity):
        t = add_task_entity(title="bad")
        t["tagIds"] = ["TODAY"]
        sample["state"]["tag"]["entities"]["TODAY"]["taskIds"].append(t["id"])
        assert any("'TODAY' in tagIds" in p for p in q.doctor(sample))

    def test_detects_due_xor_violation(self, sample, add_task_entity):
        t = add_task_entity(title="bad2")
        t["dueDay"] = "2030-01-01"
        t["dueWithTime"] = 1800000000000
        assert any("both dueDay and dueWithTime" in p for p in q.doctor(sample))

    def test_detects_subtask_in_project(self, sample, add_task_entity):
        parent = add_task_entity(title="p")
        sub = add_task_entity(title="s", parent_id=parent["id"])
        parent["subTaskIds"].append(sub["id"])
        # fixture appends non-parent tasks to project.taskIds only for
        # top-level; force the violation:
        proj = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        if sub["id"] not in proj["taskIds"]:
            proj["taskIds"].append(sub["id"])
        assert any("subtask" in p for p in q.doctor(sample))

    def test_detects_tag_membership_desync(self, sample, add_task_entity):
        t = add_task_entity(title="untagged")
        sample["state"]["tag"]["entities"]["TODAY"]  # TODAY excluded from check
        from sp_cli.model import make_tag

        tag = make_tag("G" * 21, "g")
        tag["taskIds"] = [t["id"]]
        sample["state"]["tag"]["ids"].append("G" * 21)
        sample["state"]["tag"]["entities"]["G" * 21] = tag
        assert any("membership desync" in p for p in q.doctor(sample))


class TestDurations:
    @pytest.mark.parametrize(
        "text,ms",
        [
            ("30m", 1800000),
            ("1h", 3600000),
            ("1.5h", 5400000),
            ("90m", 5400000),
            ("1h30m", 5400000),
            ("90", 5400000),
            ("0m", 0),
        ],
    )
    def test_parse(self, text, ms):
        assert parse_duration(text) == ms

    def test_parse_invalid(self):
        from sp_cli.render import RenderError

        with pytest.raises(RenderError):
            parse_duration("abc")

    @pytest.mark.parametrize(
        "ms,text",
        [(5400000, "1h 30m"), (3600000, "1h"), (1800000, "30m"), (0, "-"), (None, "-")],
    )
    def test_format(self, ms, text):
        assert format_duration(ms) == text


def _add_note(d, note_id, content="n", project_id=None, pinned=False):
    from sp_cli.model import make_note

    note = make_note(note_id, content, project_id=project_id, is_pinned_to_today=pinned)
    reg = d["state"]["note"]
    reg["ids"].insert(0, note_id)
    reg["entities"][note_id] = note
    if pinned:
        reg["todayOrder"].insert(0, note_id)
    if project_id:
        d["state"]["project"]["entities"][project_id]["noteIds"].insert(0, note_id)
    return note


class TestNotes:
    def test_resolve_note_by_prefix(self, sample):
        _add_note(sample, "noteAAAAAAAAAAAAAAAAA")
        assert q.resolve_note(sample, "noteA") == "noteAAAAAAAAAAAAAAAAA"

    def test_resolve_note_not_found(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_note(sample, "zzzz")

    def test_resolve_note_ambiguous(self, sample):
        _add_note(sample, "prefixAAAAAAAAAAAAAA1")
        _add_note(sample, "prefixAAAAAAAAAAAAAA2")
        with pytest.raises(q.AmbiguousIdError):
            q.resolve_note(sample, "prefix")

    def test_list_notes_state_order(self, sample):
        _add_note(sample, "1" * 21, "first")
        _add_note(sample, "2" * 21, "second")
        assert [n["id"] for n in q.list_notes(sample)] == ["2" * 21, "1" * 21]

    def test_list_notes_today_uses_today_order(self, sample):
        _add_note(sample, "1" * 21, "plain")
        _add_note(sample, "2" * 21, "pinned", pinned=True)
        assert [n["id"] for n in q.list_notes(sample, today=True)] == ["2" * 21]

    def test_list_notes_by_project(self, sample):
        _add_note(sample, "1" * 21, "inboxed", project_id="INBOX_PROJECT")
        _add_note(sample, "2" * 21, "loose")
        assert [n["id"] for n in q.list_notes(sample, project="inbox")] == ["1" * 21]

    def test_doctor_detects_dangling_today_order(self, sample):
        sample["state"]["note"]["todayOrder"].append("ghost")
        assert any("todayOrder" in p and "ghost" in p for p in q.doctor(sample))

    def test_doctor_detects_dangling_project_note_ids(self, sample):
        sample["state"]["project"]["entities"]["INBOX_PROJECT"]["noteIds"].append("gh")
        assert any("noteIds references missing note gh" in p for p in q.doctor(sample))

    def test_doctor_detects_ids_entities_desync(self, sample):
        sample["state"]["note"]["ids"].append("ghostnote")
        assert any("ghostnote" in p for p in q.doctor(sample))

    def test_doctor_detects_pin_desync(self, sample):
        _add_note(sample, "N" * 21, "pinned", pinned=True)
        sample["state"]["note"]["todayOrder"] = []
        assert any("not in todayOrder" in p for p in q.doctor(sample))


class TestBoardsQueries:
    def test_all_boards(self, sample):
        assert [b["id"] for b in q.all_boards(sample)] == [
            "EISENHOWER_MATRIX",
            "KANBAN_DEFAULT",
        ]

    def test_all_boards_tolerates_missing_key(self, sample):
        del sample["state"]["boards"]
        assert q.all_boards(sample) == []

    def test_resolve_board_by_id_prefix_and_title(self, sample):
        assert q.resolve_board(sample, "KANBAN_DEFAULT") == "KANBAN_DEFAULT"
        assert q.resolve_board(sample, "KANBAN") == "KANBAN_DEFAULT"
        assert (
            q.resolve_board(sample, "f.boards.default.kanban") == "KANBAN_DEFAULT"
        )

    def test_resolve_board_not_found(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_board(sample, "zzz")

    def test_resolve_panel(self, sample):
        assert q.resolve_panel(sample, "TODO") == "TODO"
        assert q.resolve_panel(sample, "URGENT_AND_IMP") == "URGENT_AND_IMPORTANT"

    def test_resolve_panel_scoped_to_board(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_panel(sample, "TODO", board_id="EISENHOWER_MATRIX")

    def test_doctor_detects_duplicate_panel_id(self, sample):
        cfgs = sample["state"]["boards"]["boardCfgs"]
        cfgs[0]["panels"].append(dict(cfgs[1]["panels"][0]))
        assert any("duplicate panel id" in p for p in q.doctor(sample))

    def test_doctor_detects_mixed_project_ids(self, sample):
        panel = sample["state"]["boards"]["boardCfgs"][0]["panels"][0]
        panel["projectIds"] = ["", "INBOX_PROJECT"]
        assert any("mixes" in p for p in q.doctor(sample))

    def test_doctor_detects_today_in_panel_tags(self, sample):
        panel = sample["state"]["boards"]["boardCfgs"][0]["panels"][0]
        panel["includedTagIds"] = ["TODAY"]
        assert any("'TODAY' in includedTagIds" in p for p in q.doctor(sample))
