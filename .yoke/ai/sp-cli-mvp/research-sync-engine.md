# SuperProductivity file-based sync (`sync-data.json`) — source analysis for an external CLI writer

Source: subagent deep-dive into johannesjo/super-productivity (Sept 2026, schemaVersion 4, file version 2). Sync engine lives in `src/app/op-log/**`, `packages/sync-core`, `packages/sync-providers`, `packages/shared-schema`.

## 1. How a client applies remote changes: delta replay vs snapshot import

**Two paths, chosen by the download cursor (`sinceSeq`):**

- The phone keeps a per-provider cursor = the last **`syncVersion`** it durably processed (`file-based-sync-adapter.service.ts:150,589-612`; `latestSeq = syncData.syncVersion`).
- **Incremental (delta) path — the normal one:** `_downloadOps` returns ALL `recentOps` from the file (minus the caller's own `clientId`). `snapshotState` is NOT returned. Dedup is by op **id** against locally applied op IDs, plus an "author counter ≤ known counter" clock cover check. New ops are **replayed as NgRx actions**. So: **delta sync by replaying recentOps.**
- **Snapshot path (`sinceSeq === 0` = fresh client, or gap detected):** the full `state` (+ top-level `archiveYoung/archiveOld` merged over `state`) is hydrated wholesale. All recentOps in the file are marked already-applied — **the file contract is: `state` already contains the effect of every op in `recentOps`.**
- **Gap conditions forcing the snapshot path**:
  - `versionWasReset`: file `syncVersion` < client's expected version.
  - `snapshotReplacement`: `sinceSeq>0 && recentOps.length===0 && state && clientId !== excludeClient`. **⇒ CLI must never upload a file with empty `recentOps`.**
  - `partialTrimGap`: `oldestOpSyncVersion > sinceSeq + 1`.
- Snapshot-path conflicts: snapshot clock vs local clock — CONCURRENT → conflict **dialog** (avoid!).

**CLI must produce BOTH:** correct `recentOps` (what the phone replays) and consistent `state` (+top-level archives) already reflecting those ops.

## 2. recentOps entry semantics

Compact format: `src/app/op-log/persistence/compact/compact-operation.types.ts`; full shape `packages/sync-core/src/operation.types.ts:37-111`.

| field | meaning |
|---|---|
| `id` | op UUID **v7** (time-ordered). Dedup key across all clients. |
| `a` | actionType short code → NgRx action type. Table: `persistence/compact/action-type-codes.ts:35-224` (frozen forever). Unknown codes = silent no-op on replay. |
| `o` | opType: `CRT`,`UPD`,`DEL`,`MOV`,`BATCH`,`SYNC_IMPORT`,`BACKUP_IMPORT`,`REPAIR`. Unknown `o` **blocks the whole batch** (cursor frozen). |
| `e` | entityType: TASK, PROJECT, TAG, NOTE, GLOBAL_CONFIG, SIMPLE_COUNTER, WORK_CONTEXT, TIME_TRACKING, TASK_REPEAT_CFG, ISSUE_PROVIDER, PLANNER, MENU_TREE, METRIC, BOARD, SECTION, REMINDER, PLUGIN_USER_DATA, PLUGIN_METADATA, MIGRATION, RECOVERY, ALL. |
| `d` | entityId (`'*'` for singletons; composite for TIME_TRACKING: `"{TAG|PROJECT}:{ctxId}:{date}"`). |
| `ds` | entityIds for bulk/multi-entity ops. |
| `p` | `{actionPayload: {...}, entityChanges: []}`. `entityChanges` empty except TIME_TRACKING/`syncTimeSpent`. **Replay dispatches the mapped NgRx action rebuilt from `actionPayload`** (`apply/operation-converter.util.ts:265-395`). |
| `c` | authoring clientId. |
| `s` | op schemaVersion. **CLI must emit `s: 4`.** `s > 4` on receiver → batch blocked; `s < 4` → migrated. |
| `sv` | syncVersion of the upload batch that added the op. |
| `t` | wall-clock epoch ms — the **LWW tie-breaker**. |
| `v` | vector clock = causal state **after** this op. |

Action-code cheat sheet: `HA`=addTask, `HU`=updateTask, `HD`=deleteTask, `HDM`=deleteTasks, `HX`=moveToArchive, `HMP`=moveToOtherProject, `HPT`=planTasksForToday, `HRT`=removeTasksFromTodayTag, `HMT`=moveTaskInTodayTagList, `HGT`=addTagToTask, `HS`/`HSR`/`HSX`=schedule/reschedule/unschedule, `HDL`=setDeadline, `TA`=addSubTask, `PA`/`PU`=project add/update, `GA`/`GU`=tag add/update, `LU`/`LP`/`LT`/`LM`/`LB`=planner, `KT`=syncTimeSpent, `KS`=syncSessions, `KW`=updateWorkContextData, `AF`=archive flushYoungToOld, `UM`=plugin upsertMetadata, `CU`=globalConfig section update, `WMU/WMD/WMT/WMB/WM`=Today-list moves, `YL`=loadAllData.

Local template (`capture/operation-log.effects.ts:214-298`): `actionPayload` = dispatched action minus `type`/`meta`; `id: uuidv7()`; `vectorClock = incrementVectorClock(currentClock, clientId)`; `schemaVersion: 4`.

## 3. Ops per feature (exact `actionPayload` on replay)

- **Task create** — `a:'HA'`, `o:'CRT'`, `e:'TASK'`, `d:taskId`. Payload: `{task: <full Task>, workContextId: <projectId|tagId>, workContextType: 'PROJECT'|'TAG', isAddToBacklog: false, isAddToBottom: true}` (`root-store/meta/task-shared.actions.ts:42-61`).
- **Task update** — `a:'HU'`, `o:'UPD'`, `d:taskId`. Payload: `{task: {id, changes: {title?, notes?, isDone?, doneOn?, timeEstimate?, dueDay?, dueWithTime?}}}`. When `isDone:true` include `doneOn`.
- **Task tags** — `HU` with `changes:{tagIds:[...]}`, or `HGT` `{tagId, taskId}` with `ds:[taskId, tagId]`.
- **Task delete** — `a:'HD'`, `o:'DEL'`, `d:taskId`. Payload `{task: <TaskWithSubTasks — full snapshot incl. subTasks:[]>}`. Bulk: `HDM` `{taskIds:[...]}`, `ds:taskIds`.
- **Subtask add** — `a:'TA'`, `o:'CRT'`, `d:subTaskId`. Payload `{task: <full Task with parentId>, parentId}`.
- **Move to project** — `a:'HMP'`, `o:'UPD'`, `d:taskId`. Payload `{task: <TaskWithSubTasks>, targetProjectId}`.
- **Reorder in project** — simplest robust: `PU` `{project:{id, changes:{taskIds:[...ordered]}}}`.
- **Plan for today** — `a:'HPT'`, `o:'UPD'`, `ds:taskIds`. Payload `{taskIds:[...], today:'YYYY-MM-DD'}`. Remove: `HRT` `{taskIds}`.
- **Planner day** — `a:'LU'` `{day, taskIds}` (`e:'PLANNER'`, `d:day`); `a:'LP'` `{task:<TaskCopy>, day, isAddToTop?}` (`d:taskId`).
- **Project create/update** — `PA` `{project:<full Project>}` / `PU` `{project:{id, changes:{...}}}`.
- **Tag create/update** — `GA` `{tag:<full Tag>}` / `GU` `{tag:{id, changes:{...}}}`.
- **Time tracking** — `KT`: `e:'TASK'`, `d:taskId`, payload `{taskId, date, duration}` — **additive delta**, strictly validated (`taskId===op.entityId`, valid date, finite ≥0 duration, else throw). Mirror into `entityChanges:[{entityType:'TASK',entityId,opType:'UPD',changes:{taskId,date,duration}}]`. Concurrent KT deltas commute.

## 4. Vector clocks, syncVersion, sv, pruning

- Comparison: per-key max, missing=0 → EQUAL/LESS/GREATER/CONCURRENT. Merge = per-key max. Max 20 entries.
- Per-op conflict: local frontier vs `op.v`: local GREATER → skip; EQUAL → dup skip; LESS → clean apply; CONCURRENT → auto-LWW by `t` (no dialog).
- **New clientId needs no registration.** Critical: op clock must be `merge(file.vectorClock, {CLI_ID: n})` — dominate the file clock → phone applies cleanly. CLI id format: ≥5 chars `[a-zA-Z0-9_-]` (native `{B|E|A|I}_{6 base62}`).
- **`syncVersion`**: +1 per upload; doubles as every client's cursor. CLI: `syncVersion = old + 1`. Regression ⇒ full resync everywhere.
- **`oldestOpSyncVersion`** = `sv` of `recentOps[0]`.
- Pruning: `recentOps.slice(-2000)` (MAX_RECENT_OPS = 2000).

## 5. Validation on import

- Envelope: prefix `^pf_(C)?(E)?(\d+)__`, JSON must parse, `version === 2`. `.bak` recovery on corruption.
- No schema validation of `state`/payloads at download. Batch-blockers: unknown `o`, `s>4`, `s<1`. Unknown `a` → silent no-op. `payload.id` mismatch force-rewritten to `d`. KT payloads throw on malformation.
- Post-apply Checkpoint D: typia validation + dataRepair — keep `state` referentially consistent (project.taskIds ↔ task, tag.taskIds ↔ task.tagIds, parent.subTaskIds).
- Conflict dialog only from snapshot path (gap signals). `lastModified` cosmetic; still set.

## 6. `pf_2__` prefix

`pf_{C?}{E?}{modelVersion}__`; C=gzip, E=encrypted, version 2 for sync-data.json. With compression/encryption off: exactly `pf_2__{json}`.

## 7. archiveYoung / archiveOld

- **Top-level is authoritative**; copies inside `state` are empty placeholders. CLI: copy both top-level blobs through unchanged unless archiving (then move task out of `state.task` into top-level `archiveYoung.task` AND emit `HX` `{tasks: TaskWithSubTasks[]}`).

## 8. External-writer checklist

1. No registration; stable clientId (e.g. `B_xxxxxx`), persist and reuse.
2. Write algorithm: GET (capture ETag) → per change: op `{id: uuidv7(), a, o, e, d, ds?, p, c: CLI_ID, s: 4, t: now, v: merge(fileClock, {CLI_ID: prev+1})}` (cumulative per op) → `newSyncVersion = syncVersion+1`; ops get `sv: newSyncVersion`; trim to 2000; recompute `oldestOpSyncVersion` → merge clocks into top-level `vectorClock` → apply same changes to `state` → set `syncVersion`, `lastModified`, `clientId: CLI_ID`, keep `version:2` → copy old content to `sync-data.json.bak` → PUT with `If-Match: <etag>` (412 = concurrent write → re-read, rebase, retry).
3. Ordering: causally dependent ops in array order with increasing counters.
4. Never: empty `recentOps` with populated state; lower `syncVersion`; remove others' vectorClock entries; strip existing ops (except head-trim); `s` ≠ 4; reuse op ids.
5. Don't edit `state.globalConfig.sync` local-only fields.
6. Phone re-downloads full file each cycle (WebDAV has no rev pre-check) — picks up CLI writes next cycle.
7. After a user force-import: re-read and rebase; next op clock dominates the import clock and survives the import filter.
