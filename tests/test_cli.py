import pytest

from sp_cli import cli
from sp_cli import queries as q
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
