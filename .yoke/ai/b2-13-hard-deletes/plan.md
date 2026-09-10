# План b2-13-hard-deletes — полное удаление проекта/тега/repeat-конфига

Research: `.yoke/ai/batch2-research/research-task-methods.md` §4.

## Команды

- `sp project rm <id|title> [--yes]` — HPD DEL e=PROJECT d=projectId. Payload: `{projectId, noteIds: <project.noteIds>, allTaskIds: <taskIds + backlogTaskIds + все их subTaskIds>, projectDeleteWins: true}` (**маркер обязателен** — верхнеуровневый ключ payload; без него конкурентный PU воскрешает проект). Гард: INBOX_PROJECT удалять нельзя. Подтверждение с числом задач/заметок.
  State-каскад: project entity; все задачи из allTaskIds (live); заметки noteIds (+ из note.todayOrder); секции проекта (state.section если есть); menuTree.projectTree prune (узлы 'p' с этим id, рекурсивно в папках); issueProvider.defaultProjectId==id → null; из архивных блобов (young/old) удалить задачи с projectId==id; tag.taskIds почистить от удалённых id; planner.days почистить; TODAY order почистить.
- `sp tag rm <id|title> [--yes]` — GD DEL `{id}`. Гард: системные TODAY/EM_URGENT/EM_IMPORTANT/KANBAN_IN_PROGRESS — отказ. **Один op, без компаньонов** (каскад дериватный). State-каскад в точном порядке SP: (1) убрать тег из tagIds всех задач; (2) **задачи с 0 тегов И без projectId И без parentId — hard-delete с сабтасками** (в нашей модели projectId всегда есть — но реализовать); (3) repeatCfg.tagIds filter, cfg без тегов и projectId — удалить; (4) delete timeTracking.tag[id]; (5) issueProvider.defaultTagIds filter; (6) tag entity; (7) menuTree.tagTree prune; (8) в архивных блобах: strip тега из задач + timeTracking.tag.
- `sp repeat rm <cfg-id> [--yes]` — **HRC** DEL e=TASK_REPEAT_CFG d=cfgId, payload **`{taskRepeatCfgId}`**. State: у всех задач (live + оба архива) с repeatCfgId==id убрать поле; cfg удалить. (RD не использовать — оставляет висячие ссылки.)

Предупреждение в подтверждении project rm: задачи удаляются БЕЗ архивации.

## Тесты
- project rm: payload с projectDeleteWins:true и полным allTaskIds (с сабтасками); каскад по всем 9 точкам; INBOX отказ.
- tag rm: системные отказ; каскад; orphan-правило (синтетическая задача без projectId).
- repeat rm: HRC payload key; refs вычищены live+архив.
- doctor чист после каждого.

## DoD
Три вида полного удаления; unit зелёные.
