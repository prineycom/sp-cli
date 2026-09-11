# sp-cli — final session report (2026-09-09 … 2026-09-10)

*Verification report (translated from Russian).*

Result of a two-day autonomous session: from zero to a fully functional CLI for managing Super Productivity through the WebDAV file `sync-data.json`, bypassing the app. Everything is in `main`, all tests are green, and the work was verified against a live phone sync.

---

## 1. What was built

### Batch 1 — MVP (2026-09-09)

The `sp_cli` package (Python 3.11+, stdlib + requests) from scratch:

- **Sync core**: GET/PUT with the `pf_2__` prefix, ETag/If-Match optimistic locking with retry (max 3), backups before every write (rotation of 10), operation log (`recentOps`), vectorClock, file finalization (syncVersion, sv, trim to 2000, oldestOpSyncVersion).
- **Tasks**: add/edit/complete/reopen/delete/subtask/move/tag/reorder/show/list with filters.
- **Day plan**: today add/rm, plan --day, agenda, Today ordering.
- **Calendar**: schedule --at --remind, unschedule, deadline, repeating tasks (repeat).
- **Time**: track, worklog (live + archive).
- **Misc**: archiving of done tasks, project/tag CRUD, pull/doctor/backup/init.

### Batch 2 — 15 areas (2026-09-10), each via its own plan → executor → review → fixes flow

| # | Area | Commands |
|---|---|---|
| 1 | Notes | `note add/show/edit/rm/move`, `notes`, pin to Today |
| 2 | Boards | `board add/edit/rm/sort`, `board panel add/edit/rm/order` |
| 3 | Counters/habits | `counter add/edit/rm/set/inc/log/order`, `counters` |
| 4 | Day metrics | `metric set/focus/rm`, `metrics` (impact/energy/notes/reflections) |
| 6 | Integrations | `provider add-ical/add-caldav/edit/rm/order`, `providers` |
| 10 | Live timer | `start/stop/current`, `untrack` (local; only time is synced) |
| 11 | Project backlog | `backlog add/rm/clear`, `sp add --backlog`, `--enable/--disable-backlog` |
| 12 | Archive | `archived`, `restore [--today]` |
| 13 | Full deletions | `project rm` (HPD + marker), `tag rm` (GD cascade), `repeat rm` (HRC) |
| 14 | Repeat editing | `repeat edit` (clearedFields!), `repeat skip`, pause/resume |
| 15 | Ordering and conversion | `today move`, `move-in-project`, `subtask move/reparent`, `demote/promote`, `plan move` |
| 16 | Attachments | `attach/attachments/attach edit/attach rm` |
| 17 | Deadline clearing | `deadline --clear/--clear-reminder`, `dismiss`, reminder recomputation on reschedule |
| 18 | shortSyntax | `sp add "bread #tag +Project @tomorrow 15m @every monday"` |
| 19 | Bulk | `sp edit <id...>`, `sp bulk` with filters and dry-run |

**~90 commands/subcommands** in total. Full reference: README.md.

---

## 2. How it works (the essentials of the format)

Full contracts: `docs/research-sync-engine.md`, `docs/research-data-model.md`, `.yoke/ai/batch2-research/*.md` — extracted by subagents from the SP sources (schemaVersion 4, file version 2, September 2026).

Key points:

- Live clients apply others' changes by **replaying ops** from `recentOps` (NgRx actions keyed by the short code `a`); fresh clients take the `state` snapshot. Therefore the CLI writes **both correct ops and a consistent state** — the contract is "state already contains the effect of every op".
- Each op: `{id: uuid7, a, o, e, d/ds, p:{actionPayload, entityChanges}, c: clientId, s: 4, t, v}`; `v` = merge(file clock, own cumulative counter) — it must dominate, otherwise conflicts/LWW.
- Fatal mistakes (never do): empty `recentOps`, `syncVersion` regression, removing others' vectorClock keys, ops with `s≠4`, non-persistent actions (BP/HUM/TU/RX/SI/AF/AC/…).
- The Today view = `dueDay == today` (or dueWithTime today); the TODAY tag is ordering only, never in `task.tagIds`. `dueDay` and `dueWithTime` may coexist when they point at the same day (SP's planTasksForToday semantics).
- The logical day respects `misc.startOfNextDayTime` ("before 4 a.m. is still yesterday" hours).
- Archives: the top-level `archiveYoung/archiveOld` are authoritative, but the code handles all 4 possible blobs (top-level + inside state).
- The timer (`currentTaskId`) is by design not synced in SP — the CLI keeps it local (`~/.local/share/sp-cli/timer.json`); only accumulated time is synced (KT deltas, commutative under conflicts).

---

## 3. How it was verified

1. **Unit**: **1070 tests** (`python3 -m pytest tests/ -q`), no network. They check exact payload shapes, action codes, `ds` ordering, state mirroring, invariants (doctor cleanliness after every mutation), and retry safety of closures.
2. **Review of every item** by a separate Opus agent cross-checked against the SP sources. Caught and fixed: **4 criticals** (mid-word project matching in shortSyntax; applying a stale bulk selection on a 412 retry; clobbering manually set weekdays when editing a repeat interval; resurrecting repeat configs of a deleted project via a full PUT) and **~20 majors** (dual archive blobs, parent time recomputation, the section model — SP uses `contextId/contextType`, not `projectId`, offset-aware "today", deadline reminder recomputation on reschedule, and more).
3. **Integration sweeps** against a test rclone WebDAV with a copy of live data: batch 1 — ~30 commands; batch 2 — 75 write ops across all areas, 49 distinct action codes, no forbidden codes, the `projectDeleteWins` marker and KT mirrors in place, clocks strictly monotonic, `doctor` clean.
4. **Live verification via phone sync** (the phone client id):
   - Batch 1: a set of "CLI:" records — the sync went through **without a conflict dialog**, everything appeared (confirmed by the maintainer), records then cleaned up.
   - Batch 2: a set of "B2:" records for every item — confirmed ("everything seems to have worked"). The phone's vector clock increments correctly alongside the CLI's.
5. **A browser E2E did not happen**: headless Chromium on this Pi does not load pages at all (not even example.com via `--dump-dom`) — an environment bug, recorded in project memory to avoid wasting time on it again.

### "B2:" test records on production

If not yet cleaned up: `./sp bulk --project "B2 Проект" --complete --yes && ./sp project rm "B2 Проект" --yes`, then `./sp tag rm b2-быт --yes`, `./sp note rm <id> --yes` ×2, `./sp board rm <id> --yes`, `./sp counter rm <id> --yes` ×2, `./sp provider rm <id> --yes`, `./sp metric rm 2026-09-10`; the archived "B2: goes to archive" record can be kept or deleted from the app. (The Cyrillic names are the literal entity names created on the test instance.)

---

## 4. What is NOT implemented

**Areas outside the ordered scope:**
- **Sections** (`section`) — section CRUD and laying tasks out across them (effects of others' ops are mirrored correctly).
- **Menu structure** (`menuTree`) — project/tag folders (new projects are visible without it).
- **App settings** (`globalConfig`) — deliberately read-only (except the shortSyntax gates and healing on project deletion).
- **Day sessions** (`timeTracking` KS/KW: workStart/workEnd/breaks) — they do not affect the worklog.

**Deliberate limitations within the covered scope:**
- Integrations: write support only for ICAL/CalDAV; Jira/GitLab/plugin providers are view-only. The CalDAV password is stored in the sync file in plain text — an explicit `--store-plaintext-credentials` is required.
- Archive maintenance (flush young→old, compression) — not emitted; the app does it.
- Repeats: `subTaskTemplates` cannot be set; instances are materialized by the app.
- The timer is not visible on other devices (an SP limitation).
- shortSyntax: English dates only, `D/M` order, `!deadline` behind the `--parse-deadline` flag.
- Notes: no reorder (op `NO`).
- The legacy shim `reassertOwnTagsAfterConvert` (for SP clients ≤ 18.20.1) is not emitted.

**Review minors backlog** (~40 items, non-blocking): `.yoke/ai/batch2-minors-backlog.md`.

**From the old plan, not done:** Hermes integration (skill + a morning cron digest of `sp agenda`), the YouTrack bridge.

---

## 5. Important operational facts

- **Environment**: the production WebDAV is rclone in docker, host port 8091, backed by a docker volume; the file is `/superproductivity/sync-data.json`.
- **CLI config**: `~/.config/sp-cli/config.toml` (created; the CLI client id — never change it and never let it match the phone's). Env overrides: `SP_CLI_CONFIG`, `SP_CLI_TIMER`.
- **Backups**: automatic before every write, in `~/.local/share/sp-cli/backups/` (rotation of 10); manual snapshots from this session — in a separate backup directory in the maintainer's home.
- **Disaster recovery**: put a backup file onto the WebDAV as `sync-data.json` (with the `pf_2__` prefix as-is) — but remember: a syncVersion regression triggers a full resync/conflict dialog on the phone; the better path is new corrective operations via the CLI.
- **`sp doctor`** — the first command to run on any oddity: checks ~30 file consistency invariants.
- Known environment bug: **browser E2Es are impossible on this Pi** (Chromium does not load pages; chrome-devtools-axi is incompatible/requires Chrome) — see project memory.
- Work history: `.yoke/journal.md`; flow artifacts — `.yoke/ai/*/` (plans, research, reports).

## 6. Commit chronology (main milestones)

`f07228f` bootstrap yoke → `d3ef2cc` MVP plan + research → `0c6e9a8` sp_cli implementation → `cc356d0` review fixes → `dd3c79b` README → batch 2: `c5ca407` research → `e1e5070` 15 plans → 15× `feat(b2-…)` + 10× `fix(b2-…)` → `264852c` journal. All in `origin/main`.
