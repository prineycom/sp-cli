# План b2-01-notes — заметки

Research: `.yoke/ai/batch2-research/research-entity-areas.md` §1. Ops: NA/NU/ND/NO/NM (e=NOTE).

## Команды

- `sp notes [--project X] [--today] [--json]` — список (id, first line of content, project, pinned). `--today` = todayOrder порядок.
- `sp note add "content" [--project X] [--pin]` — NA CRT `{note: <full>}`. Note: `{id: nanoid, projectId: <resolved|null>, isPinnedToToday: bool, content, created: now, modified: now}`. State: entities set, **ids PREPEND**; pinned → todayOrder PREPEND; projectId → project.noteIds PREPEND. Печатает id.
- `sp note show <id>` — полный контент.
- `sp note edit <id> [--content "..."|--append "..."] [--pin|--unpin] [--color '#hex']` — NU UPD `{note: {id, changes}}`. State: patch + modified; isPinnedToToday в changes → todayOrder prepend/filter (только если ключ присутствует).
- `sp note rm <id> [--yes]` — ND DEL `{id, projectId, isPinnedToToday}` (значения ИЗ текущей заметки). State: removeOne, filter todayOrder, filter project.noteIds.
- `sp note move <id> --project Y` — NM UPD `{note: <full со СТАРЫМ projectId>, targetProjectId}`. State: projectId=target; source project.noteIds filter, target **append**. Ошибка если src==target.

id-резолвинг: префикс, как resolve_task (отдельный resolve_note).

## Инварианты
- Никогда не оставлять dangling id в note.ids/todayOrder/project.noteIds (repair кидает Error).
- ND payload несёт projectId/isPinnedToToday для кросс-редьюсеров — брать из состояния до удаления.

## Тесты (unit, в стиле tests/test_mutations.py)
- add: op shape (a/o/e/d, payload keys), ids prepend, project.noteIds prepend, pinned→todayOrder.
- edit: pin/unpin двигает todayOrder только при наличии ключа; контент-патч не трогает.
- rm: payload с актуальными projectId/isPinnedToToday; все три списка чистятся.
- move: старый projectId в payload, порядок списков (filter/append).
- doctor: расширить проверками note-инвариантов (ids↔entities, project.noteIds refs, todayOrder refs).

## DoD
Полный жизненный цикл заметки из CLI; unit-тесты зелёные; doctor чист.
