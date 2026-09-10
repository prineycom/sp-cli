"""Short-syntax parser: `+project #tag @due !deadline 30m` and `@every ...`."""

import datetime as dt

import pytest

from sp_cli import shortsyntax as ss

# A fixed Thursday noon — every expectation below is relative to it.
NOW = dt.datetime(2026, 9, 10, 12, 0)
TODAY = NOW.date()


def _ms(*, h=0, m=0):
    return int((h * 60 + m) * 60_000)


def _at(day, hour, minute=0):
    return int(dt.datetime.combine(day, dt.time(hour, minute)).timestamp() * 1000)


@pytest.fixture
def d(sample):
    """Sample file + a few projects and tags to match against."""
    state = sample["state"]

    def project(pid, title, **extra):
        state["project"]["ids"].append(pid)
        state["project"]["entities"][pid] = {
            "id": pid,
            "title": title,
            "taskIds": [],
            "backlogTaskIds": [],
            **extra,
        }

    def tag(tid, title):
        state["tag"]["ids"].append(tid)
        state["tag"]["entities"][tid] = {"id": tid, "title": title, "taskIds": []}

    project("P_WORK", "Work")
    project("P_WIP", "Work in progress")
    project("P_SOME", "Some Pro")
    project("P_OLD", "Old stuff", isArchived=True)
    project("P_HID", "Hidden thing", isHiddenFromMenu=True)
    tag("T_HOME", "home")
    tag("T_UP", "Urgent")
    return sample


def parse(title, d, **kw):
    kw.setdefault("now", NOW)
    return ss.parse(title, d, **kw)


class TestStringToMs:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30m", _ms(m=30)),
            ("1h", _ms(h=1)),
            ("1.5h", _ms(h=1, m=30)),
            ("1h 30m", _ms(h=1, m=30)),
            ("90s", 90_000),
            ("2:30", _ms(h=2, m=30)),
            ("8", _ms(h=8)),  # bare, <= 8 -> hours
            ("1", _ms(h=1)),
            ("0.5", _ms(m=30)),  # fractional -> hours
            ("45", _ms(m=45)),  # bare integer > 8 -> minutes
            ("90", _ms(h=1, m=30)),
            ("", 0),
            ("nope", 0),
        ],
    )
    def test_table(self, text, expected):
        assert ss.string_to_ms(text) == expected


class TestTimeStage:
    @pytest.mark.parametrize(
        "title,est,spent,clean",
        [
            ("Task 30m", _ms(m=30), None, "Task"),
            ("Task 1h", _ms(h=1), None, "Task"),
            ("Task t2h", _ms(h=2), None, "Task"),
            ("Task 1h 30m", _ms(h=1, m=30), None, "Task"),
            ("Task 1h30m", _ms(h=1, m=30), None, "Task"),
            ("Do 1h/2h now", _ms(h=2), _ms(h=1), "Do now"),
            ("Do t1h/30m", _ms(m=30), _ms(h=1), "Do"),
            ("Do 8h", _ms(h=8), None, "Do"),
            ("Do 45m", _ms(m=45), None, "Do"),
            ("no digits here", None, None, "no digits here"),
            # a cluster must end on a word boundary, so '2m4' is not a time
            ("version 2m4 build", None, None, "version 2m4 build"),
        ],
    )
    def test_table(self, d, title, est, spent, clean):
        res = parse(title, d)
        assert res.time_estimate_ms == est
        assert res.time_spent_ms == spent
        if est is not None:
            assert res.clean_title == clean

    def test_bare_number_is_not_a_time(self, d):
        # Only `\d+[mh]` clusters are time; a plain number stays in the title.
        res = parse("call 12", d)
        assert res.time_estimate_ms is None and res.clean_title == "call 12"


class TestProjectStage:
    @pytest.mark.parametrize(
        "title,project_id,clean",
        [
            ("Buy milk +Work", "P_WORK", "Buy milk"),
            ("Buy milk +work", "P_WORK", "Buy milk"),
            ("Buy milk +Work in progress", "P_WIP", "Buy milk"),
            ("Buy milk +Work stuff", "P_WORK", "Buy milk stuff"),
            ("+Work milk", "P_WORK", "milk"),
            ("Buy +SomePro", "P_SOME", "Buy"),
            ("Buy +somepro", "P_SOME", "Buy"),
            ("title+x", None, "title+x"),
            ("Buy + Work", None, "Buy + Work"),
            ("Buy +Nothing", None, "Buy +Nothing"),
            ("Buy +Old stuff", None, "Buy +Old stuff"),
            ("Buy +Hidden thing", None, "Buy +Hidden thing"),
        ],
    )
    def test_table(self, d, title, project_id, clean):
        res = parse(title, d)
        assert res.project_id == project_id
        assert res.clean_title == clean

    def test_gate_off(self, d):
        d["state"]["globalConfig"]["shortSyntax"]["isEnableProject"] = False
        res = parse("Buy +Work", d)
        assert res.project_id is None and res.clean_title == "Buy +Work"


class TestTagStage:
    @pytest.mark.parametrize(
        "title,tag_ids,new_titles,clean",
        [
            ("a #home", ["T_HOME"], [], "a"),
            ("a #HOME", ["T_HOME"], [], "a"),
            ("a #urgent #home", ["T_UP", "T_HOME"], [], "a"),
            ("a #new", [], ["new"], "a"),
            ("a #home #new", ["T_HOME"], ["new"], "a"),
            ("a #new #new", [], ["new"], "a"),
            ("x#no", [], [], "x#no"),
            ("#123 hi", [], [], "#123 hi"),
            ("#123 hi #home", ["T_HOME"], [], "#123 hi"),
            ("a #1st", [], ["1st"], "a"),
            # TODAY is not a user tag: left untouched in the title
            ("a #today", [], [], "a #today"),
        ],
    )
    def test_table(self, d, title, tag_ids, new_titles, clean):
        res = parse(title, d)
        assert res.tag_ids == tag_ids
        assert res.new_tag_titles == new_titles
        assert res.clean_title == clean

    def test_gate_off(self, d):
        d["state"]["globalConfig"]["shortSyntax"]["isEnableTag"] = False
        res = parse("a #home", d)
        assert res.tag_ids == [] and res.clean_title == "a #home"


class TestDueStage:
    @pytest.mark.parametrize(
        "title,day,ms,clean",
        [
            ("a @today", "2026-09-10", None, "a"),
            ("a @tomorrow", "2026-09-11", None, "a"),
            ("a @friday", "2026-09-11", None, "a"),
            ("a @thursday", "2026-09-10", None, "a"),  # today counts
            ("a @mon", "2026-09-14", None, "a"),
            ("a @2026-10-01", "2026-10-01", None, "a"),
            ("a @25.12", "2026-12-25", None, "a"),
            ("a @25.12.2027", "2027-12-25", None, "a"),
            ("a @1.1", "2027-01-01", None, "a"),  # already past -> next year
            ("a @3/11", "2026-11-03", None, "a"),
            ("a @friday 15:00", None, _at(dt.date(2026, 9, 11), 15), "a"),
            ("a @15:30", None, _at(dt.date(2026, 9, 10), 15, 30), "a"),
            ("a @9:00", None, _at(dt.date(2026, 9, 11), 9), "a"),  # past -> tomorrow
            ("a @15", None, _at(dt.date(2026, 9, 10), 15), "a"),
            ("a @3pm", None, _at(dt.date(2026, 9, 10), 15), "a"),
            ("a @3 pm", None, _at(dt.date(2026, 9, 10), 15), "a"),
            ("a @tomorrow 8am", None, _at(dt.date(2026, 9, 11), 8), "a"),
            ("a @tomorrow buy stuff", "2026-09-11", None, "a buy stuff"),
            ("a @nonsense", None, None, "a @nonsense"),
            ("mail@example.com", None, None, "mail@example.com"),
        ],
    )
    def test_table(self, d, title, day, ms, clean):
        res = parse(title, d)
        assert (res.due_day, res.due_with_time) == (day, ms)
        assert res.clean_title == clean

    def test_gate_off(self, d):
        d["state"]["globalConfig"]["shortSyntax"]["isEnableDue"] = False
        res = parse("a @tomorrow", d)
        assert res.due_day is None and res.clean_title == "a @tomorrow"


class TestRepeatStage:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("a @daily", {"repeat_cycle": "DAILY", "repeat_every": 1, "days": None}),
            (
                "a @weekly",
                {"repeat_cycle": "WEEKLY", "repeat_every": 1, "days": ["thursday"]},
            ),
            ("a @monthly", {"repeat_cycle": "MONTHLY", "repeat_every": 1}),
            ("a @yearly", {"repeat_cycle": "YEARLY", "repeat_every": 1}),
            ("a @annually", {"repeat_cycle": "YEARLY", "repeat_every": 1}),
            (
                "a @every monday",
                {
                    "repeat_cycle": "WEEKLY",
                    "repeat_every": 1,
                    "days": ["monday"],
                    "start_date": "2026-09-14",
                },
            ),
            (
                "a @every fri",
                {
                    "repeat_cycle": "WEEKLY",
                    "repeat_every": 1,
                    "days": ["friday"],
                    "start_date": "2026-09-11",
                },
            ),
            ("a @every day", {"repeat_cycle": "DAILY", "repeat_every": 1}),
            ("a @every 2 days", {"repeat_cycle": "DAILY", "repeat_every": 2}),
            ("a @every 3 weeks", {"repeat_cycle": "WEEKLY", "repeat_every": 3}),
            ("a @every 2 months", {"repeat_cycle": "MONTHLY", "repeat_every": 2}),
            (
                "a @every 1 week",  # interval 1 collapses onto the weekly preset
                {"repeat_cycle": "WEEKLY", "repeat_every": 1, "days": ["thursday"]},
            ),
            (
                "a @every 15th",
                {
                    "repeat_cycle": "MONTHLY",
                    "repeat_every": 1,
                    "start_date": "2026-09-15",
                },
            ),
            (
                "a @every 3rd",  # the 3rd already passed this month
                {
                    "repeat_cycle": "MONTHLY",
                    "repeat_every": 1,
                    "start_date": "2026-10-03",
                },
            ),
            (
                "a @every weekday",
                {
                    "repeat_cycle": "WEEKLY",
                    "repeat_every": 1,
                    "days": [
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                    ],
                },
            ),
            (
                "a @every workdays",
                {
                    "repeat_cycle": "WEEKLY",
                    "repeat_every": 1,
                    "days": [
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                    ],
                },
            ),
        ],
    )
    def test_table(self, d, title, expected):
        res = parse(title, d)
        assert res.repeat is not None, title
        for key, value in expected.items():
            assert res.repeat[key] == value, key
        assert res.clean_title == "a"
        assert res.due_day is None and res.due_with_time is None

    @pytest.mark.parametrize(
        "title", ["a @every 0 days", "a @every 1000 days", "a @every 40th", "a @everyday"]
    )
    def test_not_a_repeat(self, d, title):
        assert parse(title, d).repeat is None

    def test_rest_of_the_segment_stays_in_the_title(self, d):
        res = parse("a @every monday morning", d)
        assert res.repeat["days"] == ["monday"]
        assert res.clean_title == "a morning"

    def test_cadence_fields_are_valid(self, d):
        from sp_cli.model import repeat_cadence_fields

        res = parse("a @every weekday", d)
        fields = repeat_cadence_fields(
            res.repeat["repeat_cycle"], res.repeat["repeat_every"], res.repeat["days"]
        )
        assert fields["quickSetting"] == "MONDAY_TO_FRIDAY"


class TestDeadlineStage:
    def test_off_by_default(self, d):
        res = parse("a !2026-10-01", d)
        assert res.deadline_day is None and res.clean_title == "a !2026-10-01"

    def test_config_gate_on(self, d):
        d["state"]["globalConfig"]["shortSyntax"]["isEnableDeadline"] = True
        res = parse("a !2026-10-01", d)
        assert res.deadline_day == "2026-10-01" and res.clean_title == "a"

    def test_force_flag(self, d):
        res = parse("a !tomorrow", d, force_deadline=True)
        assert res.deadline_day == "2026-09-11" and res.clean_title == "a"

    def test_with_time(self, d):
        res = parse("a !tomorrow 18:00", d, force_deadline=True)
        assert res.deadline_with_time == _at(dt.date(2026, 9, 11), 18)
        assert res.deadline_day is None

    def test_needs_leading_space(self, d):
        res = parse("wow!tomorrow", d, force_deadline=True)
        assert res.deadline_day is None and res.clean_title == "wow!tomorrow"


class TestCombined:
    def test_everything_at_once(self, d):
        res = parse("Купить хлеб #home +Work @tomorrow 15m", d)
        assert res.clean_title == "Купить хлеб"
        assert res.project_id == "P_WORK"
        assert res.tag_ids == ["T_HOME"]
        assert res.due_day == "2026-09-11"
        assert res.time_estimate_ms == _ms(m=15)
        assert res.touched

    def test_deadline_and_due_together(self, d):
        res = parse("Report @friday !25.12 +Work #new", d, force_deadline=True)
        assert (res.due_day, res.deadline_day) == ("2026-09-11", "2026-12-25")
        assert res.project_id == "P_WORK" and res.new_tag_titles == ["new"]
        assert res.clean_title == "Report"

    def test_untouched_title_reports_nothing(self, d):
        res = parse("just a plain title", d)
        assert not res.touched and res.clean_title == "just a plain title"

    def test_whitespace_is_collapsed(self, d):
        res = parse("a   #home    b", d)
        assert res.clean_title == "a b"

    def test_title_made_empty_falls_back_to_the_original(self, d):
        res = parse("#home", d)
        assert res.clean_title == "#home"

    def test_parse_never_mutates_the_state(self, d):
        import copy

        before = copy.deepcopy(d)
        parse("x #new +Work @every monday 30m", d)
        assert d == before


class TestConfigGates:
    def test_defaults_when_absent(self, sample):
        sample["state"]["globalConfig"].pop("shortSyntax", None)
        assert ss.config_gates(sample) == {
            "isEnableProject": True,
            "isEnableDue": True,
            "isEnableTag": True,
            "isEnableDeadline": False,
        }
