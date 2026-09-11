# Contributing

Issues and pull requests are welcome.

## Development setup

```sh
git clone https://github.com/prineycom/sp-cli
cd sp-cli
pip install -e '.[dev]'
python -m pytest tests/ -q
```

Python 3.11+, the only runtime dependency is `requests`. Please keep it that
way — the project is deliberately stdlib-first (the MCP server has zero extra
dependencies).

## Ground rules

- Unit tests never touch the network. Integration tests may only talk to
  loopback servers spun up by the test itself (see
  `tests/test_mcp_integration.py` for the WebDAV emulator pattern).
- Every write operation must go through the existing path
  (`SyncStore.commit` → mutations → `ops.finalize`) so the sync invariants
  hold: vectorClock increment, `recentOps` append, `lastModified`/`clientId`
  update, backup before PUT. Never hand-edit the sync file structure in a
  command.
- New CLI commands are automatically exposed as MCP tools via argparse
  introspection — add the command, and the coverage test in
  `tests/test_mcp_server.py` will hold you to schema correctness.
- Code, identifiers, and commit messages are English.
- Run the full suite before submitting: `python -m pytest tests/ -q`.

## Testing against a real server

Never test writes against a sync file an actual Super Productivity install
depends on. Use a copy on a scratch WebDAV server (rclone's `rclone serve
webdav` works well) or the loopback emulator from the integration tests.
