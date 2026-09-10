# План b3-mcp-agents — MCP-сервер + skill для интеграции sp-cli в агентов

Цель: любой MCP-совместимый агент (Claude Code, Codex, Cursor, ...) получает полный
функционал sp-cli как инструменты + skill с рабочими практиками.

## Декомпозиция

- **b3-01 mcp-core** — `sp_cli/mcp_server.py`: stdio JSON-RPC 2.0 (ndjson), методы
  `initialize` / `notifications/initialized` / `ping` / `tools/list` / `tools/call`.
  Протокол `2025-06-18` (эхо версии клиента, если поддерживаем). Чистый stdlib.
- **b3-02 introspection + bridge** — генерация схем инструментов интроспекцией
  `cli.build_parser()`: каждая подкоманда → инструмент `sp_<name с _>`;
  argparse-actions → JSON Schema (store_true→boolean, append/nargs→array,
  type=int→integer, choices→enum, positional→required). Обратный мост:
  arguments → argv → `cli.main(argv)` in-process, capture stdout/stderr,
  подмена stdin пустым потоком (EOFError → «pass yes: true»), SystemExit
  argparse → isError. Аннотации readOnlyHint/destructiveHint. Фильтры
  `--include/--exclude` (fnmatch) против раздувания контекста клиента.
- **b3-03 tests** — unit: полнота покрытия (все подкоманды парсера присутствуют
  как инструменты), корректность схем, argv round-trip, guard подтверждений.
  Интеграция: локальный stdlib-HTTP «WebDAV» (ETag/If-Match/412) с копией
  sample-sync-data.json + сервер сабпроцессом: handshake → tools/list →
  read (list --json) → write (add: vectorClock+recentOps в PUT-теле) →
  delete без yes (ошибка) и с yes (успех). Сеть — только loopback.
- **b3-04 skill + docs** — `integrations/skill/superproductivity/SKILL.md`
  (Agent Skills, англ.), симлинк `.claude/skills/superproductivity`,
  `integrations/mcp/README.md` + `docs/mcp-integration.md` (рус.): регистрация
  в Claude Code (`claude mcp add`), generic `mcpServers`-JSON, прочие агенты.
  Обёртка `./sp-mcp`, апдейт README.md и CLAUDE.md.

## Инварианты

- Сервер не изобретает свою запись: только существующий путь
  config→SyncStore→mutations→ops, т.е. все инварианты vectorClock/recentOps/
  бэкапов наследуются от CLI автоматически.
- stdin процесса — канал JSON-RPC; ни одна команда не должна из него читать.
- stdout — только JSON-RPC; любые print команд перехватываются в буфер.
- Никаких новых зависимостей.

## DoD

Полный тестовый прогон зелёный (юниты + интеграция без сети, кроме loopback);
e2e-цепочка через субпроцесс подтверждает запись с корректным op-log;
skill валиден (frontmatter name/description); direct-push в main; журнал.
