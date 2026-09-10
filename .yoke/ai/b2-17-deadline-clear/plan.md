# План b2-17-deadline-clear — снятие дедлайна и напоминаний

Research: `.yoke/ai/batch2-research/research-task-methods.md` §8.

## Команды

- `sp deadline <id> --clear` — HXD UPD `{taskId}`. State: убрать deadlineDay/deadlineWithTime/deadlineRemindAt. due* не трогать.
- `sp deadline <id> --clear-reminder` — HCR UPD `{taskId}`. State: только deadlineRemindAt.
- `sp dismiss <id>` — HRX UPD `{id}` (ключ `id`!). State: только remindAt=None; dueWithTime/dueDay/TODAY не трогать.
- Ревизия существующего `sp deadline --day/--at`: HDL — **отсутствие deadlineRemindAt в payload ОЧИЩАЕТ его** — при правке дедлайна без --remind сохранять текущий deadlineRemindAt явно в payload; autoPlan* не слать. Добавить `--remind <offset>` для дедлайна с временем (deadlineRemindAt = ts - offset).
- `sp schedule <id> --remind-only <offset>`? Нет — вне скоупа.

## Тесты
- HXD/HCR/HRX: payload keys (taskId vs id), точечные очистки, соседние поля нетронуты.
- HDL-ревизия: existing deadlineRemindAt сохраняется при правке дня; --remind ставит.

## DoD
Очистки работают точечно; unit зелёные.
