# sp-cli

CLI для управления Super Productivity через WebDAV (`sync-data.json`), минуя приложение.

## Контекст

SP установлен на телефоне, синхронизируется через WebDAV-сервер на Pi (`:8091`, префикс `pf_2__`). Нативного CLI у SP нет, мобильные плагины не подходят. Этот CLI даёт программный доступ к задачам/проектам/тегам снаружи — для автоматизации, cron-джоб и интеграции с агентом Hermes.

## Документация

- [docs/sp-plugin-contracts.md](docs/sp-plugin-contracts.md) — контракты методов (из ai-assistant-plugin) + матчинг с полями sync-data.json + sync-слой (vectorClock, recentOps)
- [docs/sample-sync-data.json](docs/sample-sync-data.json) — образец реального sync-файла (onboarding-стейт, 2026-08-04)
- Gist: https://gist.github.com/prineycom/16aaba3c8e625fa0339e86e32068d6aa

## Архитектура

```
CLI (Python) → WebDAV (GET/PUT /superproductivity/sync-data.json) → телефон подтягивает синком
```

Каждая операция:
1. GET sync-data.json (strip `pf_2__`)
2. Бэкап во временный файл
3. Мутация `state.*` + обновление `recentOps`, `vectorClock`, `lastModified`, `clientId`
4. PUT обратно (re-add `pf_2__`)

## Статус

- [x] Research: структура sync-data.json, контракты операций, sync-слой
- [ ] MVP: read-операции (list, get) + базовые write (add, complete)
- [ ] Sync-слой (vectorClock, recentOps)
- [ ] YouTrack-мост (опционально)

## ⚠️ Важно

- Оптимистичный локинг: один клиент — телефон, второй — CLI. Конфликты возможны при одновременной записи.
- Перед каждым PUT — бэкап.
- Клиент ID CLI не должен совпадать с телефоном.