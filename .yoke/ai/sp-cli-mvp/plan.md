# План реализации sp-cli (полное покрытие: задачи + день + календарь)

Основания: `research-sync-engine.md`, `research-data-model.md`. Язык кода — английский, Python 3.11+, зависимости: stdlib + requests.

## Архитектура

```
sp_cli/
  __init__.py        # version
  __main__.py        # python -m sp_cli
  config.py          # ~/.config/sp-cli/config.toml: url, user, password | password_file, client_id (генерится B_xxxxxx при init)
  ids.py             # nanoid(21), uuid7, client id
  webdav.py          # GET/PUT + ETag (If-Match), strip/add pf_2__, backups (~/.local/share/sp-cli/backups, ротация 10)
  model.py           # фабрики Task/Project/Tag/TaskRepeatCfg с дефолтами SP; TaskWithSubTasks снапшоты
  ops.py             # build_op(a,o,e,d,ds,payload): uuid7, s=4, t, v=merge(fileClock,{cli:n}); финализация файла (syncVersion+1, sv, trim 2000, oldestOpSyncVersion, vectorClock merge, lastModified, clientId)
  store.py           # SyncStore: load→mutate→commit; retry на 412 (re-read + re-apply, max 3); все мутации через него
  mutations.py       # чистые функции state+ops для каждой операции
  queries.py         # read-слой: фильтры задач, agenda, worklog
  render.py          # табличный/JSON вывод
  cli.py             # argparse subcommands
sp                   # обёртка-скрипт
tests/
  test_mutations.py  # unit, in-memory state
  test_ops.py        # unit: формат op, vector clock, финализация
  test_integration.py# против локального тестового WebDAV (копия live-файла)
```

## Операции (мутация state + op)

| Команда | Op(s) | State |
|---|---|---|
| `add` | HA CRT `{task, workContextId, workContextType, isAddToBacklog:false, isAddToBottom:true}` | task.ids+entities, project.taskIds append; если due=today → TODAY order |
| `edit` | HU UPD `{task:{id,changes}}` | patch entity; tagIds изменения — синхронизировать tag.taskIds |
| `complete/reopen` | HU UPD (isDone,doneOn) | + doneOn/modified |
| `delete` | HD DEL `{task: TaskWithSubTasks}` (bulk: HDM `{taskIds}`) | каскад: subTasks, project.taskIds/backlog, tag.taskIds, planner.days |
| `subtask` | TA CRT `{task(parentId), parentId}` | parent.subTaskIds append; НЕ в project.taskIds |
| `move` | HMP UPD `{task: TaskWithSubTasks, targetProjectId}` | projectId (+subtasks), old/new project.taskIds |
| `tag add/rm` | HGT `{tagId,taskId}` (ds=[taskId,tagId]) / HU changes.tagIds | task.tagIds + tag.taskIds |
| `reorder` | PU UPD `{project:{id,changes:{taskIds}}}` | project.taskIds |
| `today add` | HPT UPD `{taskIds, today}` ds=taskIds | dueDay=today, clear dueWithTime/remindAt, TODAY order prepend, purge из planner.days |
| `today rm` | HSX UPD `{id, today}` | clear dueDay/dueWithTime/remindAt, TODAY order remove |
| `plan <day>` | LP UPD `{task, day, isAddToTop:false}` e=PLANNER d=taskId | dueDay=day; today→TODAY order, future→planner.days[day] |
| `schedule` | HS UPD `{task, dueWithTime, remindAt?, isMoveToBacklog:false}` | dueWithTime, dueDay=None, remindAt; today→TODAY order |
| `unschedule` | HSX | как today rm |
| `deadline` | HDL UPD `{taskId, deadlineDay?|deadlineWithTime?, deadlineRemindAt?}` | поля deadline* |
| `track` | KT UPD `{taskId,date,duration}` + entityChanges mirror | timeSpentOnDay[date]+=, timeSpent recompute |
| `repeat add` | RA CRT `{taskId, taskRepeatCfg, startTime?, remindAt?}` e=TASK_REPEAT_CFG | cfg entity + task.repeatCfgId |
| `project add/edit` | PA CRT `{project}` / PU UPD | registry |
| `tag new/edit` | GA CRT `{tag}` / GU UPD | registry |
| `archive` | HX UPD `{tasks: TaskWithSubTasks[]}` | done-таски из state.task → top-level archiveYoung.task; чистка project/tag/planner |

Read-only: `list` (фильтры), `show`, `today`, `agenda`, `plan show`, `projects`, `tags`, `worklog`, `pull`, `doctor` (инварианты консистентности).

## Инварианты записи (из research-sync-engine)

- op: `{id: uuid7, a, o, e, d, ds?, p:{actionPayload, entityChanges:[]}, c, s:4, t: now_ms, v: merge(fileClock, {cli: n})}` — счётчик кумулятивный по операциям батча.
- Файл: syncVersion+1 (один батч = один инкремент), sv на новых ops, trim −2000, oldestOpSyncVersion=recentOps[0].sv, vectorClock=merge всех op.v, lastModified=now, clientId=cli, version:2, schemaVersion:4.
- PUT с If-Match: 412 → re-read → re-apply мутаций → retry (max 3).
- Бэкап перед PUT. Никогда: пустой recentOps, регресс syncVersion, удаление чужих ключей vectorClock.
- state всегда отражает эффект всех ops (контракт снапшота).

## Тесты

1. Unit: мутации + формат ops + финализация файла (без сети).
2. Integration: против тестового rclone WebDAV (:8096, копия live) — полный жизненный цикл.
3. E2E: настоящий SP web app v18 (docker, :8097) через Chrome — начальный синк, затем изменения CLI, повторный синк, проверка UI. Это доказывает «приложение подхватывает».
4. Финал: смоук на живом сервере (:8091) с бэкапом.

## DoD

Все команды работают; unit+integration зелёные; E2E: задача, созданная/изменённая CLI, видна в настоящем SP после синка без конфликт-диалога; живой файл валиден.
