---
name: superproductivity
description: Manage Super Productivity (tasks, day planning, scheduling, time tracking, repeats, notes, boards, habit counters, calendars) through sp-mcp tools (sp_*) or the sp CLI. Use when the user asks to add, list, plan, schedule, complete or delete tasks, track time, review their day or agenda, or manage projects, tags, notes, boards or counters in Super Productivity.
---

# Super Productivity via sp-cli

You control a live Super Productivity setup through its WebDAV sync file.
Every write is picked up by the user's phone/desktop app — treat writes as
real, visible changes, not a sandbox.

Tools are MCP tools named `sp_<command>` (e.g. `sp_add`, `sp_plan_show`).
If you only have shell access, the same commands exist as `sp <command>`
(dashes instead of underscores: `sp plan-show`).

## Conventions that apply everywhere

- **Ids**: tools accept unique id prefixes. Never guess an id — get it from a
  listing first (`sp_list` with `json=true`, `sp_notes`, `sp_boards`, ...).
- **Structured output**: listing tools accept `json=true`. Prefer it whenever
  you parse the result; the plain form is for showing text to the user.
- **Destructive tools** (`sp_delete`, `sp_*_rm`, `sp_archive`, `sp_bulk` on
  >10 tasks) require `yes=true`. Without it they fail with a reminder instead
  of prompting. Ask the user before deleting anything you did not just create.
- **Formats**: dates `YYYY-MM-DD` (many tools also take `today`, `tomorrow`,
  `+N` days); times of day `HH:MM`; durations `30m`, `1.5h`; timestamps in
  data are unix ms.
- **Sync safety** is built in: op-log, vector clock, automatic backups and
  conflict retry all happen inside the tool. Never try to edit the sync file
  or "fix" sync state yourself; if state looks broken, run `sp_doctor` and
  report.

## Task basics

- Find tasks: `sp_list` (filters: `project`, `tag`, `search`, `today`,
  `overdue`, `unscheduled`, `done`, `all`), one task in full: `sp_show`.
- Create: `sp_add` — the title supports short syntax the user may also use
  verbatim: `"Call mom +family #phone @tomorrow 17:00 ~15m"` sets project,
  tag, schedule and estimate in one string. Flags (`project`, `due`, `at`,
  `est`, `notes`, `tag`) override the title syntax. Returns the new task id.
- Change: `sp_edit` (title/notes/estimate/...), `sp_complete` / `sp_reopen`,
  `sp_move` (another project), `sp_tag` (add/remove tags), `sp_subtask`,
  `sp_delete`. Mass changes: `sp_bulk` — always run it with `dry_run=true`
  first and show the user what matched.

## Planning the day

- `sp_today` / `sp_today_add` / `sp_today_rm` — today's list.
- `sp_plan` — put tasks on a future day; `sp_plan_show` — the planner;
  `sp_agenda` — overdue / today / scheduled / deadlines in one view. Start
  "what's on my plate" questions with `sp_agenda`.
- `sp_schedule` — a task at a concrete time (with reminder), `sp_deadline` —
  a due date, `sp_unschedule` / `sp_dismiss` to undo/quiet.

## Time tracking

- Live timer: `sp_start` → `sp_current` → `sp_stop` (stop books the elapsed
  time onto the task).
- Corrections and manual entries: `sp_track` / `sp_untrack` (a date can be
  given); reports: `sp_worklog` (day/week/project aggregation).

## Recurring tasks, notes, boards, counters, calendars

- Repeats: `sp_repeat` attaches a recurrence to a task; `sp_repeats`,
  `sp_repeat_edit` (pause/resume/change), `sp_repeat_skip`, `sp_repeat_rm`.
- Notes: `sp_notes`, `sp_note_add`, `sp_note_show`, `sp_note_edit`,
  `sp_note_move`, `sp_note_rm`. Boards: `sp_boards`, `sp_board_*`,
  `sp_board_panel_*`. Habit counters: `sp_counters`, `sp_counter_*`
  (`sp_counter_inc` for "done my pushups"). Day ratings: `sp_metrics`,
  `sp_metric_set`. Calendars (ICAL/CalDAV): `sp_providers`, `sp_provider_*`.
- Archive: `sp_archive` (done tasks away), `sp_archived`, `sp_restore`.

## Recipes

- "Add X for tomorrow morning" → `sp_add` with `at="<YYYY-MM-DD> 09:00"` (or
  put `@tomorrow 9:00` in the title), confirm back with the returned id.
- "What did I work on this week?" → `sp_worklog` (json), summarize by task.
- "Clean up done tasks" → `sp_archive` with `yes=true` after telling the user
  how many `sp_list done=true` shows.
- "Move everything from project A to today" → `sp_list` with
  `project="A"`, `json=true` → `sp_today_add` with the ids.
- Health check / weird state → `sp_doctor`; extra safety before bulk edits →
  `sp_backup`.
