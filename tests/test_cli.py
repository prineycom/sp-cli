import json

import pytest

from sp_cli import cli
from sp_cli import queries as q
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
