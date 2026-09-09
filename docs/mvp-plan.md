# MVP Plan — sp-cli (v2, согласовано с Пашей)

**Цель:** CLI с **полным покрытием работы с задачами и планами на день** — read + write + sync-слой в первом этапе. Никакого «сначала read-only, потом write».

## Принципы

- Python 3, stdlib + `requests` (WebDAV = GET/PUT).
- Конфиг: `~/.config/sp-cli/config.toml` (WebDAV URL, юзер, путь к паролю, clientId CLI).
- Бэкап перед каждым PUT: `~/.local/share/sp-cli/backups/sync-data-<ts>.json` (ротация: последние 10).
- Отдельный clientId CLI (не как у телефона) — обязателен с первой write-операции.
- Тесты: unit на мутациях (in-memory), integration на копии файла, финальная проверка на живом WebDAV + телефон.

## Этап 1 — Задачи + планы на день (полное покрытие)

### 1a. Ядро данных + sync-слой (~2-3 чч)
- `SyncClient`: GET/PUT sync-data.json, strip/re-add `pf_2__`, бэкапы, schemaVersion-чек
- `recentOps` append, `vectorClock` инкремент своего clientId, `lastModified`
- Retry при конфликте (перечитать → повторить мутацию, max 3)

### 1b. Задачи — read (~1 чч)
- `sp list` — фильтры: `--project`, `--tag`, `--overdue`, `--today`, `--unscheduled`, `--search`, `--done`, `--parents-only`, `--json`
- `sp show <id>` — полная карточка задачи (включая подзадачи, время, заметки)

### 1c. Задачи — write (~3-4 чч)
- `sp add "тайтл" [--project X --tag Y --due 2026-08-10 --est 30m --notes "..."]` (парсер длительности: 30m/1h/1.5h)
- `sp edit <id> [--title --notes --due --est --project --tags]`
- `sp complete <id>` / `sp reopen <id>` (doneOn + timestamp)
- `sp delete <id>` — каскад на подзадачи
- `sp subtask <parent-id> "тайтл"` — подзадача
- `sp move <id> --project X` — перенос между проектами
- `sp tag <id> --add X --remove Y` — точечное управление тегами
- `sp reorder --project X <id1> <id2> ...` — порядок в проекте

### 1d. Планы на день (~1-2 чч)
- `sp today` — что в Today-виде сейчас (тег `TODAY` в `tagIds`)
- `sp today add <id...>` / `sp today remove <id...>`
- `sp agenda` — Today + дедлайны на сегодня + просроченные (утренний обзор одним вызовом)

**DoD этапа 1:** полный жизненный цикл задачи из CLI — create → plan → edit → complete; всё отражается на телефоне после синка без конфликтов. Проверка на живом WebDAV + телефон вручную.

## Этап 2 — Проекты и теги (CRUD, ~1 чч)

- `sp projects` / `sp project add "Имя" [--color]` / `sp project edit <id>` / `sp project archive <id>`
- `sp tags` / `sp tag add` / `sp tag edit`

## Этап 3 — Worklog и отчёты (~1 чч)

- `sp worklog --from --to` — время по дням/проектам/тегам, точность оценок
- `sp show <id>` уже показывает timeSpent/timeEstimate

## Этап 4 — Интеграция с Hermes (~1-2 чч)

- Скилл `sp-cli`: команды, gotchas, fallback
- Опционально cron: утренний `sp agenda`-дайджест (замена убитого Vikunja-дайджеста)

## Вне MVP (осознанно)

- Таймер start/stop — нужна delta-модель (внешний таймер не тикает), сделаем отдельно
- Repeat-конфиги (нужен образец структуры из UI)
- Планировщик дней (нужен образец из UI)
- YouTrack-мост — после MVP
- bulk-операции — синтаксис `sp complete <id1> <id2> ...` уже покрывает основное

## Риски

| Риск | Митигация |
|---|---|
| Телефон перезатрёт изменения CLI | корректный vectorClock + recentOps; живой тест синка до «релиза» |
| SP обновит schemaVersion | чек при чтении, предупреждение |
| Одновременная запись | retry + бэкапы; юзкейс редкий |

## Оценка суммарно

Этап 1: **~7-10 чч** (ядро+sync 2-3, read 1, write 3-4, today 1-2)
Этапы 2-4: ~3-4 чч
**Всего MVP: ~10-14 чч**