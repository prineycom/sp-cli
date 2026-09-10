# План b2-06-issue-providers — интеграции (календарь ICAL/CalDAV)

Research: `.yoke/ai/batch2-research/research-entity-areas.md` §5. Ops: IA/IU/IS (e=ISSUE_PROVIDER), удаление HID/HIM. Cfg-поля ПЛОСКО на объекте провайдера.

## Команды

- `sp providers [--json]` — список: id, key, enabled, url (icalUrl/caldavUrl), defaultProject, autoImport.
- `sp provider add-ical <url> [--auto-import] [--project P] [--tag T...] [--check-every 2h] [--include-regex --exclude-regex]` — IA CRT `{issueProvider: <full>}`: `{...COMMON_CFG, ...DEFAULT_CALENDAR_CFG, id: nanoid, issueProviderKey: 'ICAL', isEnabled: true, icalUrl: url, isAutoImportForCurrentDay: bool, checkUpdatesEvery: ms, showBannerBeforeThreshold: 7200000, defaultProjectId: <id|null>, defaultTagIds: [...]}`. COMMON: `{isAutoPoll: true, isAutoAddToBacklog: false, isIntegratedAddTaskBar: false, defaultProjectId: null, pinnedSearch: null, pollingMode: 'whenProjectOpen', defaultTagIds: [], defaultNote: null}`. Полный cfg обязателен (typia). State: append ids/entities. Печатает id. Задачи из календаря создаёт само приложение (`cal_*`) — CLI их НЕ синтезирует.
- `sp provider add-caldav --url U --resource R --username --password [--category-filter] [...общие]` — IA с CALDAV cfg (`{caldavUrl, resourceName, username, password, categoryFilter: null, isAddSubTasks: false?, twoWaySync: {isDone: 'pullOnly', title: 'pullOnly', notes: 'off'}}` поверх дефолтов). **Обязателен явный флаг `--store-plaintext-credentials`**, иначе отказ с объяснением (пароль ляжет открытым текстом в sync-файл).
- `sp provider edit <id> [--enable|--disable] [--url] [--auto-import/--no-auto-import] [--project|--no-project] [--check-every]` — IU UPD `{issueProvider: {id, changes}}`.
- `sp provider rm <id> [--yes]` — HID DEL `{issueProviderId, taskIdsToUnlink}`. taskIdsToUnlink = все задачи (live + archiveYoung + archiveOld) с issueProviderId==id. State mirror: удалить провайдера; на каждой такой задаче (live И в архивных блобах) убрать поля issueId/issueProviderId/issueType/issueWasUpdated/issueLastUpdated/issueAttachmentNr/issueTimeTracked/issuePoints.
- `sp provider order <id...>` — IS MOV `{ids}` (listed first, хвост сохраняется).

Не трогаем: plugin:*-провайдеры, dismissedCalendarAutoImportEventIdsByProvider.

## Тесты
- add-ical: полный объект (все ключи cfg), op shape.
- add-caldav: отказ без --store-plaintext-credentials.
- edit/order; rm: taskIdsToUnlink собран из live+архивов, поля вычищены везде.

## DoD
ICAL-календарь подключается из CLI; unit зелёные.
