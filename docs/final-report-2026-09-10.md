# sp-cli — финальный отчёт сессии (2026-09-09 … 2026-09-10)

Итог двухдневной автономной сессии: с нуля до полнофункционального CLI для управления Super Productivity через WebDAV-файл `sync-data.json`, минуя приложение. Всё в `main`, все тесты зелёные, работа проверена живым синком телефона.

---

## 1. Что сделано

### Batch 1 — MVP (2026-09-09)

Пакет `sp_cli` (Python 3.11+, stdlib + requests) с нуля:

- **Ядро синка**: GET/PUT с префиксом `pf_2__`, ETag/If-Match оптимистичный локинг с retry (max 3), бэкапы перед каждой записью (ротация 10), operation log (`recentOps`), vectorClock, финализация файла (syncVersion, sv, trim 2000, oldestOpSyncVersion).
- **Задачи**: add/edit/complete/reopen/delete/subtask/move/tag/reorder/show/list с фильтрами.
- **План дня**: today add/rm, plan --day, agenda, порядок Today.
- **Календарь**: schedule --at --remind, unschedule, deadline, повторяющиеся задачи (repeat).
- **Время**: track, worklog (live + архив).
- **Прочее**: архивация done-задач, проекты/теги CRUD, pull/doctor/backup/init.

### Batch 2 — 15 областей (2026-09-10), каждая отдельным флоу план→executor→review→фиксы

| # | Область | Команды |
|---|---|---|
| 1 | Заметки | `note add/show/edit/rm/move`, `notes`, пин в Today |
| 2 | Доски | `board add/edit/rm/sort`, `board panel add/edit/rm/order` |
| 3 | Счётчики/привычки | `counter add/edit/rm/set/inc/log/order`, `counters` |
| 4 | Метрики дня | `metric set/focus/rm`, `metrics` (impact/energy/notes/reflections) |
| 6 | Интеграции | `provider add-ical/add-caldav/edit/rm/order`, `providers` |
| 10 | Live-таймер | `start/stop/current`, `untrack` (локальный; синкается только время) |
| 11 | Бэклог проекта | `backlog add/rm/clear`, `sp add --backlog`, `--enable/--disable-backlog` |
| 12 | Архив | `archived`, `restore [--today]` |
| 13 | Полные удаления | `project rm` (HPD+маркер), `tag rm` (GD-каскад), `repeat rm` (HRC) |
| 14 | Правка повторов | `repeat edit` (clearedFields!), `repeat skip`, pause/resume |
| 15 | Порядок и конвертация | `today move`, `move-in-project`, `subtask move/reparent`, `demote/promote`, `plan move` |
| 16 | Вложения | `attach/attachments/attach edit/attach rm` |
| 17 | Снятие дедлайнов | `deadline --clear/--clear-reminder`, `dismiss`, пересчёт напоминания при переносе |
| 18 | shortSyntax | `sp add "хлеб #тег +Проект @tomorrow 15m @every monday"` |
| 19 | Bulk | `sp edit <id...>`, `sp bulk` с фильтрами и dry-run |

Всего **~90 команд/подкоманд**. Полный справочник — README.md.

---

## 2. Как это работает (главное о формате)

Полные контракты: `.yoke/ai/sp-cli-mvp/research-sync-engine.md`, `research-data-model.md`, `.yoke/ai/batch2-research/*.md` — извлечены сабагентами из исходников SP (schemaVersion 4, файл version 2, сентябрь 2026).

Ключевое:

- Живые клиенты применяют чужие изменения **реплеем op'ов** из `recentOps` (NgRx-экшены по короткому коду `a`); свежие клиенты берут снапшот `state`. Поэтому CLI пишет **и корректные op'ы, и консистентный state** — контракт «state уже содержит эффект всех op'ов».
- Каждый op: `{id: uuid7, a, o, e, d/ds, p:{actionPayload, entityChanges}, c: clientId, s: 4, t, v}`; `v` = merge(клок файла, свой кумулятивный счётчик) — обязан доминировать, иначе конфликты/LWW.
- Смертельные ошибки (никогда): пустой `recentOps`, регресс `syncVersion`, чужие ключи из vectorClock, op'ы с `s≠4`, непersistent-экшены (BP/HUM/TU/RX/SI/AF/AC/…).
- Today-вид = `dueDay == сегодня` (или dueWithTime сегодня); тег TODAYтолько порядок, в `task.tagIds` никогда. `dueDay` и `dueWithTime` могут сосуществовать, если указывают на один день (planTasksForToday-семантика SP).
- Логический день учитывает `misc.startOfNextDayTime` (часы «до 4 утра — ещё вчера»).
- Архивы: top-level `archiveYoung/archiveOld` авторитетны, но код обрабатывает все 4 возможных блоба (top-level + внутри state).
- Таймер (`currentTaskId`) в SP принципиально не синкается — у CLI он локальный (`~/.local/share/sp-cli/timer.json`), синкается только натиканное время (KT-дельты, коммутативные при конфликтах).

---

## 3. Как проверено

1. **Unit**: **1070 тестов** (`python3 -m pytest tests/ -q`), без сети. Проверяют точные формы payload'ов, коды действий, порядок ds, зеркалирование state, инварианты (doctor-чистота после каждой мутации), retry-безопасность замыканий.
2. **Ревью каждого пункта** отдельным Opus-агентом со сверкой по исходникам SP. Поймано и исправлено: **4 критикала** (мид-словный матч проектов в shortSyntax; применение невыбранной выборки bulk на 412-retry; затирание ручных дней недели при правке интервала повтора; воскрешение repeat-конфигов удалённого проекта через полный PUT) и **~20 важных** (двойные архивные блобы, пересчёт времени родителя, section-модель — у SP `contextId/contextType`, а не `projectId`, offset-aware «сегодня», пересчёт напоминания дедлайна и др.).
3. **Интеграционные свипы** на тестовом rclone-WebDAV с копией живых данных: batch 1 — ~30 команд; batch 2 — 75 write-op'ов по всем областям, 49 различных кодов действий, запрещённые коды отсутствуют, маркер `projectDeleteWins` и KT-зеркала на месте, клоки строго монотонны, `doctor` чист.
4. **Живая проверка синком телефона** (clientId `I_jHradR`):
   - Batch 1: набор записей «CLI:» — синк прошёл **без конфликт-диалога**, всё появилось (подтверждено Пашей), записи затем вычищены.
   - Batch 2: набор записей «B2:» по каждому пункту — подтверждено («всё вроде сработало»). Vector clock телефона корректно инкрементится рядом с CLI.
5. **Браузерный E2E не состоялся**: headless Chromium на этой Pi не загружает страницы вообще (даже example.com в `--dump-dom`) — баг окружения, записан в память проекта, чтобы не тратить время впредь.

### Тестовые записи «B2:» на проде

Если ещё не вычищены: `./sp bulk --project "B2 Проект" --complete --yes && ./sp project rm "B2 Проект" --yes`, затем `./sp tag rm b2-быт --yes`, `./sp note rm <id> --yes` ×2, `./sp board rm <id> --yes`, `./sp counter rm <id> --yes` ×2, `./sp provider rm <id> --yes`, `./sp metric rm 2026-09-10`, архивную «B2: уйдёт в архив» можно оставить или удалить из приложения.

---

## 4. Что НЕ реализовано

**Области, не входившие в заказ:**
- **Секции** (`section`) — CRUD секций и раскладка задач по ним (эффекты чужих op'ов зеркалим корректно).
- **Структура меню** (`menuTree`) — папки проектов/тегов (новые проекты видны и без неё).
- **Настройки приложения** (`globalConfig`) — сознательно read-only (кроме гейтов shortSyntax и healing при удалении проекта).
- **Сессии дня** (`timeTracking` KS/KW: workStart/workEnd/перерывы) — на worklog не влияют.

**Осознанные ограничения внутри покрытого:**
- Интеграции: запись только ICAL/CalDAV; Jira/GitLab/plugin-провайдеры — просмотр. CalDAV-пароль хранится в sync-файле открытым текстом — требуется явный `--store-plaintext-credentials`.
- Обслуживание архива (flush young→old, компрессия) — не эмитим, делает приложение.
- Повторы: `subTaskTemplates` не задаются; инстансы материализует приложение.
- Таймер не виден на других устройствах (ограничение SP).
- shortSyntax: только английские даты, `D/M`-порядок, `!дедлайн` за флагом `--parse-deadline`.
- Заметки: нет reorder (op `NO`).
- Legacy-shim `reassertOwnTagsAfterConvert` (для клиентов SP ≤ 18.20.1) не эмитим.

**Бэклог миноров ревью** (~40 шт., не блокируют): `.yoke/ai/batch2-minors-backlog.md`.

**Из старого плана не делалось:** интеграция с Hermes (скилл + утренний cron-дайджест `sp agenda`), YouTrack-мост.

---

## 5. Важные операционные факты

- **Окружение**: прод WebDAV — rclone в docker (`prineyai-dashboard-jeflfi-superproductivity-webdav-1`), host-порт 8091, том `/var/lib/docker/volumes/prineyai-dashboard-jeflfi_superproductivity_webdav/_data`, файл `/superproductivity/sync-data.json`.
- **Конфиг CLI**: `~/.config/sp-cli/config.toml` (создан, clientId CLI **`B_e4wazk`** — не менять и не совпадать с телефоном). Env-переопределения: `SP_CLI_CONFIG`, `SP_CLI_TIMER`.
- **Бэкапы**: автоматические перед каждой записью в `~/.local/share/sp-cli/backups/` (ротация 10); ручные снимки этой сессии — `~/sp-live-backups/`.
- **Восстановление при беде**: положить бэкап-файл на WebDAV как `sync-data.json` (с префиксом `pf_2__` как есть) — но помнить: регресс syncVersion вызовет полный ресинк/конфликт-диалог на телефоне; лучший путь — новые корректирующие операции через CLI.
- **`sp doctor`** — первая команда при любых странностях: проверяет ~30 инвариантов консистентности файла.
- Известный баг окружения: **браузерные E2E на этой Pi невозможны** (Chromium не грузит страницы; chrome-devtools-axi несовместим/требует Chrome) — см. память проекта.
- История работ: `.yoke/journal.md`; артефакты флоу — `.yoke/ai/*/` (планы, research, отчёты).

## 6. Хронология коммитов (основные)

`f07228f` bootstrap yoke → `d3ef2cc` план+research MVP → `0c6e9a8` реализация sp_cli → `cc356d0` фиксы ревью → `dd3c79b` README → батч-2: `c5ca407` research → `e1e5070` 15 планов → 15× `feat(b2-…)` + 10× `fix(b2-…)` → `264852c` журнал. Всё в `origin/main`.
