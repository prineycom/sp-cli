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
`--sort dueDate|created|title|timeEstimate [--dir asc|desc]`.

Важно: `board panel order` задаёт **только порядок** задач в панели —
состав панели всегда вычисляется из фильтров, добавить туда задачу вручную
нельзя. Панели редактируются перезаписью всего массива панелей доски
(SP не имеет рабочей операции правки одной панели).

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

## Тестирование

```sh
python3 -m pytest tests/ -q    # 216 unit-тестов, без сети
```

Плюс интеграционный свип против тестового WebDAV-сервера (копия live-файла) и проверка, что настоящий SP подхватывает изменения синком.

## Статус

- [x] Research: структура sync-data.json, контракты операций, sync-слой
- [x] MVP: полный набор read/write команд (задачи, план дня, расписание, проекты, теги, время)
- [x] Sync-слой (vectorClock, recentOps, optimistic locking + retry)
- [x] Заметки: CRUD, закрепление на сегодня, привязка к проекту
- [x] Доски: CRUD досок и панелей, фильтры панелей, порядок задач и досок
- [ ] YouTrack-мост (опционально)

## Известные ограничения

- Нет live-таймера (`start`/`stop`) — только пост-фактум `track`.
- У заметок нет переупорядочивания (`NO`) и вложений.
- У досок нет отдельной операции правки панели (`BP` — мёртвый редьюсер в SP): любая правка панели переписывает весь массив панелей доски.
- Конфликт с одновременной записью телефона решается retry (re-read + re-apply); при исчерпании попыток команда завершается ошибкой, данные не теряются.
