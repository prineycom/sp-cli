# sp-cli

CLI (Python 3.11+, stdlib + `requests`) для управления Super Productivity через WebDAV-файл `sync-data.json`, минуя приложение. Целевая инсталляция: rclone WebDAV в docker на этой же машине (`http://localhost:8091`, каталог `/superproductivity/`), синхронизируется телефон (Android SP).

## Команды

- Запуск: `python3 -m sp_cli ...` или `./sp` (обёртка)
- Тесты: `python3 -m pytest tests/ -q`
- Юнит-тесты не трогают сеть; интеграционные — только против тестовой копии файла, живой файл руками не портить.

## Архитектура

- `sp_cli/webdav.py` — GET/PUT sync-data.json, префикс `pf_2__`, бэкапы
- `sp_cli/model.py` — типизированные фабрики сущностей SP (task/project/tag), дефолты строго как в исходниках SP
- `sp_cli/ops.py` — operation log: recentOps, vectorClock, action-коды SP
- `sp_cli/mutations.py` — чистые мутации state (create/update/delete/plan/...)
- `sp_cli/cli.py` — argparse-команды

## Критичные инварианты

- Каждая write-операция обязана: инкрементить vectorClock своего clientId, аппендить корректный op в `recentOps`, обновить `lastModified` и топ-левел `clientId`. Иначе телефон не подхватит или конфликтнёт.
- clientId CLI уникален и никогда не совпадает с телефоном.
- Перед PUT — бэкап в `~/.local/share/sp-cli/backups/` (ротация 10).
- Время — unix ms; даты — строки `YYYY-MM-DD`; id — nanoid(21) как в SP.
- Документация формата: `docs/sp-plugin-contracts.md`, исследование синка — `.yoke/ai/*/research-*.md`.

## Язык

Общение и доки — русский; код, идентификаторы и commit messages — английский.
