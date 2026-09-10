# Интеграция sp-cli в агентов: MCP-сервер + skill

Два артефакта, готовые к подключению в любой агент:

- **MCP-сервер** — `sp_cli/mcp_server.py` (обёртка `./sp-mcp`). Stdio JSON-RPC,
  протокол MCP `2025-06-18`, чистый stdlib. Инструменты генерируются
  интроспекцией argparse-парсера CLI, поэтому покрытие всегда 1:1 со всеми
  командами `sp` (сейчас 97), включая будущие.
- **Skill** — `integrations/skill/superproductivity/SKILL.md` (формат
  [Agent Skills](https://agentskills.io): frontmatter `name`/`description` +
  инструкции). Учит агента рабочим приёмам: id-префиксы, `json=true`,
  short syntax, `yes=true` для деструктивных операций, рецепты.

## Требования

Настроенный конфиг sp-cli (`sp init --url ... --user ... --password ...`,
живёт в `~/.config/sp-cli/config.toml`, переопределяется env `SP_CLI_CONFIG`).
Сервер использует ровно тот же путь записи, что и CLI: op-log, vectorClock,
бэкапы и retry конфликтов наследуются автоматически.

## Подключение MCP-сервера

Команда запуска везде одна: `/абсолютный/путь/к/sp-cli/sp-mcp`
(или `python3 -m sp_cli.mcp_server` с cwd в корне репозитория).

**Claude Code:**

```bash
claude mcp add superproductivity -- /home/priney/repos/sp-cli/sp-mcp
```

**Любой клиент с `mcpServers`-конфигом** (Cursor, VS Code, Claude Desktop,
Windsurf, ...):

```json
{
  "mcpServers": {
    "superproductivity": {
      "command": "/home/priney/repos/sp-cli/sp-mcp",
      "args": []
    }
  }
}
```

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.superproductivity]
command = "/home/priney/repos/sp-cli/sp-mcp"
```

### Урезание набора инструментов

97 инструментов — заметный кусок контекста клиента. Если агенту нужна только
часть функционала, набор режется glob-фильтрами:

```bash
sp-mcp --exclude 'provider*' --exclude 'board*' --exclude 'counter*'
sp-mcp --include 'list' --include 'show' --include 'add' --include 'today*'
sp-mcp --list-tools        # посмотреть итоговый набор без запуска сервера
```

Разумный минимум для «агента-ассистента по задачам»: `--exclude 'provider*'
--exclude init` (перенастройка конфига и календарей агенту обычно не нужна;
`init` и `untrack` на всякий случай помечены `destructiveHint`).

## Подключение skill

Скопировать (или засимлинкать) каталог `integrations/skill/superproductivity/`
в каталог скиллов агента:

- Claude Code (проект): `.claude/skills/superproductivity/` — в этом
  репозитории симлинк уже стоит;
- Claude Code (глобально): `~/.claude/skills/superproductivity/`;
- другие агенты с поддержкой Agent Skills — их каталог скиллов.

Skill не зависит от MCP: он описывает и инструменты `sp_*`, и голый CLI `sp`,
так что полезен и агенту с одним лишь shell-доступом.

## Контракт инструментов

- Имя: `sp_<команда>` c `_` вместо `-` (`sp board-panel-add` → `sp_board_panel_add`).
- Схема аргументов повторяет argparse: позиционные — required, `store_true` —
  boolean, `append`/`nargs=+` — array, `choices` — enum, `type=int` — integer.
- Результат — текстовый вывод команды; при ненулевом exit code — `isError` с
  stderr.
- Команды с интерактивным подтверждением никогда не читают stdin (он занят
  протоколом): без `yes=true` возвращается ошибка с подсказкой.
- Аннотации `readOnlyHint`/`destructiveHint` проставлены — клиенты могут
  автоматически разрешать чтение и требовать подтверждение на удаления.
