# Batch2 — бэклог минорных замечаний ревью (не блокируют, чинить пакетно)

## b2-04-metrics (ревью ✅, миноры)
1. cli cmd_metric_focus: `count[0]` → `count[-1]` (на 412-retry список накапливается).
2. format_duration(24s) → "-" в выводе focus; отображение, не баг.
3. parse_duration без секунд — суб-минутные сессии не задать точно (ограничение, задокументировать).
4. queries.focus_sessions / render.metric_rows: бережный int() (corrupt элемент → traceback); doctor не проверяет типы элементов focusSessions.
5. doctor: range-check impactOfWork/energyCheckin + форма reflections на существующих данных.
6. Нет способа сбросить impact/energy в null: добавить --clear-impact/--clear-energy.
7. help metric-focus --day: упомянуть today/tomorrow/+N; metric_delete без _day_key валидации (безвредно).

## b2-11-backlog (ревью ✅, миноры)
1. `_move_item_after_anchor(X, after=X)` → ValueError вместо no-op (порядок проверок: удалять до поиска якоря, как SP).
2. `sp backlog add A B C` даёт порядок C,B,A (prepend по одному) — чейнить afterTaskId.
3. `project edit --title X --enable-backlog` шлёт два PU — слить в один changes.
4. doctor: backlogTaskIds непуст при isEnableBacklog=false — флажить.
5. doctor: exactly-one-of только внутри проекта; нет кросс-проектной проверки, projectId-консистентности бэклога, дублей в списках.
6. backlog_clear дедупит (SP — нет); расхождение только при уже сломанном инварианте.
7. move_to_project same-project вытаскивает из бэклога (SP сохраняет) — pre-existing, стало наблюдаемым.
8. --backlog+--due запрет не задокументирован в README/help.
9. backlog_list молча скипает висячие id, today_list кидает — унифицировать.
10. Нет тестов на missing-anchor ветки _move_item_after_anchor (пригодятся для PM/WM в b2-15).

## b2-12-archive-restore (ревью ✅, 3 Important — в фикс, миноры сюда)
- IMPORTANT (в фикс): logical_day_of_ms для сравнения dueWithTime-сегодня; startOfNextDayDiffMs из model в payload restoreToToday; dueDay=today ставится БЕЗУСЛОВНО (SP-семантика) — согласовать plan_today + doctor (both-set легален, если dueDay == day_of(dueWithTime)).
4. repeatCfgId-очистка и на сабтасках.
5. Отсутствие INBOX_PROJECT → деградировать, не падать.
6. Уже живой сабтаск → фильтровать и адоптить (SP), не hard error.
7. removeTasksFromAllProjects: все проекты, оба списка, включая сабтаск-id.
8. queries.archived_task — мёртвый код.
9. restored.extend в retry-замыкании — печатать из последнего прогона.
10. Тесты: 4-й блоб (state.archiveYoung), payload при dueWithTime-сегодня, ненулевой startOfNextDayTime.

## b2-15-reorder-convert (ревью ✅, миноры не в фикс)
4. Legacy-shim op reassertOwnTagsAfterConvert (для клиентов ≤18.20.1) не эмитим — сознательное упущение, задокументировано здесь.
6a. promote выполненного сабтаска перепланирует его на сегодня (SP-ветка) — упомянуть в help.
6b. move-in-project done-задачи с null-якорем препендит (src/target UNDONE hardcoded) — косметика, CLI и девайсы согласны.

## b2-16-attachments (ревью ✅, миноры)
1. title сохраняет расширение (SP отрезает) — сознательный выбор.
2. Относительные/~ пути не резолвятся в абсолютные (мертвая ссылка в приложении) — добавить expanduser/abspath.
3. /path/shot.png → IMG, а не FILE (SP: файл-дроп всегда FILE) — планово, деградация мягкая.
4. `sp attach T ""` — пустой path, добавить guard.
5. COMMAND/NOTE без иконок в ATTACHMENT_ICONS (латентно, CLI не даёт).
6. XA payload не проверен точным dict; нет assert entityChanges==[].
7. _attachment_list мутирует state на read-путях.
8. attach T --title -x криво риврайтится (узко).

## Из ревью b3-mcp-agents (2026-09-10)

- `_ctx()` в cli.py создаёт `requests.Session` на каждый вызов и никогда не
  закрывает — для one-shot CLI неважно, но долгоживущий MCP-сервер оставляет
  сокеты до GC. Фикс: context-manager вокруг `_ctx` или `session.close()` в
  конце команды.
