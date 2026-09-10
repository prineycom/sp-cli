# План b2-14-repeat-edit — редактирование повторов

Research: `.yoke/ai/batch2-research/research-task-methods.md` §5. Ops RU (с clearedFields!), RDI. RX никогда (не персистентен).

## Команды

- `sp repeat edit <cfg-id> [--title] [--every day|week|month|year] [--interval N] [--days mon,tue,...] [--start-time HH:MM] [--remind AtStart|m5|m10|m15|m30|h1] [--est 30m] [--notes] [--pause|--resume] [--clear startTime,remindAt,defaultEstimate,notes]` — RU UPD `{taskRepeatCfg: {id, changes}, clearedFields?: [...]}`. clearedFields — сиблинг в actionPayload (только там очистка работает: JSON теряет undefined). --pause/--resume = changes {isPaused}. Изменение --every/--days пересчитывает quickSetting/repeatCycle/weekday-булеаны как в `sp repeat` (переиспользовать built-логику).
- `sp repeat skip <cfg-id> --date YYYY-MM-DD|today|tomorrow` — RDI UPD `{repeatCfgId, dateStr}` d=cfgId. State: deletedInstanceDates append (идемпотентно). Подсказать в выводе: уже созданный инстанс этой даты не удаляется — удалить `sp delete rpt_<cfgId>_<date>` если существует.
- `sp repeats [--json]` — расширить: показывать isPaused, deletedInstanceDates (кол-во/последние), startTime/remindAt.

resolve_repeat_cfg по префиксу id или по title задачи-хозяина.

## Тесты
- edit: op shape, clearedFields для очистки startTime/remindAt (и что в changes их нет как undefined).
- pause/resume; смена cadence — правильные weekday-флаги.
- skip: RDI payload, идемпотентный append.

## DoD
Пауза/правка/скип повторов; unit зелёные.
