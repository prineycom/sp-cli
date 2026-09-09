# SuperProductivity data model — CLI implementation reference

Source: subagent deep-dive into johannesjo/super-productivity master (commit 2827f4c, 2026-09-09). Paths repo-relative.

## Task model (`src/app/features/tasks/task.model.ts`)

Required: `id`, `title`, `projectId` (never null), `subTaskIds: []`, `timeSpentOnDay: {}`, `timeSpent: 0`, `timeEstimate: 0`, `isDone: false`, `tagIds: []`, `created` (epoch ms), `attachments: []`.

Optional: `notes?`, `parentId?`, `doneOn?`, `modified?`, `remindAt?`, `dueDay?: 'YYYY-MM-DD'|null`, `dueWithTime?: number|null`, `hasPlannedTime?`, `deadlineDay?`, `deadlineWithTime?`, `deadlineRemindAt?`, `reminderId?` (DEPRECATED, stripped by repair), `repeatCfgId?`, `_hideSubTasksMode?`, issue-fields.

- **`dueWithTime` XOR `dueDay`** — setting one clears the other.
- `remindAt` (epoch ms) — THE reminder mechanism; `state.reminders` array is legacy, always keep `[]`.
- `doneOn` stamped on isDone=true, cleared on reopen.
- `modified` — UI-only; vector clocks drive conflicts.
- DEFAULT_TASK: `{id:'', subTaskIds:[], timeSpentOnDay:{}, timeSpent:0, timeEstimate:0, isDone:false, title:'', tagIds:[], created:now, attachments:[]}`.

## "Today" semantics (ARCHITECTURE-DECISIONS Decision #2)

**Membership = `dueWithTime` is today OR (no dueWithTime AND `dueDay === today`). `TODAY_TAG.taskIds` = ordering only. `'TODAY'` must NEVER appear in `task.tagIds`.**

- Self-healing selector rebuilds TODAY ordering after sync; disagreement is repaired.
- planTasksForToday (HPT): sets `dueDay=today`, clears `remindAt` (+`dueWithTime` if other day), prepends to `TODAY_TAG.taskIds`, removes ids from all `planner.days`.
- planTaskForDay (LP): task `{dueDay: day, dueWithTime: undef, remindAt: undef}`; if day==today → TODAY_TAG.taskIds ordering, planner untouched; future → remove from TODAY order + all planner days, insert into `planner.days[day]`.
- Real removal from today = unscheduleTask (HSX `{id, today?}`): clears dueDay/dueWithTime/remindAt + removes from TODAY_TAG.taskIds.

## Planner

`{days: {[YYYY-MM-DD]: taskId[]}, addPlannedTasksDialogLastShown}` — future days only; today's ordering lives in TODAY_TAG.taskIds. Cleanup deletes keys <= today. On day change, app itself plans `planner.days[today]` + dueDay===today tasks into Today.

## Schedule / reminders

- Scheduled = `dueWithTime` (epoch ms) + `dueDay: undefined` + `remindAt` (epoch ms, at-or-before dueWithTime).
- scheduleTaskWithTime (HS) payload: `{task: Task, dueWithTime, remindAt?, isMoveToBacklog: false}`; reschedule = HSR same shape; unschedule = HSX `{id}`.
- If scheduled day is today → also prepend to TODAY_TAG.taskIds.
- Deadlines separate: setDeadline (HDL) `{taskId, deadlineDay?, deadlineWithTime?, deadlineRemindAt?}`.

## Repeat configs (taskRepeatCfg)

Fields: `id`, `projectId: string|null`, `lastTaskCreationDay?`, `title`, `tagIds: []`, `order: 0`, `defaultEstimate?`, `startTime?: 'HH:mm'`, `remindAt?: TaskReminderOptionId`, `isPaused: false`, `quickSetting`, `repeatCycle: 'DAILY'|'WEEKLY'|'MONTHLY'|'YEARLY'`, `startDate?: 'YYYY-MM-DD'`, `repeatEvery: 1`, weekday booleans `monday..sunday`, `monthlyWeekOfMonth?`, `monthlyWeekday?`, `monthlyLastDay?`, `notes?`, `subTaskTemplates?`, `deletedInstanceDates?`, `skipOverdue?`, `waitForCompletion?`, `repeatFromCompletionDate?`, `shouldInheritSubtasks?`.

DEFAULT: `{repeatEvery:1, isPaused:false, quickSetting:'DAILY', repeatCycle:'WEEKLY', mon-fri:true, sat/sun:false, tagIds:[], order:0, skipOverdue:false, waitForCompletion:false, repeatFromCompletionDate:false, shouldInheritSubtasks:false, lastTaskCreationDay:today}`.

App materializes instances itself (deterministic id `rpt_<cfgId>_<YYYY-MM-DD>`). CLI: create cfg entity + op `RA` ('[TaskRepeatCfg][Task] Add TaskRepeatCfg to Task') payload `{taskId, taskRepeatCfg, startTime?, remindAt?}` — attach to an existing task (sets its repeatCfgId).

## Project

Fields: `id`, `title`, `icon?: null`, `isArchived: false`, `isDone: false`, `doneOn: null`, `isHiddenFromMenu: false`, `isEnableBacklog: false`, `taskIds: []` (**authoritative membership**, ordered, top-level non-backlog only), `backlogTaskIds: []`, `noteIds: []`, `advancedCfg: {worklogExportSettings}`, `theme` (primary default `#29a1aa`).
- INBOX = id `'INBOX_PROJECT'`.
- New project does NOT need a menuTree entry (missing items auto-appended).

## Tag

`{id, title, color?: null, created, icon?: null, taskIds: [], advancedCfg, theme}`.
- For regular tags **`task.tagIds` is membership truth; `tag.taskIds` is ordering** (repaired if inconsistent — keep both in sync anyway).
- System tags: TODAY, EM_URGENT, EM_IMPORTANT, KANBAN_IN_PROGRESS.

## timeTracking

`{project: {[id]: {[day]: {s,e,b,bt}}}, tag: {...}}` — session metadata only. Time per task/day lives ONLY in `task.timeSpentOnDay`. Worklog aggregates live + archive tasks.

## IDs

- Entities: nanoid v5 defaults — **21 chars, alphabet `A-Za-z0-9_-`**.
- Ops: UUIDv7. Repeat instances: `rpt_<cfgId>_<day>`.

## Key invariants for CLI

1. dueWithTime XOR dueDay.
2. `'TODAY'` never in task.tagIds.
3. Subtask: has `parentId`, listed in parent's `subTaskIds`, NOT in project.taskIds.
4. Top-level task in exactly one of project.taskIds / backlogTaskIds.
5. Dates LOCAL `YYYY-MM-DD`.
6. Keep `reminders: []`.
7. Keep entity `ids` ↔ `entities` consistent (ngrx shape).
