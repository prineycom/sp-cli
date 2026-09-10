# SuperProductivity write contracts — Notes, Boards, SimpleCounters, Metrics, IssueProviders

Source: /tmp/sp-src (master, Sept 2026). Complements `.yoke/ai/sp-cli-mvp/research-sync-engine.md` / `research-data-model.md`; op-envelope conventions unchanged.

## 0. Cross-cutting facts

- `state` registry keys (`op-log/model/model-config.ts:73-168`): `note`, `boards`, `simpleCounter`, `metric`, `issueProvider` are all top-level keys of `state` and all op-log synced. `isMainFileModel` only affects USE_REMOTE reset behavior.
- `repair: fixEntityStateConsistency` applies to note/simpleCounter/metric/issueProvider (`ids`↔`entities` must be consistent). `boards` has NO repair fn (array-shaped).
- Entity types valid: `NOTE`, `BOARD`, `SIMPLE_COUNTER`, `METRIC`, `ISSUE_PROVIDER`.
- Storage: NOTE/SIMPLE_COUNTER/METRIC/ISSUE_PROVIDER = adapter (ids/entities); **BOARD = array**, `state.boards.boardCfgs`.
- Payload validation lenient: CREATE requires `payload[entityKey].id` non-empty string; UPDATE/DELETE warn only.
- `entityChanges` stays `[]` for all these areas.
- Keep `payload.…id === d`.

## 1. Notes

### Model (`features/note/note.model.ts:3-18`)
```ts
interface Note { id; projectId: string|null; isPinnedToToday: boolean; content: string;
  imgUrl?; isLock?; backgroundColor?; created: number; modified: number }
interface NoteState extends EntityState<Note> { ids: string[]; todayOrder: string[] }
```
Construction defaults (`note.service.ts:40-61`): `{id: nanoid(), projectId: <ctx>|null, isPinnedToToday: <ctx is TAG>, content:'', created: now, modified: now}`.

### State: `state.note = {ids, entities, todayOrder}`; second list: `project.entities[pid].noteIds`.

### Actions (all persistent)
| action | a | o | e | d/ds | actionPayload |
|---|---|---|---|---|---|
| addNote | NA | CRT | NOTE | d=note.id | `{note: <full Note>, isPreventFocus?: bool}` |
| updateNote | NU | UPD | NOTE | d=note.id | `{note: {id, changes: Partial<Note>}}` |
| deleteNote | ND | DEL | NOTE | d=id | `{id, projectId: string|null, isPinnedToToday: bool}` |
| updateNoteOrder | NO | MOV | NOTE | ds=ids | `{ids, activeContextType: 'PROJECT'|'TAG', activeContextId}` |
| moveNoteToOtherProject | NM | UPD | NOTE | d=note.id | `{note: <full Note with OLD projectId>, targetProjectId}` |

### Reducer effects to mirror
- addNote: entities set; **ids PREPEND**; if isPinnedToToday → todayOrder PREPEND. Cross: if note.projectId → project.noteIds PREPEND.
- updateNote: adapter update; only if `'isPinnedToToday' in changes` → true ⇒ prepend to todayOrder, false ⇒ filter out.
- deleteNote: removeOne + filter todayOrder; cross: filter project(projectId).noteIds (payload carries projectId for this).
- updateNoteOrder: `activeContextType==='PROJECT'` → project(activeContextId).noteIds = ids; else → note.todayOrder = ids. Exactly one list per op.
- moveNoteToOtherProject: note.projectId = target; remove from source project's noteIds, **append** to target's. Same src==target is no-op.

### Pitfalls
- Repair prunes dangling ids from project.noteIds/todayOrder; orphan note (projectId dead) → projectId=null + appended to todayOrder; note.ids entry without entity → **throws**. Never leave dangling ids.
- `modified` cosmetic.

## 2. Boards

### Model (`features/boards/boards.model.ts`)
```ts
enum TaskDoneState { All=1, Done=2, UnDone=3 }
enum ScheduledState { All=1, Scheduled=2, NotScheduled=3 }
enum TaskTypeFilter { All=1, NoBacklog=2, OnlyBacklog=3 }  // backlogState
BoardPanelCfg = { id, title, taskIds: string[], includedTagIds: string[], excludedTagIds: string[],
  includedTagsMatch?: 'all'|'any', excludedTagsMatch?, projectIds?: string[] /* [''] = all */,
  taskDoneState, scheduledState, isParentTasksOnly: bool, sortBy?: 'dueDate'|'created'|'title'|'timeEstimate',
  sortDir?: 'asc'|'desc', backlogState? }
BoardCfg = { id, title, cols: number, panels: BoardPanelCfg[] }
```
DEFAULT_PANEL_CFG: `{id:'', title:'', taskIds:[], taskDoneState:1, excludedTagIds:[], includedTagIds:[], scheduledState:1, backlogState:1, isParentTasksOnly:false, projectIds:['']}`. Seeded boards: EISENHOWER_MATRIX, KANBAN_DEFAULT (titles are i18n keys).

### State: `state.boards = {boardCfgs: BoardCfg[]}` — plain array.

### Actions
| action | a | o | e | d/ds | payload | note |
|---|---|---|---|---|---|---|
| addBoard | BA | CRT | BOARD | d=board.id | `{board: BoardCfg}` | appends |
| updateBoard | BU | UPD | BOARD | d=id | `{id, updates: Partial<BoardCfg>}` | **the only way to edit panels** (whole panels array) |
| removeBoard | BD | DEL | BOARD | d=id | `{id}` | |
| updatePanelCfg | BP | — | — | — | — | **DEAD reducer — NEVER emit** |
| updatePanelCfgTaskIds | BT | UPD | BOARD | d=panelId | `{panelId, taskIds}` | first panel with that id across all boards |
| sortBoards | BS | MOV | BOARD | ds=ids | `{ids}` | unlisted boards keep tail position |

### Effects/pitfalls
- updateBoard: shallow merge on matching id; panels run through sanitizePanelCfg. Unknown id = silent no-op.
- Emit already-sanitized panels: projectIds array; array containing `''` canonicalized to exactly `['']` (mixing '' with real ids DROPS real ids); no `projectId`/`sortByDue` legacy keys; sortBy only from the 4 valid values; no null sortDir/matches.
- Panel ids must be globally unique (dedup pass replaces repeats with fresh nanoid on load).
- **panel.taskIds is manual ordering only** — membership is derived from filters (tags/project/done/scheduled). Adding a task id to taskIds does NOT put it in the panel. If sortBy set, taskIds ignored. Stale ids harmless.
- TODAY tag id never into task.tagIds via board logic.

## 3. Simple counters

### Model (`features/simple-counter/simple-counter.model.ts`)
```ts
enum SimpleCounterType { StopWatch='StopWatch', ClickCounter='ClickCounter', RepeatedCountdownReminder='RepeatedCountdownReminder' }
SimpleCounter = { id, title, isEnabled: bool, isHideButton?, icon: string|null, type,
  isTrackStreaks?, streakMinValue?, streakMode?: 'specific-days'|'weekly-frequency',
  streakWeekDays?: {[0-6]: bool}, streakWeeklyFrequency?, countdownDuration?,  // ms, countdown only
  countOnDay: {[YYYY-MM-DD]: number},  // clicks, or ms for StopWatch
  isOn: bool }  // device-local, force false
```
EMPTY_SIMPLE_COUNTER: `{id:'', title:'', isEnabled:false, icon:null, type:ClickCounter, countOnDay:{}, isOn:false, isTrackStreaks:true, streakMinValue:1, streakMode:'specific-days', streakWeekDays:{1:1,2:1,3:1,4:1,5:1,6:0,0:0 as bools}}`. Fresh installs seed STANDING_DESK_ID, COFFEE_COUNTER, STRETCHING_COUNTER. Streaks fully derived from countOnDay — store nothing.

### State: `state.simpleCounter = {ids, entities}`; ids order = UI order.

### Actions (persistent/syncable)
| action | a | o | d/ds | payload |
|---|---|---|---|---|
| addSimpleCounter | SA | CRT | d=sc.id | `{simpleCounter: <full>}` |
| updateSimpleCounter | SU | UPD | d=id | `{simpleCounter: {id, changes}}` |
| deleteSimpleCounter | SD | DEL | d=id | `{id}` |
| deleteSimpleCounters | SDM | DEL | ds=ids | `{ids}` |
| updateAllSimpleCounters | SUA | UPD | ds | `{items: SimpleCounter[]}` — **destructive: unlisted counters deleted** |
| setSimpleCounterCounterToday | ST | UPD | d=id | `{id, newVal, today: 'YYYY-MM-DD'}` — absolute, clamped ≥0 |
| setSimpleCounterCounterForDate | SFD | UPD | d=id | `{id, date, newVal}` |
| updateSimpleCounterOrder | SM | MOV | ds=ids | `{ids}` |
| syncSimpleCounterTime | SC | UPD | d=id | `{id, date, duration}` — **additive delta**; local reducer isRemote-gated (CLI must apply += to own state copy) |

**NON-persistent (never emit):** SUS upsert, SI/SX increase/decrease, SG/SF/SN/SO toggles — `isOn` is device-local by design. CLI increments via ST/SFD absolute; adds StopWatch time via SC delta.

### Pitfalls
- Write `isOn: false` always (loadAllData forces it).
- countOnDay never negative (SC unclamped — CLI must not emit negative).
- Missing entity for ST/SFD ⇒ no-op.

## 4. Metrics

### Model (`features/metric/metric.model.ts:5-36`)
```ts
Metric = { id: 'YYYY-MM-DD',  // id IS the day
  focusSessions?: number[] /* ms */, notes?: string|null, remindTomorrow?: bool,
  reflections?: {text, created}[], impactOfWork?: 1-4|null, energyCheckin?: 1-3|null,
  totalWorkMinutes?: number|null, completedTasks?: number|null, plannedTasks?: number|null }
```
**No mood/productivity/obstruction/improvement — deleted from codebase. Never write state.improvement/state.obstruction.**
DEFAULT_METRIC_FOR_DAY = `{focusSessions: [], remindTomorrow: false, reflections: []}`.

### State: `state.metric = {ids: [day...], entities: {[day]: Metric}}`.

### Actions (all persistent)
| action | a | o | d | payload |
|---|---|---|---|---|
| addMetric | EA | CRT | metric.id | `{metric: <full>}` — no-op if exists |
| updateMetric | EU | UPD | metric.id | `{metric: {id, changes}}` — **self-creating** (upserts `{id, ...DEFAULT, ...changes}` when missing) |
| upsertMetric | EX | UPD | metric.id | `{metric: <full>}` — **full replace** (partial wipes fields) |
| deleteMetric | ED | DEL | id | `{id}` |
| logFocusSession | EL | UPD | **day** | `{day, duration}` — additive append; duration<=0 no-op |

Prefer EU (self-creating patch) and EL (additive). Leaf slice, no cross-effects. Same-day edits from 2 clients LWW on `t`.

## 5. Issue providers

### Model (`features/issue/issue.model.ts`)
Keys: builtin `'JIRA'|'GITLAB'|'CALDAV'|'ICAL'|'OPEN_PROJECT'|'REDMINE'|'NEXTCLOUD_DECK'|'PLAINSPACE'`; migrated-to-plugin `'GITHUB'|'CLICKUP'|'GITEA'|'LINEAR'|'TRELLO'|'AZURE_DEVOPS'`; `plugin:*`.
```ts
IssueProviderBase = { id, isEnabled, issueProviderKey, defaultProjectId?: string|null|false,
  pinnedSearch?, isAutoPoll?, isAutoAddToBacklog?, isIntegratedAddTaskBar?,
  pollingMode?: 'whenProjectOpen'|'always', defaultTagIds?: string[], defaultNote?: string|null }
```
Cfg fields are FLATTENED onto the provider object (discriminated union on issueProviderKey).

ICAL cfg (`providers/calendar/calendar.model.ts`): `{isEnabled, icalUrl, isAutoImportForCurrentDay, isReferenceCalendar?, color?, icon?, checkUpdatesEvery: ms, showBannerBeforeThreshold: ms|null, isDisabledForWebApp?, filterIncludeRegex?: string|null, filterExcludeRegex?: string|null}`. DEFAULT_CALENDAR_CFG: `{isEnabled:false, icalUrl:'', isAutoImportForCurrentDay:false, isReferenceCalendar:false, checkUpdatesEvery:7200000, showBannerBeforeThreshold:7200000, isDisabledForWebApp:false, filterIncludeRegex:null, filterExcludeRegex:null}`.

CALDAV cfg: `{isEnabled, caldavUrl: string|null, resourceName: string|null, username: string|null, password: string|null, categoryFilter: string|null, isAddSubTasks?, pollIntervalMinutes?, twoWaySync?: {isDone?, title?, notes?}}`. DEFAULT: nulls, twoWaySync `{isDone:'pullOnly', title:'pullOnly', notes:'off'}`. **Plaintext creds in sync file — explicit user opt-in only.**

ISSUE_PROVIDER_DEFAULT_COMMON_CFG: `{isAutoPoll:true, isAutoAddToBacklog:false, isIntegratedAddTaskBar:false, defaultProjectId:null, pinnedSearch:null, pollingMode:'whenProjectOpen', defaultTagIds:[], defaultNote:null}`.
New provider = `{...COMMON, ...DEFAULT_CFG[key], id: nanoid(), isEnabled: true, issueProviderKey: key}`.

### State: `state.issueProvider = {ids, entities}`; ids order drives provider ordering.

### Actions
| action | a | o | e | d/ds | payload |
|---|---|---|---|---|---|
| addIssueProvider | IA | CRT | ISSUE_PROVIDER | d=ip.id | `{issueProvider: <full>}` |
| updateIssueProvider | IU | UPD | ISSUE_PROVIDER | d=id | `{issueProvider: {id, changes}}` |
| sortIssueProvidersFirst | IS | MOV | ISSUE_PROVIDER | ds=ids | `{ids}` — listed first, rest keep order |
| deleteIssueProvider (TaskShared) | HID | DEL | ISSUE_PROVIDER | d=issueProviderId | `{issueProviderId, taskIdsToUnlink: string[]}` |
| deleteIssueProviders | HIM | DEL | ISSUE_PROVIDER | ds=ids | `{ids, taskIdsToUnlink}` |

Non-persistent (never emit): upsert*, add*s, update*s, clear*, load*.

### Delete cascade to mirror
taskIdsToUnlink = every task (live AND archived) with `issueProviderId === id`. On each, set undefined/remove: `issueId, issueProviderId, issueType, issueWasUpdated, issueLastUpdated, issueAttachmentNr, issueTimeTracked, issuePoints`. Mirror into live state AND archive blobs. Also deleteProject sets providers' defaultProjectId=null (already handled if we implement HPD).

### Calendar → tasks
Auto-import only for providers `isEnabled && icalUrl valid`. Deterministic task id `cal_<providerId>_<eventId>` — app creates tasks itself; **CLI must NOT synthesize cal_* tasks**. Don't touch `dismissedCalendarAutoImportEventIdsByProvider` (task state).

### Validation
Built-in provider must be COMPLETE for its key (ICAL missing icalUrl/checkUpdatesEvery = real typia failure). Always emit full cfg.

## 6. Emit-allowed summary

| area | emit | never |
|---|---|---|
| Notes | NA NU ND NO NM | — |
| Boards | BA BU BD BT BS | BP (dead) |
| Counters | SA SU SD SDM ST SFD SM SC (SUA destructive) | SUS SI SX SG SO SF SN |
| Metrics | EA EU EX ED EL | — |
| IssueProviders | IA IU IS, HID/HIM | upsert*/bulk non-persistent |
