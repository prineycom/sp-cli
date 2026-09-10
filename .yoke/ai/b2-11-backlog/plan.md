# План b2-11-backlog — бэклог проекта

Research: `.yoke/ai/batch2-research/research-task-methods.md` §2. Ops PRB/PBR/PBA (e=TASK o=MOV, PBA e=PROJECT o=UPD). Инвариант: задача ровно в одном из taskIds/backlogTaskIds.

## Команды

- `sp backlog --project X [--json]` — список backlogTaskIds задач.
- `sp backlog add <id...>` — на каждую: PRB MOV `{taskId, afterTaskId: null, workContextId: <projectId>}`. **Гард: project.isEnableBacklog, иначе ошибка CLI** ("включи: sp project edit X --enable-backlog"). Только top-level задачи (сабтаск — ошибка). State: taskIds filter, backlogTaskIds prepend (afterTaskId null = prepend).
- `sp backlog rm <id...>` — PBR MOV `{taskId, afterTaskId: null, workContextId, src: 'BACKLOG', target: 'UNDONE'}`. State: backlog filter, taskIds prepend.
- `sp backlog clear --project X` — PBA UPD `{projectId}` d=projectId. State: taskIds += backlogTaskIds, backlog=[].
- `sp project edit <id> --enable-backlog|--disable-backlog` — PU changes {isEnableBacklog}. **При disable также эмитить PBA + state-слив бэклога** (SP-эффект делает это автоматически у себя; для консистентности файла сливаем сами и шлём PBA после PU).
- `sp add --backlog --project X ...` — HA с isAddToBacklog: true (payload) + state в backlogTaskIds (гард isEnableBacklog).

## Тесты
- add/rm: op shapes, списки, гард isEnableBacklog, отказ для сабтасков.
- clear/PBA; disable-backlog → PU+PBA пара и слитый state.
- sp add --backlog: payload isAddToBacklog=true, задача в backlogTaskIds, не в taskIds.

## DoD
Полный цикл бэклога; unit зелёные.
