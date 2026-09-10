# Batch 2 — дорожная карта (заказ Паши, 2026-09-09)

Каждый пункт: отдельный план (`.yoke/ai/<slug>/plan.md`) → отдельный do (executor на Opus) → ревью → фиксы → свои коммиты → живой тест на проде (проверка синком телефона). Общий research: `.yoke/ai/batch2-research/`.

| # | Slug | Что |
|---|---|---|
| 1 | b2-01-notes | Заметки: CRUD, привязка к проекту, пин в Today |
| 2 | b2-02-boards | Доски: CRUD досок и панелей |
| 3 | b2-03-counters | Счётчики/привычки (simpleCounter) |
| 4 | b2-04-metrics | Метрики/оценка дня |
| 6 | b2-06-issue-providers | Интеграции issueProvider (вкл. календарь ICAL/CalDAV) |
| 10 | b2-10-timer | Live-таймер start/stop/current |
| 11 | b2-11-backlog | Бэклог проекта: move to/from, порядок |
| 12 | b2-12-archive-restore | Восстановление из архива + обслуживание |
| 13 | b2-13-hard-deletes | Полное удаление проекта/тега/repeat-конфига |
| 14 | b2-14-repeat-edit | Repeat: edit/pause, skip инстансов |
| 15 | b2-15-reorder-convert | Переупорядочивание Today/planner/subtasks, конвертация sub↔main |
| 16 | b2-16-attachments | Вложения задач |
| 17 | b2-17-deadline-clear | Снятие дедлайна, dismiss напоминания |
| 18 | b2-18-shortsyntax | shortSyntax-парсер в `sp add` |
| 19 | b2-19-bulk-update | Bulk-update задач |

Порядок исполнения — последовательный (общие файлы mutations.py/cli.py). Финал: сводный живой тест по всем пунктам + чеклист для телефона.
