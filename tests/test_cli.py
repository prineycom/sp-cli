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
