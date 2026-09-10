# План b2-12-archive-restore — восстановление из архива

Research: `.yoke/ai/batch2-research/research-task-methods.md` §3. Op HR (e=TASK, o=UPD, d=task.id). AF/AC/AR — НЕ эмитим (задокументировать в help/README: обслуживание архива делает само приложение).

## Команды

- `sp archived [--search substr] [--json]` — задачи из top-level archiveYoung + archiveOld (пометка young/old, doneOn, project).
- `sp restore <id> [--today]` — восстановить задачу (+все её сабтаски) из архива.
  - Payload: `{task: <из архива, с subTaskIds заполненным>, subTasks: [<сабтаски из архива>], restoreToToday?: {today, startOfNextDayDiffMs: 0}}`.
  - **Пре-материализация** (payload = после трансформации креатора): при --today: task.dueDay=today, remindAt отсутствует, dueWithTime убрать если другой день; у всех subTasks убрать dueDay/dueWithTime/remindAt.
  - State: удалить `[task.id, ...task.subTaskIds]` из ОБОИХ архивных блобов (young затем old); добавить в state.task (task: isDone=false, doneOn убран, unknown projectId → INBOX_PROJECT, strip TODAY из tagIds, dangling repeatCfgId очистить; сабтаски: parentId/projectId проставить); root в project.taskIds unique-append (НЕ backlog); каждому тегу tag.taskIds unique-append; при --today — эффекты plan_today (TODAY order prepend, planner purge).
  - Идемпотентность: если task.id уже в state.task — ошибка "already active".
- id-резолвинг: префикс по архивным задачам (отдельный resolve; конфликт с live-id — уточнить).

## Тесты
- archived list из обоих блобов.
- restore: op payload (subTasks заполнены, subTaskIds на task), архив вычищен (young и old), state добавлен нормализованным, project/tag связи; --today материализация; отказ на уже-живую.
- doctor чист после restore.

## DoD
Задача возвращается из архива с сабтасками; unit зелёные.
