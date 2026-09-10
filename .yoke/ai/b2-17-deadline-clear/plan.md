# План b2-17-deadline-clear — снятие дедлайна и напоминаний

Research: `.yoke/ai/batch2-research/research-task-methods.md` §8.

## Команды

- `sp deadline <id> --clear` — HXD UPD `{taskId}`. State: убрать deadlineDay/deadlineWithTime/deadlineRemindAt. due* не трогать.
- `sp deadline <id> --clear-reminder` — HCR UPD `{taskId}`. State: только deadlineRemindAt.
- `sp dismiss <id>` — HRX UPD `{id}` (ключ `id`!). State: только remindAt=None; dueWithTime/dueDay/TODAY не трогать.
- Ревизия существующего `sp deadline --day/--at`: HDL — **отсутствие deadlineRemindAt в payload ОЧИЩАЕТ его**. При правке дедлайна с временем (--at) без --remind переносится **смещение**: new_remind = new_ts - (old_ts - old_remind); перенос отбрасывается, если старый дедлайн был днём, напоминания не было, или пересчёт попал в прошлое. Дедлайн-день (--day) напоминания не несёт (ветка day-only в диалоге SP) — ключ в payload не шлём, в state чистим. autoPlan* не слать. `--remind <offset>` для дедлайна с временем (deadlineRemindAt = ts - offset), `--remind none` — снять напоминание явно.
- `sp schedule <id> --remind-only <offset>`? Нет — вне скоупа.

## Тесты
- HXD/HCR/HRX: payload keys (taskId vs id), точечные очистки, соседние поля нетронуты.
- HDL-ревизия: --at пересчитывает смещение напоминания; --day его снимает; --remind ставит, --remind none снимает; deadlineRemindAt отсутствует в payload, когда его никто не задал.

## DoD
Очистки работают точечно; unit зелёные.
