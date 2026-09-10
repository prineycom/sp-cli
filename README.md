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
         [--remind 10m] [--est 30m] [--notes "..."] [--parent ID]
./sp edit <id> [--title T] [--notes N] [--append-notes N] [--est 1h] [--due DAY]  # --due "" очищает
./sp complete <id...>          # отметить сделанным
./sp reopen <id...>            # вернуть в работу
./sp delete <id...> [--yes]    # с подтверждением
./sp subtask <parent> "Title" [--est 30m] [--notes "..."]
./sp move <id> --project P
./sp tag <id> [--add T ...] [--remove T ...]
./sp reorder --project P <id...>
./sp archive [--yes]           # заархивировать сделанные задачи
```

### План дня

```sh
./sp today [--json]            # список на сегодня
./sp today add <id...>         # запланировать на сегодня
./sp today rm <id...>          # убрать из сегодня
./sp plan <id...> --day tomorrow   # запланировать на день (YYYY-MM-DD | today | tomorrow | +N)
./sp plan show [--day DAY] [--json]
./sp agenda [--json]           # overdue / today / scheduled / deadlines (7d)
```

### Календарь и расписание

```sh
./sp schedule <id> --at "2026-09-10 14:00" [--remind 10m]
./sp unschedule <id>
./sp deadline <id> (--day YYYY-MM-DD | --at "YYYY-MM-DD HH:MM") [--remind 1h]
./sp repeat <id> --every day|week|month|year [--interval N] [--days mon,tue] \
             [--start-time HH:MM] [--start-date YYYY-MM-DD] \
             [--remind AtStart|m5|m10|m15|m30|h1]
./sp repeats [--json]          # список repeat-конфигов
```

### Проекты и теги

```sh
./sp projects [--all] [--json]
./sp project add "Title" [--color '#a05db1']
./sp project edit <id> [--title T] [--color C] [--hide]
./sp project archive <id>
./sp tags [--json]
./sp tag new "Title" [--color C]
./sp tag edit <id> [--title T] [--color C]
```

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
    [--auto-import|--no-auto-import] [--project P|--no-project] [--check-every 2h]
./sp provider rm <id> [--yes]                 # удалить провайдер и отвязать его задачи
./sp provider order <id>...                   # перечисленные — первыми, хвост сохраняется
```

CLI только подключает и настраивает провайдера — задачи из календаря (`cal_*`)
создаёт само приложение при следующем поллинге, CLI их не синтезирует.
Провайдер всегда пишется целиком (полный cfg для своего ключа): SP валидирует
built-in провайдеров через typia и отбраковывает частичный объект.
`provider rm` собирает `taskIdsToUnlink` из живых задач **и обоих архивов**
(`archiveYoung`/`archiveOld`) и вычищает у них поля привязки к issue
(`issueId`, `issueProviderId`, `issueType`, `issueWasUpdated`,
`issueLastUpdated`, `issueAttachmentNr`, `issueTimeTracked`, `issuePoints`).

**Предупреждение про CalDAV:** логин и пароль хранятся в sync-файле
**открытым текстом** (и попадают в каждый бэкап). Поэтому `add-caldav` без
явного флага `--store-plaintext-credentials` отказывается работать.

Поддерживаются ключи `ICAL` и `CALDAV`. Провайдеры-плагины (`plugin:*`) и
`dismissedCalendarAutoImportEventIdsByProvider` CLI не трогает.

### Время / worklog

```sh
./sp track <id> 30m [--date YYYY-MM-DD]     # добавить учтённое время
./sp worklog [--from DAY] [--to DAY] [--json]  # по дням, по проектам, точность оценок
```

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
python3 -m pytest tests/ -q    # 463 unit-теста, без сети
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
- [ ] YouTrack-мост (опционально)

## Известные ограничения

- Нет live-таймера (`start`/`stop`) — только пост-фактум `track`; у счётчиков по той же причине нет старта/остановки (`isOn` не синхронизируется).
- У заметок нет переупорядочивания (`NO`) и вложений.
- У досок нет отдельной операции правки панели (`BP` — мёртвый редьюсер в SP): любая правка панели переписывает весь массив панелей доски.
- Конфликт с одновременной записью телефона решается retry (re-read + re-apply); при исчерпании попыток команда завершается ошибкой, данные не теряются.
