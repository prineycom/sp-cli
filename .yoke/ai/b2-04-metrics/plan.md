# План b2-04-metrics — метрики/оценка дня

Research: `.yoke/ai/batch2-research/research-entity-areas.md` §4. Ops: EU/EL/ED (e=METRIC; EU self-creating — предпочтителен; EX full-replace — не использовать по умолчанию). id метрики = день YYYY-MM-DD. Никаких mood/productivity/obstruction/improvement — их больше нет в SP.

## Команды

- `sp metrics [--from --to] [--json]` — таблица по дням: impact, energy, focus-сессии (кол-во/сумма), notes кратко, completed/planned.
- `sp metric set [--day YYYY-MM-DD|today] [--impact 1-4] [--energy 1-3] [--notes "..."] [--reflect "text"] [--remind-tomorrow/--no-remind-tomorrow] [--completed N] [--planned N]` — EU UPD `{metric: {id: day, changes}}` (self-creating). `--reflect` добавляет `{text, created: now}` в reflections (прочитать текущие + append → в changes полный массив). State: если нет — создать `{id, focusSessions: [], remindTomorrow: false, reflections: [], ...changes}`, иначе patch.
- `sp metric focus <duration> [--day]` — EL UPD d=day `{day, duration_ms}` (додатний, additive). State: focusSessions append (создать день при отсутствии).
- `sp metric rm <day>` — ED DEL `{id}`.

## Тесты
- set на несуществующий день: entity создаётся с DEFAULT + changes; op shape EU.
- reflect: append к существующим.
- focus: EL payload {day, duration}, d=day; append в state; duration<=0 — ошибка CLI.
- rm; ids↔entities консистентность (doctor-хелпер).

## DoD
Оценка дня и фокус-сессии пишутся; unit зелёные.
