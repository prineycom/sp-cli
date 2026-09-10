import json

import pytest

from sp_cli import cli
from sp_cli import queries as q
from sp_cli import timer
from sp_cli.model import make_tag, today_str


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

    def test_bare_start_without_timer_errors(self, fake_ctx, timer_file):
        with pytest.raises(cli.CliError):
            cli.cmd_start(_args(["start"]))

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
