# SP CLI — Exact write contracts: timer, backlog, archive, deletes, repeat, reorder/convert, attachments, deadline-clear, shortSyntax, bulk

Source: /tmp/sp-src (master, Sept 2026). Envelope conventions per `.yoke/ai/sp-cli-mvp/research-sync-engine.md`.

## 0. Envelope rules (additions)

- `ds` derivation: single-entity ops carry both `d` and 1-element `ds` (harmless either way); bulk ops carry `ds` (d absent for pure-bulk).
- `clearedFields: string[]` (sibling of the update object in actionPayload) is supported ONLY by `TUU` and **`RU`**. Max 32 keys, `id` skipped. Elsewhere clearing = reducer-side.
- `HU` auto-backfills `doneOn` on isDone:true (capture+replay). **HUM does NOT.**

## 1. Live timer — NOT SYNCED

- `setCurrentTask`/`unsetCurrentTask`: no meta, no code — unrepresentable as ops. `currentTaskId/selectedTaskId/lastCurrentTaskId` are device-local.
- Time accrual sync = **KT** `{taskId, date, duration}` additive delta (existing implementation correct; keep entityChanges mirror; receiver validates strictly; duration ≥ 0; negative → use TR).
- **TR** `[Task] Remove time spent` — payload **`{id, date, duration}`** (key `id`!), reducer `max(x-duration, 0)`, applies local AND remote. For corrections.
- **KS** `{contextType: 'TAG'|'PROJECT', contextId, date, data: {s?,e?,b?,bt?}}`, e=TIME_TRACKING, d=`"{TYPE}:{id}:{date}"` — **replaces** the day leaf. **KW** `{ctx: {id, type}, date, updates}` — **shallow-merges**. s=workStart ms, e=workEnd ms, b=break count, bt=break ms.
- `sp stop` writes: **1 KT op only** (+entityChanges) and applies the same delta to state.timeSpentOnDay + timeSpent recompute. KS/KW session metadata are NOT required alongside KT (per-task time lives only in task.timeSpentOnDay; worklog works without sessions). Optionally KW `{ctx: {id: projectId, type: 'PROJECT'}, date, updates: {s: min, e: max}}` to keep workStart/workEnd stats — nice-to-have. Never touch currentTaskId (device-local, unrepresentable as ops).
- CLI timer itself = local file (start ts + task id), not synced.

## 2. Project backlog

All `e:'TASK'`, `o:'MOV'` except PBA (`e:'PROJECT'`, `o:'UPD'`, d=projectId). All persistent.

| Code | payload |
|---|---|
| PRB regular→backlog | `{taskId, afterTaskId: string|null, workContextId}` — **requires project.isEnableBacklog else silent no-op** |
| PBR backlog→regular | `{taskId, afterTaskId, workContextId, src, target}` (src/target: 'UNDONE'|'DONE'|'BACKLOG') |
| PAB auto→backlog | `{taskId, projectId}` — requires isEnableBacklog; prepends |
| PAR auto→regular | `{taskId, projectId, isMoveToTop}` — no-op if already in taskIds or not in backlog |
| PM move in backlog | `{taskId, afterTaskId, workContextId}` |
| PMU/PMD/PMT/PMB | `{taskId, workContextId, doneBacklogTaskIds}` — ⚠️ field carries the **NOT-done** ids (wire-frozen inverted name) |
| PBA all→regular | `{projectId}` → taskIds += backlogTaskIds, backlog=[] |

`moveItemAfterAnchor`: filter id out; afterId null → prepend; anchor missing → no-op if item was present, else append. Replay-idempotent.
Invariant: task in exactly one of taskIds/backlogTaskIds.

## 3. Archive restore & maintenance

### HR restoreTask — payload `{task, subTasks: Task[], restoreToToday?: {today, startOfNextDayDiffMs}}`, o=UPD, d=task.id
⚠️ restoreToToday materialization happens in the creator — CLI must pre-materialize: task.dueDay=today, remindAt=undefined, dueWithTime cleared if other day; subtasks blanked dueDay/dueWithTime/remindAt.
State effects: no-op if task.id already active (idempotent); normalize restored task: isDone:false, doneOn:undefined, unknown projectId→INBOX_PROJECT, strip TODAY from tagIds, clear dangling repeatCfgId; addMany task+subtasks; root re-added to project.taskIds via unique append (**never backlog**); tags: unique([...tag.taskIds, ...ids]); restored tasks section-less. Archive side: remove `[task.id, ...task.subTaskIds]` from archiveYoung first, archiveOld for leftovers — **uses task.subTaskIds, populate it**.
- `HRD` restoreDeletedTask = undo-delete of active task, NO archive effect — not the un-archive op.

### AF flush young→old — `{timestamp}` only, e=ALL, o=BATCH, no d/ds. Receiver re-runs flush deterministically on its own archives. **Recommend: CLI never emits AF** (pure size optimization; devices do it on 14d cadence). AC compress: NEVER emit (destructive). AR: never emit (no meta).

### TRD roundTimeSpentForDay — `{day, taskIds, roundTo, isRoundUp, projectId?}`, bulk, absolute rounding of leaf tasks; the only conflict-decomposable bulk op.

### HX archive (already implemented) — refinement: receiver maps tasks via `{...task, reminderId: undefined, isDone: true, dueWithTime: undefined, dueDay: undefined, _hideSubTasksMode: undefined, doneOn: task.doneOn ?? parent.doneOn ?? now}`; archive ids kept `[...new Set(ids)].sort()` (alphabetical); non-today timeTracking leaves move young; remote path does NOT write lastFlush.

## 4. Full deletes

### HPD deleteProject — `{projectId, noteIds, allTaskIds}` **+ top-level payload key `projectDeleteWins: true`** (marker const 'projectDeleteWins'; read by conflict resolution so concurrent PU doesn't resurrect; emit it). e=PROJECT, o=DEL, d=projectId.
allTaskIds = project.taskIds + backlogTaskIds + their subTaskIds. Cascades to mirror: project entity removed; tasks removed (live); notes of project removed (noteIds payload); sections; menuTree.projectTree pruned; issueProvider.defaultProjectId→null; archive tasks of the project removed (archive handler does this on receivers — mirror in file blobs).

### GD/GDM deleteTag(s) — `{id}` / `{ids}`. Cascade (order): filter deleted ids from every task.tagIds; **orphan rule: task left with 0 tags AND no projectId AND no parentId is HARD-DELETED with its subtasks**; repeatCfg.tagIds filtered — cfg with no tags and no projectId REMOVED; `delete timeTracking.tag[tagId]`; issueProvider.defaultTagIds filtered; tag entity removed; menuTree.tagTree pruned; archives: strip tag from archived tasks + cleanup archive timeTracking.tag. Emit GD alone — cascade is derived on each device (no companion ops!).
NB: in our data model tasks always have projectId ⇒ orphan rule mostly moot, but implement.

### Repeat-cfg delete — **use HRC** `[Task Shared] deleteTaskRepeatCfg`, payload **`{taskRepeatCfgId}`** (not `id`!), e=TASK_REPEAT_CFG, o=DEL, d=cfgId. Effects: all tasks (live+archived) with repeatCfgId==id get repeatCfgId cleared; cfg removed. RD/RDM = entity-only removal, leaves dangling refs — don't use.

## 5. Repeat cfg edit/pause/instances

- **RU** — `{taskRepeatCfg: {id, changes}, isAskToUpdateAllTaskInstances?}` **+ optional sibling `clearedFields: string[]`**. To clear startTime/remindAt MUST list in clearedFields (JSON drops undefined). d=cfg id.
- **RUM** — `{ids, changes}` bulk, no clearedFields support.
- **RX upsert — NO meta, not persistent. Never emit.** Create=RA, update=RU.
- **RDI** — `{repeatCfgId, dateStr}`, o=UPD, d=cfgId. Append-only idempotent: deletedInstanceDates += dateStr. Suppresses future creation, does NOT remove existing instance task.
- isPaused: required field; pause via RU changes {isPaused: true}.

## 6. Reordering & conversion (naming corrections!)

- **TMU/TMD/TMT/TMB = SUB-TASK moves** `{id, parentId}`, o=MOV, d=id — write ONLY parent.subTaskIds. No-op if parent missing or id not in subTaskIds.
- **TMS** moveSubTask `{taskId, srcTaskId, targetTaskId, afterTaskId: string|null}` — src/target = old/new PARENT ids. Effects: old parent subTaskIds filter + time recalc; new parent subTaskIds anchor-insert + recalc; task.parentId/projectId updated. Guards: missing parents, self, circular.
- **WM/WMU/WMD/WMT/WMB = context list moves** (`[WorkContextMeta] Move Task ... in Today` — "Today" in name but works for any context): WM `{taskId, afterTaskId, workContextType: 'PROJECT'|'TAG', workContextId, src, target}`; WMU.. `{taskId, workContextId, doneTaskIds, workContextType}` — ⚠️ **doneTaskIds carries NOT-done ids**. e computed = TAG or PROJECT; d=workContextId; o=MOV. Write project.taskIds or tag.taskIds (never backlog). tag reducer THROWS on unknown workContextId for WM.
- **HMT** moveTaskInTodayTagList `{toTaskId, fromTaskId}`, ds **[toTaskId, fromTaskId]** (target first), o=MOV. Effect: tag['TODAY'].taskIds moveItemBeforeItem(from, to). **Silent no-op unless BOTH ids in TODAY list.**
- **Planner**: LU `{day, taskIds}` whole replace (acceptable — no LWW replace path for planner); LT `{task, prevDay, newDay, targetIndex, targetTaskId?, today}` o=MOV; LM `{targetDay, fromIndex, toIndex}` index-based (avoid); LB `{fromTask: TaskCopy, toTaskId}` — removes from every day, inserts before anchor in anchor's day + dueDay=target's dueDay; LP (implemented).
- **HCS** convertToSubTask `{taskId, targetParentId, afterTaskId: string|null}`, o=UPD. **Eligibility guard (replicate; silent no-op otherwise): both exist, not self, target has no parentId (2-level), task has NO parentId/subTaskIds/repeatCfgId/issueId/issueProviderId/issueType/dueWithTime/reminderId/remindAt.** Effects: remove from project.taskIds AND backlogTaskIds, TODAY order, planner days; new parent subTaskIds anchor-insert; task.parentId/projectId set, dueDay=undefined, modified=now; tagIds UNCHANGED; deadline fields NOT cleared; time recalc on parent.
- **HC** convertToMainTask `{task: Task, parentTagIds?, isPlanForToday?, afterTaskId?, isDone?, today?, doneOn?, modified?}`, o=UPD, d=task.id. **THROWS on receiver if parent unresolvable. Always pass today/doneOn/modified.** Effects: tagIds = own (TODAY-filtered) or inherit parent's filtered; old parent subTaskIds filter + both time recalcs; parentId=undefined; project.taskIds insert after old parent (backlog never); tags taskIds insert; TODAY if isPlanForToday; planner NOT touched.

### Section side-effects (section-shared.reducer.ts) — mirror, never emit
`Section = {id, contextId, contextType: 'PROJECT'|'TAG', title, isExpanded?, taskIds}`. **No `projectId` on a section, no `sectionId` on a task** — membership is ONLY `section.taskIds`. The same op that moves a task in a context list also runs the section meta-reducer on every device, so the CLI snapshot must apply it too:
- `handleTaskRemoval` (= strip the id + its subTaskIds from EVERY section, any context): HD/HDM, HX archive, **HCS demote**.
- `reorderTaskInContextSections`: **WMU/WMD/WMT/WMB** — in each section with the action's `contextType`/`contextId` that contains the task, apply the SAME closure (up/down step over every id missing from the payload's `doneTaskIds`, i.e. the context's not-done ids). WM (anchor) and HMT have NO section handler.
- `handleMoveToOtherProject` (HMP): strip from the OLD project's sections only.
- `HPD deleteProject`: remove sections with `contextType==='PROJECT' && contextId===projectId`, then strip `allTaskIds` from the survivors (TODAY sections hold them too).
- `HR restoreTask`: `removeTaskIdsFromProjectSections` with no project filter — a restored task comes back section-less.
- NOT mirrored: SP's diff-based TODAY-section prune (any id leaving `TODAY_TAG.taskIds` is stripped from TODAY sections), which is a post-pass over the whole reducer chain; `doctor` therefore does not cross-check TODAY sections against the TODAY ordering.

### Prefer move ops over PU/GU array rewrites
Entity-LWW loss on PU/GU re-broadcasts the whole entity (replace mode) — silently reverts concurrent adds. Move ops are ordering-only, self-healing, anchor-based degrade. Mapping: Today reorder→HMT; project/tag list→WM/WMU..; backlog→PM/PMU..; subtasks→TMU..; reparent→TMS; promote/demote→HC/HCS; planner-day reorder→LB; set whole planner day→LU ok.

## 7. Attachments — XA/XU/XD

Model: `{id: string|null, type: 'FILE'|'LINK'|'IMG'|'COMMAND'|'NOTE', title?, path?, icon?, originalImgPath?}` (Partial<DropPasteInput> base: path). Icons FILE=insert_drive_file, LINK=bookmark, IMG=image. Id = nanoid, CLI generates. Lives inline on task.attachments.
Actions all e=TASK, d=taskId, o=UPD (even delete):
- XA `{taskId, taskAttachment}` append
- XU `{taskId, taskAttachment: {id, changes}}` shallow-merge by id
- XD `{taskId, id}` filter by id
⚠️ Reducers THROW if task missing — verify existence. None bumps modified. Don't use HU changes.attachments (whole-array LWW clobber).

## 8. Deadline/reminder clearing

All e=TASK, o=UPD:
- HDL setDeadline `{taskId, deadlineDay?, deadlineWithTime?, deadlineRemindAt?, autoPlanToday?, autoPlanStartOfNextDayDiffMs?}` — mutual exclusion by reducer; **omitting deadlineRemindAt CLEARS it**; omit autoPlan* fields.
- HXD removeDeadline `{taskId}` — clears deadlineDay+deadlineWithTime+deadlineRemindAt. Doesn't touch due*.
- HCR clearDeadlineReminder `{taskId}` — clears only deadlineRemindAt.
- HRX dismissReminderOnly `{id}` (key `id`!) — clears only remindAt; keeps dueWithTime/dueDay/TODAY. Replay-safe on missing id.
- HSX unschedule `{id, isSkipToast?, isLeaveInToday?, today?}` — pass today when isLeaveInToday.
- HDT planDeadlineTasksForToday `{taskIds, today, startOfNextDayDiffMs}` bulk — avoid; use HPT.

## 9. shortSyntax — parse locally in Python, emit normal ops

HSS exists (atomic bundle of resolved changes) but buys nothing — the app itself parses locally then emits resolved payloads. TGS (new-tags dialog) NOT persistent — CLI creates tags with GA.

### Rules to port (features/tasks/short-syntax.ts)
- Trigger chars: `+`project `#`tag `@`due `!`deadline; time sep `/`.
- Time: `(?:\s|^)t?((?:\d+(?:\.\d+)?[mh]\s*)+)(?:\s*/((?:\s*\d+(?:\.\d+)?[mh])+)?)?(?=\s|$)` — with `/`: pre=timeSpent(today), post=estimate; without: pre=estimate. Clusters sum. `stringToMs`: `^(\d*\.?\d+)([smh]?)$`; bare number: fractional or ≤8 ⇒ hours, integer >8 ⇒ minutes; `h:mm` also.
- Project: `\+(?!\s)((?:(?!\s+(?:#|@|t?\d+[mh]\b)).)+)` — skip for issue tasks; char before `+` must be space/start; longest-prefix match on titles, case-insens; full title beats partial; single word may match squashed title (no spaces); exclude archived/hidden projects.
- Tags: `#[^(+|#|@|!)|\s]+` global — char before `#` must be space/start; ≥1 char; numeric-only rejected at index 0; case-insens match, missing → create (GA). Default mode append.
- Due: `@[^+#@!]+` global — dates via chrono (casual, forwardDate) — Python port: support today/tomorrow/weekday names (next occurrence)/YYYY-MM-DD/DD.MM(.YYYY)/"15:00"/bare hour ≤24 (today, roll to tomorrow if past); explicit time ⇒ dueWithTime, date-only ⇒ dueDay.
- Repeat (anchored at start of due text): `^(?:(daily|weekly|monthly|yearly|annually)|every\s+(days?|weeks?|months?|years?|weekdays?|workdays?|mon...|sun|\d{1,2}(?:st|nd|rd|th))|every\s+([1-9]\d{0,2})\s+(days?|...|weekday-units))(?=[\s.,;:!?]|$)` case-insens. Mapping: daily→DAILY quickSetting; weekly→WEEKLY_CURRENT_WEEKDAY; monthly→MONTHLY_CURRENT_DATE; yearly→YEARLY_CURRENT_DATE; every <weekday>→weekly + that weekday; every N(st|nd|rd|th)→MONTHLY_CURRENT_DATE + dayOfMonth (1-31); every weekday/workday→MONDAY_TO_FRIDAY; every N <unit>→CUSTOM interval repeatEvery=N (N=1 collapses to preset).
- Deadline `![^+#@!]+` — only if isEnableDeadline; char before `!` must be space/start.
- Stage order: time → repeat → due → deadline → project → tags → title = residual.
- Config: globalConfig.shortSyntax `{isEnableProject: true, isEnableDue: true, isEnableDeadline: false, isEnableTag: true}` — read from file at runtime.

## 10. Bulk update — emit N × HU, never HUM

- HUM `{tasks: Update<Task>[]}` (key `tasks`), ds=ids: bare updateMany — no modified bump, no tag/project reconciliation, NO doneOn backfill; **multi-entity ops are BLOCKED in conflict resolution** (UnsupportedMultiEntityConflictError → sync ERROR snack). Only TRD is decomposable. Sanctioned HUM use: archived-task-only updates.
- N×HU in one file batch (one syncVersion increment, N ops) = correct pattern; per-entity LWW; full handleUpdateTask semantics.

## 11. Payload key inconsistencies (hardcode per action)

HU={task}, HUM={tasks}, TR/HRX/HSX={id}, HDL/HXD/HCR/KT={taskId}, HRC={taskRepeatCfgId}, RD={id}, XD={taskId, id}.

## 12. Guards summary

- THROW on replay (guard CLI-side): HC (missing parent), XA/XU/XD (missing task), WM unknown tag ctx, KT malformed.
- SILENT no-op (verify preconditions): HMT (both in TODAY), HCS eligibility, PRB/PAB (isEnableBacklog), PAR, TM* membership, HR already-active.
- Pass explicit today/doneOn/modified where accepted (HC, HSX isLeaveInToday, LP local-today caveat).
