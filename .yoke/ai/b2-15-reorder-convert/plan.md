# План b2-15-reorder-convert — переупорядочивание и конвертация

Research: `.yoke/ai/batch2-research/research-task-methods.md` §6 (внимание к коррекции имён: TM* = сабтаски, WM* = списки контекста). Предпочитать move-ops вместо PU/GU перезаписей.

## Команды

- `sp today move <id> --before <other> | --up|--down|--top|--bottom`:
  - --before: HMT MOV `{toTaskId: <other>, fromTaskId: <id>}` **ds=[to, from]** (target первым!). Гард CLI: оба в TODAY-order (иначе no-op на девайсах — ошибка CLI). State: moveItemBeforeItem в tag.TODAY.taskIds.
  - --up/...: WMU/WMD/WMT/WMB MOV e=TAG d='TODAY' `{taskId, workContextId: 'TODAY', doneTaskIds: <массив НЕ-done id из TODAY-order>, workContextType: 'TAG'}` ⚠️ doneTaskIds = NOT-done (инвертированное имя). State: сдвиг в TODAY order.
- `sp move-in-project <id> --up|--down|--top|--bottom|--after <other>` — WM*/WM e=PROJECT d=projectId `{taskId, workContextId: projectId, doneTaskIds: <не-done из project.taskIds>, workContextType: 'PROJECT'}`; --after: WM `{taskId, afterTaskId, workContextType, workContextId, src: 'UNDONE', target: 'UNDONE'}`. State: project.taskIds.
- `sp subtask move <id> --up|--down|--top|--bottom` — TMU/TMD/TMT/TMB MOV `{id, parentId}` d=id. State: parent.subTaskIds сдвиг. Гард: parentId есть, id в subTaskIds.
- `sp subtask reparent <id> --parent <new-parent> [--after <sibling>]` — TMS MOV `{taskId, srcTaskId: <старый parent>, targetTaskId: <новый>, afterTaskId: <sibling|null>}`. Гарды: не сам себе, у target нет parentId, нет цикла. State: старый parent.subTaskIds filter + пересчёт времён обоих родителей (timeSpent/timeEstimate = сумма сабтасков), новый — anchor-insert; task.parentId/projectId.
- `sp demote <id> --parent <target> [--after <sib>]` — HCS UPD `{taskId, targetParentId, afterTaskId: null|<sib>}`. **Гард-репликация eligibility** (иначе silent no-op): оба есть, не сам, у target нет parentId, у задачи нет parentId/subTaskIds/repeatCfgId/issue*/dueWithTime/remindAt — иначе ошибка CLI с причиной. State: убрать из project.taskIds И backlogTaskIds, TODAY order, planner days; parent.subTaskIds anchor-insert; task: parentId, projectId=parent's, dueDay=None, modified=now; пересчёт времени родителя. tagIds НЕ трогать.
- `sp promote <id> [--today]` — HC UPD `{task: <полный снапшот ДО изменения>, parentTagIds: <теги родителя>, isPlanForToday: bool, afterTaskId: <id родителя>, isDone: <task.isDone>, today: <YYYY-MM-DD>, doneOn: <task.doneOn|absent>, modified: <now>}` — **все опциональные поля передавать явно** (детерминизм реплея; отсутствие родителя на приёмнике = throw, наш state гарантирует наличие). State: наследование тегов (свои TODAY-filtered или родительские), старый parent.subTaskIds filter + пересчёт, task.parentId=None + modified, вставка в project.taskIds после родителя, tag.taskIds вставка, TODAY при isPlanForToday; planner НЕ трогать.
- `sp plan move <id> --before <other>` — LB MOV `{fromTask: <снапшот задачи>, toTaskId}` d=fromTask.id. State: убрать id из всех planner.days, вставить перед anchor в его дне; task.dueDay = dueDay задачи-якоря, dueWithTime=None; TODAY-переходы.

Существующий `sp reorder` (PU) оставить, но пометить в help как "полная перезапись порядка (при конфликте LWW может откатить чужие правки); для точечных сдвигов — move-команды".

## Тесты
Для каждого op: shape (a/o/e/d/ds точные, включая ds-порядок HMT и инверсию doneTaskIds), state-эффекты, гарды (eligibility HCS — все причины; TMS цикл; HMT оба в TODAY). Пересчёт родительских времён при reparent/promote/demote.

## DoD
Все сдвиги и конвертации; unit зелёные.
