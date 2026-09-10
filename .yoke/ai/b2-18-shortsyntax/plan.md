# План b2-18-shortsyntax — парсер синтаксиса в sp add

Research: `.yoke/ai/batch2-research/research-task-methods.md` §9 (регексы дословно). Стратегия SP: парсить локально и эмитить обычные ops (HSS не нужен). TGS не персистентен — теги создаём GA.

## Реализация

Новый модуль `sp_cli/shortsyntax.py`: `parse(title, state, config) -> ParseResult{clean_title, project_id?, tag_ids, new_tag_titles, time_estimate_ms?, time_spent_ms?, due_day?, due_with_time?, deadline_day?, deadline_with_time?, repeat_cfg_params?}`.

Порт правил (порядок стадий: время → repeat → due → deadline → проект → теги):
- Время: `(?:\s|^)t?((?:\d+(?:\.\d+)?[mh]\s*)+)(?:\s*/((?:\s*\d+(?:\.\d+)?[mh])+)?)?(?=\s|$)`; с `/`: pre=timeSpent(сегодня), post=estimate; без — estimate. stringToMs: `^(\d*\.?\d+)([smh]?)$`; голое число: дробное или ≤8 → часы, целое >8 → минуты; `h:mm`.
- Проект `+`: перед `+` пробел/начало; longest-prefix по титулам (case-insens), полный титул бьёт частичный, одно слово матчится по squashed; исключить archived/hidden. Regex из research.
- Теги `#[^+#@!|\s]+` gi: перед `#` пробел/начало; чисто-числовые с позиции 0 — реджект; несуществующие → создать (GA) если `--create-tags`/по умолчанию создаём (как в SP через диалог — у нас сразу; отразить в help).
- Due `@[^+#@!]+` gi: даты Python-подмножество chrono: today/tomorrow/послезавтра? нет — англ. weekday-имена (следующее вхождение), YYYY-MM-DD, DD.MM(.YYYY), D/M, "15:00"/"3pm", голое число ≤24 = час сегодня (прошёл → завтра), комбинация "friday 15:00". Есть время → dueWithTime, нет → dueDay.
- Repeat `@every ...` / `@daily|weekly|monthly|yearly|annually`: полный regex из research (анкер к началу due-текста, interval 1-999, N=1 → пресет). Маппинг quickSetting/repeatCycle/weekday-флаги/dayOfMonth. → создаёт RA-конфиг после создания задачи (переиспользовать b2-14/существующую логику repeat).
- Deadline `![^+#@!]+`: только при config.shortSyntax.isEnableDeadline (читать из state.globalConfig; по умолчанию false) ИЛИ явном флаге `--parse-deadline`. Перед `!` пробел/начало.
- clean_title = остаток после вырезаний, схлопнуть пробелы.

Интеграция в `sp add`: парсинг по умолчанию, `--no-parse` отключает; явные флаги (--project/--due/--est/...) имеют приоритет над распарсенным. Конфиг-гейты из state.globalConfig.shortSyntax (isEnableProject/isEnableDue/isEnableTag). Эмитятся обычные ops: HA (+ поля), GA новые теги, HS при dueWithTime, RA при repeat, HDL при deadline.

## Тесты (таблично, много кейсов)
- "Task 30m" → est; "t1h/2h", "/1h", "1h 30m" суммы; "8" часы vs "45" минуты.
- "+Work", "+Work in progress" полный>частичный, "+SomePro" squashed, "title+x" не проект.
- "#tag1 #tag2", "x#no", "#123" реджект в 0.
- "@tomorrow", "@friday 15:00", "@2026-10-01", "@15", "@25.12".
- "@every monday", "@daily", "@every 2 days", "@every 15th", "@every weekday", "@every 1 week"→пресет.
- "!2026-10-01" гейт.
- Комбинация всего в одном тайтле; clean_title.
- Приоритет явных флагов; --no-parse.

## DoD
`sp add "Купить хлеб #быт +Дом @завтра 15m"` работает; unit зелёные.
