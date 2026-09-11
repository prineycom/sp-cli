# Changelog

## 0.1.0 — 2026-09-11

First public release.

- Full-featured CLI for Super Productivity over WebDAV (`sync-data.json`):
  tasks (CRUD, subtasks, tags, moving, ordering), day planning
  (today/planner/agenda), scheduling (reminders, deadlines, repeats), time
  tracking (live timer, corrections, worklog), notes, boards, habit counters,
  day metrics, ICAL/CalDAV providers, archive/restore, backups, `doctor`
  invariant checks.
- Sync-safe write path: operation log (`recentOps`), vector clock, ETag/
  If-Match conflict detection with retry, automatic rotating backups before
  every PUT.
- MCP stdio server (`sp-mcp`) exposing every CLI command as an `sp_*` tool,
  generated from the argparse parser (1:1 coverage, zero extra dependencies),
  with read-only/destructive annotations and `--include/--exclude` filtering.
- Agent skill (`integrations/skill/superproductivity/`) and registration
  guides for Claude Code, Cursor/VS Code/Claude Desktop, Codex, Hermes Agent,
  and OpenClaw.
- 1108 tests: unit (no network) + MCP end-to-end against a loopback WebDAV
  emulator.
