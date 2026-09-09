# MVP Plan — sp-cli

**Цель:** рабочий CLI, через который Hermes (и Паша с клавиатуры) управляет задачами SP на телефоне — с корректным sync-слоем, чтобы телефон не потерял и не перезатёр изменения.

## Принципы

- Python 3, stdlib + `webdav-client` (или `requests` — WebDAV = HTTP, GET/PUT хватит). Ноль тяжёлых зависимостей.
- Один файл-модуль `sp_cli/` + entrypoint `sp` (или `python -m sp_cli`).
- Конфиг: `~/.config/sp-cli/config.toml` (WebDAV URL, юзер, путь к паролю, clientId).
- Бэкап перед каждым PUT: `~/.local/share/sp-cli/backups/sync-data-<ts>.json` (хранить последние 10).
- Тесты: unit на мутациях (in-memory dict), integration — против локального файла, smoke — против живого WebDAV (опционально, с флагом).

## Phase 1 — Read-only (безопасно, ~2 чч)

1. `sp list` — задачи (не выполненные по умолчанию)
   - фильтры: `--project`, `--tag`, `--overdue`, `--today`, `--unscheduled`, `--search`, `--done`, `--json`
2. `sp projects` — список проектов
3. `sp tags` — список тегов
4. `sp worklog --from --to` — агрегат timeSpentOnDay по дням/проектам

**Definition of done:** я могу ответить Паше «что у тебя сегодня в Today» и «сколько времени на проекте X за неделю» из CLI.

## Phase 2 — Sync-слой (критично, ~2-3 чч)

5. `SyncClient` — GET/PUT sync-data.json: strip/re-add `pf_2__`, бэкапы, retry при конфликте (перечитать → повторить мутацию, max 3 попытки).
6. Генерация clientId CLI (сохраняется в конфиг). Инкремент vectorClock своего clientId при каждой записи.
7. `recentOps` append: `{id: uuid7, a:"HA", o:<OP>, e:"TASK", p:{actionPayload, entityChanges}, c:<clientId>}`.
8. `lastModified` = now ms при каждой записи.

**Definition of done:** изменение, сделанное CLI, видит телефон после синка — и не превращается в конфликт. Проверяем вручную: создаём задачу CLI → синк на телефоне → задача в приложении.

## Phase 3 — Write-операции (~3 чч)

9. `sp add "тайтл" [--project X --tag Y --due 2026-08-10 --est 30m]` — создать (парсер длительности: 30m/1h/1.5h → ms)
10. `sp complete <id>` / `sp complete --search "..."`
11. `sp edit <id> [--title --notes --due --est --project --tag]`
12. `sp delete <id>` (каскад для подзадач)
13. `sp today <id>` / `sp today --remove <id>` — план на сегодня (TODAY-тег)
14. `sp move <id> --project X`
15. `sp subtask <parent-id> "тайтл"`

**Definition of done:** полный цикл create → plan → complete из CLI, телефон отражает всё после синка.

## Phase 4 — Интеграция с Hermes (~1-2 чч)

16. Скилл `sp-cli` для Hermes: команды, gotchas, fallback.
17. Опционально cron: утренний дайджест «что в Today» (замена убитого Vikunja-дайджеста).

## Вне MVP (осознанно)

- Таймер start/stop — требует живого тика, модель delta-start/stop сделаем позже
- Repeat-конфиги (нужен образец структуры из UI)
- Планировщик дней (нужен образец из UI)
- YouTrack-мост — отдельный этап после MVP
- bulk-операции — юзкейс неясен, добавим при необходимости

## Риски

| Риск | Митигация |
|---|---|
| Телефон перезатрёт изменения CLI | sync-слой Phase 2: корректный vectorClock + recentOps; тест на живом синке ДО релиза write-операций |
| SP обновит схему (schemaVersion 4 → 5) | проверка schemaVersion при чтении; предупреждение при неожиданной версии |
| Конфликт при одновременной записи | retry-логика + бэкапы; юзкейс редкий (один человек) |

## Оценка суммарно

~8-10 чч чистой работы. Порядок фаз строгий: read → sync → write → интеграция.