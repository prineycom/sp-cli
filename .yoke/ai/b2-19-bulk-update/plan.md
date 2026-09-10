# План b2-19-bulk-update — массовые правки

Research: `.yoke/ai/batch2-research/research-task-methods.md` §10: **N×HU в одном батче, HUM не эмитить** (мульти-entity ops блокируют конфликт-резолюшн; нет doneOn-бэкфилла и реконсиляции).

## Команды

- `sp edit <id...> [--flags]` — расширить до множества id: один батч (1 инкремент syncVersion), по HU на задачу; --title при >1 id — ошибка (бессмысленно).
- `sp bulk <id...|--project X|--tag Y|--overdue|--search S> [--due D|--clear-due] [--tag-add T...] [--tag-rm T...] [--est E] [--move-project P] [--complete|--reopen]` — селектор задач (union явных id и фильтров, как в list) + применение: HU/HGT/HMP на каждую в одном батче. `--dry-run` — показать какие задачи затронет, без записи. Подтверждение при >10 задач без --yes.
- Существующие `sp complete/reopen/delete <id...>` уже bulk — унифицировать через общий хелпер батча.

## Тесты
- Несколько HU в одном батче: syncVersion +1, счётчик клока кумулятивный, порядок ops.
- bulk селекторы (project+overdue), dry-run без PUT, tag-add через HGT idempotent.
- Проверка что HUM нигде не эмитится.

## DoD
Массовые правки одним синком; unit зелёные.
