import datetime

import pytest

from sp_cli import queries as q
from sp_cli.model import make_issue_provider, make_task, today_str
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

    @staticmethod
    def _seed_provider(d, pid="P" * 21, key="ICAL", **overrides):
        provider = make_issue_provider(pid, key, **overrides)
        reg = d["state"].setdefault("issueProvider", {"ids": [], "entities": {}})
        reg.setdefault("ids", []).append(pid)
        reg.setdefault("entities", {})[pid] = provider
        return provider

    def test_dangling_issue_provider_id_on_live_task(self, sample, add_task_entity):
        t = add_task_entity(task_id="D" * 21)
        t["issueProviderId"] = "gone"
        assert any(
            "issueProviderId 'gone' does not exist" in p for p in q.doctor(sample)
        )
        self._seed_provider(sample, "gone")
        assert q.doctor(sample) == []

    def test_dangling_issue_provider_id_in_archives(self, sample):
        for key, holder in (("archiveYoung", sample), ("archiveOld", sample["state"])):
            task = make_task(key[7] * 21, "archived", "INBOX_PROJECT")
            task["issueProviderId"] = "gone"
            holder[key] = {"task": {"ids": [task["id"]], "entities": {task["id"]: task}}}
        problems = [p for p in q.doctor(sample) if "issueProviderId" in p]
        assert len(problems) == 2

    def test_incomplete_builtin_provider_cfg(self, sample):
        provider = self._seed_provider(sample)
        del provider["icalUrl"]
        del provider["pollingMode"]
        problems = [p for p in q.doctor(sample) if "cfg is incomplete" in p]
        assert len(problems) == 1
        assert "icalUrl" in problems[0] and "pollingMode" in problems[0]

    def test_non_builtin_provider_cfg_not_checked(self, sample):
        reg = sample["state"].setdefault("issueProvider", {"ids": [], "entities": {}})
        reg["ids"].append("J" * 21)
        reg["entities"]["J" * 21] = {
            "id": "J" * 21,
            "issueProviderKey": "JIRA",
            "isEnabled": True,
        }
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

    def test_list_notes_by_project_uses_project_note_ids_order(self, sample):
        _add_note(sample, "1" * 21, "a", project_id="INBOX_PROJECT")
        _add_note(sample, "2" * 21, "b", project_id="INBOX_PROJECT")
        inbox = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        # The app's manual order differs from note.ids order.
        inbox["noteIds"] = ["1" * 21, "2" * 21]
        assert [n["id"] for n in q.list_notes(sample, project="inbox")] == [
            "1" * 21,
            "2" * 21,
        ]

    def test_list_notes_by_project_appends_notes_missing_from_note_ids(self, sample):
        _add_note(sample, "1" * 21, "a", project_id="INBOX_PROJECT")
        _add_note(sample, "2" * 21, "b", project_id="INBOX_PROJECT")
        inbox = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        inbox["noteIds"] = ["2" * 21]  # "1" missing from the app's list
        assert [n["id"] for n in q.list_notes(sample, project="inbox")] == [
            "2" * 21,
            "1" * 21,
        ]

    def test_doctor_detects_duplicate_project_note_ids(self, sample):
        _add_note(sample, "N" * 21, "a", project_id="INBOX_PROJECT")
        sample["state"]["project"]["entities"]["INBOX_PROJECT"]["noteIds"].append(
            "N" * 21
        )
        assert any("noteIds has duplicate ids" in p for p in q.doctor(sample))

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

    def _panel(self, sample):
        return sample["state"]["boards"]["boardCfgs"][0]["panels"][0]

    def test_doctor_accepts_absent_or_empty_project_ids(self, sample):
        """Absent/empty projectIds means 'All Projects' — not a problem."""
        panel = self._panel(sample)
        del panel["projectIds"]
        assert q.doctor(sample) == []
        panel["projectIds"] = []
        assert q.doctor(sample) == []

    def test_doctor_detects_non_list_project_ids(self, sample):
        self._panel(sample)["projectIds"] = "WORK"
        assert any("projectIds must be an array" in p for p in q.doctor(sample))

    @pytest.mark.parametrize("legacy", ["projectId", "sortByDue"])
    def test_doctor_detects_leftover_legacy_keys(self, sample, legacy):
        self._panel(sample)[legacy] = "asc"
        assert any(f"leftover legacy key '{legacy}'" in p for p in q.doctor(sample))

    def test_doctor_detects_invalid_sort_by(self, sample):
        self._panel(sample)["sortBy"] = "nonsense"
        assert any("invalid sortBy" in p for p in q.doctor(sample))

    @pytest.mark.parametrize(
        "key", ["sortDir", "includedTagsMatch", "excludedTagsMatch"]
    )
    def test_doctor_detects_null_optional_keys(self, sample, key):
        self._panel(sample)[key] = None
        assert any(f"{key} is null" in p for p in q.doctor(sample))

    @pytest.mark.parametrize(
        "key", ["taskDoneState", "scheduledState", "backlogState"]
    )
    def test_doctor_detects_non_numeric_enums(self, sample, key):
        self._panel(sample)[key] = "all"
        assert any(f"{key} = 'all' is not a number" in p for p in q.doctor(sample))

    def test_doctor_detects_missing_task_ids(self, sample):
        del self._panel(sample)["taskIds"]
        assert any("taskIds must be an array" in p for p in q.doctor(sample))


class TestCounters:
    def test_all_counters_in_state_order(self, sample):
        assert [c["id"] for c in q.all_counters(sample)] == [
            "STANDING_DESK_ID",
            "COFFEE_COUNTER",
            "STRETCHING_COUNTER",
        ]

    def test_resolve_by_id_title_and_prefix(self, sample):
        assert q.resolve_counter(sample, "COFFEE_COUNTER") == "COFFEE_COUNTER"
        assert q.resolve_counter(sample, "coffee counter") == "COFFEE_COUNTER"
        assert q.resolve_counter(sample, "STAND") == "STANDING_DESK_ID"

    def test_resolve_unknown_raises(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_counter(sample, "zzz")

    def test_resolve_ambiguous_prefix_raises(self, sample):
        with pytest.raises(q.AmbiguousIdError):
            q.resolve_counter(sample, "ST")

    def test_counter_value_defaults_to_zero(self, sample):
        counter = sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]
        assert q.counter_value(counter) == 0
        counter["countOnDay"][today_str()] = 4
        assert q.counter_value(counter) == 4
        assert q.counter_value(counter, "1999-01-01") == 0

    def test_doctor_detects_is_on(self, sample):
        sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]["isOn"] = True
        assert any("isOn is true" in p for p in q.doctor(sample))

    def test_doctor_detects_negative_count(self, sample):
        counter = sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]
        counter["countOnDay"]["2024-01-01"] = -3
        assert any("countOnDay" in p for p in q.doctor(sample))

    def test_doctor_rejects_bool_count(self, sample):
        counter = sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]
        counter["countOnDay"]["2024-01-01"] = True
        assert any("countOnDay" in p for p in q.doctor(sample))

    def test_doctor_detects_invalid_type(self, sample):
        sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]["type"] = "Nope"
        assert any("invalid type" in p for p in q.doctor(sample))

    def test_doctor_detects_missing_countdown_duration(self, sample):
        del sample["state"]["simpleCounter"]["entities"]["STRETCHING_COUNTER"][
            "countdownDuration"
        ]
        assert any("without countdownDuration" in p for p in q.doctor(sample))

    def test_doctor_detects_stray_countdown_duration(self, sample):
        counter = sample["state"]["simpleCounter"]["entities"]["COFFEE_COUNTER"]
        counter["countdownDuration"] = 1800000
        assert any(
            "countdownDuration on a non-countdown" in p for p in q.doctor(sample)
        )

    def test_doctor_detects_registry_desync(self, sample):
        sample["state"]["simpleCounter"]["ids"].remove("COFFEE_COUNTER")
        assert any("COFFEE_COUNTER" in p for p in q.doctor(sample))


def _add_metric(d, day, **fields):
    metric = {
        "id": day,
        "focusSessions": [],
        "remindTomorrow": False,
        "reflections": [],
        **fields,
    }
    reg = d["state"].setdefault("metric", {"ids": [], "entities": {}})
    reg["ids"].append(day)
    reg["entities"][day] = metric
    return metric


class TestMetricsQueries:
    def test_all_metrics_sorted_by_day(self, sample):
        _add_metric(sample, "2026-09-09")
        _add_metric(sample, "2026-01-02")
        assert [m["id"] for m in q.all_metrics(sample)] == [
            "2026-01-02",
            "2026-09-09",
        ]

    def test_missing_slice_is_empty(self, sample):
        sample["state"].pop("metric", None)
        assert q.all_metrics(sample) == []

    def test_range_filter_is_inclusive(self, sample):
        for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
            _add_metric(sample, day)
        days = [m["id"] for m in q.list_metrics(sample, "2026-01-02", "2026-01-03")]
        assert days == ["2026-01-02", "2026-01-03"]

    def test_focus_sessions_count_and_total(self, sample):
        metric = _add_metric(sample, "2026-01-01", focusSessions=[1000, 2000])
        assert q.focus_sessions(metric) == (2, 3000)
        assert q.focus_sessions(_add_metric(sample, "2026-01-02")) == (0, 0)

    def test_doctor_flags_bad_day_id(self, sample):
        _add_metric(sample, "not-a-day")
        assert any("must be a 'YYYY-MM-DD' day" in p for p in q.doctor(sample))

    def test_doctor_flags_ids_entities_desync(self, sample):
        _add_metric(sample, "2026-01-01")
        sample["state"]["metric"]["ids"] = []
        assert any("not listed in ids" in p for p in q.doctor(sample))

    def test_doctor_flags_removed_fields(self, sample):
        _add_metric(sample, "2026-01-01", mood=5)
        assert any("leftover field 'mood'" in p for p in q.doctor(sample))

    def test_doctor_clean_on_good_metric(self, sample):
        _add_metric(sample, "2026-01-01", impactOfWork=3, focusSessions=[1000])
        assert q.doctor(sample) == []


def _add_provider(d, pid, key="ICAL", **overrides):
    provider = make_issue_provider(pid, key, **overrides)
    reg = d["state"]["issueProvider"]
    reg["ids"].append(pid)
    reg["entities"][pid] = provider
    return provider


class TestIssueProviderQueries:
    def test_all_providers_follows_ids_order(self, sample):
        _add_provider(sample, "A" * 21)
        _add_provider(sample, "B" * 21)
        sample["state"]["issueProvider"]["ids"] = ["B" * 21, "A" * 21]
        assert [p["id"] for p in q.all_providers(sample)] == ["B" * 21, "A" * 21]

    def test_provider_url_per_key(self, sample):
        ical = _add_provider(sample, "A" * 21, icalUrl="https://c/x.ics")
        dav = _add_provider(sample, "B" * 21, key="CALDAV", caldavUrl="https://dav/")
        assert q.provider_url(ical) == "https://c/x.ics"
        assert q.provider_url(dav) == "https://dav/"
        assert q.provider_url({"issueProviderKey": "plugin:foo"}) == ""

    def test_resolve_by_id_prefix_and_url(self, sample):
        _add_provider(sample, "A" * 21, icalUrl="https://c/x.ics")
        assert q.resolve_provider(sample, "A" * 21) == "A" * 21
        assert q.resolve_provider(sample, "AAA") == "A" * 21
        assert q.resolve_provider(sample, "https://C/x.ics") == "A" * 21

    def test_resolve_ambiguous_prefix(self, sample):
        _add_provider(sample, "AB" + "x" * 19)
        _add_provider(sample, "AC" + "x" * 19)
        with pytest.raises(q.AmbiguousIdError):
            q.resolve_provider(sample, "A")

    def test_resolve_missing(self, sample):
        with pytest.raises(q.NotFoundError):
            q.resolve_provider(sample, "nope")

    def test_tasks_of_provider_spans_archives(self, sample):
        pid = "P" * 21
        _add_provider(sample, pid)
        live = make_task("L" * 21, "live", "INBOX_PROJECT")
        live["issueProviderId"] = pid
        sample["state"]["task"]["ids"].append(live["id"])
        sample["state"]["task"]["entities"][live["id"]] = live
        for key, tid in (("archiveYoung", "Y" * 21), ("archiveOld", "O" * 21)):
            task = make_task(tid, "arch", "INBOX_PROJECT")
            task["issueProviderId"] = pid
            reg = sample["state"][key]["task"]
            reg["ids"].append(tid)
            reg["entities"][tid] = task
        unrelated = make_task("U" * 21, "other", "INBOX_PROJECT")
        unrelated["issueProviderId"] = "other"
        sample["state"]["task"]["ids"].append(unrelated["id"])
        sample["state"]["task"]["entities"][unrelated["id"]] = unrelated

        assert sorted(q.tasks_of_provider(sample, pid)) == sorted(
            ["L" * 21, "Y" * 21, "O" * 21]
        )

    def test_doctor_checks_issue_provider_registry(self, sample):
        _add_provider(sample, "A" * 21)
        assert q.doctor(sample) == []
        sample["state"]["issueProvider"]["ids"] = []
        assert any("issueProvider" in p for p in q.doctor(sample))


class TestBacklogQueries:
    def _seed(self, d, add_task_entity, n=2):
        project = d["state"]["project"]["entities"]["INBOX_PROJECT"]
        project["isEnableBacklog"] = True
        ids = []
        for i in range(n):
            task = add_task_entity(title=f"b{i}")
            project["taskIds"].remove(task["id"])
            project.setdefault("backlogTaskIds", []).append(task["id"])
            ids.append(task["id"])
        return project, ids

    def test_backlog_list_follows_backlog_order(self, sample, add_task_entity):
        project, ids = self._seed(sample, add_task_entity)
        project["backlogTaskIds"] = list(reversed(ids))
        assert [t["id"] for t in q.backlog_list(sample, "INBOX_PROJECT")] == list(
            reversed(ids)
        )

    def test_backlog_list_empty(self, sample):
        assert q.backlog_list(sample, "INBOX_PROJECT") == []

    def test_backlog_list_unknown_project(self, sample):
        with pytest.raises(q.NotFoundError):
            q.backlog_list(sample, "NOPE")

    def test_doctor_flags_a_task_in_both_lists(self, sample, add_task_entity):
        project, ids = self._seed(sample, add_task_entity, n=1)
        project["taskIds"].append(ids[0])
        assert any("both taskIds and backlogTaskIds" in p for p in q.doctor(sample))

    def test_doctor_flags_a_subtask_in_the_backlog(self, sample, add_task_entity):
        project = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        parent = add_task_entity(title="parent")
        sub = add_task_entity(title="sub", parent_id=parent["id"])
        parent["subTaskIds"].append(sub["id"])
        project.setdefault("backlogTaskIds", []).append(sub["id"])
        assert any(
            "listed in project.backlogTaskIds" in p for p in q.doctor(sample)
        )

    def test_doctor_clean_on_a_healthy_backlog(self, sample, add_task_entity):
        self._seed(sample, add_task_entity)
        assert q.doctor(sample) == []


class TestArchivedQueries:
    @staticmethod
    def _archive(d, key, task, under_state=False):
        target = d["state"] if under_state else d
        blob = target.setdefault(key, {"task": {"ids": [], "entities": {}}})
        reg = blob.setdefault("task", {"ids": [], "entities": {}})
        reg.setdefault("ids", []).append(task["id"])
        reg.setdefault("entities", {})[task["id"]] = task
        return task

    @classmethod
    def _archived_task(cls, d, key, tid, title="archived", **fields):
        task = make_task(tid, title, "INBOX_PROJECT")
        task["isDone"] = True
        task["doneOn"] = 1_700_000_000_000
        task.update(fields)
        return cls._archive(d, key, task)

    def test_lists_both_blobs_with_age(self, sample):
        self._archived_task(sample, "archiveYoung", "Y" * 21, "young one")
        self._archived_task(sample, "archiveOld", "O" * 21, "old one")
        rows = q.archived_tasks(sample)
        assert {r["id"]: r["age"] for r in rows} == {
            "Y" * 21: "young",
            "O" * 21: "old",
        }

    def test_lists_state_level_blobs_too(self, sample):
        self._archived_task(sample, "archiveOld", "S" * 21, "under state")
        sample["archiveOld"] = None
        self._archive(
            sample,
            "archiveOld",
            make_task("S" * 21, "under state", "INBOX_PROJECT"),
            under_state=True,
        )
        assert [t["id"] for t in q.archived_tasks(sample)] == ["S" * 21]

    def test_dedupes_by_id_young_wins(self, sample):
        self._archived_task(sample, "archiveYoung", "D" * 21, "dupe")
        self._archived_task(sample, "archiveOld", "D" * 21, "dupe")
        rows = q.archived_tasks(sample)
        assert len(rows) == 1 and rows[0]["age"] == "young"

    def test_subtasks_hidden_by_default(self, sample):
        self._archived_task(sample, "archiveYoung", "P" * 21, "parent")
        self._archived_task(
            sample, "archiveYoung", "C" * 21, "child", parentId="P" * 21
        )
        assert [t["id"] for t in q.archived_tasks(sample)] == ["P" * 21]
        assert len(q.archived_tasks(sample, include_subtasks=True)) == 2

    def test_search_filters_by_title(self, sample):
        self._archived_task(sample, "archiveYoung", "A" * 21, "Write REPORT")
        self._archived_task(sample, "archiveYoung", "B" * 21, "other")
        assert [t["id"] for t in q.archived_tasks(sample, search="report")] == [
            "A" * 21
        ]

    def test_sorted_newest_done_first(self, sample):
        self._archived_task(sample, "archiveYoung", "A" * 21, "older", doneOn=1000)
        self._archived_task(sample, "archiveYoung", "B" * 21, "newer", doneOn=2000)
        assert [t["id"] for t in q.archived_tasks(sample)] == ["B" * 21, "A" * 21]

    def test_resolve_prefix(self, sample):
        self._archived_task(sample, "archiveOld", "Z" * 21, "old one")
        assert q.resolve_archived_task(sample, "ZZZ") == "Z" * 21
        assert q.resolve_archived_task(sample, "Z" * 21) == "Z" * 21

    def test_resolve_unknown_raises(self, sample):
        with pytest.raises(q.NotFoundError, match="no archived task"):
            q.resolve_archived_task(sample, "nope")

    def test_resolve_ambiguous_within_archive(self, sample):
        self._archived_task(sample, "archiveYoung", "AB" + "x" * 19)
        self._archived_task(sample, "archiveYoung", "AB" + "y" * 19)
        with pytest.raises(q.AmbiguousIdError):
            q.resolve_archived_task(sample, "AB")

    def test_resolve_conflict_with_a_live_task(self, sample, add_task_entity):
        self._archived_task(sample, "archiveYoung", "AB" + "x" * 19)
        add_task_entity(task_id="AB" + "y" * 19, title="live")
        with pytest.raises(q.AmbiguousIdError):
            q.resolve_archived_task(sample, "AB")

    def test_resolve_full_id_wins_over_a_live_prefix_sibling(
        self, sample, add_task_entity
    ):
        self._archived_task(sample, "archiveYoung", "AB" + "x" * 19)
        add_task_entity(task_id="AB" + "y" * 19, title="live")
        assert q.resolve_archived_task(sample, "AB" + "x" * 19) == "AB" + "x" * 19

    def test_archived_task_lookup(self, sample):
        self._archived_task(sample, "archiveOld", "Q" * 21, "look me up")
        assert q.archived_task(sample, "Q" * 21)["title"] == "look me up"
        assert q.archived_task(sample, "nope") is None
