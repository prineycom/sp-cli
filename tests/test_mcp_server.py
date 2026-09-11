"""MCP server: tool generation, argv bridge, JSON-RPC handling (no network)."""

import argparse
import io
import json

import pytest

from sp_cli import cli
from sp_cli import mcp_server as mcp
from sp_cli.mcp_server import (
    SpMcpServer,
    ToolError,
    build_argv,
    iter_subcommands,
    tool_name,
)


@pytest.fixture(scope="module")
def server() -> SpMcpServer:
    return SpMcpServer()


@pytest.fixture(scope="module")
def tools_by_name(server) -> dict:
    return {t["name"]: t for t in server.tools}


class TestCoverage:
    def test_every_cli_command_is_a_tool(self, tools_by_name):
        commands = [name for name, _, _ in iter_subcommands(cli.build_parser())]
        assert commands, "parser introspection found no commands"
        missing = [c for c in commands if tool_name(c) not in tools_by_name]
        assert missing == []
        assert len(tools_by_name) == len(commands)

    def test_tool_names_round_trip_uniquely(self, server):
        commands = [name for name, _, _ in iter_subcommands(cli.build_parser())]
        # tool name -> command mapping relies on commands never containing "_"
        assert all("_" not in c for c in commands)
        assert len({tool_name(c) for c in commands}) == len(commands)

    def test_read_only_and_destructive_sets_reference_real_commands(self):
        commands = {name for name, _, _ in iter_subcommands(cli.build_parser())}
        assert mcp.READ_ONLY <= commands
        assert mcp.DESTRUCTIVE <= commands

    def test_include_exclude_filters(self):
        only = SpMcpServer(include=["list", "show"])
        assert {t["name"] for t in only.tools} == {"sp_list", "sp_show"}
        no_providers = SpMcpServer(exclude=["provider*"])
        names = {t["name"] for t in no_providers.tools}
        assert "sp_providers" not in names and "sp_provider_rm" not in names
        assert "sp_list" in names


class TestSchemas:
    def test_add(self, tools_by_name):
        schema = tools_by_name["sp_add"]["inputSchema"]
        assert schema["required"] == ["title"]
        assert schema["properties"]["title"]["type"] == "string"
        assert schema["properties"]["tag"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert schema["properties"]["create_tags"]["type"] == "boolean"
        assert schema["additionalProperties"] is False

    def test_variadic_positional(self, tools_by_name):
        ids = tools_by_name["sp_delete"]["inputSchema"]["properties"]["ids"]
        assert ids == {"type": "array", "items": {"type": "string"}, "minItems": 1}
        assert tools_by_name["sp_delete"]["inputSchema"]["required"] == ["ids"]

    def test_optional_positional_not_required(self, tools_by_name):
        schema = tools_by_name["sp_start"]["inputSchema"]
        assert "required" not in schema or "id" not in schema["required"]

    def test_choices_become_enum(self, tools_by_name):
        prop = tools_by_name["sp_counter_add"]["inputSchema"]["properties"]["type"]
        assert set(prop["enum"]) == {"click", "stopwatch", "countdown"}

    def test_int_type(self, tools_by_name):
        props = tools_by_name["sp_board_add"]["inputSchema"]["properties"]
        assert props["cols"]["type"] == "integer"

    def test_annotations(self, tools_by_name):
        assert tools_by_name["sp_list"]["annotations"]["readOnlyHint"] is True
        assert tools_by_name["sp_delete"]["annotations"]["destructiveHint"] is True
        assert tools_by_name["sp_note_rm"]["annotations"]["destructiveHint"] is True
        assert tools_by_name["sp_add"]["annotations"]["readOnlyHint"] is False
        assert tools_by_name["sp_add"]["annotations"]["destructiveHint"] is False

    def test_help_text_becomes_description(self, tools_by_name):
        assert tools_by_name["sp_list"]["description"] == "list tasks"


def _subparser(command: str) -> argparse.ArgumentParser:
    for name, sp, _ in iter_subcommands(cli.build_parser()):
        if name == command:
            return sp
    raise AssertionError(command)


class TestBuildArgv:
    def test_add_full(self):
        argv = build_argv(
            "add",
            _subparser("add"),
            {
                "title": "buy milk",
                "project": "home",
                "tag": ["errand", "shop"],
                "create_tags": True,
                "est": "30m",
            },
        )
        assert argv[0] == "add"
        assert argv[-1] == "buy milk"  # positional last, after "--"
        assert argv.count("--tag=errand") == 1 and argv.count("--tag=shop") == 1
        assert "--project=home" in argv
        assert "--create-tags" in argv
        assert argv[-2] == "--"
        # a full parse must succeed
        args = _subparser("add").parse_args(argv[1:])
        assert args.title == "buy milk" and args.tag == ["errand", "shop"]

    def test_false_and_none_omitted(self):
        argv = build_argv(
            "list", _subparser("list"), {"json": False, "project": None}
        )
        assert argv == ["list"]

    def test_variadic_positional(self):
        argv = build_argv("delete", _subparser("delete"), {"ids": ["a", "b"], "yes": True})
        assert argv == ["delete", "--yes", "--", "a", "b"]

    def test_integral_float_normalized(self):
        argv = build_argv("board-add", _subparser("board-add"), {"title": "b", "cols": 3.0})
        assert argv == ["board-add", "--cols=3", "--", "b"]

    def test_dash_leading_positional(self):
        argv = build_argv("add", _subparser("add"), {"title": "-urgent"})
        assert argv == ["add", "--", "-urgent"]
        assert _subparser("add").parse_args(argv[1:]).title == "-urgent"

    def test_dash_leading_option_value(self):
        argv = build_argv("list", _subparser("list"), {"search": "--json"})
        assert argv == ["list", "--search=--json"]
        args = _subparser("list").parse_args(argv[1:])
        assert args.search == "--json" and args.json is False

    def test_immune_to_cli_alias_rewrites(self):
        argv = build_argv(
            "subtask", _subparser("subtask"), {"parent": "move", "title": "x"}
        )
        assert argv == ["subtask", "--", "move", "x"]
        assert cli._rewrite_argv(list(argv)) == argv  # "--" blocks alias match
        args = _subparser("subtask").parse_args(argv[1:])
        assert (args.parent, args.title) == ("move", "x")

    def test_string_boolean_coerced(self):
        argv = build_argv("list", _subparser("list"), {"json": "true"})
        assert argv == ["list", "--json"]
        argv = build_argv("list", _subparser("list"), {"json": "false"})
        assert argv == ["list"]

    def test_unknown_argument_rejected(self):
        with pytest.raises(ToolError, match="unknown argument"):
            build_argv("list", _subparser("list"), {"bogus": 1})


class TestJsonRpc:
    def _req(self, method, params=None, id_=1):
        msg = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            msg["params"] = params
        return msg

    def test_initialize_echoes_supported_version(self, server):
        resp = server.handle(
            self._req("initialize", {"protocolVersion": "2025-03-26"})
        )
        assert resp["result"]["protocolVersion"] == "2025-03-26"
        assert resp["result"]["serverInfo"]["name"] == "sp-mcp"
        assert resp["result"]["capabilities"]["tools"] == {"listChanged": False}
        assert "instructions" in resp["result"]

    def test_initialize_falls_back_on_unknown_version(self, server):
        resp = server.handle(self._req("initialize", {"protocolVersion": "1999-01-01"}))
        assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION

    def test_ping(self, server):
        assert server.handle(self._req("ping"))["result"] == {}

    def test_tools_list(self, server):
        tools = server.handle(self._req("tools/list"))["result"]["tools"]
        assert len(tools) == len(server.tools)
        assert all(t["inputSchema"]["type"] == "object" for t in tools)

    def test_unknown_method_is_error(self, server):
        resp = server.handle(self._req("bogus/method"))
        assert resp["error"]["code"] == -32601

    def test_notification_returns_none(self, server):
        assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        assert server.handle({"jsonrpc": "2.0", "method": "bogus"}) is None

    def test_call_unknown_tool_is_protocol_error(self, server):
        resp = server.handle(
            self._req("tools/call", {"name": "sp_nope", "arguments": {}})
        )
        assert resp["error"]["code"] == -32602

    def test_call_missing_required_arg_is_tool_error(self, server):
        resp = server.handle(
            self._req("tools/call", {"name": "sp_show", "arguments": {}})
        )
        assert resp["result"]["isError"] is True
        assert "id" in resp["result"]["content"][0]["text"]

    def test_missing_method_is_invalid_request(self, server):
        resp = server.handle({"jsonrpc": "2.0", "id": 3})
        assert resp["error"]["code"] == -32600

    def test_non_object_frames_do_not_kill_serve(self, server):
        frames = [b"5", b'"x"', b"[]", b'{"jsonrpc":"2.0","id":7,"method":"ping"}']
        stdin = io.BytesIO(b"\n".join(frames) + b"\n")
        stdout = io.BytesIO()
        server.serve(stdin=stdin, stdout=stdout)
        lines = [json.loads(l) for l in stdout.getvalue().decode().splitlines()]
        assert [m["error"]["code"] for m in lines[:3]] == [-32600, -32600, -32600]
        assert lines[3]["result"] == {}  # loop survived to serve the ping

    def test_batch_gets_batch_response(self, server):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            7,
        ]
        stdin = io.BytesIO(json.dumps(batch).encode() + b"\n")
        stdout = io.BytesIO()
        server.serve(stdin=stdin, stdout=stdout)
        resp = json.loads(stdout.getvalue().decode())
        assert isinstance(resp, list) and len(resp) == 2
        assert resp[0]["result"] == {}
        assert resp[1]["error"]["code"] == -32600

    def test_parse_error_and_serve_loop(self, server):
        stdin = io.BytesIO(b"not json\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"}
        ).encode() + b"\n")
        stdout = io.BytesIO()
        server.serve(stdin=stdin, stdout=stdout)
        lines = stdout.getvalue().decode().splitlines()
        assert json.loads(lines[0])["error"]["code"] == -32700
        assert json.loads(lines[1])["result"] == {}


class TestCallToolGuards:
    def test_rewrite_disabled_for_mcp_argv(self, server, monkeypatch):
        seen = {}

        def fake_main(argv, rewrite=True):
            seen["rewrite"] = rewrite
            return 0

        monkeypatch.setattr(cli, "main", fake_main)
        server.call_tool("sp_list", {})
        assert seen["rewrite"] is False

    def test_eof_confirmation_guard(self, server, monkeypatch):
        def fake_main(argv, rewrite=True):
            input("delete everything? [y/N] ")

        monkeypatch.setattr(cli, "main", fake_main)
        text, is_error = server.call_tool("sp_delete", {"ids": ["x"]})
        assert is_error and "yes=true" in text

    def test_stdin_restored_after_call(self, server, monkeypatch):
        import sys as _sys

        monkeypatch.setattr(cli, "main", lambda argv, rewrite=True: 0)
        before = _sys.stdin
        server.call_tool("sp_list", {})
        assert _sys.stdin is before

    def test_crash_is_tool_error_not_server_death(self, server, monkeypatch):
        monkeypatch.setattr(cli, "main", lambda argv, rewrite=True: 1 / 0)
        text, is_error = server.call_tool("sp_list", {})
        assert is_error and "ZeroDivisionError" in text

    def test_nonzero_exit_collects_stderr(self, server, monkeypatch):
        import sys as _sys

        def fake_main(argv, rewrite=True):
            print("partial", file=_sys.stdout)
            print("error: boom", file=_sys.stderr)
            return 2

        monkeypatch.setattr(cli, "main", fake_main)
        text, is_error = server.call_tool("sp_list", {})
        assert is_error and "error: boom" in text and "partial" in text


class TestReadOnlyMode:
    def test_read_only_exposes_only_read_tools(self):
        server = SpMcpServer(read_only=True)
        names = {t["name"] for t in server.tools}
        assert names == {tool_name(c) for c in mcp.READ_ONLY}
        assert all(t["annotations"]["readOnlyHint"] for t in server.tools)

    def test_read_only_composes_with_exclude(self):
        server = SpMcpServer(read_only=True, exclude=["worklog"])
        names = {t["name"] for t in server.tools}
        assert "sp_worklog" not in names and "sp_list" in names
