"""End-to-end: MCP server subprocess against a loopback WebDAV emulator.

The emulator serves a copy of docs/sample-sync-data.json with ETag/If-Match
semantics (412 on mismatch), so the whole CLI write path — config, SyncStore,
mutations, op-log, backups — runs for real without touching any live server.
"""

import json
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sp_cli.webdav import strip_prefix

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = REPO_ROOT / "docs" / "sample-sync-data.json"
CLIENT_ID = "CLI_mcp_e2e_test0000"
TIMEOUT = 30


class _SyncState:
    def __init__(self, body: bytes):
        self.body = body
        self.rev = 1

    @property
    def etag(self) -> str:
        return f'"rev-{self.rev}"'


class _Handler(BaseHTTPRequestHandler):
    state: _SyncState

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("ETag", self.state.etag)
        self.send_header("Content-Length", str(len(self.state.body)))
        self.end_headers()
        self.wfile.write(self.state.body)

    def do_PUT(self):
        if_match = self.headers.get("If-Match")
        if if_match is not None and if_match != self.state.etag:
            self.send_response(412)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        self.state.body = self.rfile.read(length)
        self.state.rev += 1
        self.send_response(204)
        self.send_header("ETag", self.state.etag)
        self.end_headers()


@pytest.fixture
def webdav():
    state = _SyncState(SAMPLE_PATH.read_bytes())
    handler = type("Handler", (_Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], state
    finally:
        httpd.shutdown()


class McpClient:
    """Minimal ndjson JSON-RPC client over a subprocess's stdio."""

    def __init__(self, env: dict):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "sp_cli.mcp_server"],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._lines: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._next_id = 0

    def _read_loop(self):
        for line in self.proc.stdout:
            self._lines.put(line)

    def _send(self, msg: dict):
        self.proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        line = self._lines.get(timeout=TIMEOUT)
        resp = json.loads(line)
        assert resp.get("id") == self._next_id, resp
        assert "error" not in resp, resp
        return resp["result"]

    def call(self, tool: str, arguments: dict | None = None) -> tuple[str, bool]:
        result = self.request(
            "tools/call", {"name": tool, "arguments": arguments or {}}
        )
        text = "".join(
            c["text"] for c in result["content"] if c["type"] == "text"
        )
        return text, bool(result.get("isError"))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def client(webdav, tmp_path, monkeypatch):
    port, _ = webdav
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                f'url = "http://127.0.0.1:{port}"',
                'folder = "superproductivity"',
                'user = "test"',
                'password = "test"',
                f'client_id = "{CLIENT_ID}"',
                f'backup_dir = "{tmp_path / "backups"}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    import os

    env = dict(os.environ)
    env["SP_CLI_CONFIG"] = str(config)
    env["SP_CLI_TIMER"] = str(tmp_path / "timer.json")
    c = McpClient(env)
    try:
        yield c
    finally:
        c.close()


def _server_state(state: _SyncState) -> dict:
    return json.loads(strip_prefix(state.body.decode("utf-8")))


def test_full_lifecycle_over_stdio(client, webdav):
    _, state = webdav

    # handshake
    init = client.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    assert init["serverInfo"]["name"] == "sp-mcp"
    client.notify("notifications/initialized")

    tools = client.request("tools/list")["tools"]
    assert len(tools) > 90
    assert {"sp_add", "sp_list", "sp_delete", "sp_doctor"} <= {
        t["name"] for t in tools
    }

    # read: structured list
    text, is_error = client.call("sp_list", {"json": True})
    assert not is_error
    baseline = json.loads(text)
    assert isinstance(baseline, list)

    # write: add a task, then verify the actual PUT body on the "server"
    rev_before = state.rev
    text, is_error = client.call(
        "sp_add", {"title": "MCP e2e task", "est": "30m"}
    )
    assert not is_error, text
    task_id = text.strip().splitlines()[-1]
    assert len(task_id) == 21

    assert state.rev == rev_before + 1  # exactly one PUT
    d = _server_state(state)
    task = d["state"]["task"]["entities"][task_id]
    assert task["title"] == "MCP e2e task"
    assert task["timeEstimate"] == 30 * 60 * 1000
    assert d["vectorClock"][CLIENT_ID] >= 1
    assert d["clientId"] == CLIENT_ID
    last_op = d["recentOps"][-1]
    assert last_op["e"] == "TASK"
    assert last_op["c"] == CLIENT_ID

    # the new task is visible through a read tool
    text, _ = client.call("sp_list", {"json": True})
    assert task_id in {t["id"] for t in json.loads(text)}

    # destructive without yes: refused with guidance, nothing written
    rev_before = state.rev
    text, is_error = client.call("sp_delete", {"ids": [task_id]})
    assert is_error and "yes=true" in text
    assert state.rev == rev_before

    # destructive with yes: executed
    text, is_error = client.call("sp_delete", {"ids": [task_id], "yes": True})
    assert not is_error, text
    d = _server_state(state)
    assert task_id not in d["state"]["task"]["entities"]

    # invariants hold after the whole session
    text, is_error = client.call("sp_doctor")
    assert not is_error and "ok" in text

    # argparse-level failure surfaces as a tool error, server stays alive
    text, is_error = client.call("sp_show", {})
    assert is_error
    assert client.request("ping") == {}
