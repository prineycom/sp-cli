import pytest

from sp_cli import cli
from sp_cli.model import make_tag


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
