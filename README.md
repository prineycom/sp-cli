# sp-cli

![CI](https://github.com/prineycom/sp-cli/actions/workflows/ci.yml/badge.svg)

A command-line client for [Super Productivity](https://super-productivity.com/) that works directly with the WebDAV sync file (`sync-data.json`) — no app required.

## Disclaimer

This is an unofficial tool. It is not affiliated with or endorsed by the Super Productivity project. sp-cli writes directly to your live sync file: automatic backups are made before every write (rotation of 10, kept in `~/.local/share/sp-cli/backups/`), but use it at your own risk.

## What it is and how it works

If you run the Super Productivity app (for example on a phone) and sync it against a WebDAV server, sp-cli lets you manage your tasks from the command line without touching the app. SP has no native CLI, so sp-cli operates on the sync file itself: it downloads `sync-data.json`, mutates `state.*`, appends operations to the operation log (`recentOps` + `vectorClock`, `syncVersion`, `lastModified`) and puts the file back. Your devices pick up the changes through a regular sync — no conflict dialog.

```
CLI (Python) → WebDAV (GET/PUT /superproductivity/sync-data.json) → devices pick it up via sync
```

Every write goes: GET (strip the `pf_2__` prefix) → backup → mutate state + ops → PUT with `If-Match` (re-add `pf_2__`).

## Documentation

- [docs/final-report-2026-09-10.md](docs/final-report-2026-09-10.md) — **final report**: what was built, how it was verified, limitations, operational facts
- [docs/research-sync-engine.md](docs/research-sync-engine.md) — the sync layer: op format, vectorClock, file finalization
- [docs/research-data-model.md](docs/research-data-model.md) — the `state.*` data model
- [docs/mvp-plan.md](docs/mvp-plan.md) — architecture and the operation matrix
- [docs/sp-plugin-contracts.md](docs/sp-plugin-contracts.md) — early research on plugin contracts (the research docs above are newer and more accurate)
- [docs/sample-sync-data.json](docs/sample-sync-data.json) — pristine sample sync file

## Agent integration (MCP + skill)

The full CLI surface is available to agents (Claude Code, Codex, Cursor, ...) as MCP tools: the `./sp-mcp` server (stdio, pure stdlib) generates `sp_*` tools by introspecting the argparse parser — 1:1 coverage of all 97 commands, and future commands are picked up automatically. An agent skill with working recipes is included. Setup and details: [integrations/README.md](integrations/README.md).

```bash
claude mcp add superproductivity -- /path/to/sp-cli/sp-mcp
```

## Installation

Requirements: Python 3.11+; the only external dependency is `requests`.

Via pipx or pip (the project ships a `pyproject.toml` exposing the `sp` and `sp-mcp` console scripts):

```sh
pipx install git+https://github.com/prineycom/sp-cli
# or, from a clone:
pip install -e .
```

Or just clone and use the wrapper:

```sh
git clone https://github.com/prineycom/sp-cli
cd sp-cli
./sp --help        # or: python3 -m sp_cli
```

## Configuration

```sh
./sp init --url http://localhost:8091 --user sp --password '...'
# (URL is an example — point it at your WebDAV server)
```

The config is written to `~/.config/sp-cli/config.toml` (override with the `SP_CLI_CONFIG` env variable). Keys:

| Key | Required | Description |
|---|---|---|
| `url` | yes | base URL of the WebDAV server |
| `user` | yes | WebDAV login |
| `password` / `password_file` | one of the two | password, or path to a file containing it |
| `client_id` | yes | generated once by `init`; a repeated `init` keeps it |
| `folder` | no | folder on the server (default `superproductivity`) |
| `backup_dir` | no | where automatic pre-write backups go (default `~/.local/share/sp-cli/backups`) |
| `backup_keep` | no | rotation for automatic backups (default `10`; `0` keeps everything) |
| `manual_backup_dir` | no | default `--dir` for `sp backup` (scheduled/manual backups) |
| `manual_backup_keep` | no | default `--keep` for `sp backup` |
| `mcp_read_only` | no | `true` = sp-mcp exposes only read-only tools (a `--read-only` flag can't override it back) |
| `mcp_include` | no | list of command globs sp-mcp exposes (e.g. `["list", "today*"]`) |
| `mcp_exclude` | no | list of command globs sp-mcp hides (e.g. `["provider*", "init"]`) |

CLI flags beat config values (`sp backup --dir/--keep`, `sp-mcp --include/--exclude`);
a repeated `sp init` rewrites the connection keys but preserves everything else.

`init` flags: `--url`, `--user`, `--password`, `--password-file`, `--folder`, `--backup-dir`.

## Commands

Tasks/projects/tags can be referenced by id, by a unique id prefix, or by title (notes — by id or prefix). Read commands support `--json`.

### Tasks

```sh
./sp list [--project P] [--tag T] [--done|--all] [--overdue] [--today] \
          [--unscheduled] [--search TEXT] [--parents-only] [--json]
./sp show <id> [--json]
./sp add "Title" [--project P] [--tag T ...] [--create-tags] \
         [--due today|tomorrow|+N|YYYY-MM-DD] [--at "YYYY-MM-DD HH:MM"] \
         [--remind 10m] [--est 30m] [--notes "..."] [--parent ID] [--backlog] \
         [--no-parse] [--parse-deadline]
./sp edit <id...> [--title T] [--notes N] [--append-notes N] [--est 1h] [--due DAY]
                               # multiple ids — one sync; --due "" clears; --title only with a single id
./sp bulk [<id...>] [--project P] [--tag T] [--overdue] [--search TEXT] [--done] [--all] \
          [--due DAY|--clear-due] [--tag-add T ...] [--tag-rm T ...] [--est 1h] \
          [--move-project P] [--complete|--reopen] [--dry-run] [--yes]
                               # bulk edit: selection = explicit ids ∪ filters (same as list);
                               # --all is a separate selector (all tasks, including done);
                               # everything is written as a single batch; --dry-run only
                               # shows the selection; with >10 tasks it asks for
                               # confirmation (--yes skips it).
                               # --due "" = --clear-due; --due goes through the planner (LP,
                               # a past day is rejected), --clear-due goes through HSX (also
                               # clears the planner). --due/--clear-due/--tag-add/--tag-rm
                               # skip subtasks (with a note on stderr).
                               # If between selection and write the number of matches GREW
                               # (the file changed), the write is aborted — rerun the command
./sp complete <id...>          # mark done
./sp reopen <id...>            # put back in progress
./sp delete <id...> [--yes]    # asks for confirmation
./sp subtask <parent> "Title" [--est 30m] [--notes "..."]
./sp move <id> --project P
./sp tag <id> [--add T ...] [--remove T ...]
./sp reorder --project P <id...>   # full rewrite of the order, see below
./sp archive [--yes]           # archive done tasks
```

### Short syntax in `sp add`

`sp add` parses the title the same way the SP app does: parsing happens locally, and what goes over the wire are ordinary operations (`HA`, `GA` for new tags, `HS` for a time, `HDL` for a deadline, `KT` for time spent, `RA` for a repeat). Recognized fragments are cut out of the title; the remainder (with whitespace collapsed) becomes the task title. What was recognized is printed as a `parsed: ...` line on stderr (stdout is still just the id).

| Token | Meaning |
|---|---|
| `30m`, `1h`, `1.5h`, `1h 30m`, `t2h` | time estimate (time parsing is gated behind `isEnableDue`, as in SP) |
| `1h/2h`, `30m/` | spent / estimate (time spent is logged for today); the `/` is what decides, so `30m/` is time spent only, no estimate. `/1h` is not matched by SP's regex at all — the token stays in the title |
| `+Project` | project by title, word-boundary matching: words must match in full, only the last one may be shortened (`+Wor` → "Work", but `+Workshop` with a project named "Work" is not a match); a single word matches the title with spaces removed (`+SomePro` → "Some Pro"); a fully typed title beats a partial one, on a tie the shorter title wins; archived and hidden projects are excluded |
| `#tag` | tag; a nonexistent one is created; a purely numeric `#123` is not a tag only at the very start of the title (issue reference) — in the middle (`Fix bug #123`) it is a regular tag; `#today` is reserved |
| `@date` | due date: `today`, `tomorrow`, `monday`…`sunday` (nearest occurrence, today counts), `2026-10-01`, `25.12`, `25.12.2027`, `3/11`, `15:00`, `3pm`, a bare hour ≤24 (`@15`), combinations like `@friday 15:00` |
| `@every …`, `@daily` | repeat (see below) |
| `!date` | deadline, disabled by default |

Repeats: `@daily`, `@weekly`, `@monthly`, `@yearly`/`@annually`, `@every monday` (and the `mon`…`sun` abbreviations), `@every 15th` (day of month), `@every weekday`/`@every workday` (Mon–Fri), `@every N days|weeks|months|years` and `@every N mondays…sundays` (N from 1 to 999; `N=1` collapses into the plain preset). `@every 2 weekdays`/`workdays` is NOT treated as a repeat (as in SP: "every other workday" cannot be expressed with a weekly cycle). For `@every monday`, `@every 2 fridays` and `@every 15th`, `startDate` is set to the nearest matching date.

A time immediately following the repeat phrase is taken into the schedule: `@every monday 9:00` → `startTime: "09:00"`, `startDate` = the nearest Monday, and the task itself is created as the first occurrence (`dueWithTime` on that Monday at 9:00). If the time has already passed today, the occurrence shifts by a full period (week/month/year), or to the next day for `@daily` (`@every weekday` skips weekends when doing so). A repeat without a time also produces a first occurrence: `dueDay` = `startDate`. Anything that is not a time (`@every monday and friday`) stays in the title.

```sh
./sp add "Buy bread #chores +Home @tomorrow 15m"   # date words are English only
./sp add "Standup @every monday +Work"
./sp add "Report @friday 15:00 !2026-10-01" --parse-deadline
./sp add "Literally #not a tag +not a project" --no-parse
```

Config gates (`globalConfig.shortSyntax` in the sync file, read on the fly): `isEnableProject`, `isEnableDue`, `isEnableTag` — default `true`; `isEnableDeadline` — default `false`; `--parse-deadline` enables `!` parsing for a single invocation. `--no-parse` disables parsing entirely; explicit flags (`--project`, `--tag`, `--due`, `--at`, `--est`) always override what was parsed, but the title still gets cleaned.

Differences from SP's parser:

- dates are a deterministic subset instead of chrono: only English words (`today`, `tomorrow`, weekday names), ISO dates, `DD.MM[.YYYY]`, `D/M`, `HH:MM`, `N am|pm` and a bare hour; other languages are not understood;
- numeric dates are day/month (`25.12` and `3/11` = December 25 and November 3), not month/day; a missing year is filled in so the date is not in the past;
- `@` must be preceded by whitespace or the start of the string (otherwise `mail@example.com` would turn into a due date);
- `#today` does not select the system TODAY tag (it can never live in `task.tagIds`); for "today" use `@today` or `sp plan`;
- new tags are created immediately, without a confirmation dialog;
- short syntax is not applied to subtasks (`--parent`) — a subtask has no project, tags or schedule of its own;
- with `--backlog`, a parsed `@due` is an error, exactly like explicit `--due`/`--at` (a backlog task is by definition unscheduled);
- a time parsed from the title (`@tomorrow 9:00`) does NOT set a reminder: in SP the parser returns `remindAt: null`. Explicit `--at` still defaults to a reminder "at start"; to be reminded about a parsed time, pass an explicit `--remind`;
- if the title ends up empty after parsing (`sp add "#home"`), parsing is aborted entirely: the task is created with the original text — no tags, project or schedule (stderr gets a `not parsed: ...` line). In SP you could fix such a title in the add bar; the CLI gets one shot.

### Ordering and conversion

Targeted moves are emitted as individual move operations (a shift, not a list rewrite):

```sh
./sp today move <id> --before <other>          # place directly before another task
./sp today move <id> --up|--down|--top|--bottom
./sp move-in-project <id> --up|--down|--top|--bottom
./sp move-in-project <id> --after <other>
./sp subtask move <id> --up|--down|--top|--bottom     # among the parent's subtasks
./sp subtask reparent <id> --parent <new> [--after <sib>]   # move to another parent
./sp demote <id> --parent <target> [--after <sib>]    # task → subtask
./sp promote <id> [--today]                           # subtask → task
./sp plan move <id> --before <other>                  # in the planner (the day is taken from the anchor)
```

`--up`/`--down` jump over done neighbors — like the app does — but only in context lists (`today move`, `move-in-project`). Subtasks (`subtask move`) do no skipping: all neighbors count.

`plan move` takes the day from the anchor, so it refuses if the anchor is not placed in any planner day and has no `dueDay`.

`promote` of a done subtask moves it to today (`dueDay` = today, `dueWithTime` removed) — this is what SP's reducer does.

When subtasks change parents (`reparent`, `demote`, `promote`), both parents' times are recalculated: `timeSpentOnDay`/`timeSpent` — sum over subtasks, `timeEstimate` — remaining work (`max(0, estimate − spent)` over undone subtasks).

`demote` refuses when the app would not apply the conversion anyway: the task has a parent, its own subtasks, a repeat, an issue link, a time (`--at`) or a reminder; or the target is itself a subtask (nesting is two levels only).

⚠️ **Gotcha:** the old `sp reorder` rewrites the project's `taskIds` in full (op `PU`). In a race with another device, entity-LWW can roll back the other device's project changes. Use the move commands above for targeted reordering.

### Archive

```sh
./sp archived [--search TEXT] [--subtasks] [--json]   # what's in the archive
./sp restore <id> [--today]                           # bring a task back from the archive
```

`archived` reads **both** archive blobs (`archiveYoung` and `archiveOld` — both at the top level of the file and inside `state`), marks them with an `age` column (`young`/`old`), shows the project, completion date and subtask count. Subtasks are not shown as separate rows (they come back with their parent) — `--subtasks` includes them. Archived task ids resolve by prefix **separately** from live ones: a prefix that matches in both is rejected — use a longer id.

`restore` sends a single `HR` op with a fully materialized payload (`{task, subTasks, restoreToToday?}`): the task comes back together with all its subtasks, the ids are scrubbed from all archive blobs, the task becomes not-done (`isDone`, `doneOn` cleared), a vanished project is replaced with the inbox, `TODAY` and dead tags are removed from `tagIds`, a dangling `repeatCfgId` is cleared. The root task is appended to the project list (**never to the backlog**) and to the `taskIds` of its tags. With `--today` the task is immediately planned for today (`dueDay`, reminder cleanup on the task and its subtasks, prepend into `TODAY`, removal from the planner). Restoring a task that is already live is an error.

Archive maintenance belongs to the app: the CLI **never** emits the `archiveYoung`→`archiveOld` flush (`AF`), compaction (`AC`) or `AR` — devices do that on their own schedule, and `AC` is destructive on top of that. The CLI only reads the archive, writes to it via `archive`, and removes from it via `restore`.

### Day planning

```sh
./sp today [--json]            # today's list
./sp today add <id...>         # plan for today
./sp today rm <id...>          # remove from today
./sp plan <id...> --day tomorrow   # plan for a day (YYYY-MM-DD | today | tomorrow | +N)
./sp plan show [--day DAY] [--json]
./sp agenda [--json]           # overdue / today / scheduled / deadlines (7d)
```

### Project backlog

The backlog is a project's second task list; it is enabled per project. A task always lives in exactly one of the two lists.

```sh
./sp backlog --project P [--json]     # backlog tasks, in backlogTaskIds order
./sp backlog add <id...>              # from the project list into the backlog (to the top)
./sp backlog rm <id...>               # back into the project list
./sp backlog clear --project P        # entire backlog back into the list
./sp project edit <id> --enable-backlog | --disable-backlog
./sp add "Title" --project P --backlog   # create straight into the backlog
```

The backlog must be enabled (`--enable-backlog`), otherwise `backlog add` and `add --backlog` fail: the app silently ignores such operations. `--disable-backlog` merges the backlog back into the project list (same as the app). A subtask cannot go into the backlog — move the parent.

### Calendar and scheduling

```sh
./sp schedule <id> --at "2026-09-10 14:00" [--remind 10m]
./sp unschedule <id>
./sp deadline <id> (--day YYYY-MM-DD | --at "YYYY-MM-DD HH:MM") [--remind 1h|none]
./sp deadline <id> --clear            # remove the deadline entirely (and its reminder)
./sp deadline <id> --clear-reminder   # keep the deadline, drop the reminder
./sp dismiss <id>                     # drop the task's reminder, keep the time
./sp repeat <id> --every day|week|month|year [--interval N] [--days mon,tue] \
             [--start-time HH:MM] [--start-date YYYY-MM-DD] \
             [--remind AtStart|m5|m10|m15|m30|h1]
./sp repeats [--json]          # list repeat configs (paused, time, reminder, skips)
./sp repeat edit <cfg-id|title> [--title T] [--every day|week|month|year] [--interval N] \
             [--days mon,thu] [--start-time HH:MM] [--start-date YYYY-MM-DD] \
             [--remind AtStart|m5|m10|m15|m30|h1] [--est 30m] [--notes N] \
             [--pause|--resume] [--clear startTime,remindAt,defaultEstimate,notes,
                                         monthlyWeekOfMonth,monthlyWeekday,monthlyLastDay]
./sp repeat skip <cfg-id|title> --date YYYY-MM-DD|today|tomorrow
./sp repeat rm <cfg-id|title> [--yes]   # delete a repeat config; the tasks themselves stay,
                                        # the repeatCfgId link is removed (live + archive)
```

`deadline --clear` does not touch `due*` (planning and deadlines are independent axes), and `dismiss` does not touch the deadline or unschedule the task. Editing a timed deadline (`--at`) without `--remind` preserves the reminder's **offset**: it is recomputed from the new time (and dropped if it lands in the past). A day deadline (`--day`) carries no reminder — like the SP dialog, it removes it; `--remind none` removes the reminder explicitly.

`repeat edit` sends `RU`. Fields are cleared only through `--clear`: the keys to clear travel as a `clearedFields` sibling next to `taskRepeatCfg` in the actionPayload, because `changes: {startTime: undefined}` is lost by any JSON serialization and turns into a no-op on other devices. `--clear startTime` also clears `remindAt` (the reminder is tied to the start time, as in the SP dialog).

Changing `--every/--days/--interval` recomputes `quickSetting`, `repeatCycle` and — for the weekly cycle only — the seven weekday flags. Weekdays are always preserved when the cycle does not change (even for configs created in SP with the `MONDAY_TO_FRIDAY`/`WEEKLY_CURRENT_WEEKDAY` presets), so `--interval 2` on "Wednesday only" stays "Wednesday only". `quickSetting` is always kept consistent with the flags: mon–fri → `MONDAY_TO_FRIDAY`, exactly one day → `WEEKLY_CURRENT_WEEKDAY`, everything else (and any `--interval` ≠ 1) → `CUSTOM` — otherwise SP would rewrite the days itself on the next dialog save. Changing the cycle clears the monthly anchors (`monthlyWeekOfMonth`, `monthlyWeekday`, `monthlyLastDay`) via `clearedFields` — this is SP's `MONTHLY_ANCHOR_RESET`.

`repeat skip` sends `RDI` (append-only, idempotent): the date lands in `deletedInstanceDates` and the future instance is not created. An already created task is not deleted by this — remove it separately (`sp delete rpt_<cfgId>_<date>`).

### Projects and tags

```sh
./sp projects [--all] [--json]
./sp project add "Title" [--color '#a05db1']
./sp project edit <id> [--title T] [--color C] [--hide] \
                       [--enable-backlog|--disable-backlog]
./sp project archive <id>
./sp project rm <id> [--yes]   # FULL project deletion
./sp tags [--json]
./sp tag new "Title" [--color C]
./sp tag edit <id> [--title T] [--color C]
./sp tag rm <id> [--yes]       # delete a tag; it is removed from all tasks
```

⚠️ `project rm` deletes the project outright: all its tasks (including subtasks and the backlog), notes, sections and the project's already-archived tasks — **WITHOUT archiving**; there is no way to restore them. If you want the soft option, use `project archive`. It also deletes the project's repeat configs (regardless of their tags) and its time statistics (`timeTracking.project`, live and in both archives), and `globalConfig.tasks.defaultProjectId` / `misc.defaultStartPage` are repaired to `INBOX_PROJECT` / `0` if they pointed at the deleted project — exactly as SP does. The inbox cannot be deleted.

`tag rm` only removes the tag from tasks; the tasks themselves stay (except a task with no tags, no project and no parent at all — SP's rule deletes it together with its subtasks; the same rule applies to archived tasks). System tags (`TODAY`, `EM_URGENT`, `EM_IMPORTANT`, `KANBAN_IN_PROGRESS`) cannot be deleted.

### Notes

```sh
./sp notes [--project P] [--today] [--json]   # --today — pinned for today
                                              # `./sp note` with no subcommand = ./sp notes
                                              # --project: order as in the app (project.noteIds)
./sp note add "Text" [--project P] [--pin]    # prints the id
./sp note show <id> [--json]
./sp note edit <id> [--content T | --append T] [--pin|--unpin] [--color '#a05db1']
./sp note rm <id> [--yes]                     # asks for confirmation
./sp note move <id> --project P
```

### Task attachments

```sh
./sp attach <task> <url-or-path> [--title T] [--type link|img|file]
                                              # prints the attachment id
                                              # default type: image → img,
                                              # file:// or a local path → file, else link
./sp attachments <task> [--json]              # `./sp attach <task>` without a path = the same
./sp attach edit <task> <attach-id> [--title T] [--path P] [--type ...]
./sp attach rm <task> <attach-id>
```

Attachments are also shown by `./sp show <task>`. `attach-id` resolves by prefix within the task.

### Boards

```sh
./sp boards [--json]                          # boards and their panels with filters
./sp board add "Title" [--cols N]             # prints the id (default cols=2)
./sp board edit <id> [--title T] [--cols N]
./sp board rm <id> [--yes]                    # asks for confirmation
./sp board sort <id>...                       # the listed boards go first
```

Panels (board/panel ids can be given as a prefix or a title):

```sh
./sp board panel add <board-id> "Title" [filters]
./sp board panel edit <panel-id> [--title T] [filters]
./sp board panel rm <panel-id>
./sp board panel order <panel-id> <task-id>...
```

Panel filters: `--tags a,b`, `--exclude-tags c`, `--tags-match all|any`, `--exclude-tags-match all|any`, `--project P` (repeatable) | `--all-projects`, `--done all|done|undone`, `--scheduled all|scheduled|not`, `--backlog all|no|only`, `--parents-only` | `--no-parents-only`, `--sort dueDate|created|title|timeEstimate [--dir asc|desc]`, `--sort manual` (= `--no-sort`) — drop sorting and go back to manual order.

Note: `board panel order` only works on a panel without sorting: if the panel has `sortBy` set, SP ignores manual order — run `./sp board panel edit <panel-id> --sort manual` first.

Note: `board panel order` sets **only the order** of tasks within the panel — panel membership is always computed from the filters; you cannot add a task to a panel by hand. Panels are edited by rewriting the board's entire panel array (SP has no working single-panel edit operation).

### Counters / habits (simple counters)

```sh
./sp counters [--json]                        # id, type, on/off, today's value, streak
                                              # `./sp counter` with no subcommand = ./sp counters
./sp counter add "Title" [--type click|stopwatch|countdown] [--icon name] \
    [--countdown 30m] [--no-streak] [--streak-min N] [--streak-days mon,tue,...]
./sp counter edit <id> [--title T] [--icon name] [--enable|--disable] \
    [--streak|--no-streak] [--streak-min N] [--streak-days mon,...] [--countdown 30m]
./sp counter rm <id> [--yes]                  # asks for confirmation
./sp counter set <id> <value> [--date DAY]    # absolute value for a day
./sp counter inc <id> [--by N] [--date DAY]   # +1 click by default (not for stopwatch)
./sp counter log <id> 30m [--date DAY]        # add time to a stopwatch counter
./sp counter order <id>...                    # the listed ones go first
```

For `stopwatch` counters the value is a duration (`45m`, `1.5h`); for the rest it is a click count; the same rule applies to `--streak-min`. `inc`/`--by` is clicks only: for a stopwatch counter the command refuses and points you to `counter log` (time) or `counter set` (absolute duration). `set`/`inc` always send an absolute value (clamped to ≥0), `log` sends a delta. The "counter currently running" flag (`isOn`) is device-local: the CLI always writes `false` and cannot start/stop the timer.

### Metrics / day rating

```sh
./sp metrics [--from DAY] [--to DAY] [--json]  # per-day table: impact, energy,
                                               # focus sessions, done/plan, notes
                                               # `./sp metric` with no subcommand = ./sp metrics
./sp metric set [--day DAY] [--impact 1-4] [--energy 1-3] [--notes "..."] \
    [--reflect "text"] [--remind-tomorrow|--no-remind-tomorrow] \
    [--completed N] [--planned N]              # defaults to today
./sp metric focus 25m [--day DAY]              # add a focus session (additive)
./sp metric rm <YYYY-MM-DD> [--yes]            # delete a day's rating
```

A metric's id is the day itself (`YYYY-MM-DD`); there is no separate "weekly" metric. `metric set` sends a self-creating patch (`EU`): if the day has no rating yet, one is created with defaults plus the applied fields. `--reflect` appends a reflection to the existing ones (the array is read from fresh state, so a retry on 412 does not lose entries). `metric focus` is an additive operation (`EL`); the duration must be positive. Full metric rewrite (`EX`) is not used: a partial payload would wipe fields. The mood/productivity/obstruction/improvement fields no longer exist in SP; the CLI does not write them and `doctor` complains about them.

### Integrations / calendars (issue providers)

```sh
./sp providers [--json]                       # id, key, on/off, url, project, auto-import
                                              # `./sp provider` with no subcommand = ./sp providers
./sp provider add-ical <url> [--auto-import] [--project P] [--tag T ...] [--create-tags] \
    [--check-every 2h] [--banner-before 2h] [--include-regex RE] [--exclude-regex RE]
./sp provider add-caldav --url U --resource R --username U --password P \
    [--category-filter C] [--project P] [--tag T ...] --store-plaintext-credentials
./sp provider edit <id> [--enable|--disable] [--url U] \
    [--auto-import|--no-auto-import] [--project P|--no-project] \
    [--check-every 2h] [--banner-before 2h] [--include-regex RE] [--exclude-regex RE] \
    [--username U] [--password P --store-plaintext-credentials] [--category-filter C]
./sp provider rm <id> [--yes]                 # delete a provider and unlink its tasks
./sp provider order <id>...                   # the listed ones go first, the tail is kept
```

The CLI only connects and configures a provider — calendar tasks (`cal_*`) are created by the app itself on the next poll; the CLI does not synthesize them. A provider is always written in full (a complete cfg for its key): SP validates built-in providers with typia and rejects a partial object. `provider rm` collects `taskIdsToUnlink` from live tasks **and all archive blobs** (`archiveYoung`/`archiveOld` — both at the top level of the file and inside `state`: a file can carry both at once) and scrubs their issue-link fields (`issueId`, `issueProviderId`, `issueType`, `issueWasUpdated`, `issueLastUpdated`, `issueAttachmentNr`, `issueTimeTracked`, `issuePoints`).

**CalDAV warning:** the login and password are stored in the sync file **in plain text** (and end up in every backup). That is why `add-caldav` refuses to run without the explicit `--store-plaintext-credentials` flag.

Supported keys are `ICAL` and `CALDAV`. Plugin providers (`plugin:*`), Jira/GitHub/GitLab and `dismissedCalendarAutoImportEventIdsByProvider` are not touched by the CLI: `provider edit`/`provider rm` refuse on a foreign key — edit such a provider in the app.

`sp doctor` additionally complains about tasks (live and archived) whose `issueProviderId` points nowhere, and about built-in ICAL/CALDAV providers with an incomplete cfg.

### Time / worklog

```sh
./sp track <id> 30m [--date YYYY-MM-DD]     # add tracked time
./sp untrack <id> 30m [--date YYYY-MM-DD]   # remove excess time (op TR, clamped at 0)
./sp worklog [--from DAY] [--to DAY] [--json]  # by day, by project, estimate accuracy
```

### Live timer

```sh
./sp start <id>          # start the timer (a running one is auto-stopped first)
./sp start               # show the current timer (alias for `current`: exit 1 if none)
./sp current [--json]    # what's ticking: task, start, elapsed (exit 1 if no timer)
./sp stop                # stop and log the time (KT), exit 2 if no timer
./sp stop --discard      # reset without logging time (the file is wiped blindly —
                         #   works even on a corrupted timer.json)
```

The timer is local: SP's `currentTaskId` is not synced (there is no op representation for it), so the state lives in `~/.local/share/sp-cli/timer.json` (atomic writes, override via the `SP_CLI_TIMER` env variable), and only the accumulated time goes over the wire on `stop` — one `KT` op per day. A timer running across midnight is split into multiple `KT` ops in a single batch. Less than a minute — a warning and 1m is logged (or `--discard`). If the task has vanished in the meantime (deleted on another device), `stop` warns on stderr, drops the interval, cleans the timer file and exits 0; the auto-stop in `start` behaves the same and still starts the new task. A `started_at` in the future is an error (exit 2); the timer file is kept.

The day used by `track`/`stop` is **logical**: `globalConfig.misc.startOfNextDayTime` (an `"HH:MM"` string, the canonical form) or the legacy numeric `misc.startOfNextDay` (hours, honored only if the string is absent entirely). As in SP, a malformed string resets the offset to 0 rather than falling back to the number. With the default value (0) the boundary is plain midnight.

Subtask time is rolled up to the parent: `track`/`untrack` recompute the parent task's `timeSpentOnDay[date]` and `timeSpent` (like `updateParentTimeSpentIncremental` in SP) — in state only, the op payload is unchanged; every device derives the rollup itself.

### Utility

```sh
./sp pull [--raw]    # download and show a summary of the sync file (--raw — the full JSON)
./sp doctor          # check state invariants
./sp backup [--dir DIR] [--keep N]   # download and store a backup (see "Backups")
./sp init ...        # see "Configuration"
```

## Timezones

All wall-clock times — `--at "YYYY-MM-DD HH:MM"`, `--remind` offsets, deadline
times, and times parsed from the short syntax (`@friday 15:00`) — are converted
to unix ms with a naive `strptime().timestamp()`, which resolves them in the
**process's local timezone** (the `TZ` env var), not UTC and not the timezone
of your SP clients. If the host's timezone differs from the one you actually
live in, every scheduled task and reminder lands at the wrong local time on
your devices.

This bites hardest on headless hosts, which are very often configured as UTC:

```sh
timedatectl                     # or: cat /etc/timezone
# "Time zone: Etc/UTC (UTC, +0000)"  →  "20:00" you type becomes 22:00 in
#                                       a Europe/Belgrade SP client
```

**Fix per invocation** (CLI):

```sh
TZ=Europe/Belgrade ./sp schedule <id> --at "2026-09-17 20:00" --remind 0m
```

**Fix for the MCP server:** MCP clients typically pass a *filtered* environment
to stdio subprocesses (e.g. Hermes passes only `PATH`, `HOME`, `LANG` and a few
other safe variables — `TZ` is **not** among them, even if your shell has it
exported). Set it in the client's server config instead, e.g. for Hermes in
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  superproductivity:
    command: /path/to/sp-cli/sp-mcp
    env:
      TZ: Europe/Belgrade
```

MCP servers spawn at agent startup, so the env change requires an agent
restart to take effect.

Related gotchas in the same area:

- `sp schedule` without `--remind` clears the task's `remindAt` — an at-start
  reminder must be re-armed explicitly with `--remind 0m`. This bites during
  timezone-fix reschedules: fixing the time silently drops the reminder.
- Display commands (`sp list`, `sp show`, …) format timestamps back through the
  same process timezone — a host in UTC will *render* scheduled times as UTC,
  which makes a correct file look wrong. The sync file is always the source of
  truth; check `dueWithTime` there, or run the command with the same `TZ=` you
  scheduled with.
- Repeat configs (`startTime`) are interpreted by the SP app itself in the
  client's timezone — the CLI does not convert them, so they are unaffected by
  the host's `TZ`.

## Write safety

- Before every PUT, a backup is made automatically in `~/.local/share/sp-cli/backups` (rotation: the last 10 are kept).
- Optimistic locking: PUT goes with `ETag`/`If-Match`; on HTTP 412 (a concurrent write from another device) the file is re-read and the mutations are re-applied, up to 3 attempts.
- The CLI writes under its own `client_id` (generated by `init`), distinct from your other devices — the vector clock resolves the ordering of changes.
- A CalDAV provider's password is stored in the sync file (and in the backups) in plain text — the command requires an explicit `--store-plaintext-credentials`.

## Backups

Two independent layers:

- **Automatic** — before every write, the just-downloaded file is saved to the
  config `backup_dir` (default `~/.local/share/sp-cli/backups`; rotation
  `backup_keep`, default 10). This protects against a bad write, not against
  data loss over time — `backup_keep` writes later the old state is gone.
- **Explicit** — `sp backup` downloads the current file and stores it:

  ```sh
  ./sp backup                          # into the config backup_dir (rotation 10)
  ./sp backup --dir ~/sp-backups --keep 30   # separate directory, keep the newest 30
  ./sp backup --dir ~/sp-backups --keep 0    # keep everything
  ```

  For scheduled backups always use a directory **outside** the config
  `backup_dir`: write operations rotate that directory down to `backup_keep`
  files, so scheduled backups stored there would be evicted within a few
  writes. Instead of flags you can set the defaults once in the config —

  ```toml
  manual_backup_dir = "~/sp-backups"
  manual_backup_keep = 31
  ```

  — after which a plain `sp backup` (and the cron line below) needs no
  arguments.

### Scheduled backups with cron

```sh
crontab -e
```

```cron
# daily at 03:00, keep a month of backups
0 3 * * * /path/to/sp-cli/sp backup --dir "$HOME/sp-backups" --keep 31 >> "$HOME/sp-backups/backup.log" 2>&1
```

(If you installed via pipx/pip, use the `sp` on your PATH — cron's PATH is
minimal, so an absolute path like `$HOME/.local/bin/sp` is the safe choice.)
On systemd machines a timer unit works just as well; cron is simply the
lowest-common-denominator recipe.

### Restoring from a backup

A backup is a byte-exact copy of the remote file (including the `pf_2__`
prefix), so restoring is uploading it back to the WebDAV server:

```sh
curl -u USER:PASSWORD -T sync-data-20260911-030000-000000.json \
  "http://your-server/superproductivity/sync-data.json"
```

Do this while other devices are idle. A restore rewinds the operation log and
vector clock, so on their next sync your devices may show a conflict dialog —
choose the **remote** version to accept the restored state.

## Testing

```sh
python3 -m pytest tests/ -q    # 1113 tests: unit tests with no network + MCP e2e against a loopback WebDAV emulator
```

Plus an integration sweep against a test WebDAV server (a copy of a live file) and a check that a real SP install picks the changes up via sync.

## Status

- [x] Research: sync-data.json structure, operation contracts, sync layer
- [x] MVP: the full read/write command set (tasks, day plan, scheduling, projects, tags, time)
- [x] Sync layer (vectorClock, recentOps, optimistic locking + retry)
- [x] Notes: CRUD, pin for today, project attachment
- [x] Boards: board and panel CRUD, panel filters, task and board ordering
- [x] Counters/habits: CRUD, set/inc, stopwatch time log, ordering
- [x] Metrics: day rating (impact/energy/notes/reflections), focus sessions
- [x] Integrations: ICAL/CalDAV calendars — connect, edit, delete, ordering
- [x] Archive: listing archived tasks, restoring a task with its subtasks
- [x] Task attachments: add, edit, delete, list
- [ ] YouTrack bridge (optional)

## Known limitations

- The live timer (`start`/`stop`) is local: the fact that a timer is running is not synced, only the accumulated time. Counters have no start/stop for the same reason (`isOn` does not sync).
- Notes have no reordering (`NO`) and no attachments.
- Boards have no single-panel edit operation (`BP` is a dead reducer in SP): any panel edit rewrites the board's entire panel array.
- A conflict with a concurrent write from another device is handled by retry (re-read + re-apply); when attempts are exhausted, the command exits with an error and no data is lost.
