# Yoke context — sp-cli

## Stack

- Python 3.11+ (Raspberry Pi, Linux arm64), зависимости: stdlib + `requests`.
- Тесты: pytest.
- Пакетирование: без сборки, запускается как модуль/скрипт.

## Environment

- WebDAV: rclone serve webdav в docker (`prineyai-dashboard-jeflfi-superproductivity-webdav-1`), host-порт `8091` → контейнер `8080`, корень `/data`, basic auth (`sp` / пароль в конфиге CLI).
- Файл: `/superproductivity/sync-data.json`, формат `pf_2__` + JSON, schemaVersion 4, version 2.
- Синхронизирующийся клиент: телефон Android SP, clientId `I_jHradR`.
- Конфиг CLI: `~/.config/sp-cli/config.toml`; бэкапы: `~/.local/share/sp-cli/backups/`.

## Conventions

- Общение/доки — русский, код и коммиты — английский.
- Conventional commits (`feat:`, `docs:`, `chore:`).
