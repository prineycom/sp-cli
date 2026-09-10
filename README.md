# sp-cli

CLI для управления Super Productivity через WebDAV (`sync-data.json`), минуя приложение.

## Что это и как работает

SP установлен на телефоне и синхронизируется через WebDAV-сервер на Pi (`:8091`, префикс `pf_2__`). Нативного CLI у SP нет, поэтому sp-cli работает напрямую с sync-файлом: скачивает `sync-data.json`, мутирует `state.*`, дописывает операции в operation log (`recentOps` + `vectorClock`, `syncVersion`, `lastModified`) и кладёт файл обратно — телефон подтягивает изменения обычным синком, без конфликт-диалога.

```
CLI (Python) → WebDAV (GET/PUT /superproductivity/sync-data.json) → телефон подтягивает синком
```

Каждая запись: GET (strip `pf_2__`) → бэкап → мутация state + ops → PUT с `If-Match` (re-add `pf_2__`).

## Документация

- [.yoke/ai/sp-cli-mvp/research-sync-engine.md](.yoke/ai/sp-cli-mvp/research-sync-engine.md) — sync-слой: формат op, vectorClock, финализация файла
- [.yoke/ai/sp-cli-mvp/research-data-model.md](.yoke/ai/sp-cli-mvp/research-data-model.md) — модель данных `state.*`
- [.yoke/ai/sp-cli-mvp/plan.md](.yoke/ai/sp-cli-mvp/plan.md) — архитектура и матрица операций
- [docs/sp-plugin-contracts.md](docs/sp-plugin-contracts.md) — ранний research по контрактам плагина (research-доки выше новее и точнее)
- [docs/sample-sync-data.json](docs/sample-sync-data.json) — образец реального sync-файла

## Установка / требования

- Python 3.11+
- Единственная внешняя зависимость — `requests`:

```sh
pip install requests
```

Запуск — через обёртку `./sp` (или `python3 -m sp_cli`).

## Настройка

```sh
./sp init --url https://webdav.example:8091 --user sp --password '...'
```

Конфиг пишется в `~/.config/sp-cli/config.toml` (переопределяется env-переменной `SP_CLI_CONFIG`). Ключи:

| Ключ | Обязателен | Описание |
|---|---|---|
| `url` | да | базовый URL WebDAV-сервера |
| `user` | да | логин WebDAV |
| `password` / `password_file` | одно из двух | пароль или путь к файлу с паролем |
| `client_id` | да | генерится при `init` один раз, при повторном `init` сохраняется |
| `folder` | нет | папка на сервере (default `superproductivity`) |
| `backup_dir` | нет | куда класть бэкапы (default `~/.local/share/sp-cli/backups`) |

Флаги `init`: `--url`, `--user`, `--password`, `--password-file`, `--folder`, `--backup-dir`.

## Команды

Ссылаться на задачи/проекты/теги можно по id, короткому префиксу id или названию (заметки — по id или префиксу). У read-команд есть `--json`.

### Задачи

```sh
./sp list [--project P] [--tag T] [--done|--all] [--overdue] [--today] \
          [--unscheduled] [--search TEXT] [--parents-only] [--json]
./sp show <id> [--json]
./sp add "Title" [--project P] [--tag T ...] [--create-tags] \
         [--due today|tomorrow|+N|YYYY-MM-DD] [--at "YYYY-MM-DD HH:MM"] \
         [--remind 10m] [--est 30m] [--notes "..."] [--parent ID] [--backlog] \
         [--no-parse] [--parse-deadline]
./sp edit <id...> [--title T] [--notes N] [--append-notes N] [--est 1h] [--due DAY]
                               # несколько id — один синк; --due "" очищает; --title только с одним id
./sp bulk [<id...>] [--project P] [--tag T] [--overdue] [--search TEXT] [--done] [--all] \
          [--due DAY|--clear-due] [--tag-add T ...] [--tag-rm T ...] [--est 1h] \
          [--move-project P] [--complete|--reopen] [--dry-run] [--yes]
                               # массовая правка: выбор = явные id ∪ фильтры (как в list),
                               # всё пишется одним батчем; --dry-run только показывает выборку,
                               # при >10 задачах спрашивает подтверждение (--yes отключает)
./sp complete <id...>          # отметить сделанным
./sp reopen <id...>            # вернуть в работу
./sp delete <id...> [--yes]    # с подтверждением
./sp subtask <parent> "Title" [--est 30m] [--notes "..."]
./sp move <id> --project P
./sp tag <id> [--add T ...] [--remove T ...]
./sp reorder --project P <id...>   # полная перезапись порядка, см. ниже
./sp archive [--yes]           # заархивировать сделанные задачи
```

### Короткий синтаксис в `sp add`

`sp add` разбирает заголовок так же, как это делает само приложение SP: парсинг
идёт локально, а наружу уходят обычные операции (`HA`, `GA` для новых тегов,
`HS` при времени, `HDL` при дедлайне, `KT` при потраченном времени, `RA` при
повторе). Разобранные куски вырезаются из заголовка, остаток (со схлопнутыми
пробелами) становится названием задачи; что именно распозналось, печатается
строкой `parsed: ...` в stderr (stdout — по-прежнему только id).

| Токен | Значение |
|---|---|
| `30m`, `1h`, `1.5h`, `1h 30m`, `t2h` | оценка времени (разбор времени живёт под гейтом `isEnableDue`, как в SP) |
| `1h/2h`, `30m/` | потрачено / оценка (потраченное пишется на сегодня); решает именно `/`, поэтому `30m/` — это только потраченное время, без оценки. `/1h` регулярка SP не матчит вовсе — токен остаётся в заголовке |
| `+Проект` | проект по названию, по границам слов: слова совпадают целиком, сократить можно только последнее (`+Wor` → «Work», а `+Workshop` при проекте «Work» — не матч), одно слово матчит название без пробелов (`+SomePro` → «Some Pro»); полностью набранное название бьёт частичное, при равенстве выигрывает более короткое; архивные и скрытые проекты не участвуют |
| `#тег` | тег; несуществующий создаётся; чисто числовой `#123` — не тег только в самом начале заголовка (ссылка на issue), в середине (`Fix bug #123`) это обычный тег; `#today` зарезервирован |
| `@дата` | срок: `today`, `tomorrow`, `monday`…`sunday` (ближайшее вхождение, сегодня считается), `2026-10-01`, `25.12`, `25.12.2027`, `3/11`, `15:00`, `3pm`, голый час ≤24 (`@15`), комбинация `@friday 15:00` |
| `@every …`, `@daily` | повтор (см. ниже) |
| `!дата` | дедлайн, по умолчанию выключен |

Повторы: `@daily`, `@weekly`, `@monthly`, `@yearly`/`@annually`,
`@every monday` (и сокращения `mon`…`sun`), `@every 15th` (число месяца),
`@every weekday`/`@every workday` (пн–пт), `@every N days|weeks|months|years`
и `@every N mondays…sundays` (N от 1 до 999; `N=1` схлопывается в обычный
пресет). `@every 2 weekdays`/`workdays` повтором НЕ считается (как в SP:
«через один рабочий день» недельным циклом не выражается). Для
`@every monday`, `@every 2 fridays` и `@every 15th` `startDate` ставится на
ближайшее подходящее число.

Время сразу после фразы повтора забирается в расписание:
`@every monday 9:00` → `startTime: "09:00"`, `startDate` — ближайший
понедельник, и сама задача создаётся первым вхождением (`dueWithTime` на этот
понедельник 9:00). Если время на сегодня уже прошло — вхождение сдвигается на
целый период (неделя/месяц/год) или на следующий день для `@daily`
(`@every weekday` при этом перепрыгивает выходные). Повтор без времени тоже
даёт первое вхождение: `dueDay` = `startDate`. Всё, что не является временем
(`@every monday and friday`), остаётся в заголовке.

```sh
./sp add "Купить хлеб #быт +Дом @tomorrow 15m"   # «@завтра» не понимается: слова дат только английские
./sp add "Стендап @every monday +Work"
./sp add "Отчёт @friday 15:00 !2026-10-01" --parse-deadline
./sp add "Литерально #не тег +не проект" --no-parse
```

Гейты конфига (`globalConfig.shortSyntax` в файле синхронизации, читается на
лету): `isEnableProject`, `isEnableDue`, `isEnableTag` — по умолчанию `true`;
`isEnableDeadline` — по умолчанию `false`, включить разбор `!` разово можно
флагом `--parse-deadline`. `--no-parse` выключает разбор целиком; явные флаги
(`--project`, `--tag`, `--due`, `--at`, `--est`) всегда важнее разобранного, но
заголовок всё равно чистится.

Отличия от парсера SP:

- даты — детерминированное подмножество вместо chrono: только английские слова
  (`today`, `tomorrow`, названия дней недели), ISO-дата, `DD.MM[.YYYY]`, `D/M`,
  `HH:MM`, `N am|pm` и голый час; русские слова не понимаются;
- порядок в числовых датах — день/месяц (`25.12` и `3/11` = 25 декабря и
  3 ноября), а не месяц/день; год без указания подставляется так, чтобы дата
  была не в прошлом;
- перед `@` требуется пробел или начало строки (иначе `mail@example.com`
  превращался бы в срок);
- `#today` не выбирает системный тег TODAY (он не может лежать в `task.tagIds`);
  для «сегодня» есть `@today` и `sp plan`;
- новые теги создаются сразу, без диалога подтверждения;
- у подзадач (`--parent`) короткий синтаксис не применяется — у подзадачи нет
  своего проекта, тегов и расписания;
- при `--backlog` разобранный `@срок` — ошибка, ровно как явные `--due`/`--at`
  (задача в бэклоге по смыслу не запланирована);
- время из заголовка (`@tomorrow 9:00`) НЕ ставит напоминание: в SP парсер
  возвращает `remindAt: null`. У явного `--at` напоминание по-прежнему по
  умолчанию «в момент начала»; чтобы напомнить о разобранном времени, нужен
  явный `--remind`;
- если после разбора заголовок оказывается пустым (`sp add "#home"`), разбор
  отменяется целиком: задача создаётся с исходным текстом, без тегов, проекта
  и расписания (в stderr — строка `not parsed: ...`). В SP такой заголовок
  можно поправить в строке добавления, у CLI попытка одна.

### Порядок и конвертация

Точечные сдвиги — отдельными move-операциями (сдвиг, а не перезапись списка):

```sh
./sp today move <id> --before <other>          # поставить прямо перед другой задачей
./sp today move <id> --up|--down|--top|--bottom
./sp move-in-project <id> --up|--down|--top|--bottom
./sp move-in-project <id> --after <other>
./sp subtask move <id> --up|--down|--top|--bottom     # среди сабтасков родителя
./sp subtask reparent <id> --parent <new> [--after <sib>]   # к другому родителю
./sp demote <id> --parent <target> [--after <sib>]    # задача → сабтаск
./sp promote <id> [--today]                           # сабтаск → задача
./sp plan move <id> --before <other>                  # в планировщике (день берётся у якоря)
```

`--up`/`--down` перепрыгивают через выполненные соседи — как в приложении, —
но только в списках контекста (`today move`, `move-in-project`). У сабтасков
(`subtask move`) пропуска нет: соседи считаются все подряд.

`plan move` берёт день у якоря, поэтому откажется, если якорь не стоит ни в
одном дне планировщика и у него нет `dueDay`.

`promote` выполненного сабтаска переносит его на сегодня (`dueDay` = сегодня,
`dueWithTime` снимается) — так делает редьюсер SP.

При переносе сабтасков (`reparent`, `demote`, `promote`) времена обоих
родителей пересчитываются: `timeSpentOnDay`/`timeSpent` — сумма по сабтаскам,
`timeEstimate` — остаток работы (`max(0, оценка − потрачено)` по невыполненным).

`demote` откажется, если приложение всё равно не применит конвертацию: у задачи
есть родитель, свои сабтаски, повтор, привязка к issue, время (`--at`) или
напоминание; либо цель сама сабтаск (вложенность только двухуровневая).

⚠️ **Каверза:** старый `sp reorder` переписывает `taskIds` проекта целиком (op
`PU`). При гонке с другим устройством entity-LWW может откатить чужие правки
проекта. Для точечных перестановок используйте move-команды выше.

### Архив

```sh
./sp archived [--search TEXT] [--subtasks] [--json]   # что лежит в архиве
./sp restore <id> [--today]                           # вернуть задачу из архива
```

`archived` читает **оба** архивных блоба (`archiveYoung` и `archiveOld` — и на
верхнем уровне файла, и внутри `state`), помечает колонкой `age` (`young`/`old`),
показывает проект, дату завершения и число сабтасков. Сабтаски отдельными
строками не показываются (они возвращаются вместе с родителем) — `--subtasks`
включает их. Id архивных задач резолвятся по префиксу **отдельно** от живых:
префикс, попадающий и туда и туда, отклоняется — уточните id.

`restore` шлёт один op `HR` с уже материализованным payload (`{task, subTasks,
restoreToToday?}`): задача возвращается вместе со всеми своими сабтасками, ids
вычищаются из всех архивных блобов, задача становится незавершённой (`isDone`,
`doneOn` снимаются), исчезнувший проект заменяется на inbox, `TODAY` и мёртвые
теги из `tagIds` убираются, повисший `repeatCfgId` очищается. Корневая задача
дописывается в список проекта (**никогда в бэклог**) и в `taskIds` своих тегов.
С `--today` задача сразу планируется на сегодня (`dueDay`, чистка напоминаний у
задачи и сабтасков, prepend в `TODAY`, чистка из планировщика). Восстановление
уже живой задачи — ошибка.

Обслуживание архива — дело приложения: CLI **никогда** не эмитит flush
`archiveYoung`→`archiveOld` (`AF`), сжатие (`AC`) и `AR` — устройства делают это
сами по своему расписанию, а `AC` вдобавок разрушающая. CLI архив только читает,
пишет в него `archive` и вычищает из него `restore`.

### План дня

```sh
./sp today [--json]            # список на сегодня
./sp today add <id...>         # запланировать на сегодня
./sp today rm <id...>          # убрать из сегодня
./sp plan <id...> --day tomorrow   # запланировать на день (YYYY-MM-DD | today | tomorrow | +N)
./sp plan show [--day DAY] [--json]
./sp agenda [--json]           # overdue / today / scheduled / deadlines (7d)
```

### Бэклог проекта

Бэклог — второй список задач проекта; включается флагом проекта. Задача всегда ровно в одном из списков.

```sh
./sp backlog --project P [--json]     # задачи в бэклоге, в порядке backlogTaskIds
./sp backlog add <id...>              # из списка проекта в бэклог (наверх)
./sp backlog rm <id...>               # обратно в список проекта
./sp backlog clear --project P        # весь бэклог обратно в список
./sp project edit <id> --enable-backlog | --disable-backlog
./sp add "Title" --project P --backlog   # создать сразу в бэклоге
```

Бэклог должен быть включён (`--enable-backlog`), иначе `backlog add` и `add --backlog` завершатся ошибкой: приложение молча игнорирует такие операции. `--disable-backlog` сливает бэклог обратно в список проекта (как и в приложении). Сабтаск в бэклог не кладётся — переносите родителя.

### Календарь и расписание

```sh
./sp schedule <id> --at "2026-09-10 14:00" [--remind 10m]
./sp unschedule <id>
./sp deadline <id> (--day YYYY-MM-DD | --at "YYYY-MM-DD HH:MM") [--remind 1h|none]
./sp deadline <id> --clear            # снять дедлайн целиком (и его напоминание)
./sp deadline <id> --clear-reminder   # оставить дедлайн, убрать напоминание
./sp dismiss <id>                     # убрать напоминание задачи, время оставить
./sp repeat <id> --every day|week|month|year [--interval N] [--days mon,tue] \
             [--start-time HH:MM] [--start-date YYYY-MM-DD] \
             [--remind AtStart|m5|m10|m15|m30|h1]
./sp repeats [--json]          # список repeat-конфигов (пауза, время, напоминание, скипы)
./sp repeat edit <cfg-id|title> [--title T] [--every day|week|month|year] [--interval N] \
             [--days mon,thu] [--start-time HH:MM] [--start-date YYYY-MM-DD] \
             [--remind AtStart|m5|m10|m15|m30|h1] [--est 30m] [--notes N] \
             [--pause|--resume] [--clear startTime,remindAt,defaultEstimate,notes,
                                         monthlyWeekOfMonth,monthlyWeekday,monthlyLastDay]
./sp repeat skip <cfg-id|title> --date YYYY-MM-DD|today|tomorrow
./sp repeat rm <cfg-id|title> [--yes]   # удалить repeat-конфиг; сами задачи остаются,
                                        # ссылка repeatCfgId снимается (живые + архив)
```

`deadline --clear` не трогает `due*` (планирование и дедлайн — независимые оси),
а `dismiss` не трогает дедлайн и не снимает задачу с расписания. Правка дедлайна
с временем (`--at`) без `--remind` сохраняет **смещение** напоминания: оно
пересчитывается от нового времени (и отбрасывается, если попадает в прошлое).
Дедлайн-день (`--day`) напоминания не несёт — как и диалог SP, он его снимает;
`--remind none` снимает напоминание явно.

`repeat edit` шлёт `RU`. Очистка полей — только через `--clear`: очищаемые ключи
уходят сиблингом `clearedFields` рядом с `taskRepeatCfg` в actionPayload, потому
что `changes: {startTime: undefined}` теряется при любой JSON-сериализации и на
других устройствах превращается в no-op. `--clear startTime` заодно чистит
`remindAt` (напоминание привязано к времени старта, как в диалоге SP).

Смена `--every/--days/--interval` пересчитывает `quickSetting`, `repeatCycle` и
— только для недельного цикла — семь weekday-флагов. Дни недели сохраняются
всегда, если цикл не меняется (даже у конфигов, созданных в SP с пресетами
`MONDAY_TO_FRIDAY`/`WEEKLY_CURRENT_WEEKDAY`), так что `--interval 2` на «только
среда» остаётся «только средой». `quickSetting` всегда согласован с флагами:
mon–fri → `MONDAY_TO_FRIDAY`, ровно один день → `WEEKLY_CURRENT_WEEKDAY`,
остальное (и любой `--interval` ≠ 1) → `CUSTOM` — иначе SP при следующем
сохранении диалога перепишет дни сам. Смена цикла чистит месячные якоря
(`monthlyWeekOfMonth`, `monthlyWeekday`, `monthlyLastDay`) через `clearedFields`
— это `MONTHLY_ANCHOR_RESET` из SP.

`repeat skip` шлёт `RDI` (append-only, идемпотентно): дата попадает в
`deletedInstanceDates` и будущий инстанс не создаётся. Уже созданную задачу это
не удаляет — её нужно снести отдельно (`sp delete rpt_<cfgId>_<date>`).

### Проекты и теги

```sh
./sp projects [--all] [--json]
./sp project add "Title" [--color '#a05db1']
./sp project edit <id> [--title T] [--color C] [--hide] \
                       [--enable-backlog|--disable-backlog]
./sp project archive <id>
./sp project rm <id> [--yes]   # ПОЛНОЕ удаление проекта
./sp tags [--json]
./sp tag new "Title" [--color C]
./sp tag edit <id> [--title T] [--color C]
./sp tag rm <id> [--yes]       # удалить тег; он снимается со всех задач
```

⚠️ `project rm` удаляет проект целиком: все его задачи (включая сабтаски и бэклог),
заметки, секции и уже заархивированные задачи проекта — **БЕЗ архивации**,
восстановить их нельзя. Нужен мягкий вариант — `project archive`.
Заодно удаляются repeat-конфиги этого проекта (независимо от их тегов) и его
статистика времени (`timeTracking.project`, живая и в обоих архивах), а
`globalConfig.tasks.defaultProjectId` / `misc.defaultStartPage` чинятся на
`INBOX_PROJECT` / `0`, если указывали на удалённый проект — ровно как в SP.
Inbox удалить нельзя.

`tag rm` тег только снимается с задач, сами задачи остаются (кроме задачи вообще без
тегов, проекта и родителя — правило SP удаляет её вместе с сабтасками; то же
правило применяется и к заархивированным задачам).
Системные теги (`TODAY`, `EM_URGENT`, `EM_IMPORTANT`, `KANBAN_IN_PROGRESS`) удалить нельзя.

### Заметки

```sh
./sp notes [--project P] [--today] [--json]   # --today — закреплённые на сегодня
                                              # `./sp note` без подкоманды = ./sp notes
                                              # --project: порядок как в приложении (project.noteIds)
./sp note add "Текст" [--project P] [--pin]   # печатает id
./sp note show <id> [--json]
./sp note edit <id> [--content T | --append T] [--pin|--unpin] [--color '#a05db1']
./sp note rm <id> [--yes]                     # с подтверждением
./sp note move <id> --project P
```

### Вложения задач

```sh
./sp attach <task> <url-or-path> [--title T] [--type link|img|file]
                                              # печатает id вложения
                                              # тип по умолчанию: картинка → img,
                                              # file:// или локальный путь → file, иначе link
./sp attachments <task> [--json]              # `./sp attach <task>` без пути = то же
./sp attach edit <task> <attach-id> [--title T] [--path P] [--type ...]
./sp attach rm <task> <attach-id>
```

Вложения показываются и в `./sp show <task>`. `attach-id` резолвится по префиксу внутри задачи.

### Доски (boards)

```sh
./sp boards [--json]                          # доски и их панели с фильтрами
./sp board add "Title" [--cols N]             # печатает id (по умолчанию cols=2)
./sp board edit <id> [--title T] [--cols N]
./sp board rm <id> [--yes]                    # с подтверждением
./sp board sort <id>...                       # перечисленные доски — первыми
```

Панели (id доски/панели можно указывать префиксом или названием):

```sh
./sp board panel add <board-id> "Title" [фильтры]
./sp board panel edit <panel-id> [--title T] [фильтры]
./sp board panel rm <panel-id>
./sp board panel order <panel-id> <task-id>...
```

Фильтры панели: `--tags a,b`, `--exclude-tags c`, `--tags-match all|any`,
`--exclude-tags-match all|any`, `--project P` (повторяемый) | `--all-projects`,
`--done all|done|undone`, `--scheduled all|scheduled|not`,
`--backlog all|no|only`, `--parents-only` | `--no-parents-only`,
`--sort dueDate|created|title|timeEstimate [--dir asc|desc]`,
`--sort manual` (= `--no-sort`) — убрать сортировку и вернуться к ручному
порядку.

Важно: `board panel order` работает только на панели без сортировки: если у
панели задан `sortBy`, SP игнорирует ручной порядок — сначала
`./sp board panel edit <panel-id> --sort manual`.

Важно: `board panel order` задаёт **только порядок** задач в панели —
состав панели всегда вычисляется из фильтров, добавить туда задачу вручную
нельзя. Панели редактируются перезаписью всего массива панелей доски
(SP не имеет рабочей операции правки одной панели).

### Счётчики / привычки (simple counters)

```sh
./sp counters [--json]                        # id, тип, вкл/выкл, значение за сегодня, стрик
                                              # `./sp counter` без подкоманды = ./sp counters
./sp counter add "Title" [--type click|stopwatch|countdown] [--icon name] \
    [--countdown 30m] [--no-streak] [--streak-min N] [--streak-days mon,tue,...]
./sp counter edit <id> [--title T] [--icon name] [--enable|--disable] \
    [--streak|--no-streak] [--streak-min N] [--streak-days mon,...] [--countdown 30m]
./sp counter rm <id> [--yes]                  # с подтверждением
./sp counter set <id> <value> [--date DAY]    # абсолютное значение за день
./sp counter inc <id> [--by N] [--date DAY]   # +1 клик по умолчанию (не для stopwatch)
./sp counter log <id> 30m [--date DAY]        # добавить время в stopwatch-счётчик
./sp counter order <id>...                    # перечисленные — первыми
```

У `stopwatch`-счётчиков значение — длительность (`45m`, `1.5h`), у остальных — число
кликов; то же правило действует для `--streak-min`. `inc`/`--by` — только про клики:
для stopwatch-счётчика команда откажется работать и отправит к `counter log` (время)
или `counter set` (абсолютная длительность). `set`/`inc` всегда шлют
абсолютное значение (клампится до ≥0), `log` — дельту. Флаг «счётчик сейчас запущен»
(`isOn`) device-local: CLI всегда пишет `false` и не умеет запускать/останавливать таймер.

### Метрики / оценка дня

```sh
./sp metrics [--from DAY] [--to DAY] [--json]  # таблица по дням: impact, energy,
                                               # фокус-сессии, done/plan, заметки
                                               # `./sp metric` без подкоманды = ./sp metrics
./sp metric set [--day DAY] [--impact 1-4] [--energy 1-3] [--notes "..."] \
    [--reflect "текст"] [--remind-tomorrow|--no-remind-tomorrow] \
    [--completed N] [--planned N]              # по умолчанию — сегодня
./sp metric focus 25m [--day DAY]              # добавить фокус-сессию (аддитивно)
./sp metric rm <YYYY-MM-DD> [--yes]            # удалить оценку дня
```

id метрики — это сам день (`YYYY-MM-DD`), отдельной метрики «за неделю» не бывает.
`metric set` шлёт самосоздающий патч (`EU`): если оценки за день ещё нет, она
создаётся с дефолтами и применёнными полями. `--reflect` дописывает рефлексию к
уже существующим (массив читается из свежего состояния, поэтому retry при 412 не
теряет записи). `metric focus` — аддитивная операция (`EL`), длительность должна
быть положительной. Полная перезапись метрики (`EX`) не используется: частичный
payload затирает поля. Полей mood/productivity/obstruction/improvement в SP
больше нет, CLI их не пишет и ругается на них в `doctor`.

### Интеграции / календари (issue providers)

```sh
./sp providers [--json]                       # id, ключ, вкл/выкл, url, проект, авто-импорт
                                              # `./sp provider` без подкоманды = ./sp providers
./sp provider add-ical <url> [--auto-import] [--project P] [--tag T ...] [--create-tags] \
    [--check-every 2h] [--banner-before 2h] [--include-regex RE] [--exclude-regex RE]
./sp provider add-caldav --url U --resource R --username U --password P \
    [--category-filter C] [--project P] [--tag T ...] --store-plaintext-credentials
./sp provider edit <id> [--enable|--disable] [--url U] \
    [--auto-import|--no-auto-import] [--project P|--no-project] \
    [--check-every 2h] [--banner-before 2h] [--include-regex RE] [--exclude-regex RE] \
    [--username U] [--password P --store-plaintext-credentials] [--category-filter C]
./sp provider rm <id> [--yes]                 # удалить провайдер и отвязать его задачи
./sp provider order <id>...                   # перечисленные — первыми, хвост сохраняется
```

CLI только подключает и настраивает провайдера — задачи из календаря (`cal_*`)
создаёт само приложение при следующем поллинге, CLI их не синтезирует.
Провайдер всегда пишется целиком (полный cfg для своего ключа): SP валидирует
built-in провайдеров через typia и отбраковывает частичный объект.
`provider rm` собирает `taskIdsToUnlink` из живых задач **и всех архивных блобов**
(`archiveYoung`/`archiveOld` — и на верхнем уровне файла, и внутри `state`:
файл может нести оба сразу) и вычищает у них поля привязки к issue
(`issueId`, `issueProviderId`, `issueType`, `issueWasUpdated`,
`issueLastUpdated`, `issueAttachmentNr`, `issueTimeTracked`, `issuePoints`).

**Предупреждение про CalDAV:** логин и пароль хранятся в sync-файле
**открытым текстом** (и попадают в каждый бэкап). Поэтому `add-caldav` без
явного флага `--store-plaintext-credentials` отказывается работать.

Поддерживаются ключи `ICAL` и `CALDAV`. Провайдеры-плагины (`plugin:*`),
Jira/GitHub/GitLab и `dismissedCalendarAutoImportEventIdsByProvider` CLI не
трогает: `provider edit`/`provider rm` для чужого ключа отказывают — такой
провайдер редактируется в приложении.

`sp doctor` дополнительно ругается на задачи (живые и архивные) с
`issueProviderId`, указывающим в никуда, и на built-in ICAL/CALDAV провайдеров
с неполным cfg.

### Время / worklog

```sh
./sp track <id> 30m [--date YYYY-MM-DD]     # добавить учтённое время
./sp untrack <id> 30m [--date YYYY-MM-DD]   # снять лишнее время (op TR, клампится в 0)
./sp worklog [--from DAY] [--to DAY] [--json]  # по дням, по проектам, точность оценок
```

### Live-таймер

```sh
./sp start <id>          # запустить таймер (если уже идёт другой — сначала автостоп)
./sp start               # показать текущий таймер (алиас `current`: exit 1, если его нет)
./sp current [--json]    # что тикает: задача, старт, elapsed (exit 1, если таймера нет)
./sp stop                # остановить и списать время (KT), exit 2 если таймера нет
./sp stop --discard      # сбросить без записи времени (файл трётся вслепую —
                         #   работает и на битом timer.json)
```

Таймер локальный: `currentTaskId` в SP не синкается (нет op-представления), поэтому
состояние лежит в `~/.local/share/sp-cli/timer.json` (атомарная запись, override —
env `SP_CLI_TIMER`), а по сети уходит только натиканное время при `stop` — один op
`KT` на день. Таймер через полночь разбивается на несколько `KT` в одном батче.
Меньше минуты — предупреждение и списание 1m (или `--discard`).
Если задача успела исчезнуть (удалена на другом устройстве), `stop` предупреждает
в stderr, отбрасывает отрезок, чистит файл таймера и выходит с 0; автостоп в
`start` ведёт себя так же и всё равно запускает новую задачу. `started_at` из
будущего — ошибка (exit 2), файл таймера сохраняется.

День у `track`/`stop` — **логический**: `globalConfig.misc.startOfNextDayTime`
(строка `"HH:MM"`, канон) или устаревший числовой `misc.startOfNextDay` (часы,
учитывается только если строки нет вовсе). Как и в SP, битая строка сбрасывает
смещение в 0, а не откатывается к числу. При значении по умолчанию (0) границей
остаётся обычная полночь.

Время подзадачи агрегируется на родителя: `track`/`untrack` пересчитывают
`timeSpentOnDay[date]` и `timeSpent` родительской задачи (как
`updateParentTimeSpentIncremental` в SP) — только в state, payload op не
меняется, каждое устройство выводит роллап само.

### Сервисные

```sh
./sp pull [--raw]    # скачать и показать сводку sync-файла (--raw — весь JSON)
./sp doctor          # проверить инварианты state
./sp backup          # скачать и сохранить бэкап
./sp init ...        # см. «Настройка»
```

## Безопасность записи

- Перед каждым PUT автоматически делается бэкап в `~/.local/share/sp-cli/backups` (ротация: хранятся последние 10).
- Оптимистичный локинг: PUT идёт с `ETag`/`If-Match`; на HTTP 412 (одновременная запись телефона) файл перечитывается и мутации применяются заново, до 3 попыток.
- CLI пишет под собственным `client_id` (генерится при `init`), отличным от телефона — vector clock разруливает порядок изменений.
- Пароль CalDAV-провайдера сохраняется в sync-файл (и в бэкапы) открытым текстом — команда требует явного `--store-plaintext-credentials`.

## Тестирование

```sh
python3 -m pytest tests/ -q    # 604 unit-теста, без сети
```

Плюс интеграционный свип против тестового WebDAV-сервера (копия live-файла) и проверка, что настоящий SP подхватывает изменения синком.

## Статус

- [x] Research: структура sync-data.json, контракты операций, sync-слой
- [x] MVP: полный набор read/write команд (задачи, план дня, расписание, проекты, теги, время)
- [x] Sync-слой (vectorClock, recentOps, optimistic locking + retry)
- [x] Заметки: CRUD, закрепление на сегодня, привязка к проекту
- [x] Доски: CRUD досок и панелей, фильтры панелей, порядок задач и досок
- [x] Счётчики/привычки: CRUD, set/inc, лог времени stopwatch, порядок
- [x] Метрики: оценка дня (impact/energy/notes/рефлексии), фокус-сессии
- [x] Интеграции: календари ICAL/CalDAV — подключение, правка, удаление, порядок
- [x] Архив: список архивных задач, восстановление задачи с сабтасками
- [x] Вложения задач: добавление, правка, удаление, список
- [ ] YouTrack-мост (опционально)

## Известные ограничения

- Live-таймер (`start`/`stop`) локальный: сам факт запуска не синкается, только натиканное время. У счётчиков старта/остановки нет по той же причине (`isOn` не синхронизируется).
- У заметок нет переупорядочивания (`NO`) и вложений.
- У досок нет отдельной операции правки панели (`BP` — мёртвый редьюсер в SP): любая правка панели переписывает весь массив панелей доски.
- Конфликт с одновременной записью телефона решается retry (re-read + re-apply); при исчерпании попыток команда завершается ошибкой, данные не теряются.
