import datetime as dt
import json

import pytest

from sp_cli import cli
from sp_cli import mutations as mut
from sp_cli import queries as q
from sp_cli import model
from sp_cli import timer
from sp_cli.model import make_tag, today_str
from sp_cli.webdav import ConflictError


class TestAddParentFlagRejection:
    @pytest.mark.parametrize(
        "extra",
        [
            ["--project", "inbox"],
            ["--due", "today"],
            ["--at", "2030-01-01 10:00"],
            ["--remind", "10m"],
            ["--tag", "work"],
        ],
    )
    def test_incompatible_flags_exit_2(self, extra):
        argv = ["add", "sub title", "--parent", "abc123"] + extra
        # Validation runs before any config/network access, so main()
        # must return exit code 2 with no environment set up.
        assert cli.main(argv) == 2

    def test_flag_named_in_error(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            ["add", "sub", "--parent", "abc123", "--due", "today", "--tag", "x"]
        )
        with pytest.raises(cli.CliError, match=r"--due, --tag"):
            cli.cmd_add(args)


class TestResolveTagRefs:
    def test_dedupes_repeated_missing_names(self, sample):
        tag_ids, extra = cli._resolve_tag_refs(
            sample, ["newtag", "newtag", "NEWTAG"], create_missing=True
        )
        assert len(tag_ids) == 1
        assert len(extra) == 1  # only one creation mutation

    def test_dedupes_repeated_existing_refs(self, sample):
        tag = make_tag("G" * 21, "existing")
        sample["state"]["tag"]["ids"].append(tag["id"])
        sample["state"]["tag"]["entities"][tag["id"]] = tag
        tag_ids, extra = cli._resolve_tag_refs(
            sample, ["existing", "G" * 21, "Existing"], create_missing=False
        )
        assert tag_ids == ["G" * 21]
        assert extra == []

    def test_missing_without_create_flag_raises(self, sample):
        with pytest.raises(cli.CliError, match="--create-tags"):
            cli._resolve_tag_refs(sample, ["nope"], create_missing=False)


class TestNoteSubcommandRewrite:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["note", "add", "x"], "note-add"),
            (["note", "show", "abc"], "note-show"),
            (["note", "edit", "abc"], "note-edit"),
            (["note", "rm", "abc"], "note-rm"),
            (["note", "move", "abc"], "note-move"),
        ],
    )
    def test_rewritten(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_notes_is_not_rewritten(self):
        assert cli._rewrite_argv(["notes", "--today"]) == ["notes", "--today"]


class TestNoteEditFlagRejection:
    def test_pin_and_unpin_conflict(self):
        args = cli.build_parser().parse_args(
            cli._rewrite_argv(["note", "edit", "abc", "--pin", "--unpin"])
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_note_edit(args)

    def test_content_and_append_conflict(self):
        args = cli.build_parser().parse_args(
            cli._rewrite_argv(["note", "edit", "abc", "--content", "a", "--append", "b"])
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_note_edit(args)


class TestNoteAliasAndColor:
    def test_bare_note_is_notes(self):
        assert cli._rewrite_argv(["note"]) == ["notes"]

    def test_bare_note_with_flags_is_notes(self):
        assert cli._rewrite_argv(["note", "--today", "--json"]) == [
            "notes",
            "--today",
            "--json",
        ]

    @pytest.mark.parametrize("color", ["a05db1", "#a05db", "#a05db1x", "red", "#GGGGGG"])
    def test_bad_color_exits_2(self, color):
        # Validation runs before any config/network access.
        assert cli.main(["note", "edit", "abc", "--color", color]) == 2

    def test_good_color_passes_validation(self, fake_ctx, sample):
        note = _seed_note(sample, "N" * 21, "hello")
        args = _args(["note", "edit", note["id"], "--color", "#A05db1"])
        assert cli.cmd_note_edit(args) == 0
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["note"]["changes"]
        assert changes["backgroundColor"] == "#A05db1"


def _args(argv):
    return cli.build_parser().parse_args(cli._rewrite_argv(argv))


def _seed_note(d, note_id, content, project_id=None, pinned=False):
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


class _FakeCtx:
    """Stand-in for cli._ctx(): no config, no network; records emitted ops."""

    def __init__(self, d):
        self.d = d
        self.ops = []
        self.commits = 0

    # client side
    def get(self):
        return self.d

    # store side
    def commit(self, mutations, initial=None):
        from sp_cli.ops import OpBuilder

        d = initial if initial is not None else self.d
        b = OpBuilder(d, "B_test01")
        for fn in mutations:
            fn(d, b)
        if b.ops:
            self.commits += 1
            self.ops.extend(b.ops)
        return d


class _RetryCtx(_FakeCtx):
    """commit() drops the caller's stale snapshot and applies the closures to a
    state that changed underneath — the SyncStore 412-retry path."""

    def __init__(self, d, mutate):
        super().__init__(d)
        self.mutate = mutate

    def commit(self, mutations, initial=None):
        self.mutate(self.d)
        return super().commit(mutations, initial=None)


@pytest.fixture
def fake_ctx(sample, monkeypatch):
    ctx = _FakeCtx(sample)
    monkeypatch.setattr(cli, "_ctx", lambda: (ctx, ctx))
    return ctx


class TestNoteCommands:
    def test_add_prints_id_and_emits_na(self, fake_ctx, sample, capsys):
        rc = cli.cmd_note_add(_args(["note", "add", "hello", "--pin"]))
        assert rc == 0
        printed = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert op["a"] == "NA"
        assert op["d"] == printed
        assert sample["state"]["note"]["todayOrder"] == [printed]

    def test_add_with_project(self, fake_ctx, sample):
        assert cli.cmd_note_add(_args(["note", "add", "x", "--project", "inbox"])) == 0
        note = fake_ctx.ops[-1]["p"]["actionPayload"]["note"]
        assert note["projectId"] == "INBOX_PROJECT"

    def test_edit_append_joins_with_newline(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "line1")
        assert cli.cmd_note_edit(_args(["note", "edit", "N" * 21, "--append", "line2"])) == 0
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["note"]["changes"]
        assert changes["content"] == "line1\nline2"

    def test_edit_append_on_empty_note_has_no_leading_newline(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "")
        cli.cmd_note_edit(_args(["note", "edit", "N" * 21, "--append", "first"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["note"]["changes"]
        assert changes["content"] == "first"

    def test_edit_nothing_to_change_raises(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "x")
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_note_edit(_args(["note", "edit", "N" * 21]))

    def test_edit_redundant_pin_writes_nothing(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "x", pinned=True)
        assert cli.cmd_note_edit(_args(["note", "edit", "N" * 21, "--pin"])) == 0
        assert fake_ctx.ops == []
        assert sample["state"]["note"]["todayOrder"] == ["N" * 21]

    def test_rm_abort_writes_nothing(self, fake_ctx, sample, monkeypatch, capsys):
        _seed_note(sample, "N" * 21, "keep me")
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        rc = cli.cmd_note_rm(_args(["note", "rm", "N" * 21]))
        assert rc == 1
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        assert "aborted" in capsys.readouterr().err
        assert sample["state"]["note"]["ids"] == ["N" * 21]

    def test_rm_yes_deletes(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "bye", project_id="INBOX_PROJECT")
        assert cli.cmd_note_rm(_args(["note", "rm", "N" * 21, "--yes"])) == 0
        assert fake_ctx.ops[-1]["a"] == "ND"
        assert sample["state"]["note"]["ids"] == []

    def test_rm_confirmed_interactively_deletes(self, fake_ctx, sample, monkeypatch):
        _seed_note(sample, "N" * 21, "bye")
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        assert cli.cmd_note_rm(_args(["note", "rm", "N" * 21])) == 0
        assert fake_ctx.ops[-1]["a"] == "ND"

    def test_rm_unknown_id_raises(self, fake_ctx, sample):
        with pytest.raises(q.NotFoundError):
            cli.cmd_note_rm(_args(["note", "rm", "zzzz", "--yes"]))

    def test_move_emits_nm(self, fake_ctx, sample):
        from sp_cli.model import make_project

        proj = make_project("P" * 21, "Other")
        sample["state"]["project"]["ids"].append(proj["id"])
        sample["state"]["project"]["entities"][proj["id"]] = proj
        _seed_note(sample, "N" * 21, "x", project_id="INBOX_PROJECT")
        assert cli.cmd_note_move(_args(["note", "move", "N" * 21, "--project", "Other"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "NM"
        assert op["p"]["actionPayload"]["targetProjectId"] == "P" * 21

    def test_move_to_same_project_is_exit_2(self, fake_ctx, sample):
        _seed_note(sample, "N" * 21, "x", project_id="INBOX_PROJECT")
        assert cli.main(["note", "move", "N" * 21, "--project", "inbox"]) == 2


class TestBoardArgvRewrites:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["board", "add", "T"], "board-add"),
            (["board", "edit", "x"], "board-edit"),
            (["board", "rm", "x"], "board-rm"),
            (["board", "sort", "x"], "board-sort"),
            (["board", "panel", "add", "b", "T"], "board-panel-add"),
            (["board", "panel", "edit", "p"], "board-panel-edit"),
            (["board", "panel", "rm", "p"], "board-panel-rm"),
            (["board", "panel", "order", "p", "t"], "board-panel-order"),
        ],
    )
    def test_rewrite(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_boards_list_command_parses(self):
        args = cli.build_parser().parse_args(["boards", "--json"])
        assert args.fn is cli.cmd_boards


class TestPanelChanges:
    def _args(self, argv):
        return cli.build_parser().parse_args(cli._rewrite_argv(argv))

    def test_enum_flags_map_to_numbers(self, sample):
        args = self._args(
            [
                "board",
                "panel",
                "add",
                "KANBAN_DEFAULT",
                "Doing",
                "--done",
                "undone",
                "--scheduled",
                "scheduled",
                "--backlog",
                "only",
                "--parents-only",
            ]
        )
        changes = cli._panel_changes(sample, args)
        assert changes["taskDoneState"] == 3
        assert changes["scheduledState"] == 2
        assert changes["backlogState"] == 3
        assert changes["isParentTasksOnly"] is True

    def test_all_projects_flag(self, sample):
        args = self._args(
            ["board", "panel", "add", "b", "T", "--all-projects"]
        )
        assert cli._panel_changes(sample, args)["projectIds"] == [""]

    def test_project_flag_resolves(self, sample):
        args = self._args(
            ["board", "panel", "add", "b", "T", "--project", "inbox"]
        )
        assert cli._panel_changes(sample, args)["projectIds"] == ["INBOX_PROJECT"]

    def test_project_and_all_projects_conflict(self, sample):
        args = self._args(
            ["board", "panel", "add", "b", "T", "--project", "inbox", "--all-projects"]
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli._panel_changes(sample, args)

    def test_today_tag_rejected(self, sample):
        args = self._args(["board", "panel", "add", "b", "T", "--tags", "TODAY"])
        with pytest.raises(cli.CliError, match="TODAY"):
            cli._panel_changes(sample, args)

    def test_sort_defaults_dir_asc(self, sample):
        args = self._args(["board", "panel", "add", "b", "T", "--sort", "title"])
        changes = cli._panel_changes(sample, args)
        assert (changes["sortBy"], changes["sortDir"]) == ("title", "asc")

    def test_dir_without_sort_rejected(self, sample):
        args = self._args(["board", "panel", "add", "b", "T", "--dir", "desc"])
        with pytest.raises(cli.CliError, match="--dir requires --sort"):
            cli._panel_changes(sample, args)

    def test_no_flags_means_no_changes(self, sample):
        args = self._args(["board", "panel", "add", "b", "T"])
        assert cli._panel_changes(sample, args) == {}

    @pytest.mark.parametrize("flag", [["--sort", "manual"], ["--no-sort"]])
    def test_manual_sort_clears_sort_keys(self, sample, flag):
        args = self._args(["board", "panel", "edit", "TODO"] + flag)
        changes = cli._panel_changes(sample, args)
        assert changes == {"sortBy": None, "sortDir": None}

    def test_manual_sort_rejects_dir(self, sample):
        args = self._args(
            ["board", "panel", "edit", "TODO", "--sort", "manual", "--dir", "asc"]
        )
        with pytest.raises(cli.CliError, match="--sort manual"):
            cli._panel_changes(sample, args)

    def test_no_sort_conflicts_with_a_real_sort(self, sample):
        args = self._args(
            ["board", "panel", "edit", "TODO", "--no-sort", "--sort", "title"]
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli._panel_changes(sample, args)


class TestBoardCommands:
    def _args(self, argv):
        return cli.build_parser().parse_args(cli._rewrite_argv(argv))

    def test_add_prints_id_and_emits_ba_without_ds(self, fake_ctx, sample, capsys):
        assert cli.cmd_board_add(self._args(["board", "add", "Sprint"])) == 0
        printed = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert (op["a"], op["d"]) == ("BA", printed)
        assert "ds" not in op
        assert op["p"]["actionPayload"]["board"]["cols"] == 2

    def test_edit_emits_bu_without_ds(self, fake_ctx, sample):
        rc = cli.cmd_board_edit(self._args(["board", "edit", "KANBAN", "--cols", "4"]))
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["d"]) == ("BU", "KANBAN_DEFAULT")
        assert "ds" not in op
        assert op["p"]["actionPayload"]["updates"] == {"cols": 4}

    def test_panel_add_emits_bu_with_whole_array(self, fake_ctx, sample, capsys):
        rc = cli.cmd_board_panel_add(
            self._args(["board", "panel", "add", "KANBAN", "Extra"])
        )
        assert rc == 0
        panel_id = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert op["a"] == "BU" and "ds" not in op
        panels = op["p"]["actionPayload"]["updates"]["panels"]
        assert [p["id"] for p in panels][-1] == panel_id
        assert len(panels) == 4

    def test_panel_edit_sort_manual_drops_sort_keys(self, fake_ctx, sample):
        panel = sample["state"]["boards"]["boardCfgs"][1]["panels"][0]
        panel["sortBy"] = "title"
        panel["sortDir"] = "asc"
        rc = cli.cmd_board_panel_edit(
            self._args(["board", "panel", "edit", "TODO", "--sort", "manual"])
        )
        assert rc == 0
        written = fake_ctx.ops[-1]["p"]["actionPayload"]["updates"]["panels"][0]
        assert "sortBy" not in written and "sortDir" not in written

    def test_panel_order_on_sorted_panel_is_exit_2(
        self, fake_ctx, sample, add_task_entity, capsys
    ):
        tid = add_task_entity()["id"]
        sample["state"]["boards"]["boardCfgs"][1]["panels"][0]["sortBy"] = "title"
        assert cli.main(["board", "panel", "order", "TODO", tid]) == 2
        assert "--sort manual" in capsys.readouterr().err
        assert fake_ctx.ops == []

    def test_sort_emits_bs_with_ds(self, fake_ctx, sample):
        rc = cli.cmd_board_sort(self._args(["board", "sort", "KANBAN"]))
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "BS" and op["ds"] == ["KANBAN_DEFAULT"]

    @pytest.mark.parametrize(
        "argv",
        [
            ["board", "add", "T", "--cols", "0"],
            ["board", "edit", "KANBAN", "--cols", "-1"],
        ],
    )
    def test_bad_cols_is_exit_2(self, fake_ctx, argv):
        assert cli.main(argv) == 2
        assert fake_ctx.ops == []

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["board"], "needs a subcommand"),
            (["board", "panel"], "sp board panel needs a subcommand"),
            (["board", "panel", "bogus"], "unknown board panel subcommand"),
            (["board", "bogus"], "unknown board subcommand"),
        ],
    )
    def test_incomplete_board_command_prints_usage(self, argv, expected, capsys):
        assert cli.main(argv) == 2
        err = capsys.readouterr().err
        assert expected in err and "usage: sp board" in err


class TestCounterArgvRewrites:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["counter", "add", "T"], "counter-add"),
            (["counter", "edit", "x"], "counter-edit"),
            (["counter", "rm", "x"], "counter-rm"),
            (["counter", "set", "x", "1"], "counter-set"),
            (["counter", "inc", "x"], "counter-inc"),
            (["counter", "log", "x", "30m"], "counter-log"),
            (["counter", "order", "x"], "counter-order"),
        ],
    )
    def test_rewrite(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_bare_counter_is_counters(self):
        assert cli._rewrite_argv(["counter"]) == ["counters"]
        assert cli._rewrite_argv(["counter", "--json"]) == ["counters", "--json"]


class TestCounterCommands:
    @staticmethod
    def _counter(d, cid):
        return d["state"]["simpleCounter"]["entities"][cid]

    def test_add_click_counter(self, fake_ctx, sample, capsys):
        rc = cli.cmd_counter_add(
            _args(["counter", "add", "Pushups", "--streak-min", "20"])
        )
        assert rc == 0
        printed = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"], op["d"]) == ("SA", "SIMPLE_COUNTER", printed)
        counter = op["p"]["actionPayload"]["simpleCounter"]
        assert counter["type"] == "ClickCounter"
        assert counter["isOn"] is False
        assert counter["isEnabled"] is True
        assert counter["streakMinValue"] == 20

    def test_add_stopwatch_streak_min_is_a_duration(self, fake_ctx):
        cli.cmd_counter_add(
            _args(
                [
                    "counter", "add", "Desk",
                    "--type", "stopwatch",
                    "--streak-min", "30m",
                    "--icon", "chair",
                ]
            )
        )
        counter = fake_ctx.ops[-1]["p"]["actionPayload"]["simpleCounter"]
        assert counter["type"] == "StopWatch"
        assert counter["streakMinValue"] == 1800000
        assert counter["icon"] == "chair"

    def test_add_countdown_with_duration_and_days(self, fake_ctx):
        cli.cmd_counter_add(
            _args(
                [
                    "counter", "add", "Stretch",
                    "--type", "countdown",
                    "--countdown", "30m",
                    "--streak-days", "mon,sat",
                    "--no-streak",
                ]
            )
        )
        counter = fake_ctx.ops[-1]["p"]["actionPayload"]["simpleCounter"]
        assert counter["countdownDuration"] == 1800000
        assert counter["isTrackStreaks"] is False
        assert counter["streakWeekDays"] == {
            "0": False,
            "1": True,
            "2": False,
            "3": False,
            "4": False,
            "5": False,
            "6": True,
        }

    def test_add_countdown_flag_on_click_counter_is_exit_2(self):
        assert cli.main(["counter", "add", "X", "--countdown", "30m"]) == 2

    def test_add_bad_weekday_is_exit_2(self, fake_ctx):
        assert cli.main(["counter", "add", "X", "--streak-days", "monday"]) == 2

    def test_edit_emits_su(self, fake_ctx, sample):
        rc = cli.cmd_counter_edit(
            _args(["counter", "edit", "COFFEE_COUNTER", "--title", "Tea", "--enable"])
        )
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "SU"
        assert op["p"]["actionPayload"]["simpleCounter"]["changes"] == {
            "title": "Tea",
            "isEnabled": True,
        }

    def test_edit_streak_days_rewrites_the_whole_map(self, fake_ctx):
        cli.cmd_counter_edit(
            _args(["counter", "edit", "COFFEE_COUNTER", "--streak-days", "sun"])
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["simpleCounter"]["changes"]
        assert changes["streakWeekDays"]["0"] is True
        assert changes["streakWeekDays"]["1"] is False

    def test_edit_nothing_to_change_raises(self, fake_ctx):
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_counter_edit(_args(["counter", "edit", "COFFEE_COUNTER"]))

    def test_edit_enable_and_disable_conflict(self, fake_ctx):
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_counter_edit(
                _args(["counter", "edit", "COFFEE_COUNTER", "--enable", "--disable"])
            )

    def test_rm_abort_writes_nothing(self, fake_ctx, sample, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        assert cli.cmd_counter_rm(_args(["counter", "rm", "COFFEE_COUNTER"])) == 1
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        assert "aborted" in capsys.readouterr().err
        assert "COFFEE_COUNTER" in sample["state"]["simpleCounter"]["ids"]

    def test_rm_yes_deletes(self, fake_ctx, sample):
        assert cli.cmd_counter_rm(_args(["counter", "rm", "COFFEE_COUNTER", "--yes"])) == 0
        assert fake_ctx.ops[-1]["a"] == "SD"
        assert "COFFEE_COUNTER" not in sample["state"]["simpleCounter"]["ids"]

    def test_set_today_emits_st(self, fake_ctx, sample):
        from sp_cli.model import today_str

        assert cli.cmd_counter_set(_args(["counter", "set", "COFFEE_COUNTER", "3"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "ST"
        assert op["p"]["actionPayload"]["newVal"] == 3
        assert self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today_str()] == 3

    def test_set_past_date_emits_sfd(self, fake_ctx):
        cli.cmd_counter_set(
            _args(["counter", "set", "COFFEE_COUNTER", "2", "--date", "2020-01-02"])
        )
        op = fake_ctx.ops[-1]
        assert op["a"] == "SFD"
        assert op["p"]["actionPayload"] == {
            "id": "COFFEE_COUNTER",
            "date": "2020-01-02",
            "newVal": 2,
        }

    def test_set_stopwatch_value_is_a_duration(self, fake_ctx):
        cli.cmd_counter_set(_args(["counter", "set", "STANDING_DESK_ID", "45m"]))
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["newVal"] == 2700000

    def test_set_non_numeric_on_click_counter_is_exit_2(self, fake_ctx):
        assert cli.main(["counter", "set", "COFFEE_COUNTER", "abc"]) == 2

    def test_inc_defaults_to_one_and_is_absolute(self, fake_ctx, sample):
        from sp_cli.model import today_str

        self._counter(sample, "COFFEE_COUNTER")["countOnDay"][today_str()] = 2
        assert cli.cmd_counter_inc(_args(["counter", "inc", "COFFEE_COUNTER"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "ST"
        assert op["p"]["actionPayload"]["newVal"] == 3

    def test_inc_by_amount(self, fake_ctx):
        cli.cmd_counter_inc(_args(["counter", "inc", "COFFEE_COUNTER", "--by", "5"]))
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["newVal"] == 5

    def test_log_emits_sc_and_accumulates(self, fake_ctx, sample):
        from sp_cli.model import today_str

        assert cli.cmd_counter_log(_args(["counter", "log", "STANDING_DESK_ID", "30m"])) == 0
        cli.cmd_counter_log(_args(["counter", "log", "STANDING_DESK_ID", "15m"]))
        assert [op["a"] for op in fake_ctx.ops] == ["SC", "SC"]
        assert [op["p"]["actionPayload"]["duration"] for op in fake_ctx.ops] == [
            1800000,
            900000,
        ]
        assert (
            self._counter(sample, "STANDING_DESK_ID")["countOnDay"][today_str()]
            == 2700000
        )

    def test_log_on_click_counter_is_exit_2(self, fake_ctx):
        assert cli.main(["counter", "log", "COFFEE_COUNTER", "30m"]) == 2

    def test_log_negative_duration_is_exit_2(self, fake_ctx):
        assert cli.main(["counter", "log", "STANDING_DESK_ID", "--", "-30m"]) == 2

    def test_order_completes_the_list(self, fake_ctx, sample):
        assert cli.cmd_counter_order(_args(["counter", "order", "COFFEE_COUNTER"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "SM"
        assert op["p"]["actionPayload"]["ids"] == [
            "COFFEE_COUNTER",
            "STANDING_DESK_ID",
            "STRETCHING_COUNTER",
        ]
        assert sample["state"]["simpleCounter"]["ids"][0] == "COFFEE_COUNTER"

    def test_inc_on_stopwatch_is_exit_2(self, fake_ctx, capsys):
        assert cli.main(["counter", "inc", "STANDING_DESK_ID"]) == 2
        assert "counter log" in capsys.readouterr().err
        assert fake_ctx.ops == []

    def test_inc_by_on_stopwatch_is_exit_2(self, fake_ctx):
        assert cli.main(["counter", "inc", "STANDING_DESK_ID", "--by", "30m"]) == 2
        assert fake_ctx.ops == []

    def test_order_recomputes_ids_on_retry(self, sample, monkeypatch, capsys):
        from sp_cli.model import make_simple_counter

        def _foreign_add(d):
            reg = d["state"]["simpleCounter"]
            if "N" * 21 in reg["entities"]:
                return
            counter = make_simple_counter("N" * 21, "Foreign")
            reg["ids"].append("N" * 21)
            reg["entities"]["N" * 21] = counter

        ctx = _RetryCtx(sample, _foreign_add)
        monkeypatch.setattr(cli, "_ctx", lambda: (ctx, ctx))
        assert cli.cmd_counter_order(_args(["counter", "order", "COFFEE_COUNTER"])) == 0
        ids = ctx.ops[-1]["p"]["actionPayload"]["ids"]
        assert ids[0] == "COFFEE_COUNTER"
        assert "N" * 21 in ids
        assert sample["state"]["simpleCounter"]["ids"] == ids
        assert "N" * 21 in capsys.readouterr().out

    def test_counters_list_renders(self, fake_ctx, sample, capsys):
        from sp_cli.model import today_str

        self._counter(sample, "STANDING_DESK_ID")["countOnDay"][today_str()] = 1800000
        assert cli.cmd_counters(_args(["counters"])) == 0
        out = capsys.readouterr().out
        assert "STANDING_DESK_ID" in out
        assert "stopwatch" in out
        assert "30m" in out

    def test_counters_json(self, fake_ctx, capsys):
        assert cli.cmd_counters(_args(["counters", "--json"])) == 0
        assert '"COFFEE_COUNTER"' in capsys.readouterr().out

    def test_zero_stopwatch_renders_as_duration(self, fake_ctx, capsys):
        assert cli.cmd_counters(_args(["counters"])) == 0
        row = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if "STANDING_DESK_ID" in line
        )
        assert "0m" in row


class TestReorderRetry:
    def test_reorder_recomputes_task_ids_on_retry(self, sample, monkeypatch, capsys):
        from sp_cli.model import make_task

        project = sample["state"]["project"]["entities"]["INBOX_PROJECT"]
        first, second = project["taskIds"][0], project["taskIds"][1]

        def _foreign_add(d):
            if "F" * 21 in d["state"]["task"]["entities"]:
                return
            task = make_task("F" * 21, "foreign", "INBOX_PROJECT")
            d["state"]["task"]["ids"].append("F" * 21)
            d["state"]["task"]["entities"]["F" * 21] = task
            d["state"]["project"]["entities"]["INBOX_PROJECT"]["taskIds"].append(
                "F" * 21
            )

        ctx = _RetryCtx(sample, _foreign_add)
        monkeypatch.setattr(cli, "_ctx", lambda: (ctx, ctx))
        args = _args(["reorder", "--project", "INBOX_PROJECT", second, first])
        assert cli.cmd_reorder(args) == 0
        changes = ctx.ops[-1]["p"]["actionPayload"]["project"]["changes"]
        assert changes["taskIds"][:2] == [second, first]
        assert "F" * 21 in changes["taskIds"]
        assert project["taskIds"] == changes["taskIds"]


class TestMetricSubcommandRewrite:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["metric", "set", "--impact", "3"], "metric-set"),
            (["metric", "focus", "25m"], "metric-focus"),
            (["metric", "rm", "2026-01-01"], "metric-rm"),
            (["metric"], "metrics"),
            (["metric", "--json"], "metrics"),
        ],
    )
    def test_rewritten(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_metrics_is_not_rewritten(self):
        assert cli._rewrite_argv(["metrics", "--json"]) == ["metrics", "--json"]


class TestMetricCommands:
    def _entities(self, d):
        return d["state"]["metric"]["entities"]

    def test_set_emits_eu_and_creates_the_day(self, fake_ctx, sample, capsys):
        rc = cli.cmd_metric_set(
            _args(["metric", "set", "--day", "2026-01-01", "--impact", "4"])
        )
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"], op["d"]) == ("EU", "METRIC", "2026-01-01")
        assert op["p"]["actionPayload"]["metric"]["changes"] == {"impactOfWork": 4}
        assert self._entities(sample)["2026-01-01"]["focusSessions"] == []
        assert "2026-01-01" in capsys.readouterr().out

    def test_set_defaults_to_today(self, fake_ctx, sample):
        cli.cmd_metric_set(_args(["metric", "set", "--energy", "2"]))
        assert fake_ctx.ops[-1]["d"] == today_str()

    def test_set_without_flags_raises(self, fake_ctx):
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_metric_set(_args(["metric", "set"]))

    def test_remind_flags_conflict(self, fake_ctx):
        args = _args(
            ["metric", "set", "--remind-tomorrow", "--no-remind-tomorrow"]
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_metric_set(args)

    def test_out_of_range_impact_exits_2(self, fake_ctx):
        assert cli.main(["metric", "set", "--impact", "9"]) == 2
        assert fake_ctx.ops == []

    def test_empty_notes_clears(self, fake_ctx):
        cli.cmd_metric_set(_args(["metric", "set", "--notes", ""]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["metric"]["changes"]
        assert changes == {"notes": None}

    def test_reflect_appends_to_existing(self, fake_ctx, sample):
        day = "2026-01-01"
        cli.cmd_metric_set(_args(["metric", "set", "--day", day, "--reflect", "one"]))
        cli.cmd_metric_set(_args(["metric", "set", "--day", day, "--reflect", "two"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["metric"]["changes"]
        assert [r["text"] for r in changes["reflections"]] == ["one", "two"]
        assert all("created" in r for r in changes["reflections"])
        assert len(self._entities(sample)[day]["reflections"]) == 2

    def test_focus_emits_el_with_day_payload(self, fake_ctx, sample, capsys):
        rc = cli.cmd_metric_focus(
            _args(["metric", "focus", "25m", "--day", "2026-01-01"])
        )
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"], op["d"]) == ("EL", "METRIC", "2026-01-01")
        assert op["p"]["actionPayload"] == {"day": "2026-01-01", "duration": 1500000}
        assert self._entities(sample)["2026-01-01"]["focusSessions"] == [1500000]
        assert "total" in capsys.readouterr().out

    def test_focus_zero_duration_raises(self, fake_ctx):
        with pytest.raises(cli.CliError, match="positive"):
            cli.cmd_metric_focus(_args(["metric", "focus", "0m"]))

    def test_rm_missing_day_raises(self, fake_ctx):
        with pytest.raises(cli.CliError, match="no metric for"):
            cli.cmd_metric_rm(_args(["metric", "rm", "2026-01-01"]))

    def test_rm_abort_writes_nothing(self, fake_ctx, sample, monkeypatch, capsys):
        cli.cmd_metric_set(_args(["metric", "set", "--day", "2026-01-01", "--impact", "1"]))
        fake_ctx.ops.clear()
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        assert cli.cmd_metric_rm(_args(["metric", "rm", "2026-01-01"])) == 1
        assert fake_ctx.ops == []
        assert "aborted" in capsys.readouterr().err
        assert "2026-01-01" in self._entities(sample)

    def test_rm_yes_deletes(self, fake_ctx, sample):
        cli.cmd_metric_set(_args(["metric", "set", "--day", "2026-01-01", "--impact", "1"]))
        assert cli.cmd_metric_rm(_args(["metric", "rm", "2026-01-01", "--yes"])) == 0
        assert fake_ctx.ops[-1]["a"] == "ED"
        assert self._entities(sample) == {}

    def test_metrics_range_and_json(self, fake_ctx, sample, capsys):
        for day in ("2026-01-01", "2026-01-05"):
            cli.cmd_metric_set(_args(["metric", "set", "--day", day, "--impact", "2"]))
        capsys.readouterr()  # drop the `metric set` cards
        cli.cmd_metrics(_args(["metrics", "--from", "2026-01-02", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert [m["id"] for m in out] == ["2026-01-05"]

    def test_metrics_table_lists_focus(self, fake_ctx, capsys):
        cli.cmd_metric_focus(_args(["metric", "focus", "25m", "--day", "2026-01-01"]))
        cli.cmd_metrics(_args(["metrics"]))
        out = capsys.readouterr().out
        assert "2026-01-01" in out and "1x 25m" in out


def _seed_provider(d, pid, key="ICAL", **overrides):
    from sp_cli.model import make_issue_provider

    provider = make_issue_provider(pid, key, **overrides)
    reg = d["state"]["issueProvider"]
    reg["ids"].append(pid)
    reg["entities"][pid] = provider
    return provider


class TestProviderArgvRewrites:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["provider", "add-ical", "u"], "provider-add-ical"),
            (["provider", "add-caldav"], "provider-add-caldav"),
            (["provider", "edit", "x"], "provider-edit"),
            (["provider", "rm", "x"], "provider-rm"),
            (["provider", "order", "x"], "provider-order"),
        ],
    )
    def test_rewrite(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_bare_provider_is_providers(self):
        assert cli._rewrite_argv(["provider"]) == ["providers"]
        assert cli._rewrite_argv(["provider", "--json"]) == ["providers", "--json"]

    def test_unknown_subcommand_gets_group_help(self, capsys):
        assert cli.main(["provider", "add"]) == 2
        assert "sp provider add-ical" in capsys.readouterr().err


class TestProviderCommands:
    @staticmethod
    def _reg(d):
        return d["state"]["issueProvider"]

    def test_add_ical_emits_full_provider(self, fake_ctx, sample, capsys):
        rc = cli.cmd_provider_add_ical(
            _args(
                [
                    "provider", "add-ical", "https://cal/x.ics",
                    "--auto-import",
                    "--project", "inbox",
                    "--check-every", "30m",
                ]
            )
        )
        assert rc == 0
        printed = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "IA", "CRT", "ISSUE_PROVIDER", printed,
        )
        provider = op["p"]["actionPayload"]["issueProvider"]
        assert provider["issueProviderKey"] == "ICAL"
        assert provider["icalUrl"] == "https://cal/x.ics"
        assert provider["isAutoImportForCurrentDay"] is True
        assert provider["isEnabled"] is True
        assert provider["defaultProjectId"] == "INBOX_PROJECT"
        assert provider["checkUpdatesEvery"] == 1800000
        assert provider["showBannerBeforeThreshold"] == 7200000
        assert provider["pollingMode"] == "whenProjectOpen"
        assert provider["defaultTagIds"] == []
        assert self._reg(sample)["ids"] == [printed]

    def test_add_ical_does_not_create_cal_tasks(self, fake_ctx, sample):
        before = list(sample["state"]["task"]["ids"])
        cli.cmd_provider_add_ical(_args(["provider", "add-ical", "https://c/x.ics"]))
        assert sample["state"]["task"]["ids"] == before
        assert [op["a"] for op in fake_ctx.ops] == ["IA"]

    def test_add_ical_with_tags(self, fake_ctx, sample):
        cli.cmd_provider_add_ical(
            _args(
                [
                    "provider", "add-ical", "https://c/x.ics",
                    "--tag", "meetings", "--create-tags",
                ]
            )
        )
        provider = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]
        assert len(provider["defaultTagIds"]) == 1
        assert [op["a"] for op in fake_ctx.ops] == ["GA", "IA"]

    def test_add_ical_regex_filters(self, fake_ctx):
        cli.cmd_provider_add_ical(
            _args(
                [
                    "provider", "add-ical", "https://c/x.ics",
                    "--include-regex", "standup",
                    "--exclude-regex", "",
                ]
            )
        )
        provider = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]
        assert provider["filterIncludeRegex"] == "standup"
        assert provider["filterExcludeRegex"] is None

    def test_add_caldav_without_optin_is_refused(self, fake_ctx):
        argv = [
            "provider", "add-caldav",
            "--url", "https://dav/",
            "--resource", "cal",
            "--username", "u",
            "--password", "pw",
        ]
        with pytest.raises(cli.CliError, match="PLAIN TEXT"):
            cli.cmd_provider_add_caldav(_args(argv))
        assert fake_ctx.ops == []
        assert cli.main(argv) == 2

    def test_add_caldav_with_optin(self, fake_ctx, capsys):
        rc = cli.cmd_provider_add_caldav(
            _args(
                [
                    "provider", "add-caldav",
                    "--url", "https://dav/",
                    "--resource", "cal",
                    "--username", "u",
                    "--password", "pw",
                    "--category-filter", "work",
                    "--store-plaintext-credentials",
                ]
            )
        )
        assert rc == 0
        capsys.readouterr()
        provider = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]
        assert provider["issueProviderKey"] == "CALDAV"
        assert provider["caldavUrl"] == "https://dav/"
        assert provider["resourceName"] == "cal"
        assert provider["username"] == "u"
        assert provider["password"] == "pw"
        assert provider["categoryFilter"] == "work"
        assert provider["isAddSubTasks"] is False

    def test_edit_emits_iu(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21, icalUrl="https://old/x.ics")
        rc = cli.cmd_provider_edit(
            _args(
                [
                    "provider", "edit", "P" * 21,
                    "--disable",
                    "--url", "https://new/x.ics",
                    "--no-auto-import",
                    "--project", "inbox",
                ]
            )
        )
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "IU", "UPD", "ISSUE_PROVIDER", "P" * 21,
        )
        changes = op["p"]["actionPayload"]["issueProvider"]["changes"]
        assert changes == {
            "isEnabled": False,
            "icalUrl": "https://new/x.ics",
            "isAutoImportForCurrentDay": False,
            "defaultProjectId": "INBOX_PROJECT",
        }

    def test_edit_no_project_clears(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21, defaultProjectId="INBOX_PROJECT")
        cli.cmd_provider_edit(_args(["provider", "edit", "P" * 21, "--no-project"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]["changes"]
        assert changes == {"defaultProjectId": None}

    def test_edit_caldav_url_field(self, fake_ctx, sample):
        _seed_provider(sample, "C" * 21, key="CALDAV", caldavUrl="https://a/")
        cli.cmd_provider_edit(
            _args(["provider", "edit", "C" * 21, "--url", "https://b/"])
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]["changes"]
        assert changes == {"caldavUrl": "https://b/"}

    def test_edit_ical_only_flags_on_caldav_rejected(self, fake_ctx, sample):
        _seed_provider(sample, "C" * 21, key="CALDAV")
        with pytest.raises(cli.CliError, match="only apply to ICAL"):
            cli.cmd_provider_edit(
                _args(["provider", "edit", "C" * 21, "--auto-import"])
            )
        assert fake_ctx.ops == []

    def test_edit_ical_extra_flags(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21)
        cli.cmd_provider_edit(
            _args(
                [
                    "provider", "edit", "P" * 21,
                    "--banner-before", "30m",
                    "--include-regex", "standup",
                    "--exclude-regex", "",
                ]
            )
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]["changes"]
        assert changes == {
            "showBannerBeforeThreshold": 1800000,
            "filterIncludeRegex": "standup",
            "filterExcludeRegex": None,
        }

    def test_edit_caldav_credentials(self, fake_ctx, sample):
        _seed_provider(sample, "C" * 21, key="CALDAV")
        cli.cmd_provider_edit(
            _args(
                [
                    "provider", "edit", "C" * 21,
                    "--username", "u2",
                    "--password", "pw2",
                    "--category-filter", "work",
                    "--store-plaintext-credentials",
                ]
            )
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["issueProvider"]["changes"]
        assert changes == {
            "username": "u2",
            "password": "pw2",
            "categoryFilter": "work",
        }

    def test_edit_password_requires_the_plaintext_flag(self, fake_ctx, sample):
        _seed_provider(sample, "C" * 21, key="CALDAV")
        with pytest.raises(cli.CliError, match="PLAIN TEXT"):
            cli.cmd_provider_edit(
                _args(["provider", "edit", "C" * 21, "--password", "pw"])
            )
        assert fake_ctx.ops == []

    def test_edit_caldav_only_flags_on_ical_rejected(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21)
        with pytest.raises(cli.CliError, match="only apply to CalDAV"):
            cli.cmd_provider_edit(
                _args(["provider", "edit", "P" * 21, "--username", "u"])
            )
        assert fake_ctx.ops == []

    def test_edit_rejects_empty_url(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21, icalUrl="https://c/x.ics")
        with pytest.raises(cli.CliError, match="cannot be empty"):
            cli.cmd_provider_edit(_args(["provider", "edit", "P" * 21, "--url", ""]))
        with pytest.raises(cli.CliError, match="cannot be empty"):
            cli.cmd_provider_edit(_args(["provider", "edit", "P" * 21, "--url", "  "]))
        assert fake_ctx.ops == []

    def _seed_jira(self, sample):
        reg = sample["state"].setdefault("issueProvider", {"ids": [], "entities": {}})
        reg["ids"].append("J" * 21)
        reg["entities"]["J" * 21] = {
            "id": "J" * 21,
            "issueProviderKey": "JIRA",
            "isEnabled": True,
        }

    def test_edit_refuses_non_builtin_key(self, fake_ctx, sample):
        self._seed_jira(sample)
        with pytest.raises(cli.CliError, match="managed by the app"):
            cli.cmd_provider_edit(
                _args(["provider", "edit", "J" * 21, "--disable"])
            )
        assert fake_ctx.ops == []

    def test_rm_refuses_non_builtin_key(self, fake_ctx, sample):
        self._seed_jira(sample)
        with pytest.raises(cli.CliError, match="managed by the app"):
            cli.cmd_provider_rm(_args(["provider", "rm", "J" * 21, "--yes"]))
        assert fake_ctx.ops == []

    def test_edit_conflicting_flags(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21)
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_provider_edit(
                _args(["provider", "edit", "P" * 21, "--enable", "--disable"])
            )

    def test_edit_nothing_to_change(self, fake_ctx, sample):
        _seed_provider(sample, "P" * 21)
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_provider_edit(_args(["provider", "edit", "P" * 21]))

    def test_rm_unlinks_live_and_archived(self, fake_ctx, sample, capsys):
        from sp_cli.model import make_task

        pid = "P" * 21
        _seed_provider(sample, pid)
        live = make_task("L" * 21, "cal event", "INBOX_PROJECT")
        live["issueProviderId"] = pid
        live["issueId"] = "ev1"
        sample["state"]["task"]["ids"].append(live["id"])
        sample["state"]["task"]["entities"][live["id"]] = live
        sample["state"]["project"]["entities"]["INBOX_PROJECT"]["taskIds"].append(
            live["id"]
        )
        arch = make_task("A" * 21, "old event", "INBOX_PROJECT")
        arch["issueProviderId"] = pid
        arch["issueId"] = "ev0"
        sample["state"]["archiveOld"]["task"]["ids"].append(arch["id"])
        sample["state"]["archiveOld"]["task"]["entities"][arch["id"]] = arch

        rc = cli.cmd_provider_rm(_args(["provider", "rm", pid, "--yes"]))
        assert rc == 0
        assert "unlinked 2 task(s)" in capsys.readouterr().out
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "HID", "DEL", "ISSUE_PROVIDER", pid,
        )
        assert sorted(op["p"]["actionPayload"]["taskIdsToUnlink"]) == sorted(
            [live["id"], arch["id"]]
        )
        assert "issueProviderId" not in live and "issueId" not in live
        assert "issueProviderId" not in arch and "issueId" not in arch
        assert self._reg(sample)["ids"] == []

    def test_order_lists_providers_first(self, fake_ctx, sample, capsys):
        for pid in ("A" * 21, "B" * 21, "C" * 21):
            _seed_provider(sample, pid)
        rc = cli.cmd_provider_order(_args(["provider", "order", "C" * 21]))
        assert rc == 0
        capsys.readouterr()
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"]) == ("IS", "MOV", "ISSUE_PROVIDER")
        assert op["p"]["actionPayload"] == {"ids": ["C" * 21]}
        assert self._reg(sample)["ids"] == ["C" * 21, "A" * 21, "B" * 21]

    def test_providers_list_json(self, fake_ctx, sample, capsys):
        _seed_provider(sample, "P" * 21, icalUrl="https://c/x.ics")
        assert cli.cmd_providers(_args(["providers", "--json"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert [p["id"] for p in data] == ["P" * 21]

    def test_providers_list_table(self, fake_ctx, sample, capsys):
        _seed_provider(
            sample,
            "P" * 21,
            icalUrl="https://c/x.ics",
            defaultProjectId="INBOX_PROJECT",
        )
        assert cli.cmd_providers(_args(["providers"])) == 0
        out = capsys.readouterr().out
        assert "ICAL" in out and "https://c/x.ics" in out and "Inbox" in out

    def test_providers_empty(self, fake_ctx, capsys):
        assert cli.cmd_providers(_args(["providers"])) == 0
        assert capsys.readouterr().out.strip() == "(none)"


# ---------------------------------------------------------------- live timer

@pytest.fixture
def timer_file(tmp_path, monkeypatch):
    """Redirect the local timer file into tmp_path."""
    path = tmp_path / "timer.json"
    monkeypatch.setenv("SP_CLI_TIMER", str(path))
    return path


def _ms(y, mo, d, h, mi):
    import datetime

    return int(datetime.datetime(y, mo, d, h, mi).timestamp() * 1000)


class TestTimerFile:
    def test_write_read_clear_roundtrip(self, timer_file):
        timer.write_timer("A" * 21, "hi", 1700000000000)
        assert timer.read_timer() == {
            "task_id": "A" * 21,
            "title": "hi",
            "started_at": 1700000000000,
        }
        assert timer.clear_timer() is True
        assert timer.read_timer() is None
        assert timer.clear_timer() is False

    def test_write_is_atomic_and_leaves_no_tmp(self, timer_file):
        timer.write_timer("A" * 21, "one", 1)
        timer.write_timer("B" * 21, "two", 2)
        assert timer.read_timer()["task_id"] == "B" * 21
        assert [p.name for p in timer_file.parent.iterdir()] == ["timer.json"]

    def test_corrupt_file_raises(self, timer_file):
        timer_file.write_text("{not json", encoding="utf-8")
        with pytest.raises(timer.TimerError):
            timer.read_timer()

    def test_wrong_shape_raises(self, timer_file):
        timer_file.write_text('{"task_id": 3}', encoding="utf-8")
        with pytest.raises(timer.TimerError):
            timer.read_timer()


class TestSplitByDay:
    def test_single_day(self):
        start = _ms(2026, 9, 9, 10, 0)
        end = _ms(2026, 9, 9, 10, 30)
        assert timer.split_by_day(start, end) == [("2026-09-09", 1800000)]

    def test_across_midnight(self):
        start = _ms(2026, 9, 8, 23, 50)
        end = _ms(2026, 9, 9, 0, 20)
        assert timer.split_by_day(start, end) == [
            ("2026-09-08", 600000),
            ("2026-09-09", 1200000),
        ]

    def test_three_days(self):
        start = _ms(2026, 9, 8, 23, 0)
        end = _ms(2026, 9, 10, 1, 0)
        days = [d for d, _ in timer.split_by_day(start, end)]
        assert days == ["2026-09-08", "2026-09-09", "2026-09-10"]

    def test_empty_interval(self):
        start = _ms(2026, 9, 9, 10, 0)
        assert timer.split_by_day(start, start) == []

    def test_offset_shifts_the_boundary(self):
        # startOfNextDay 04:00 => work at 01:00 still belongs to the day before.
        diff = 4 * 3600000
        start = _ms(2026, 9, 9, 0, 30)
        end = _ms(2026, 9, 9, 1, 30)
        assert timer.split_by_day(start, end, diff) == [("2026-09-08", 3600000)]

    def test_offset_splits_at_the_configured_hour(self):
        diff = 4 * 3600000
        start = _ms(2026, 9, 9, 3, 30)
        end = _ms(2026, 9, 9, 4, 30)
        assert timer.split_by_day(start, end, diff) == [
            ("2026-09-08", 1800000),
            ("2026-09-09", 1800000),
        ]

    def test_zero_offset_is_plain_midnight(self):
        start = _ms(2026, 9, 8, 23, 50)
        end = _ms(2026, 9, 9, 0, 20)
        assert timer.split_by_day(start, end, 0) == timer.split_by_day(start, end)


class TestStartOfNextDay:
    @staticmethod
    def _misc(d, **misc):
        d["state"]["globalConfig"]["misc"].update(misc)
        return d

    def test_sample_default_is_zero(self, sample):
        assert cli.start_of_next_day_diff_ms(sample) == 0
        assert model.logical_day_of_ms(_ms(2026, 9, 9, 1, 0), sample) == "2026-09-09"

    def test_time_string_wins(self, sample):
        self._misc(sample, startOfNextDayTime="04:30", startOfNextDay=9)
        assert cli.start_of_next_day_diff_ms(sample) == (4 * 60 + 30) * 60000
        assert model.logical_day_of_ms(_ms(2026, 9, 9, 4, 0), sample) == "2026-09-08"
        assert model.logical_day_of_ms(_ms(2026, 9, 9, 5, 0), sample) == "2026-09-09"

    def test_invalid_time_string_resets_to_zero(self, sample):
        # SP #7645: a bad string makes the whole pair untrustworthy — it does
        # NOT fall back to the legacy hour.
        self._misc(sample, startOfNextDayTime="nonsense", startOfNextDay=9)
        assert cli.start_of_next_day_diff_ms(sample) == 0

    def test_legacy_hour_used_only_without_a_time_string(self, sample):
        sample["state"]["globalConfig"]["misc"].pop("startOfNextDayTime")
        self._misc(sample, startOfNextDay=3)
        assert cli.start_of_next_day_diff_ms(sample) == 3 * 3600000
        self._misc(sample, startOfNextDay=0)
        assert cli.start_of_next_day_diff_ms(sample) == 0
        self._misc(sample, startOfNextDay=99)
        assert cli.start_of_next_day_diff_ms(sample) == 0
        self._misc(sample, startOfNextDay=None)
        assert cli.start_of_next_day_diff_ms(sample) == 0

    def test_missing_config_is_zero(self):
        assert cli.start_of_next_day_diff_ms({"state": {}}) == 0
        assert cli.start_of_next_day_diff_ms(None) == 0

    def test_track_defaults_to_the_logical_day(
        self, fake_ctx, sample, add_task_entity, monkeypatch
    ):
        t = add_task_entity(task_id="L" * 21, title="late night")
        self._misc(sample, startOfNextDayTime="04:00")
        monkeypatch.setattr(cli, "now_ms", lambda: _ms(2026, 9, 9, 1, 0))
        monkeypatch.setattr(model, "now_ms", lambda: _ms(2026, 9, 9, 1, 0))
        assert cli.cmd_track(_args(["track", t["id"], "30m"])) == 0
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["date"] == "2026-09-08"

    def test_stop_uses_the_logical_day(
        self, fake_ctx, sample, add_task_entity, timer_file, monkeypatch
    ):
        t = add_task_entity(task_id="T" * 21, title="timed")
        self._misc(sample, startOfNextDayTime="04:00")
        monkeypatch.setattr(cli, "now_ms", lambda: _ms(2026, 9, 9, 1, 0))
        timer.write_timer(t["id"], "timed", _ms(2026, 9, 9, 0, 0))
        assert cli.cmd_stop(_args(["stop"])) == 0
        assert [op["p"]["actionPayload"]["date"] for op in fake_ctx.ops] == [
            "2026-09-08"
        ]


class TestTimerCommands:
    def _task(self, add_task_entity, **kw):
        return add_task_entity(task_id="T" * 21, title="timed", **kw)

    def test_start_writes_file_without_ops(self, fake_ctx, add_task_entity, timer_file):
        t = self._task(add_task_entity)
        assert cli.cmd_start(_args(["start", t["id"]])) == 0
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        running = timer.read_timer()
        assert running["task_id"] == t["id"] and running["title"] == "timed"

    def test_start_rejects_done_task(self, fake_ctx, add_task_entity, timer_file):
        t = self._task(add_task_entity)
        t["isDone"] = True
        with pytest.raises(cli.CliError, match="done"):
            cli.cmd_start(_args(["start", t["id"]]))
        assert timer.read_timer() is None

    def test_bare_start_shows_running(self, fake_ctx, timer_file, capsys):
        timer.write_timer("T" * 21, "timed", cli.now_ms() - 3600000)
        assert cli.cmd_start(_args(["start"])) == 0
        assert "1h" in capsys.readouterr().out

    def test_bare_start_without_timer_is_current(self, fake_ctx, timer_file, capsys):
        # Bare `sp start` is an alias of `sp current`: exit 1, "no timer".
        assert cli.cmd_start(_args(["start"])) == 1
        assert capsys.readouterr().out.strip() == "no timer"

    def test_start_same_task_keeps_timer(self, fake_ctx, add_task_entity, timer_file):
        t = self._task(add_task_entity)
        started = cli.now_ms() - 600000
        timer.write_timer(t["id"], "timed", started)
        assert cli.cmd_start(_args(["start", t["id"]])) == 0
        assert fake_ctx.ops == []
        assert timer.read_timer()["started_at"] == started

    def test_start_auto_stops_previous(
        self, fake_ctx, add_task_entity, timer_file, monkeypatch
    ):
        old = add_task_entity(task_id="O" * 21, title="old")
        new = self._task(add_task_entity)
        now = _ms(2026, 9, 9, 12, 0)
        monkeypatch.setattr(cli, "now_ms", lambda: now)
        timer.write_timer(old["id"], "old", now - 1800000)
        assert cli.cmd_start(_args(["start", new["id"]])) == 0
        assert len(fake_ctx.ops) == 1
        op = fake_ctx.ops[0]
        assert op["a"] == "KT"
        assert op["p"]["actionPayload"] == {
            "taskId": old["id"],
            "date": "2026-09-09",
            "duration": 1800000,
        }
        assert timer.read_timer()["task_id"] == new["id"]

    def test_stop_emits_kt_and_clears_file(
        self, fake_ctx, sample, add_task_entity, timer_file, monkeypatch, capsys
    ):
        t = self._task(add_task_entity)
        now = _ms(2026, 9, 9, 15, 0)
        monkeypatch.setattr(cli, "now_ms", lambda: now)
        timer.write_timer(t["id"], "timed", now - 3600000)
        assert cli.cmd_stop(_args(["stop"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "KT"
        assert op["p"]["actionPayload"]["duration"] == 3600000
        assert op["p"]["entityChanges"][0]["entityId"] == t["id"]
        assert sample["state"]["task"]["entities"][t["id"]]["timeSpent"] == 3600000
        assert timer.read_timer() is None
        assert "1h" in capsys.readouterr().out

    def test_stop_without_timer_errors(self, fake_ctx, timer_file):
        with pytest.raises(cli.CliError, match="no timer"):
            cli.cmd_stop(_args(["stop"]))

    def test_stop_discard_writes_nothing(self, fake_ctx, timer_file):
        timer.write_timer("T" * 21, "timed", cli.now_ms() - 600000)
        assert cli.cmd_stop(_args(["stop", "--discard"])) == 0
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        assert timer.read_timer() is None

    def test_stop_under_a_minute_tracks_one_minute(
        self, fake_ctx, add_task_entity, timer_file, capsys
    ):
        t = self._task(add_task_entity)
        timer.write_timer(t["id"], "timed", cli.now_ms() - 5000)
        assert cli.cmd_stop(_args(["stop"])) == 0
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["duration"] == 60000
        assert "under 1m" in capsys.readouterr().err

    def test_stop_across_midnight_two_ops_one_batch(
        self, fake_ctx, add_task_entity, timer_file, monkeypatch
    ):
        t = self._task(add_task_entity)
        now = _ms(2026, 9, 9, 0, 20)
        monkeypatch.setattr(cli, "now_ms", lambda: now)
        timer.write_timer(t["id"], "timed", _ms(2026, 9, 8, 23, 50))
        assert cli.cmd_stop(_args(["stop"])) == 0
        assert fake_ctx.commits == 1
        assert [op["a"] for op in fake_ctx.ops] == ["KT", "KT"]
        assert [op["p"]["actionPayload"] for op in fake_ctx.ops] == [
            {"taskId": t["id"], "date": "2026-09-08", "duration": 600000},
            {"taskId": t["id"], "date": "2026-09-09", "duration": 1200000},
        ]

    def test_stop_when_task_is_gone_discards_and_exits_0(
        self, fake_ctx, timer_file, capsys
    ):
        timer.write_timer("G" * 21, "vanished", cli.now_ms() - 3600000)
        assert cli.cmd_stop(_args(["stop"])) == 0
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        assert timer.read_timer() is None
        out = capsys.readouterr()
        assert "no longer exists" in out.err
        assert "the task is gone" in out.out

    def test_start_auto_stop_of_gone_task_still_starts(
        self, fake_ctx, add_task_entity, timer_file, capsys
    ):
        new = self._task(add_task_entity)
        timer.write_timer("G" * 21, "vanished", cli.now_ms() - 3600000)
        assert cli.cmd_start(_args(["start", new["id"]])) == 0
        assert fake_ctx.ops == []
        assert "no longer exists" in capsys.readouterr().err
        assert timer.read_timer()["task_id"] == new["id"]

    def test_stop_with_future_start_errors_and_keeps_timer(
        self, fake_ctx, add_task_entity, timer_file
    ):
        t = self._task(add_task_entity)
        timer.write_timer(t["id"], "timed", cli.now_ms() + 3600000)
        with pytest.raises(cli.CliError, match="future"):
            cli.cmd_stop(_args(["stop"]))
        assert fake_ctx.ops == []
        assert timer.read_timer()["task_id"] == t["id"]

    def test_stop_discard_does_not_parse_the_file(self, fake_ctx, timer_file, capsys):
        # --discard is the escape hatch for a corrupt file: clear it blindly.
        timer_file.write_text("{not json", encoding="utf-8")
        assert cli.cmd_stop(_args(["stop", "--discard"])) == 0
        assert not timer_file.exists()
        assert fake_ctx.ops == []

    def test_stop_discard_without_timer_is_still_ok(self, fake_ctx, timer_file):
        assert cli.cmd_stop(_args(["stop", "--discard"])) == 0

    def test_failed_put_keeps_the_timer_file(
        self, sample, add_task_entity, timer_file, monkeypatch
    ):
        t = add_task_entity(task_id="T" * 21, title="timed")

        class _ConflictCtx(_FakeCtx):
            def commit(self, mutations, initial=None):
                raise ConflictError("PUT: HTTP 412 (concurrent write)")

        ctx = _ConflictCtx(sample)
        monkeypatch.setattr(cli, "_ctx", lambda: (ctx, ctx))
        timer.write_timer(t["id"], "timed", cli.now_ms() - 3600000)
        with pytest.raises(ConflictError):
            cli.cmd_stop(_args(["stop"]))
        # The time is still recoverable: the timer file survives.
        assert timer.read_timer()["task_id"] == t["id"]

    def test_current_without_timer(self, timer_file, capsys):
        assert cli.cmd_current(_args(["current"])) == 1
        assert capsys.readouterr().out.strip() == "no timer"

    def test_current_running(self, timer_file, capsys):
        timer.write_timer("T" * 21, "timed", cli.now_ms() - 1800000)
        assert cli.cmd_current(_args(["current"])) == 0
        assert "30m" in capsys.readouterr().out

    def test_current_json(self, timer_file, capsys):
        started = cli.now_ms() - 60000
        timer.write_timer("T" * 21, "timed", started)
        assert cli.cmd_current(_args(["current", "--json"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["task_id"] == "T" * 21 and data["started_at"] == started
        assert data["elapsed"] >= 60000


class TestUntrackCommand:
    def test_untrack_emits_tr(self, fake_ctx, add_task_entity, capsys):
        t = add_task_entity(task_id="U" * 21, title="over")
        assert cli.cmd_untrack(_args(["untrack", t["id"], "10m"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "TR"
        assert op["p"]["actionPayload"] == {
            "id": t["id"],
            "date": today_str(),
            "duration": 600000,
        }
        assert "untracked" in capsys.readouterr().out

    def test_untrack_prints_the_clamped_delta(self, fake_ctx, add_task_entity, capsys):
        t = add_task_entity(task_id="C" * 21, title="clamped")
        t["timeSpentOnDay"] = {today_str(): 300000}
        t["timeSpent"] = 300000
        assert cli.cmd_untrack(_args(["untrack", t["id"], "1h"])) == 0
        # The op still asks for the full hour (SP's reducer clamps), but the
        # message reports what actually came off.
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["duration"] == 3600000
        out = capsys.readouterr().out
        assert "untracked 5m" in out and "1h" not in out


class TestBacklogArgvRewrites:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["backlog", "add", "abc"], "backlog-add"),
            (["backlog", "rm", "abc"], "backlog-rm"),
            (["backlog", "clear", "--project", "inbox"], "backlog-clear"),
        ],
    )
    def test_rewritten(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_bare_backlog_is_the_listing(self):
        assert cli._rewrite_argv(["backlog", "--project", "inbox"]) == [
            "backlog",
            "--project",
            "inbox",
        ]


def _enable_project_backlog(d, project_id="INBOX_PROJECT"):
    project = d["state"]["project"]["entities"][project_id]
    project["isEnableBacklog"] = True
    project.setdefault("backlogTaskIds", [])
    return project


class TestBacklogCommands:
    def test_list_shows_backlog_order(self, fake_ctx, sample, add_task_entity, capsys):
        proj = _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="in the backlog")
        proj["taskIds"].remove(t["id"])
        proj["backlogTaskIds"].append(t["id"])
        assert cli.cmd_backlog(_args(["backlog", "--project", "inbox"])) == 0
        assert "in the backlog" in capsys.readouterr().out

    def test_list_json(self, fake_ctx, sample, add_task_entity, capsys):
        proj = _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="json me")
        proj["taskIds"].remove(t["id"])
        proj["backlogTaskIds"].append(t["id"])
        assert cli.cmd_backlog(_args(["backlog", "--project", "inbox", "--json"])) == 0
        assert json.loads(capsys.readouterr().out)[0]["id"] == t["id"]

    def test_add_emits_prb(self, fake_ctx, sample, add_task_entity):
        _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="later")
        assert cli.cmd_backlog_add(_args(["backlog", "add", t["id"]])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "PRB"
        assert op["p"]["actionPayload"]["workContextId"] == "INBOX_PROJECT"

    def test_add_without_backlog_enabled_errors(self, fake_ctx, add_task_entity):
        t = add_task_entity(task_id="K" * 21, title="later")
        with pytest.raises(cli.mut.MutationError, match="--enable-backlog"):
            cli.cmd_backlog_add(_args(["backlog", "add", t["id"]]))
        assert fake_ctx.ops == []

    def test_rm_emits_pbr(self, fake_ctx, sample, add_task_entity):
        proj = _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="back")
        proj["taskIds"].remove(t["id"])
        proj["backlogTaskIds"].append(t["id"])
        assert cli.cmd_backlog_rm(_args(["backlog", "rm", t["id"]])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "PBR"
        assert op["p"]["actionPayload"]["src"] == "BACKLOG"
        assert proj["backlogTaskIds"] == []

    def test_clear_emits_pba(self, fake_ctx, sample, add_task_entity, capsys):
        proj = _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="back")
        proj["taskIds"].remove(t["id"])
        proj["backlogTaskIds"].append(t["id"])
        assert cli.cmd_backlog_clear(_args(["backlog", "clear", "--project", "inbox"])) == 0
        assert fake_ctx.ops[-1]["a"] == "PBA"
        assert proj["backlogTaskIds"] == []
        assert "1 task(s)" in capsys.readouterr().out

    def test_clear_on_empty_backlog_errors(self, fake_ctx, sample):
        _enable_project_backlog(sample)
        with pytest.raises(cli.CliError, match="already empty"):
            cli.cmd_backlog_clear(_args(["backlog", "clear", "--project", "inbox"]))
        assert fake_ctx.ops == []


class TestAddToBacklogCommand:
    def test_add_backlog_flag(self, fake_ctx, sample, capsys):
        proj = _enable_project_backlog(sample)
        listed_before = list(proj["taskIds"])
        assert cli.cmd_add(_args(["add", "later", "--project", "inbox", "--backlog"])) == 0
        task_id = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert op["a"] == "HA"
        assert op["p"]["actionPayload"]["isAddToBacklog"] is True
        assert proj["backlogTaskIds"] == [task_id]
        assert proj["taskIds"] == listed_before

    def test_add_backlog_with_due_is_rejected(self, fake_ctx, sample):
        _enable_project_backlog(sample)
        args = _args(["add", "later", "--backlog", "--due", "today"])
        with pytest.raises(cli.CliError, match="--backlog is incompatible"):
            cli.cmd_add(args)

    def test_add_backlog_with_parent_is_rejected(self):
        assert cli.main(["add", "sub", "--parent", "abc123", "--backlog"]) == 2


class TestProjectBacklogToggle:
    def test_enable_emits_pu_only(self, fake_ctx, sample):
        assert cli.cmd_project_edit(
            _args(["project", "edit", "inbox", "--enable-backlog"])
        ) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["PU"]
        assert sample["state"]["project"]["entities"]["INBOX_PROJECT"][
            "isEnableBacklog"
        ] is True

    def test_disable_emits_pu_and_pba(self, fake_ctx, sample, add_task_entity):
        proj = _enable_project_backlog(sample)
        t = add_task_entity(task_id="K" * 21, title="stranded")
        proj["taskIds"].remove(t["id"])
        proj["backlogTaskIds"].append(t["id"])
        assert cli.cmd_project_edit(
            _args(["project", "edit", "inbox", "--disable-backlog"])
        ) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["PU", "PBA"]
        assert proj["isEnableBacklog"] is False
        assert proj["backlogTaskIds"] == []
        assert t["id"] in proj["taskIds"]

    def test_enable_and_disable_conflict(self, fake_ctx):
        args = _args(
            ["project", "edit", "inbox", "--enable-backlog", "--disable-backlog"]
        )
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_project_edit(args)

    def test_title_and_toggle_together(self, fake_ctx, sample):
        assert cli.cmd_project_edit(
            _args(["project", "edit", "inbox", "--title", "In", "--enable-backlog"])
        ) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["PU", "PU"]


def _seed_archived(d, task_id, title="archived", key="archiveYoung", **fields):
    from sp_cli.model import make_task

    task = make_task(task_id, title, "INBOX_PROJECT")
    task["isDone"] = True
    task["doneOn"] = 1_700_000_000_000
    task.update(fields)
    blob = d.setdefault(key, {"task": {"ids": [], "entities": {}}})
    reg = blob.setdefault("task", {"ids": [], "entities": {}})
    reg.setdefault("ids", []).append(task_id)
    reg.setdefault("entities", {})[task_id] = task
    return task


class TestArchivedCommands:
    def test_archived_lists_both_blobs(self, fake_ctx, sample, capsys):
        _seed_archived(sample, "Y" * 21, "young one")
        _seed_archived(sample, "O" * 21, "old one", key="archiveOld")
        assert cli.cmd_archived(_args(["archived"])) == 0
        out = capsys.readouterr().out
        assert "young one" in out and "old one" in out
        assert "young" in out and "old" in out

    def test_archived_json_carries_age(self, fake_ctx, sample, capsys):
        _seed_archived(sample, "Y" * 21, "young one")
        assert cli.cmd_archived(_args(["archived", "--json"])) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["age"] == "young"

    def test_archived_search(self, fake_ctx, sample, capsys):
        _seed_archived(sample, "A" * 21, "keep me")
        _seed_archived(sample, "B" * 21, "other")
        assert cli.cmd_archived(_args(["archived", "--search", "keep"])) == 0
        out = capsys.readouterr().out
        assert "keep me" in out and "other" not in out

    def test_archived_empty(self, fake_ctx, sample, capsys):
        assert cli.cmd_archived(_args(["archived"])) == 0
        assert "(none)" in capsys.readouterr().out

    def test_restore_emits_hr_and_prints_subtask_count(
        self, fake_ctx, sample, capsys
    ):
        _seed_archived(sample, "R" * 21, "root", subTaskIds=["S" * 21])
        _seed_archived(sample, "S" * 21, "sub", parentId="R" * 21)
        assert cli.cmd_restore(_args(["restore", "RRR"])) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["HR"]
        out = capsys.readouterr().out
        assert "R" * 21 in out and "+1 subtask" in out
        assert "R" * 21 in sample["state"]["task"]["entities"]
        assert sample["archiveYoung"]["task"]["ids"] == []

    def test_restore_today_flag(self, fake_ctx, sample, capsys):
        _seed_archived(sample, "R" * 21, "root")
        assert cli.cmd_restore(_args(["restore", "RRR", "--today"])) == 0
        payload = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert payload["restoreToToday"]["today"] == today_str()
        assert "to today" in capsys.readouterr().out

    def test_restore_unknown_id_raises(self, fake_ctx, sample):
        with pytest.raises(q.NotFoundError, match="no archived task"):
            cli.cmd_restore(_args(["restore", "nope"]))

    def test_restore_live_task_is_refused(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="live")
        with pytest.raises(q.NotFoundError):
            cli.cmd_restore(_args(["restore", t["id"]]))
        assert fake_ctx.ops == []


class TestHardDeleteCommands:
    @staticmethod
    def _seed_project(sample):
        from sp_cli.model import make_project, make_task

        proj = make_project("P" * 21, "Doomed")
        sample["state"]["project"]["ids"].append(proj["id"])
        sample["state"]["project"]["entities"][proj["id"]] = proj
        task = make_task("T" * 21, "doomed task", proj["id"])
        sample["state"]["task"]["ids"].append(task["id"])
        sample["state"]["task"]["entities"][task["id"]] = task
        proj["taskIds"].append(task["id"])
        return proj

    def test_project_rm_yes_emits_hpd(self, fake_ctx, sample, capsys):
        self._seed_project(sample)
        assert cli.cmd_project_rm(_args(["project", "rm", "Doomed", "--yes"])) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["d"]) == ("HPD", "P" * 21)
        assert op["p"]["actionPayload"]["projectDeleteWins"] is True
        assert "P" * 21 not in sample["state"]["project"]["entities"]
        assert "T" * 21 not in sample["state"]["task"]["entities"]

    def test_project_rm_prompt_warns_tasks_are_not_archived(
        self, fake_ctx, sample, monkeypatch
    ):
        self._seed_project(sample)
        seen = []
        monkeypatch.setattr("builtins.input", lambda p: seen.append(p) or "n")
        assert cli.cmd_project_rm(_args(["project", "rm", "Doomed"])) == 1
        assert "NOT archived" in seen[0]
        assert fake_ctx.ops == [] and fake_ctx.commits == 0

    def test_project_rm_inbox_is_refused(self, fake_ctx, sample):
        with pytest.raises(cli.CliError, match="Inbox"):
            cli.cmd_project_rm(_args(["project", "rm", "inbox", "--yes"]))
        assert fake_ctx.ops == []

    def test_tag_rm_yes_emits_gd(self, fake_ctx, sample):
        tag = make_tag("G" * 21, "doomed")
        sample["state"]["tag"]["ids"].append(tag["id"])
        sample["state"]["tag"]["entities"][tag["id"]] = tag
        assert cli.cmd_tag_rm(_args(["tag", "rm", "doomed", "--yes"])) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["GD"]
        assert fake_ctx.ops[-1]["p"]["actionPayload"] == {"id": "G" * 21}
        assert "G" * 21 not in sample["state"]["tag"]["entities"]

    def test_tag_rm_system_tag_is_refused(self, fake_ctx, sample):
        with pytest.raises(cli.CliError, match="built-in"):
            cli.cmd_tag_rm(_args(["tag", "rm", "TODAY", "--yes"]))
        assert fake_ctx.ops == []

    def test_tag_rm_abort_writes_nothing(self, fake_ctx, sample, monkeypatch):
        tag = make_tag("G" * 21, "doomed")
        sample["state"]["tag"]["ids"].append(tag["id"])
        sample["state"]["tag"]["entities"][tag["id"]] = tag
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        assert cli.cmd_tag_rm(_args(["tag", "rm", "doomed"])) == 1
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        assert "G" * 21 in sample["state"]["tag"]["entities"]

    def test_repeat_rm_yes_emits_hrc(self, fake_ctx, sample):
        sample["state"]["taskRepeatCfg"] = {
            "ids": ["R" * 21],
            "entities": {
                "R" * 21: {"id": "R" * 21, "title": "daily", "projectId": None,
                           "tagIds": []}
            },
        }
        assert cli.cmd_repeat_rm(_args(["repeat", "rm", "daily", "--yes"])) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"], op["d"]) == ("HRC", "TASK_REPEAT_CFG", "R" * 21)
        assert op["p"]["actionPayload"] == {"taskRepeatCfgId": "R" * 21}
        assert sample["state"]["taskRepeatCfg"]["ids"] == []

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["project", "rm", "x"], "project-rm"),
            (["tag", "rm", "x"], "tag-rm"),
            (["repeat", "rm", "x"], "repeat-rm"),
        ],
    )
    def test_subcommand_rewrites(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected


def _seed_repeat_cfg(d, cfg_id="R" * 21, **extra):
    from sp_cli.model import make_repeat_cfg

    cfg = make_repeat_cfg(cfg_id, "daily thing", None, "DAILY", start_time="09:00")
    cfg["remindAt"] = "AtStart"
    cfg.update(extra)
    d["state"]["taskRepeatCfg"] = {"ids": [cfg_id], "entities": {cfg_id: cfg}}
    return cfg


class TestRepeatCreateCli:
    def test_weekly_single_day_gets_current_weekday_preset(
        self, fake_ctx, sample, add_task_entity
    ):
        t = add_task_entity(title="gym")
        cli.cmd_repeat(
            _args(["repeat", t["id"], "--every", "week", "--days", "wed"])
        )
        cfg = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]
        assert cfg["quickSetting"] == "WEEKLY_CURRENT_WEEKDAY"
        assert cfg["wednesday"] is True and cfg["monday"] is False

    def test_weekly_without_days_is_monday_to_friday(
        self, fake_ctx, sample, add_task_entity
    ):
        t = add_task_entity(title="standup")
        cli.cmd_repeat(_args(["repeat", t["id"], "--every", "week"]))
        cfg = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]
        assert cfg["quickSetting"] == "MONDAY_TO_FRIDAY"

    def test_start_time_normalized(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="pills")
        cli.cmd_repeat(
            _args(["repeat", t["id"], "--every", "day", "--start-time", "8:05"])
        )
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["startTime"] == "08:05"

    def test_invalid_start_time_refused(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="pills")
        with pytest.raises(cli.CliError, match="invalid time"):
            cli.cmd_repeat(
                _args(["repeat", t["id"], "--every", "day", "--start-time", "25:99"])
            )
        assert fake_ctx.ops == []

    def test_interval_below_one_refused(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="pills")
        with pytest.raises(cli.CliError, match=">= 1"):
            cli.cmd_repeat(
                _args(["repeat", t["id"], "--every", "day", "--interval", "0"])
            )
        assert fake_ctx.ops == []

    def test_days_on_non_weekly_refused(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="pills")
        with pytest.raises(cli.CliError, match="--every week"):
            cli.cmd_repeat(
                _args(["repeat", t["id"], "--every", "month", "--days", "mon"])
            )
        assert fake_ctx.ops == []

    def test_remind_without_start_time_refused(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="pills")
        with pytest.raises(cli.CliError, match="needs a start time"):
            cli.cmd_repeat(
                _args(["repeat", t["id"], "--every", "day", "--remind", "m10"])
            )
        assert fake_ctx.ops == []


class TestRepeatEditCli:
    def test_pause_emits_ru(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        assert cli.cmd_repeat_edit(_args(["repeat", "edit", "daily thing", "--pause"])) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"], op["d"]) == ("RU", "TASK_REPEAT_CFG", "R" * 21)
        assert op["p"]["actionPayload"]["taskRepeatCfg"]["changes"] == {"isPaused": True}
        assert sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]["isPaused"] is True

    def test_resume(self, fake_ctx, sample):
        _seed_repeat_cfg(sample, isPaused=True)
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--resume"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes == {"isPaused": False}

    def test_pause_and_resume_together_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--pause", "--resume"]))
        assert fake_ctx.ops == []

    def test_clear_goes_to_cleared_fields(self, fake_ctx, sample, capsys):
        _seed_repeat_cfg(sample, defaultEstimate=1800000, notes="n")
        rc = cli.cmd_repeat_edit(
            _args(["repeat", "edit", "RRRR", "--clear", "startTime, remindAt"])
        )
        assert rc == 0
        payload = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert payload["clearedFields"] == ["startTime", "remindAt"]
        assert payload["taskRepeatCfg"]["changes"] == {}
        # clearedFields is a SIBLING of taskRepeatCfg — nested it is ignored.
        assert "clearedFields" not in payload["taskRepeatCfg"]
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]
        assert "startTime" not in cfg and "remindAt" not in cfg

    def test_clear_unknown_field_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="cannot clear"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--clear", "isPaused"]))
        assert fake_ctx.ops == []

    def test_clear_and_set_same_field_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="both set and cleared"):
            cli.cmd_repeat_edit(
                _args(["repeat", "edit", "RRRR", "--start-time", "8:00",
                       "--clear", "startTime"])
            )
        assert fake_ctx.ops == []

    def test_nothing_to_change_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR"]))

    def test_cadence_switch_to_weekly_days(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_edit(
            _args(["repeat", "edit", "RRRR", "--every", "week", "--days", "mon,thu"])
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes["repeatCycle"] == "WEEKLY"
        assert changes["quickSetting"] == "CUSTOM"
        assert changes["monday"] is True and changes["thursday"] is True
        assert changes["tuesday"] is False and changes["sunday"] is False
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]
        assert cfg["friday"] is False

    def test_every_without_days_resets_to_sp_default(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--every", "week"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        # mon-fri booleans are exactly what MONDAY_TO_FRIDAY means — tagging them
        # WEEKLY_CURRENT_WEEKDAY would make SP rewrite them to a single weekday.
        assert changes["quickSetting"] == "MONDAY_TO_FRIDAY"
        assert changes["repeatEvery"] == 1
        assert changes["saturday"] is False and changes["monday"] is True

    def test_interval_only_keeps_custom_weekdays(self, fake_ctx, sample):
        cfg = _seed_repeat_cfg(sample)
        cfg.update(
            model.repeat_cadence_fields("WEEKLY", 1, ["monday", "thursday"])
        )
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--interval", "2"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes["repeatEvery"] == 2
        assert changes["repeatCycle"] == "WEEKLY"
        assert changes["quickSetting"] == "CUSTOM"
        assert changes["monday"] is True and changes["thursday"] is True
        assert changes["tuesday"] is False

    def test_interval_keeps_weekdays_of_sp_authored_cfg(self, fake_ctx, sample):
        # SP writes single-weekday cfgs as WEEKLY_CURRENT_WEEKDAY, not CUSTOM;
        # a plain --interval change must still keep that weekday.
        cfg = _seed_repeat_cfg(sample)
        cfg.update(model.repeat_cadence_fields("WEEKLY", 1, ["wednesday"]))
        assert cfg["quickSetting"] == "WEEKLY_CURRENT_WEEKDAY"
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--interval", "2"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes["wednesday"] is True
        assert changes["monday"] is False and changes["friday"] is False
        assert changes["quickSetting"] == "CUSTOM"

    def test_interval_on_mon_fri_cfg_keeps_mon_fri(self, fake_ctx, sample):
        cfg = _seed_repeat_cfg(sample)
        cfg.update(model.repeat_cadence_fields("WEEKLY", 1, None))
        assert cfg["quickSetting"] == "MONDAY_TO_FRIDAY"
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--interval", "3"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes["friday"] is True and changes["sunday"] is False
        assert changes["quickSetting"] == "CUSTOM"

    def test_non_weekly_edit_does_not_touch_weekdays(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--every", "month"]))
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes["quickSetting"] == "MONTHLY_CURRENT_DATE"
        assert not any(k in changes for k in model.WEEKDAY_KEYS)

    def test_switch_to_monthly_clears_anchors(self, fake_ctx, sample):
        _seed_repeat_cfg(
            sample, monthlyWeekOfMonth=2, monthlyWeekday=3, monthlyLastDay=True
        )
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--every", "month"]))
        payload = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert payload["clearedFields"] == [
            "monthlyWeekOfMonth", "monthlyWeekday", "monthlyLastDay",
        ]
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]
        assert not any(k in cfg for k in model.MONTHLY_ANCHOR_FIELDS)

    def test_anchor_clear_skipped_when_cycle_unchanged(self, fake_ctx, sample):
        _seed_repeat_cfg(sample, monthlyLastDay=True)
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--interval", "2"]))
        payload = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert "clearedFields" not in payload

    def test_interval_below_one_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match=">= 1"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--interval", "0"]))
        assert fake_ctx.ops == []

    def test_days_on_non_weekly_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="--every week"):
            cli.cmd_repeat_edit(
                _args(["repeat", "edit", "RRRR", "--every", "month", "--days", "mon"])
            )
        assert fake_ctx.ops == []

    def test_clear_start_time_also_clears_remind(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--clear", "startTime"]))
        payload = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert payload["clearedFields"] == ["startTime", "remindAt"]
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]
        assert "remindAt" not in cfg

    def test_remind_without_start_time_refused(self, fake_ctx, sample):
        cfg = _seed_repeat_cfg(sample)
        del cfg["startTime"]
        with pytest.raises(cli.CliError, match="needs a start time"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--remind", "m10"]))
        assert fake_ctx.ops == []

    def test_remind_with_start_time_in_same_edit_ok(self, fake_ctx, sample):
        cfg = _seed_repeat_cfg(sample)
        del cfg["startTime"]
        cli.cmd_repeat_edit(
            _args(["repeat", "edit", "RRRR", "--start-time", "8:00",
                   "--remind", "m10"])
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes == {"startTime": "08:00", "remindAt": "m10"}

    def test_invalid_start_time_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="invalid time"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--start-time", "25:99"]))
        assert fake_ctx.ops == []

    def test_empty_title_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="empty title"):
            cli.cmd_repeat_edit(_args(["repeat", "edit", "RRRR", "--title", ""]))
        assert fake_ctx.ops == []

    def test_scalar_flags(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_edit(
            _args(["repeat", "edit", "RRRR", "--title", "new", "--start-time", "07:30",
                   "--remind", "m10", "--est", "45m", "--notes", "hi",
                   "--start-date", "2026-10-01"])
        )
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskRepeatCfg"]["changes"]
        assert changes == {
            "title": "new",
            "startTime": "07:30",
            "remindAt": "m10",
            "defaultEstimate": 45 * 60 * 1000,
            "notes": "hi",
            "startDate": "2026-10-01",
        }

    def test_invalid_weekday_refused(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        with pytest.raises(cli.CliError, match="invalid weekday"):
            cli.cmd_repeat_edit(
                _args(["repeat", "edit", "RRRR", "--every", "week", "--days", "xxx"])
            )


class TestRepeatSkipCli:
    def test_skip_emits_rdi_and_hints(self, fake_ctx, sample, capsys):
        _seed_repeat_cfg(sample)
        rc = cli.cmd_repeat_skip(
            _args(["repeat", "skip", "RRRR", "--date", "2026-10-01"])
        )
        assert rc == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"], op["d"]) == (
            "RDI", "UPD", "TASK_REPEAT_CFG", "R" * 21,
        )
        assert op["p"]["actionPayload"] == {
            "repeatCfgId": "R" * 21, "dateStr": "2026-10-01",
        }
        out = capsys.readouterr().out
        assert f"sp delete rpt_{'R' * 21}_2026-10-01" in out

    def test_skip_twice_is_idempotent(self, fake_ctx, sample, capsys):
        _seed_repeat_cfg(sample, deletedInstanceDates=["2026-10-01"])
        assert cli.cmd_repeat_skip(
            _args(["repeat", "skip", "RRRR", "--date", "2026-10-01"])
        ) == 0
        assert fake_ctx.ops == []
        assert "already skipped" in capsys.readouterr().out
        cfg = sample["state"]["taskRepeatCfg"]["entities"]["R" * 21]
        assert cfg["deletedInstanceDates"] == ["2026-10-01"]

    def test_skip_today_keyword_is_logical_today(self, fake_ctx, sample):
        _seed_repeat_cfg(sample)
        cli.cmd_repeat_skip(_args(["repeat", "skip", "RRRR", "--date", "today"]))
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["dateStr"] == model.logical_today_str(
            sample
        )

    def test_skip_today_honours_start_of_next_day(self, fake_ctx, sample, monkeypatch):
        _seed_repeat_cfg(sample)
        sample["state"].setdefault("globalConfig", {}).setdefault("misc", {})[
            "startOfNextDayTime"
        ] = "04:00"
        # 02:00 wall clock is still "yesterday" for SP with a 04:00 day boundary.
        two_am = dt.datetime(2026, 10, 2, 2, 0)
        monkeypatch.setattr(
            model, "now_ms", lambda: int(two_am.timestamp() * 1000)
        )
        cli.cmd_repeat_skip(_args(["repeat", "skip", "RRRR", "--date", "today"]))
        assert fake_ctx.ops[-1]["p"]["actionPayload"]["dateStr"] == "2026-10-01"


class TestRepeatsListing:
    def test_listing_shows_pause_time_and_skips(self, fake_ctx, sample, capsys):
        _seed_repeat_cfg(
            sample, isPaused=True,
            deletedInstanceDates=["2026-10-01", "2026-10-02", "2026-10-03"],
        )
        assert cli.cmd_repeats(_args(["repeats"])) == 0
        out = capsys.readouterr().out
        assert "paused" in out
        assert "09:00" in out and "AtStart" in out
        assert "3 (2026-10-02, 2026-10-03)" in out

    def test_json_listing(self, fake_ctx, sample, capsys):
        _seed_repeat_cfg(sample)
        assert cli.cmd_repeats(_args(["repeats", "--json"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert data[0]["id"] == "R" * 21

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["repeat", "edit", "x"], "repeat-edit"),
            (["repeat", "skip", "x"], "repeat-skip"),
        ],
    )
    def test_subcommand_rewrites(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected


class TestReorderConvertCommands:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["today", "move", "x", "--up"], "today-move"),
            (["plan", "move", "x", "--before", "y"], "plan-move"),
            (["subtask", "move", "x", "--up"], "subtask-move"),
            (["subtask", "reparent", "x", "--parent", "y"], "subtask-reparent"),
        ],
    )
    def test_subcommand_rewrites(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    @staticmethod
    def _seed(sample, n=3):
        from sp_cli.model import make_task

        ids = []
        for i in range(n):
            tid = chr(65 + i) * 21
            task = make_task(tid, f"t{i}", "INBOX_PROJECT")
            sample["state"]["task"]["ids"].append(tid)
            sample["state"]["task"]["entities"][tid] = task
            sample["state"]["project"]["entities"]["INBOX_PROJECT"]["taskIds"].append(tid)
            ids.append(tid)
        return ids

    @staticmethod
    def _seed_sub(sample, parent_id, sub_id):
        from sp_cli.model import make_task

        sub = make_task(sub_id, "sub", "INBOX_PROJECT", parent_id=parent_id)
        sample["state"]["task"]["ids"].append(sub_id)
        sample["state"]["task"]["entities"][sub_id] = sub
        sample["state"]["task"]["entities"][parent_id]["subTaskIds"].append(sub_id)
        return sub

    def test_today_move_up(self, fake_ctx, sample):
        ids = self._seed(sample)
        sample["state"]["tag"]["entities"]["TODAY"]["taskIds"] = list(ids)
        assert cli.cmd_today_move(_args(["today", "move", ids[2], "--up"])) == 0
        assert fake_ctx.ops[-1]["a"] == "WMU"

    def test_today_move_before(self, fake_ctx, sample):
        ids = self._seed(sample)
        sample["state"]["tag"]["entities"]["TODAY"]["taskIds"] = list(ids)
        argv = ["today", "move", ids[2], "--before", ids[0]]
        assert cli.cmd_today_move(_args(argv)) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "HMT" and op["ds"] == [ids[0], ids[2]]

    def test_today_move_needs_a_direction(self, fake_ctx, sample):
        ids = self._seed(sample)
        with pytest.raises(cli.CliError, match="--before"):
            cli.cmd_today_move(_args(["today", "move", ids[0]]))
        assert fake_ctx.ops == []

    def test_today_move_rejects_mixed_flags(self, fake_ctx, sample):
        ids = self._seed(sample)
        argv = ["today", "move", ids[0], "--before", ids[1], "--up"]
        with pytest.raises(cli.CliError, match="cannot be combined"):
            cli.cmd_today_move(_args(argv))

    def test_two_directions_rejected(self, fake_ctx, sample):
        ids = self._seed(sample)
        argv = ["today", "move", ids[0], "--up", "--down"]
        with pytest.raises(cli.CliError, match="exactly one direction"):
            cli.cmd_today_move(_args(argv))

    def test_move_in_project_top(self, fake_ctx, sample):
        ids = self._seed(sample)
        assert cli.cmd_move_in_project(_args(["move-in-project", ids[2], "--top"])) == 0
        assert fake_ctx.ops[-1]["a"] == "WMT"
        assert sample["state"]["project"]["entities"]["INBOX_PROJECT"]["taskIds"][0] == ids[2]

    def test_move_in_project_after(self, fake_ctx, sample):
        ids = self._seed(sample)
        argv = ["move-in-project", ids[0], "--after", ids[2]]
        assert cli.cmd_move_in_project(_args(argv)) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "WM" and op["p"]["actionPayload"]["afterTaskId"] == ids[2]

    def test_move_in_project_needs_an_option(self, fake_ctx, sample):
        ids = self._seed(sample)
        with pytest.raises(cli.CliError, match="--after"):
            cli.cmd_move_in_project(_args(["move-in-project", ids[0]]))

    def test_subtask_move(self, fake_ctx, sample):
        ids = self._seed(sample, n=1)
        sub = self._seed_sub(sample, ids[0], "S" * 21)
        self._seed_sub(sample, ids[0], "T" * 21)
        assert cli.cmd_subtask_move(_args(["subtask", "move", sub["id"], "--down"])) == 0
        assert fake_ctx.ops[-1]["a"] == "TMD"

    def test_subtask_move_needs_a_direction(self, fake_ctx, sample):
        ids = self._seed(sample, n=1)
        sub = self._seed_sub(sample, ids[0], "S" * 21)
        with pytest.raises(cli.CliError, match="--up"):
            cli.cmd_subtask_move(_args(["subtask", "move", sub["id"]]))

    def test_subtask_reparent(self, fake_ctx, sample):
        ids = self._seed(sample, n=2)
        sub = self._seed_sub(sample, ids[0], "S" * 21)
        argv = ["subtask", "reparent", sub["id"], "--parent", ids[1]]
        assert cli.cmd_subtask_reparent(_args(argv)) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "TMS"
        assert op["p"]["actionPayload"]["targetTaskId"] == ids[1]
        assert sub["parentId"] == ids[1]

    def test_demote(self, fake_ctx, sample):
        ids = self._seed(sample, n=2)
        assert cli.cmd_demote(_args(["demote", ids[1], "--parent", ids[0]])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "HCS"
        assert op["p"]["actionPayload"]["targetParentId"] == ids[0]

    def test_demote_reports_the_reason(self, fake_ctx, sample):
        ids = self._seed(sample, n=2)
        sample["state"]["task"]["entities"][ids[1]]["repeatCfgId"] = "R" * 21
        with pytest.raises(mut.MutationError, match="repeating"):
            cli.cmd_demote(_args(["demote", ids[1], "--parent", ids[0]]))
        assert fake_ctx.ops == []

    def test_promote(self, fake_ctx, sample):
        ids = self._seed(sample, n=1)
        sub = self._seed_sub(sample, ids[0], "S" * 21)
        assert cli.cmd_promote(_args(["promote", sub["id"], "--today"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "HC"
        assert op["p"]["actionPayload"]["isPlanForToday"] is True

    def test_plan_move(self, fake_ctx, sample):
        ids = self._seed(sample, n=2)
        sample["state"]["planner"]["days"]["2030-01-01"] = [ids[0]]
        sample["state"]["task"]["entities"][ids[1]]["dueDay"] = "2030-01-02"
        sample["state"]["planner"]["days"]["2030-01-02"] = [ids[1]]
        argv = ["plan", "move", ids[0], "--before", ids[1]]
        assert cli.cmd_plan_move(_args(argv)) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"]) == ("LB", "PLANNER")
        assert sample["state"]["planner"]["days"]["2030-01-02"] == [ids[0], ids[1]]


class TestAttachArgvRewrite:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["attach", "edit", "T1", "A1"], "attach-edit"),
            (["attach", "rm", "T1", "A1"], "attach-rm"),
        ],
    )
    def test_subcommands_rewritten(self, argv, expected):
        assert cli._rewrite_argv(argv)[0] == expected

    def test_attach_with_path_is_not_rewritten(self):
        argv = ["attach", "T1", "https://a"]
        assert cli._rewrite_argv(argv) == argv

    def test_attach_without_path_lists(self):
        assert cli._rewrite_argv(["attach", "T1"]) == ["attachments", "T1"]
        assert cli._rewrite_argv(["attach", "T1", "--json"]) == [
            "attachments",
            "T1",
            "--json",
        ]


class TestAttachCommands:
    def _task(self, sample, add_task_entity):
        return add_task_entity(title="task with links")

    def _attach(self, sample, task, **kw):
        from sp_cli.model import make_attachment

        att = make_attachment(kw.pop("aid", "A" * 21), kw.pop("path", "https://a"), **kw)
        task.setdefault("attachments", []).append(att)
        return att

    def test_attach_prints_id_and_emits_xa(
        self, fake_ctx, sample, add_task_entity, capsys
    ):
        task = self._task(sample, add_task_entity)
        argv = ["attach", task["id"], "https://example.com/spec.pdf"]
        assert cli.cmd_attach(_args(argv)) == 0
        printed = capsys.readouterr().out.strip()
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"], op["d"]) == ("XA", "UPD", "TASK", task["id"])
        att = op["p"]["actionPayload"]["taskAttachment"]
        assert att["id"] == printed
        assert (att["type"], att["icon"], att["title"]) == (
            "LINK",
            "bookmark",
            "spec.pdf",
        )
        assert task["attachments"][-1]["id"] == printed

    def test_attach_flags_override(self, fake_ctx, sample, add_task_entity):
        task = self._task(sample, add_task_entity)
        argv = [
            "attach",
            task["id"],
            "https://example.com/x.png",
            "--title",
            "Mockup",
            "--type",
            "file",
        ]
        assert cli.cmd_attach(_args(argv)) == 0
        att = fake_ctx.ops[-1]["p"]["actionPayload"]["taskAttachment"]
        assert (att["type"], att["icon"], att["title"]) == (
            "FILE",
            "insert_drive_file",
            "Mockup",
        )

    def test_attach_to_missing_task_writes_nothing(self, fake_ctx, sample):
        with pytest.raises(q.NotFoundError):
            cli.cmd_attach(_args(["attach", "Z" * 21, "https://a"]))
        assert fake_ctx.ops == []

    def test_attachments_lists(self, fake_ctx, sample, add_task_entity, capsys):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21, path="https://a/one.txt")
        assert cli.cmd_attachments(_args(["attach", task["id"]])) == 0
        out = capsys.readouterr().out
        assert "one.txt" in out and "LINK" in out

    def test_attachments_json(self, fake_ctx, sample, add_task_entity, capsys):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21, path="https://a/one.txt")
        assert cli.cmd_attachments(_args(["attach", task["id"], "--json"])) == 0
        data = json.loads(capsys.readouterr().out)
        assert [a["id"] for a in data] == ["B" * 21]

    def test_attachments_empty(self, fake_ctx, sample, add_task_entity, capsys):
        task = self._task(sample, add_task_entity)
        assert cli.cmd_attachments(_args(["attach", task["id"]])) == 0
        assert "(none)" in capsys.readouterr().out

    def test_edit_by_prefix_emits_xu(self, fake_ctx, sample, add_task_entity):
        task = self._task(sample, add_task_entity)
        att = self._attach(sample, task, aid="B" * 21, path="https://a/one.txt")
        argv = ["attach", "edit", task["id"], "BBBB", "--title", "renamed"]
        assert cli.cmd_attach_edit(_args(argv)) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "XU" and op["o"] == "UPD" and op["e"] == "TASK"
        assert op["p"]["actionPayload"]["taskAttachment"] == {
            "id": att["id"],
            "changes": {"title": "renamed"},
        }
        assert task["attachments"][0]["title"] == "renamed"

    def test_edit_type_also_swaps_the_icon(self, fake_ctx, sample, add_task_entity):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21, path="https://a/one.txt")
        argv = ["attach", "edit", task["id"], "BBBB", "--type", "img", "--path", "p.png"]
        assert cli.cmd_attach_edit(_args(argv)) == 0
        changes = fake_ctx.ops[-1]["p"]["actionPayload"]["taskAttachment"]["changes"]
        assert changes == {"path": "p.png", "type": "IMG", "icon": "image"}

    def test_edit_without_flags_is_refused(self, fake_ctx, sample, add_task_entity):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21)
        with pytest.raises(cli.CliError, match="nothing to change"):
            cli.cmd_attach_edit(_args(["attach", "edit", task["id"], "BBBB"]))
        assert fake_ctx.ops == []

    def test_rm_emits_xd(self, fake_ctx, sample, add_task_entity, capsys):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21)
        assert cli.cmd_attach_rm(_args(["attach", "rm", task["id"], "BBBB"])) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["o"], op["e"]) == ("XD", "UPD", "TASK")
        assert op["p"]["actionPayload"] == {"taskId": task["id"], "id": "B" * 21}
        assert task["attachments"] == []
        assert "deleted" in capsys.readouterr().out

    def test_rm_unknown_attachment_writes_nothing(
        self, fake_ctx, sample, add_task_entity
    ):
        task = self._task(sample, add_task_entity)
        with pytest.raises(q.NotFoundError):
            cli.cmd_attach_rm(_args(["attach", "rm", task["id"], "ZZZZ"]))
        assert fake_ctx.ops == []

    def test_show_renders_attachments(self, fake_ctx, sample, add_task_entity, capsys):
        task = self._task(sample, add_task_entity)
        self._attach(sample, task, aid="B" * 21, path="https://a/one.txt", title="One")
        assert cli.cmd_show(_args(["show", task["id"]])) == 0
        out = capsys.readouterr().out
        assert "attachments:" in out
        assert "[LINK] One — https://a/one.txt" in out


class TestDeadlineClearCommands:
    def test_clear_emits_hxd(self, fake_ctx, sample, add_task_entity, capsys):
        t = add_task_entity(title="dl")
        t["deadlineDay"] = "2030-06-01"
        assert cli.cmd_deadline(_args(["deadline", t["id"], "--clear"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "HXD"
        assert op["p"]["actionPayload"] == {"taskId": t["id"]}
        assert "cleared" in capsys.readouterr().out

    def test_clear_reminder_emits_hcr(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        t["deadlineWithTime"] = 1900000000000
        t["deadlineRemindAt"] = 1899999000000
        assert cli.cmd_deadline(_args(["deadline", t["id"], "--clear-reminder"])) == 0
        op = fake_ctx.ops[-1]
        assert op["a"] == "HCR"
        assert op["p"]["actionPayload"] == {"taskId": t["id"]}
        assert sample["state"]["task"]["entities"][t["id"]]["deadlineWithTime"] == (
            1900000000000
        )

    def test_clear_rejects_both_flags(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        with pytest.raises(cli.CliError, match="exclusive"):
            cli.cmd_deadline(
                _args(["deadline", t["id"], "--clear", "--clear-reminder"])
            )
        assert fake_ctx.ops == []

    def test_clear_rejects_a_value_flag(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        with pytest.raises(cli.CliError, match="takes no"):
            cli.cmd_deadline(
                _args(["deadline", t["id"], "--clear", "--day", "2030-06-01"])
            )
        assert fake_ctx.ops == []

    def test_clear_rejects_day_and_remind(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        for extra in (["--day", "2030-06-01"], ["--remind", "1h"]):
            with pytest.raises(cli.CliError, match="takes no"):
                cli.cmd_deadline(
                    _args(["deadline", t["id"], "--clear-reminder", *extra])
                )
        assert fake_ctx.ops == []

    def test_a_day_edit_drops_the_reminder(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        t["deadlineWithTime"] = 1900000000000
        t["deadlineRemindAt"] = 1899999000000
        assert cli.cmd_deadline(
            _args(["deadline", t["id"], "--day", "2030-07-01"])
        ) == 0
        p = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert p["deadlineDay"] == "2030-07-01"
        assert "deadlineRemindAt" not in p
        assert sample["state"]["task"]["entities"][t["id"]]["deadlineRemindAt"] is None

    def test_timed_edit_recomputes_the_reminder_offset(
        self, fake_ctx, sample, add_task_entity
    ):
        t = add_task_entity(title="dl")
        ts = int(dt.datetime(2030, 6, 1, 12, 0).timestamp() * 1000)
        t["deadlineWithTime"] = ts
        t["deadlineRemindAt"] = ts - 3600000
        assert cli.cmd_deadline(
            _args(["deadline", t["id"], "--at", "2030-06-02 12:00"])
        ) == 0
        p = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert p["deadlineRemindAt"] == p["deadlineWithTime"] - 3600000

    def test_no_reminder_in_payload_when_nothing_supplies_one(
        self, fake_ctx, sample, add_task_entity
    ):
        t = add_task_entity(title="dl")
        assert cli.cmd_deadline(
            _args(["deadline", t["id"], "--at", "2030-06-02 12:00"])
        ) == 0
        assert "deadlineRemindAt" not in fake_ctx.ops[-1]["p"]["actionPayload"]

    def test_remind_none_drops_the_reminder(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        t["deadlineWithTime"] = 1900000000000
        t["deadlineRemindAt"] = 1899999000000
        assert cli.cmd_deadline(
            _args(["deadline", t["id"], "--at", "2030-06-02 12:00", "--remind", "none"])
        ) == 0
        p = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert "deadlineRemindAt" not in p
        assert sample["state"]["task"]["entities"][t["id"]]["deadlineRemindAt"] is None

    def test_remind_none_is_accepted_with_day(self, fake_ctx, sample, add_task_entity):
        t = add_task_entity(title="dl")
        assert cli.cmd_deadline(
            _args(["deadline", t["id"], "--day", "2030-07-01", "--remind", "none"])
        ) == 0
        p = fake_ctx.ops[-1]["p"]["actionPayload"]
        assert p["deadlineDay"] == "2030-07-01"
        assert "deadlineRemindAt" not in p

    def test_remind_offset_with_day_is_rejected(
        self, fake_ctx, sample, add_task_entity
    ):
        t = add_task_entity(title="dl")
        with pytest.raises(cli.CliError, match="carries no reminder"):
            cli.cmd_deadline(
                _args(["deadline", t["id"], "--day", "2030-07-01", "--remind", "1h"])
            )
        assert fake_ctx.ops == []

    def test_dismiss_emits_hrx_with_id_key(
        self, fake_ctx, sample, add_task_entity, capsys
    ):
        t = add_task_entity(title="rem", due_with_time=1900000000000)
        t["remindAt"] = 1899999000000
        assert cli.cmd_dismiss(_args(["dismiss", t["id"]])) == 0
        op = fake_ctx.ops[-1]
        assert (op["a"], op["e"]) == ("HRX", "TASK")
        assert op["p"]["actionPayload"] == {"id": t["id"]}
        task = sample["state"]["task"]["entities"][t["id"]]
        assert task["remindAt"] is None
        assert task["dueWithTime"] == 1900000000000
        assert "dismissed" in capsys.readouterr().out


def _seed_shortsyntax_world(d):
    """A project and a tag for the short-syntax integration tests."""
    state = d["state"]
    state["project"]["ids"].append("P_WORK")
    state["project"]["entities"]["P_WORK"] = {
        "id": "P_WORK",
        "title": "Work",
        "taskIds": [],
        "backlogTaskIds": [],
        "isEnableBacklog": True,
    }
    state["tag"]["ids"].append("T_HOME")
    state["tag"]["entities"]["T_HOME"] = {
        "id": "T_HOME",
        "title": "home",
        "taskIds": [],
    }


def _payload(ops, action):
    for op in ops:
        if op["a"] == action:
            return op["p"]["actionPayload"]
    raise AssertionError(f"no {action} op in {[o['a'] for o in ops]}")


class TestAddShortSyntax:
    def test_full_title_is_parsed(self, fake_ctx, sample, capsys):
        _seed_shortsyntax_world(sample)
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        assert cli.cmd_add(
            _args(["add", "Buy milk #home +Work @tomorrow 30m"])
        ) == 0
        out = capsys.readouterr()
        task = _payload(fake_ctx.ops, "HA")["task"]
        assert task["title"] == "Buy milk"
        assert task["projectId"] == "P_WORK"
        assert task["tagIds"] == ["T_HOME"]
        assert task["timeEstimate"] == 1_800_000
        assert task["dueDay"] == tomorrow
        assert out.out.strip() == task["id"]  # stdout stays machine-readable
        assert "parsed:" in out.err

    def test_unknown_tag_is_created_with_ga_before_ha(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x #brandnew"])) == 0
        actions = [op["a"] for op in fake_ctx.ops]
        assert actions.index("GA") < actions.index("HA")
        tag = _payload(fake_ctx.ops, "GA")["tag"]
        assert tag["title"] == "brandnew"
        assert _payload(fake_ctx.ops, "HA")["task"]["tagIds"] == [tag["id"]]

    def test_no_parse_keeps_the_raw_title(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x #home +Work 30m", "--no-parse"])) == 0
        task = _payload(fake_ctx.ops, "HA")["task"]
        assert task["title"] == "x #home +Work 30m"
        assert task["projectId"] == "INBOX_PROJECT"
        assert task["tagIds"] == [] and task["timeEstimate"] == 0

    def test_explicit_flags_override_parsed_values(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        argv = [
            "add",
            "x +Work #home @tomorrow 30m",
            "--project",
            "inbox",
            "--tag",
            "home",
            "--est",
            "2h",
            "--due",
            "2030-01-01",
        ]
        assert cli.cmd_add(_args(argv)) == 0
        task = _payload(fake_ctx.ops, "HA")["task"]
        assert task["projectId"] == "INBOX_PROJECT"
        assert task["tagIds"] == ["T_HOME"]
        assert task["timeEstimate"] == 7_200_000
        assert task["dueDay"] == "2030-01-01"
        assert task["title"] == "x"  # the title is still cleaned

    def test_time_with_slash_tracks_spent_time(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x 1h/2h"])) == 0
        assert _payload(fake_ctx.ops, "HA")["task"]["timeEstimate"] == 7_200_000
        kt = _payload(fake_ctx.ops, "KT")
        assert kt["duration"] == 3_600_000
        assert kt["date"] == model.logical_today_str(sample)

    def test_due_with_time_emits_hs_after_ha(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x @tomorrow 9:00"])) == 0
        actions = [op["a"] for op in fake_ctx.ops]
        assert actions == ["HA", "HS"]
        expected = dt.datetime.combine(
            dt.date.today() + dt.timedelta(days=1), dt.time(9, 0)
        )
        assert _payload(fake_ctx.ops, "HS")["dueWithTime"] == int(
            expected.timestamp() * 1000
        )

    def test_deadline_needs_the_flag(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x !2030-01-01"])) == 0
        assert [op["a"] for op in fake_ctx.ops] == ["HA"]
        assert _payload(fake_ctx.ops, "HA")["task"]["title"] == "x !2030-01-01"

    def test_deadline_with_flag_emits_hdl(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "x !2030-01-01", "--parse-deadline"])) == 0
        assert _payload(fake_ctx.ops, "HDL")["deadlineDay"] == "2030-01-01"
        assert _payload(fake_ctx.ops, "HA")["task"]["title"] == "x"

    def test_every_emits_ra_with_a_weekly_cfg(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(_args(["add", "standup @every monday +Work"])) == 0
        actions = [op["a"] for op in fake_ctx.ops]
        assert actions == ["HA", "RA"]
        cfg = _payload(fake_ctx.ops, "RA")["taskRepeatCfg"]
        assert cfg["quickSetting"] == "WEEKLY_CURRENT_WEEKDAY"
        assert cfg["repeatCycle"] == "WEEKLY" and cfg["monday"] is True
        assert cfg["title"] == "standup" and cfg["projectId"] == "P_WORK"
        assert cfg["lastTaskCreationDay"] == model.today_str()
        task_id = _payload(fake_ctx.ops, "HA")["task"]["id"]
        assert sample["state"]["task"]["entities"][task_id]["repeatCfgId"] == cfg["id"]

    def test_daily_preset(self, fake_ctx, sample):
        assert cli.cmd_add(_args(["add", "pills @daily"])) == 0
        cfg = _payload(fake_ctx.ops, "RA")["taskRepeatCfg"]
        assert (cfg["quickSetting"], cfg["repeatCycle"]) == ("DAILY", "DAILY")

    def test_backlog_drops_a_parsed_due(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(
            _args(["add", "later +Work @tomorrow", "--backlog"])
        ) == 0
        task = _payload(fake_ctx.ops, "HA")["task"]
        assert task.get("dueDay") is None
        assert sample["state"]["project"]["entities"]["P_WORK"]["backlogTaskIds"] == [
            task["id"]
        ]

    def test_subtask_is_never_parsed(self, fake_ctx, sample, add_task_entity):
        parent = add_task_entity(title="parent")
        assert cli.cmd_add(
            _args(["add", "sub #home 30m", "--parent", parent["id"]])
        ) == 0
        sub = _payload(fake_ctx.ops, "TA")["task"]
        assert sub["title"] == "sub #home 30m"

    def test_state_stays_consistent(self, fake_ctx, sample):
        _seed_shortsyntax_world(sample)
        assert cli.cmd_add(
            _args(["add", "Report #home #new +Work @tomorrow 1h/2h"])
        ) == 0
        from conftest import assert_doctor_clean

        assert_doctor_clean(sample)


# ---------------------------------------------------------------- bulk edits

def _actions(ops):
    return [op["a"] for op in ops]


class TestMultiIdEdit:
    def test_two_ids_emit_two_hu_in_one_batch(self, fake_ctx, sample, add_task_entity):
        a = add_task_entity(title="a")
        b = add_task_entity(title="b")
        rc = cli.cmd_edit(_args(["edit", a["id"], b["id"], "--est", "30m"]))
        assert rc == 0
        assert _actions(fake_ctx.ops) == ["HU", "HU"]
        assert fake_ctx.commits == 1  # one batch, one syncVersion increment
        assert [op["d"] for op in fake_ctx.ops] == [a["id"], b["id"]]
        for op in fake_ctx.ops:
            assert op["p"]["actionPayload"]["task"]["changes"]["timeEstimate"] == 1_800_000

    def test_clock_counter_is_cumulative_and_sync_version_bumps_once(
        self, fake_ctx, sample, add_task_entity
    ):
        from sp_cli.ops import finalize

        ids = [add_task_entity(title=f"t{i}")["id"] for i in range(3)]
        assert cli.cmd_edit(_args(["edit", *ids, "--due", "2030-01-01"])) == 0
        assert [op["v"]["B_test01"] for op in fake_ctx.ops] == [1, 2, 3]
        before = sample["syncVersion"]
        finalize(sample, fake_ctx.ops, "B_test01")
        assert sample["syncVersion"] == before + 1
        assert {op["sv"] for op in fake_ctx.ops} == {before + 1}

    def test_title_with_many_ids_is_rejected(self, fake_ctx, add_task_entity):
        a, b = add_task_entity(title="a"), add_task_entity(title="b")
        with pytest.raises(cli.CliError, match="--title"):
            cli.cmd_edit(_args(["edit", a["id"], b["id"], "--title", "x"]))
        assert fake_ctx.ops == []

    def test_duplicate_ids_are_edited_once(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a")
        assert cli.cmd_edit(_args(["edit", a["id"], a["id"], "--est", "1h"])) == 0
        assert _actions(fake_ctx.ops) == ["HU"]

    def test_single_id_still_works(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a", notes="old")
        assert cli.cmd_edit(_args(["edit", a["id"], "--append-notes", "new"])) == 0
        changes = _payload(fake_ctx.ops, "HU")["task"]["changes"]
        assert changes["notes"] == "old\nnew"


class TestBulkSelector:
    def test_no_selector_is_an_error(self, fake_ctx):
        with pytest.raises(cli.CliError, match="no selector"):
            cli.cmd_bulk(_args(["bulk", "--est", "1h"]))

    def test_no_action_is_an_error(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a")
        with pytest.raises(cli.CliError, match="nothing to do"):
            cli.cmd_bulk(_args(["bulk", a["id"]]))

    def test_due_and_clear_due_conflict(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a")
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_bulk(_args(["bulk", a["id"], "--due", "today", "--clear-due"]))

    def test_complete_and_reopen_conflict(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a")
        with pytest.raises(cli.CliError, match="mutually exclusive"):
            cli.cmd_bulk(_args(["bulk", a["id"], "--complete", "--reopen"]))

    def test_project_and_overdue_combine_like_list(self, fake_ctx, sample, add_task_entity):
        _seed_shortsyntax_world(sample)
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        late = add_task_entity(project_id="P_WORK", title="late", due_day=yesterday)
        add_task_entity(project_id="P_WORK", title="on time")
        add_task_entity(title="inbox late", due_day=yesterday)
        assert cli.cmd_bulk(
            _args(["bulk", "--project", "Work", "--overdue", "--est", "1h"])
        ) == 0
        assert [op["d"] for op in fake_ctx.ops] == [late["id"]]

    def test_explicit_ids_union_with_filters_without_duplicates(
        self, fake_ctx, sample, add_task_entity
    ):
        _seed_shortsyntax_world(sample)
        a = add_task_entity(project_id="P_WORK", title="a")
        other = add_task_entity(title="other")
        assert cli.cmd_bulk(
            _args(["bulk", a["id"], other["id"], "--project", "Work", "--est", "1h"])
        ) == 0
        assert [op["d"] for op in fake_ctx.ops] == [a["id"], other["id"]]

    def test_done_selector_finds_done_tasks(self, fake_ctx, sample, add_task_entity):
        done = add_task_entity(title="done one")
        done["isDone"] = True
        done["doneOn"] = model.now_ms()
        add_task_entity(title="open one")
        assert cli.cmd_bulk(_args(["bulk", "--done", "--reopen"])) == 0
        assert [op["d"] for op in fake_ctx.ops] == [done["id"]]
        assert _payload(fake_ctx.ops, "HU")["task"]["changes"]["isDone"] is False

    def test_no_match_returns_1_and_writes_nothing(self, fake_ctx, capsys):
        assert cli.cmd_bulk(_args(["bulk", "--search", "zzz-nope", "--est", "1h"])) == 1
        assert fake_ctx.ops == []
        assert "no tasks matched" in capsys.readouterr().err

    def test_selection_is_recomputed_inside_the_commit(self, sample, monkeypatch, add_task_entity):
        """412 retry: the closure must re-run the selector on the fresh file."""
        _seed_shortsyntax_world(sample)
        add_task_entity(project_id="P_WORK", title="first")

        def _mutate(d):
            add_task_entity(d=d, project_id="P_WORK", title="appeared later")

        ctx = _RetryCtx(sample, _mutate)
        monkeypatch.setattr(cli, "_ctx", lambda: (ctx, ctx))
        assert cli.cmd_bulk(_args(["bulk", "--project", "Work", "--est", "1h"])) == 0
        assert len(ctx.ops) == 2  # the task that appeared underneath is included


class TestBulkActions:
    def test_dry_run_writes_nothing_and_lists_tasks(self, fake_ctx, add_task_entity, capsys):
        a = add_task_entity(title="dry me")
        assert cli.cmd_bulk(_args(["bulk", a["id"], "--complete", "--dry-run"])) == 0
        assert fake_ctx.ops == [] and fake_ctx.commits == 0
        out = capsys.readouterr().out
        assert "dry me" in out and "dry run" in out

    def test_dry_run_without_an_action_is_allowed(self, fake_ctx, add_task_entity):
        add_task_entity(title="x")
        assert cli.cmd_bulk(_args(["bulk", "--search", "x", "--dry-run"])) == 0
        assert fake_ctx.ops == []

    def test_due_est_and_complete_share_one_hu_per_task(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a")
        b = add_task_entity(title="b")
        assert cli.cmd_bulk(
            _args(["bulk", a["id"], b["id"], "--due", "2030-05-05", "--est", "2h",
                   "--complete"])
        ) == 0
        assert _actions(fake_ctx.ops) == ["HU", "HU"]
        changes = fake_ctx.ops[0]["p"]["actionPayload"]["task"]["changes"]
        assert changes["dueDay"] == "2030-05-05"
        assert changes["timeEstimate"] == 7_200_000
        assert changes["isDone"] is True and changes["doneOn"] > 0

    def test_clear_due_nulls_the_scheduling_fields(self, fake_ctx, add_task_entity):
        a = add_task_entity(title="a", due_day="2030-01-01")
        assert cli.cmd_bulk(_args(["bulk", a["id"], "--clear-due"])) == 0
        changes = _payload(fake_ctx.ops, "HU")["task"]["changes"]
        assert changes == {"dueDay": None, "dueWithTime": None, "remindAt": None}

    def test_complete_skips_already_done_tasks(self, fake_ctx, add_task_entity):
        done = add_task_entity(title="done")
        done["isDone"] = True
        open_ = add_task_entity(title="open")
        assert cli.cmd_bulk(
            _args(["bulk", done["id"], open_["id"], "--complete"])
        ) == 0
        assert [op["d"] for op in fake_ctx.ops] == [open_["id"]]

    def test_tag_add_emits_hgt_and_is_idempotent(self, fake_ctx, sample, add_task_entity):
        _seed_shortsyntax_world(sample)
        a = add_task_entity(title="a")
        b = add_task_entity(title="b", tag_ids=["T_HOME"])
        sample["state"]["tag"]["entities"]["T_HOME"]["taskIds"].append(b["id"])
        assert cli.cmd_bulk(
            _args(["bulk", a["id"], b["id"], "--tag-add", "home"])
        ) == 0
        assert _actions(fake_ctx.ops) == ["HGT"]
        op = fake_ctx.ops[0]
        assert op["p"]["actionPayload"] == {"tagId": "T_HOME", "taskId": a["id"]}
        assert op["ds"] == [a["id"], "T_HOME"]

    def test_tag_rm_rewrites_tag_ids_with_hu(self, fake_ctx, sample, add_task_entity):
        _seed_shortsyntax_world(sample)
        a = add_task_entity(title="a", tag_ids=["T_HOME"])
        sample["state"]["tag"]["entities"]["T_HOME"]["taskIds"].append(a["id"])
        assert cli.cmd_bulk(_args(["bulk", a["id"], "--tag-rm", "home"])) == 0
        assert _payload(fake_ctx.ops, "HU")["task"]["changes"]["tagIds"] == []

    def test_move_project_emits_hmp_and_skips_no_ops(
        self, fake_ctx, sample, add_task_entity, capsys
    ):
        _seed_shortsyntax_world(sample)
        moving = add_task_entity(title="moving")
        already = add_task_entity(project_id="P_WORK", title="already there")
        parent = add_task_entity(title="parent")
        sub = add_task_entity(title="sub", parent_id=parent["id"])
        parent["subTaskIds"] = [sub["id"]]
        assert cli.cmd_bulk(
            _args(["bulk", moving["id"], already["id"], sub["id"],
                   "--move-project", "Work"])
        ) == 0
        assert _actions(fake_ctx.ops) == ["HMP"]
        assert fake_ctx.ops[0]["d"] == moving["id"]
        assert "subtask" in capsys.readouterr().err

    def test_confirmation_over_ten_tasks(self, fake_ctx, monkeypatch, add_task_entity, capsys):
        for i in range(11):
            add_task_entity(title=f"bulky {i}")
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        assert cli.cmd_bulk(_args(["bulk", "--search", "bulky", "--est", "1h"])) == 1
        assert fake_ctx.ops == []
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        assert cli.cmd_bulk(_args(["bulk", "--search", "bulky", "--est", "1h"])) == 0
        assert len(fake_ctx.ops) == 11

    def test_yes_skips_the_prompt(self, fake_ctx, monkeypatch, add_task_entity):
        for i in range(11):
            add_task_entity(title=f"bulky {i}")

        def _boom(_prompt):
            raise AssertionError("must not prompt with --yes")

        monkeypatch.setattr("builtins.input", _boom)
        assert cli.cmd_bulk(
            _args(["bulk", "--search", "bulky", "--est", "1h", "--yes"])
        ) == 0
        assert len(fake_ctx.ops) == 11

    def test_ten_tasks_do_not_prompt(self, fake_ctx, monkeypatch, add_task_entity):
        for i in range(10):
            add_task_entity(title=f"bulky {i}")

        def _boom(_prompt):
            raise AssertionError("must not prompt at 10")

        monkeypatch.setattr("builtins.input", _boom)
        assert cli.cmd_bulk(_args(["bulk", "--search", "bulky", "--est", "1h"])) == 0


class TestBulkNeverEmitsMultiEntityOps:
    def test_every_bulk_action_stays_on_per_task_ops(
        self, fake_ctx, sample, add_task_entity
    ):
        """Contract (research §10): N × HU in one batch, never HUM/TU — a
        multi-entity op blocks conflict resolution on the receiving device."""
        _seed_shortsyntax_world(sample)
        a = add_task_entity(title="a", due_day="2030-01-01")
        b = add_task_entity(title="b")
        assert cli.cmd_bulk(
            _args(["bulk", a["id"], b["id"], "--due", "2031-02-02", "--est", "1h",
                   "--tag-add", "home", "--complete", "--move-project", "Work"])
        ) == 0
        assert cli.cmd_edit(_args(["edit", a["id"], b["id"], "--est", "2h"])) == 0
        actions = set(_actions(fake_ctx.ops))
        assert actions <= {"HU", "HGT", "HMP"}
        assert "HUM" not in actions and "TU" not in actions
        assert fake_ctx.commits == 2  # one batch per command

    def test_state_stays_consistent_after_a_bulk(
        self, fake_ctx, sample, add_task_entity
    ):
        from conftest import assert_doctor_clean

        _seed_shortsyntax_world(sample)
        add_task_entity(title="a")
        add_task_entity(title="b")
        assert cli.cmd_bulk(
            _args(["bulk", "--project", "inbox", "--tag-add", "home", "--complete"])
        ) == 0
        assert_doctor_clean(sample)
