# Отчёт выполнения — sp-cli MVP (полное покрытие задач/дня/календаря)

Дата: 2026-09-09. Режим: sub-agents (yoke:do), автономный прогон.

## Статусы задач

| Этап | Статус | Коммит |
|---|---|---|
| Research: sync-движок SP (op-log, vectorClock, валидация) | DONE (сабагент) | d3ef2cc |
| Research: модель данных (Task/Today/planner/schedule/repeat) | DONE (сабагент) | d3ef2cc |
| План | DONE | d3ef2cc |
| Реализация пакета `sp_cli` + unit-тесты | DONE (сабагент-исполнитель) | 0c6e9a8 |
| Ревью (сабагент): ✅ approved, 2 Important + 8 Minor | DONE | — |
| Фиксы по ревью (сабагент) | DONE | cc356d0 |
| Полиш: пустые дни planner | DONE | 733adc2 |
| README | DONE (сабагент) | см. git log |
| Интеграционный свип (тестовый WebDAV, копия live) | DONE — ~30 команд, syncVersion 3→28, doctor чист | — |
| Живой прогон (:8091) | DONE — 19 write-батчей, syncVersion 3→20, структурная валидация пройдена | — |
| E2E через SP web app в браузере | ОТМЕНЁН — headless Chromium на Pi не грузит страницы (env-баг); по решению пользователя проверка на телефоне | — |

## Валидация

- `python3 -m pytest tests/ -q` — **131 passed**.
- Интеграционный свип: project/tag CRUD, add/subtask/edit/tag±/schedule/unschedule/deadline/plan/today±/track/complete/reopen/repeat/move/reorder/worklog/archive/delete — всё зелёное, файл после — `doctor` чист, op-envelope и клоки проверены скриптом.
- Живой файл: 12 op телефона нетронуты, 18 op CLI (uuid7, s=4, монотонные клоки, sv возрастают), все инварианты стейта соблюдены.

## Замечания

- Ревью-финдинги №1 (HDM без сабтасков) и №2 (PUT без If-Match) были реальными угрозами потери данных — исправлены и покрыты тестами.
- Деплой = git push в main (flow: direct-push).
