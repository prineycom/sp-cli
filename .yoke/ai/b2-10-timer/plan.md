# План b2-10-timer — live-таймер

Research: `.yoke/ai/batch2-research/research-task-methods.md` §1. currentTaskId НЕ синкается (нет op-представления) — таймер локальный, синкается только натиканное время (KT).

## Механика

Локальный файл `~/.local/share/sp-cli/timer.json`: `{"task_id": ..., "title": ..., "started_at": ms}` (атомарная запись). Никаких op'ов на start.

## Команды

- `sp start <task-id>` — валидирует задачу (существует, не done); если таймер уже идёт — сначала автостоп (записать время старого), потом старт нового. Пишет timer.json. Без сетевых записей (только GET для валидации).
- `sp stop` — если таймер не идёт: ошибка exit 2. Иначе elapsed = now - started_at; если elapsed < 60s — предупредить и списать 1m (или `--discard` для отмены без записи). Запись: существующий механизм track_time → 1 op KT `{taskId, date: <день старта? нет — сегодня>, duration}` + entityChanges mirror + state timeSpentOnDay/timeSpent. Пересечение полуночи: разбить на два KT по дням. Удалить timer.json. Показать итог.
- `sp start` при запущенном — показывает текущий (алиас поведения `sp current`).
- `sp current [--json]` — что тикает: задача, started_at, elapsed. Если нет — "no timer".
- `sp stop --discard` — сброс без записи времени.

Дополнительно: `sp track <id> -- отрицательное` не поддерживаем; вместо этого `sp untrack <id> <duration> [--date]` — op TR `{id, date, duration}` (payload key `id`!), state: max(x-duration,0) + timeSpent recompute. (Коррекция переучтённого времени.)

## Тесты
- start/stop: timer.json жизненный цикл (tmp_path + monkeypatch путей), KT op при stop, автостоп при повторном start.
- Полуночное разбиение: started вчера 23:50, stop 00:20 → два KT (10m вчера, 20m сегодня).
- untrack: TR op shape (key id), clamp на 0.
- current: вывод и exit-коды.

## DoD
start → работа → stop даёт корректный KT; unit зелёные.
