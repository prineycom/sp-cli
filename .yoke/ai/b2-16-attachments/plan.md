# План b2-16-attachments — вложения задач

Research: `.yoke/ai/batch2-research/research-task-methods.md` §7. Ops XA/XU/XD (e=TASK, d=taskId, o=UPD все три). Никогда HU changes.attachments.

## Команды

- `sp attach <task-id> <url-or-path> [--title "..."] [--type link|img|file]` — XA UPD `{taskId, taskAttachment: {id: nanoid, type: 'LINK'|'IMG'|'FILE', path: url, title: <title|basename>, icon: bookmark|image|insert_drive_file}}`. Тип авто: image-расширение → IMG, file:// → FILE, иначе LINK. State: task.attachments append. Гард: задача существует (редьюсер кидает).
- `sp attachments <task-id> [--json]` — список вложений (id, type, title, path). Также показывать в `sp show`.
- `sp attach edit <task-id> <attach-id> [--title --path]` — XU UPD `{taskId, taskAttachment: {id, changes}}`. State: shallow-merge по id.
- `sp attach rm <task-id> <attach-id>` — XD UPD `{taskId, id}`. State: filter.

attach-id резолвить по префиксу внутри задачи.

## Тесты
- XA: op shape, авто-тип/иконка/title-basename, append.
- XU/XD: точечные изменения.
- Гард несуществующей задачи.
- `sp show` выводит вложения.

## DoD
Вложения полный CRUD; unit зелёные.
