# План b2-02-boards — доски

Research: `.yoke/ai/batch2-research/research-entity-areas.md` §2. Ops: BA/BU/BD/BT/BS (e=BOARD). **BP никогда** (мёртвый редьюсер). state.boards = {boardCfgs: []} — массив, не registry.

## Команды

- `sp boards [--json]` — список досок с панелями (id, title, cols, панели: id/title/фильтры кратко).
- `sp board add "Title" [--cols N]` — BA CRT `{board: {id: nanoid, title, cols: N|2, panels: []}}`. State: append.
- `sp board edit <id> [--title --cols]` — BU UPD `{id, updates}`.
- `sp board rm <id> [--yes]` — BD DEL `{id}`.
- `sp board panel add <board-id> "Title" [--tags a,b] [--exclude-tags c] [--project P|--all-projects] [--done all|done|undone] [--scheduled all|scheduled|not] [--backlog all|no|only] [--parents-only] [--sort dueDate|created|title|timeEstimate --dir asc|desc]` — строит панель от DEFAULT_PANEL_CFG (id: nanoid, taskIds: [], enums числами: All=1...), санитизированную; эмитит **BU** `{id: boardId, updates: {panels: <вся новая panels-массив>}}`.
- `sp board panel edit <panel-id> [...те же флаги, --title]` — найти доску по panel-id, переписать panels через BU.
- `sp board panel rm <panel-id>` — BU с panels без панели.
- `sp board panel order <panel-id> <task-id...>` — BT UPD `{panelId, taskIds}` (только порядок! членство определяется фильтрами — предупредить в help).
- `sp board sort <id...>` — BS MOV `{ids}` ds=ids.

## Санитизация панелей (обязательно перед записью)
projectIds: массив; содержит '' → ровно ['']; без legacy projectId/sortByDue; sortBy только из 4 значений или отсутствует; без null sortDir/matches; panel id глобально уникален. Теги в фильтрах — резолвить имена в id; TODAY в includedTagIds не класть.

## Тесты
- add/edit/rm board: op shapes, массив boardCfgs.
- panel add: BU с полной panels, дефолты панели, санитизация ('' + real ids → ['']), enum-значения.
- BT: только taskIds панели; BS: перестановка с хвостом нетронутых.
- Проверка что BP нигде не эмитится.

## DoD
CRUD досок и панелей; unit зелёные.
