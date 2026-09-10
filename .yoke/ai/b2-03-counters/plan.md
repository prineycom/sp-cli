# План b2-03-counters — счётчики/привычки

Research: `.yoke/ai/batch2-research/research-entity-areas.md` §3. Ops: SA/SU/SD/ST/SFD/SM/SC (e=SIMPLE_COUNTER). Никогда: SI/SX/SG/SO/SF/SN/SUS (не персистентны), isOn не трогаем (device-local, писать false).

## Команды

- `sp counters [--json]` — список: id, title, type, enabled, значение сегодня (клики или длительность для StopWatch), стрик-настройки кратко.
- `sp counter add "Title" --type click|stopwatch|countdown [--icon name] [--countdown 30m] [--no-streak] [--streak-min N] [--streak-days mon,...]` — SA CRT `{simpleCounter: <full от EMPTY_SIMPLE_COUNTER>}`: `{id: nanoid, title, isEnabled: true, icon: icon|null, type: <SimpleCounterType строка>, countOnDay: {}, isOn: false, isTrackStreaks, streakMinValue, streakMode: 'specific-days', streakWeekDays: {0..6: bool}}` + countdownDuration для countdown. State: append ids/entities.
- `sp counter edit <id> [--title --icon --enable/--disable --streak-min ...]` — SU UPD `{simpleCounter: {id, changes}}`.
- `sp counter rm <id> [--yes]` — SD DEL `{id}`.
- `sp counter set <id> <value> [--date]` — ST (если date=today) `{id, newVal, today}` / SFD `{id, date, newVal}`. value: число для клика; длительность (30m) для stopwatch → ms. Clamp ≥0. State: countOnDay[date]=val.
- `sp counter inc <id> [--by N] [--date]` — читает текущее значение, эмитит ST/SFD с абсолютным newVal (никогда SI).
- `sp counter log <id> <duration> [--date]` — SC UPD `{id, date, duration_ms}` дельта для StopWatch; state: countOnDay[date] += (редьюсер isRemote-gated — на своей копии применяем сами). Отрицательное — ошибка.
- `sp counter order <id...>` — SM MOV `{ids}`.

## Тесты
- add: полный дефолтный объект, isOn=false; edit; rm.
- set/inc: абсолютные значения, clamp, ST vs SFD по дате.
- log: SC delta + state прирост; отказ на отрицательное.
- Проверить что SI/SG и пр. не эмитятся.

## DoD
Привычка создаётся, инкрементится, время логируется; unit зелёные.
