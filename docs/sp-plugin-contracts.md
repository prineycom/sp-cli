# Super Productivity AI Plugin — Method Contracts

Extracted from [ai-eifying/ai-assistant-plugin](https://github.com/ai-eifying/ai-assistant-plugin) source code (`index.html`, OpenAI function-calling tool definitions).

All operations run inside the SP app via PluginAPI. Time values in **milliseconds**. Dates as `YYYY-MM-DD` strings.

---

# PART 1: Plugin tool contracts

## Tasks

### `create_task`
Create a new task. Tasks without a project go to the Inbox.

| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | Task title |
| `notes` | string | | Task notes/description |
| `project_id` | string | | Project ID to assign task to |
| `parent_id` | string | | Parent task ID (for subtasks) |
| `tag_ids` | string[] | | Tag IDs to assign |
| `due_day` | string | | Due date `YYYY-MM-DD` |
| `time_estimate` | number | | Time estimate in **ms** |

### `get_tasks`
List tasks with optional filters. By default returns non-done, non-archived tasks.

| Param | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | | Filter by project ID |
| `tag_id` | string | | Filter by tag ID |
| `include_done` | boolean | | Include completed (default false) |
| `include_archived` | boolean | | Include archived (default false) |
| `search_query` | string | | Case-insensitive title/notes search |
| `parents_only` | boolean | | Exclude subtasks |
| `overdue` | boolean | | Only tasks due before today |
| `unscheduled` | boolean | | Only tasks with no due date |
| `planned_for_today` | boolean | | Only tasks planned for today |
| `recurring_only` | boolean | | Only recurring tasks |

### `update_task`
Update an existing task. Only pass fields to change.

| Param | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | ✅ | Task ID |
| `title` | string | | New title |
| `notes` | string | | New notes |
| `is_done` | boolean | | Mark done/undone |
| `due_day` | string | | `YYYY-MM-DD` or empty string to clear |
| `planned_at` | number | | Unix ms timestamp to plan (null to unplan) |
| `time_estimate` | number | | Time estimate in ms |
| `time_spent` | number | | Time spent in ms |
| `tag_ids` | string[] | | Bulk-replace all tags |
| `project_id` | string | | Move to project |

### `complete_task`
| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |

### `delete_task`
Permanently delete. Deleting a parent also removes all subtasks.

| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |

### `create_task_with_subtasks`
Create parent + subtasks in one call. Returns `parentId` and `subtaskIds`.

| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | Parent task title |
| `subtasks` | object[] | ✅ | `[{title, notes}]` |
| `notes` | string | | Parent notes |
| `project_id` | string | | Project for parent |
| `tag_ids` | string[] | | Tags for parent |

## Bulk

### `bulk_complete_tasks` (max 100)
| Param | Type | Required |
|---|---|---|
| `task_ids` | string[] | ✅ |

### `bulk_update_tasks` (max 100)
| Param | Type | Required | Description |
|---|---|---|---|
| `updates` | object[] | ✅ | Each: `{task_id, title?, notes?, due_day?, tag_ids?, time_estimate?, time_spent?}` |

## Tags on tasks

### `add_tag_to_task` (idempotent)
| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |
| `tag_id` | string | ✅ |

### `remove_tag_from_task` (errors if tag not present)
| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |
| `tag_id` | string | ✅ |

## Timer

### `get_current_task`
No params. Returns currently time-tracked task or `null`.

### `start_task`
Start time tracker. Stops other tracking automatically. Cannot start a completed task.

| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |

### `stop_task`
No params. Idempotent.

## Planning

### `plan_tasks_for_today` (max 100)
| Param | Type | Required | Description |
|---|---|---|---|
| `task_ids` | string[] | ✅ | IDs to plan/unplan |
| `unplan` | boolean | | If true, removes from today (default false) |

### `move_task_to_project`
Move a top-level task. Errors on subtasks.

| Param | Type | Required |
|---|---|---|
| `task_id` | string | ✅ |
| `project_id` | string | ✅ |

### `reorder_tasks`
Provide the **complete** ordered list.

| Param | Type | Required | Description |
|---|---|---|---|
| `task_ids` | string[] | ✅ | Complete ordered list |
| `context_id` | string | ✅ | Project ID or parent task ID |
| `context_type` | enum `project` \| `parent` | ✅ | |

## Worklog

### `get_worklog`
Time per day/project/tag, tasks completed, estimate accuracy.

| Param | Type | Required |
|---|---|---|
| `start_date` | string `YYYY-MM-DD` | ✅ |
| `end_date` | string `YYYY-MM-DD` | ✅ |

## Projects

### `get_projects`
No params.

### `create_project`
| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | |
| `description` | string | | |
| `color` | string | | Hex (e.g. `#2196F3`) |

### `update_project`
| Param | Type | Required |
|---|---|---|
| `project_id` | string | ✅ |
| `title` | string | |
| `color` | string | |

## Tags

### `get_tags`
No params.

### `create_tag`
| Param | Type | Required | Description |
|---|---|---|---|
| `title` | string | ✅ | |
| `color` | string | | Hex (e.g. `#FF9800`) |

### `update_tag`
| Param | Type | Required | Description |
|---|---|---|
| `tag_id` | string | ✅ | |
| `title` | string | | |
| `color` | string | | |
| `icon` | string | | |

## App state / UI

### `set_active_work_context`
| Param | Type | Required | Description |
|---|---|---|---|
| `context_id` | string | ✅ | Project or Tag ID to switch view to |

### `show_notification`
| Param | Type | Required | Description |
|---|---|---|---|
| `message` | string | ✅ | |
| `type` | enum `SUCCESS` \| `INFO` \| `WARNING` \| `ERROR` | | Default `INFO` |

---

# PART 2: Mapping to `sync-data.json` (WebDAV)

Verified against a real sync file (prefix `pf_2__` stripped before JSON). Structure:

```
{version, syncVersion, schemaVersion, vectorClock, lastModified, clientId,
 state: {task, project, tag, note, planner, taskRepeatCfg, timeTracking,
          reminders, section, archiveYoung, archiveOld, globalConfig, ...},
 recentOps: [...], oldestOpSyncVersion}
```

## Real task entity fields (camelCase in JSON)

| JSON field | Type | Plugin param | Notes |
|---|---|---|---|
| `id` | string | `task_id` | nanoid-style, e.g. `UVSxFt6EKIxJq952Pgj_O` |
| `title` | string | `title` | |
| `notes` | string | `notes` | |
| `isDone` | boolean | `is_done` | |
| `projectId` | string | `project_id` | `"INBOX_PROJECT"` for Inbox |
| `tagIds` | string[] | `tag_ids` | incl. special `"TODAY"` tag id |
| `subTaskIds` | string[] | — | order of subtasks = order in array |
| `created` | number (unix ms) | — | set on create |
| `timeSpent` | number (ms) | `time_spent` | cumulative |
| `timeSpentOnDay` | object | — | `{"2026-08-04": ms}` — per-day worklog source |
| `timeEstimate` | number (ms) | `time_estimate` | |
| `attachments` | array | — | |
| `dueDay`* | string | `due_day` | `YYYY-MM-DD`; absent key = no due date |
| `myDay` / `plannedAt`* | object/number | `planned_at` | planning metadata; absent in sample |
| `doneOn`* | number | — | set when `isDone=true` |
| `repeatCfgId`* | string | — | → `state.taskRepeatCfg` |
| `reminderId`* | string | — | → `state.reminders` |
| `parentId`* | string | `parent_id` | for subtasks |

\* = in SP task schema, not present in the current onboarding sample (fields appear once used).

## Registry pattern (all entities)

```
state.task.ids:     [id, ...]           // order matters (list order)
state.task.entities: {id: {...}}         // keyed by id
state.project.ids / entities             // same
state.tag.ids / entities                 // TODAY tag has id="TODAY"
```

## Per-method mapping

| Plugin method | sync-data.json write path |
|---|---|
| `create_task` | gen `id` (nanoid 21) → `state.task.ids.unshift(id)` (newest first; plugin uses `isAddToBottom`) → `entities[id] = {id, title, notes, isDone:false, tagIds, projectId, subTaskIds:[], timeSpent:0, timeSpentOnDay:{}, timeEstimate, created: <now ms>, attachments:[]}` → append to `project.taskIds` → op `CRT`/`TASK` in `recentOps` |
| `get_tasks` (read) | filter `state.task.entities` by `projectId`, `tagIds`, `isDone`, `dueDay` (overdue/unscheduled compare vs today), `repeatCfgId` (recurring), title/notes substring; archived = in `state.archiveYoung/Old` |
| `update_task` | patch `entities[id]` fields; `due_day:""` → delete `dueDay`; `tag_ids` → replace `tagIds` array |
| `complete_task` | `isDone=true` (+`doneOn=now`); subtasks are NOT auto-completed |
| `delete_task` | remove from `ids`, `entities`, `project.taskIds`; if parent → cascade delete every `subTaskIds` entry |
| `create_task_with_subtasks` | create parent, then create each subtask with `parentId`, push its id into parent's `subTaskIds` |
| `bulk_*` | loop the single-entity paths |
| `add_tag_to_task` | append to `entities[task].tagIds` if absent (idempotent); ensure tag exists in `state.tag.entities` |
| `remove_tag_from_task` | remove from `tagIds`; error if absent; also remove task id from `tag.taskIds` |
| `get_current_task` | read `state.task.currentTaskId` (null = no timer) |
| `start_task` | set `state.task.currentTaskId = task_id`; SP itself accrues `timeSpentOnDay[today]` while running — external CLI must compute elapsed itself on `stop` |
| `stop_task` | `currentTaskId = null` + add elapsed ms to `timeSpent` and `timeSpentOnDay[today]` |
| `plan_tasks_for_today` | add `"TODAY"` to `task.tagIds` + (in current SP schema) set `myDay={plannedAt}`; unplan → remove |
| `move_task_to_project` | `task.projectId = new`; pop from old `project.taskIds`, push to new |
| `reorder_tasks` | rewrite `project.taskIds` (context_type=project) or `parent.subTaskIds` (context_type=parent) to match given order |
| `get_worklog` | aggregate `timeSpentOnDay` across tasks in date range; group by `projectId` / `tagIds`; estimate accuracy = `timeSpent` vs `timeEstimate` |
| `get_projects` | read `state.project.entities` |
| `create_project` | gen id → `project.ids` + `entities[id] = {id, title, taskIds:[], backlogTaskIds:[], noteIds:[], isDone:false, isArchived:false, isHiddenFromMenu:false, isEnableBacklog:false, doneOn:null, advancedCfg:{worklogExportSettings:{...}}, theme:{...}}`; color → `theme.primary` |
| `update_project` | patch `title` / `theme.primary` |
| `get_tags` / `create_tag` / `update_tag` | same registry pattern in `state.tag`; `color`, `icon`, `title` fields; keep special `TODAY` tag intact |
| `set_active_work_context` | UI-only state — NOT in sync-data; **skip in CLI** |
| `show_notification` | UI-only — **skip in CLI** |

## Sync layer — REQUIRED on every write (the part the plugin gets for free)

The plugin mutates the live app state; the app then handles sync. A WebDAV CLI must replicate this manually:

1. **`recentOps`** — append `{id: uuid7, a:"HA", o:<op>, e:"TASK"|"PROJECT"|"TAG", p:{actionPayload:{...}, entityChanges:[]}, c:<clientId>}` — this is how SP propagates changes to other devices
2. **`vectorClock`** — `{<clientId>: <counter>}`; increment your client's counter on every write; pick a **unique `clientId`** for the CLI (never reuse the phone's)
3. **`lastModified`** — update to now (unix ms)
4. **`clientId`** (top-level) — set to the CLI's client id
5. Backup `sync-data.json` before every push; on `vectorClock` mismatch between read and write → re-read, re-apply, retry (optimistic locking)

## Gotchas

- Prefix `pf_2__` before JSON on the wire — strip on read, re-add on write
- `TODAY` is a real tag (`id="TODAY"`, `icon:"wb_sunny"`) — "Today view" = `tagIds` membership
- `state.task.isDataLoaded: false` in the file — app-local flag, ignore
- Task registry order (`ids`) drives UI order; `project.taskIds` drives order within project
- `timeSpentOnDay` keys are `YYYY-MM-DD` strings; values ms
- Timer (`start_task`) from an external CLI can't tick in real time — model it as: record start, on stop compute delta and write `timeSpentOnDay`