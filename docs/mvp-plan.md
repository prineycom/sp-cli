*Historical planning document (translated from Russian).*

# MVP Plan — sp-cli (v2, agreed with the maintainer)

**Goal:** a CLI with **full coverage of tasks and day planning** — read + write + sync layer in the first stage. No "read-only first, write later".

## Principles

- Python 3, stdlib + `requests` (WebDAV = GET/PUT).
- Config: `~/.config/sp-cli/config.toml` (WebDAV URL, user, path to the password, CLI clientId).
- Backup before every PUT: `~/.local/share/sp-cli/backups/sync-data-<ts>.json` (rotation: last 10).
- A dedicated CLI clientId (distinct from the phone's) — mandatory from the very first write operation.
- Tests: unit tests on mutations (in-memory), integration tests on a copy of the file, final check against the live WebDAV + phone.

## Stage 1 — Tasks + day plans (full coverage)

### 1a. Data core + sync layer (~2-3 h)
- `SyncClient`: GET/PUT sync-data.json, strip/re-add `pf_2__`, backups, schemaVersion check
- `recentOps` append, `vectorClock` increment of our clientId, `lastModified`
- Retry on conflict (re-read → replay the mutation, max 3)

### 1b. Tasks — read (~1 h)
- `sp list` — filters: `--project`, `--tag`, `--overdue`, `--today`, `--unscheduled`, `--search`, `--done`, `--parents-only`, `--json`
- `sp show <id>` — full task card (including subtasks, time, notes)

### 1c. Tasks — write (~3-4 h)
- `sp add "title" [--project X --tag Y --due 2026-08-10 --est 30m --notes "..."]` (duration parser: 30m/1h/1.5h)
- `sp edit <id> [--title --notes --due --est --project --tags]`
- `sp complete <id>` / `sp reopen <id>` (doneOn + timestamp)
- `sp delete <id>` — cascades to subtasks
- `sp subtask <parent-id> "title"` — subtask
- `sp move <id> --project X` — move between projects
- `sp tag <id> --add X --remove Y` — fine-grained tag management
- `sp reorder --project X <id1> <id2> ...` — ordering within a project

### 1d. Day plans (~1-2 h)
- `sp today` — what is in the Today view right now (the `TODAY` tag in `tagIds`)
- `sp today add <id...>` / `sp today remove <id...>`
- `sp agenda` — Today + deadlines due today + overdue (a morning review in a single call)

**Stage 1 DoD:** the full task lifecycle from the CLI — create → plan → edit → complete; everything shows up on the phone after sync without conflicts. Verified manually against the live WebDAV + phone.

## Stage 2 — Projects and tags (CRUD, ~1 h)

- `sp projects` / `sp project add "Name" [--color]` / `sp project edit <id>` / `sp project archive <id>`
- `sp tags` / `sp tag add` / `sp tag edit`

## Stage 3 — Worklog and reports (~1 h)

- `sp worklog --from --to` — time by day/project/tag, estimate accuracy
- `sp show <id>` already shows timeSpent/timeEstimate

## Stage 4 — Hermes integration (~1-2 h)

- `sp-cli` skill: commands, gotchas, fallback
- Optional cron: a morning `sp agenda` digest (replacing the retired Vikunja digest)

## Out of MVP scope (deliberately)

- Timer start/stop — needs a delta model (an external timer does not tick), will be done separately
- Repeat configs (needs a sample structure produced by the UI)
- Day planner (needs a sample from the UI)
- YouTrack bridge — after the MVP
- Bulk operations — the `sp complete <id1> <id2> ...` syntax already covers the essentials

## Risks

| Risk | Mitigation |
|---|---|
| The phone overwrites CLI changes | correct vectorClock + recentOps; live sync test before "release" |
| SP bumps schemaVersion | check on read, warn |
| Concurrent writes | retry + backups; a rare use case |

## Total estimate

Stage 1: **~7-10 h** (core+sync 2-3, read 1, write 3-4, today 1-2)
Stages 2-4: ~3-4 h
**Total MVP: ~10-14 h**
