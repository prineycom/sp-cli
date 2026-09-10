"""State mutations. Each function applies a logical change to d['state']
(and top-level archives where relevant) AND appends the matching op(s) via
the OpBuilder — keeping the snapshot contract: state always reflects the
effect of every op in recentOps."""

from __future__ import annotations

import copy
import datetime

from sp_cli.model import (
    INBOX_PROJECT_ID,
    ISSUE_TASK_FIELDS,
    METRIC_DEAD_FIELDS,
    METRIC_FIELDS,
    MONTHLY_ANCHOR_FIELDS,
    TODAY_TAG_ID,
    archive_task_blobs,
    archive_task_entity_maps,
    day_of_ms,
    logical_day_of_ms,
    logical_today_str,
    make_metric,
    make_repeat_cfg,
    now_ms,
    sanitize_panel,
    start_of_next_day_diff_ms,
    task_with_subtasks,
    today_str,
)
from sp_cli.ops import OpBuilder


class MutationError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def _state(d: dict) -> dict:
    return d["state"]


def _task(state: dict, task_id: str) -> dict:
    try:
        return state["task"]["entities"][task_id]
    except KeyError:
        raise MutationError(f"task not found: {task_id}") from None


def _project(state: dict, project_id: str) -> dict:
    try:
        return state["project"]["entities"][project_id]
    except KeyError:
        raise MutationError(f"project not found: {project_id}") from None


def _require_backlog(project: dict) -> None:
    """PRB/PAB and the isAddToBacklog flag are SILENT no-ops on devices when
    the project has no backlog — refuse instead of shipping a dead op."""
    if not project.get("isEnableBacklog"):
        raise MutationError(
            f"project {project['id']} has no backlog "
            f"(enable it: sp project edit {project['id']} --enable-backlog)"
        )


def _tag(state: dict, tag_id: str) -> dict:
    try:
        return state["tag"]["entities"][tag_id]
    except KeyError:
        raise MutationError(f"tag not found: {tag_id}") from None


def _reg_add(registry: dict, entity: dict) -> None:
    eid = entity["id"]
    if eid in registry["entities"]:
        raise MutationError(f"entity already exists: {eid}")
    registry["ids"].append(eid)
    registry["entities"][eid] = entity


def _reg_remove(registry: dict, entity_id: str) -> None:
    registry["entities"].pop(entity_id, None)
    if entity_id in registry["ids"]:
        registry["ids"].remove(entity_id)


def _list_remove(lst: list, value) -> None:
    while value in lst:
        lst.remove(value)


def _move_item_after_anchor(lst: list, item_id: str, after_id: str | None) -> None:
    """SP's `moveItemAfterAnchor`: filter the id out, then insert after the
    anchor. `after_id` None prepends; a missing anchor is a no-op when the
    item was already in the list, an append otherwise. Replay-idempotent."""
    if after_id is None:
        _list_remove(lst, item_id)
        lst.insert(0, item_id)
        return
    if after_id not in lst:
        if item_id in lst:
            return
        lst.append(item_id)
        return
    _list_remove(lst, item_id)
    lst.insert(lst.index(after_id) + 1, item_id)


# ---------------------------------------------------------------- sections
# SP's Section (features/section/section.model.ts) is
#   {id, contextId, contextType: 'PROJECT'|'TAG', title, isExpanded?, taskIds}
# — there is NO `projectId` on a section and NO `sectionId` on a task:
# membership lives ONLY in `section.taskIds`. Sections are mirrored (never
# emitted): the op we send re-runs section-shared.reducer.ts on every device,
# so our snapshot has to end up where theirs does.


def _sections(state: dict) -> list[dict]:
    reg = state.get("section")
    if not isinstance(reg, dict):
        return []
    entities = reg.get("entities")
    if not isinstance(entities, dict):
        return []
    return [s for s in entities.values() if isinstance(s, dict)]


def _section_task_ids(section: dict) -> list | None:
    ids = section.get("taskIds")
    return ids if isinstance(ids, list) else None


def _section_remove_task_ids(
    state: dict,
    task_ids: list[str],
    context_type: str | None = None,
    context_id: str | None = None,
) -> None:
    """`cleanupSectionTaskIds` (both context filters None) and the
    `removeTaskIdsFrom{Project,Today}Sections` variants: strip the ids from
    every matching section's `taskIds`."""
    if not task_ids:
        return
    for section in _sections(state):
        if context_type is not None and section.get("contextType") != context_type:
            continue
        if context_id is not None and section.get("contextId") != context_id:
            continue
        ids = _section_task_ids(section)
        if ids is None:
            continue
        for tid in task_ids:
            _list_remove(ids, tid)


def _section_reorder_task(
    state: dict, context_type: str, context_id: str, task_id: str, apply_move
) -> None:
    """`reorderTaskInContextSections`: the work-context move that reorders
    project/tag `taskIds` also reorders the task inside the section that holds
    it for that context — same reducer pass, same `updateOrder` closure."""
    for section in _sections(state):
        if section.get("contextType") != context_type:
            continue
        if section.get("contextId") != context_id:
            continue
        ids = _section_task_ids(section)
        if ids is None or task_id not in ids:
            continue
        apply_move(ids)


def _today_order(state: dict) -> list:
    """The TODAY tag's ordering list; degrades to a detached no-op list
    when the TODAY tag is missing (matching queries.py's tolerance)."""
    tag = state["tag"]["entities"].get(TODAY_TAG_ID)
    if tag is None:
        return []
    return tag.setdefault("taskIds", [])


def _today_prepend(state: dict, task_ids: list[str]) -> None:
    order = _today_order(state)
    for tid in reversed(task_ids):
        _list_remove(order, tid)
        order.insert(0, tid)


def _planner_purge(state: dict, task_ids: list[str]) -> None:
    days = state.get("planner", {}).get("days", {})
    for day, day_list in list(days.items()):
        for tid in task_ids:
            _list_remove(day_list, tid)
        if not day_list:
            del days[day]


def _clear_due_with_time(task: dict, changes: dict | None = None) -> None:
    task["dueWithTime"] = None
    task["remindAt"] = None
    if changes is not None:
        changes["dueWithTime"] = None
        changes["remindAt"] = None


def _should_clear_due_time_for_today(
    due_with_time: int | None, today: str, d: dict | None
) -> bool:
    """SP's `shouldClearDueTimeForToday` (util/is-today.util.ts): when a task is
    (re)planned onto `today`, its `dueWithTime` is kept only if that timestamp
    falls on the SAME logical day (offset-aware); a missing time is 'nothing to
    clear', a corrupt one is cleared instead of blowing up."""
    if due_with_time is None:
        return False
    try:
        ts = int(due_with_time)
    except (TypeError, ValueError):
        return True
    if ts <= 0:
        return True
    try:
        return logical_day_of_ms(ts, d) != today
    except (OverflowError, OSError, ValueError):
        return True


def _plan_for_today(task: dict, today: str, d: dict | None) -> None:
    """SP's planTasksForToday update, per task: `dueDay` is set to today
    UNCONDITIONALLY, `remindAt` is ALWAYS cleared, and `dueWithTime` survives
    when it already points at today — so a task may legitimately carry BOTH
    dueDay=today and a same-day dueWithTime."""
    task["dueDay"] = today
    task["remindAt"] = None
    if _should_clear_due_time_for_today(task.get("dueWithTime"), today, d):
        task["dueWithTime"] = None
    else:
        task.setdefault("dueWithTime", None)


# ---------------------------------------------------------------- tasks

def add_task(d: dict, b: OpBuilder, task: dict, to_backlog: bool = False) -> str:
    """Create a top-level task (already built via model.make_task).

    `to_backlog` puts it straight into the project's backlog — SP's reducer
    silently ignores that flag when the project has no backlog enabled, so
    the guard is enforced here instead."""
    state = _state(d)
    if TODAY_TAG_ID in task.get("tagIds", []):
        raise MutationError("'TODAY' must never appear in task.tagIds")
    # A CLI-level rule, not a data invariant: SP itself may hold both (planning
    # onto today keeps a same-day time), but asking for both at CREATION time is
    # an ambiguous request, so it is refused here.
    if task.get("dueDay") is not None and task.get("dueWithTime") is not None:
        raise MutationError("dueDay and dueWithTime are mutually exclusive")
    project = _project(state, task["projectId"])
    if to_backlog:
        _require_backlog(project)
    for tag_id in task.get("tagIds", []):
        _tag(state, tag_id)  # validate existence

    b.op(
        "HA",
        "CRT",
        "TASK",
        task["id"],
        {
            "task": copy.deepcopy(task),
            "workContextId": task["projectId"],
            "workContextType": "PROJECT",
            "isAddToBacklog": bool(to_backlog),
            "isAddToBottom": True,
        },
    )

    _reg_add(state["task"], task)
    if to_backlog:
        project.setdefault("backlogTaskIds", []).append(task["id"])
    else:
        project["taskIds"].append(task["id"])
    for tag_id in task.get("tagIds", []):
        tag = _tag(state, tag_id)
        if task["id"] not in tag["taskIds"]:
            tag["taskIds"].append(task["id"])
    today = today_str()
    if task.get("dueDay") == today or (
        task.get("dueWithTime") is not None
        and day_of_ms(task["dueWithTime"]) == today
    ):
        _today_prepend(state, [task["id"]])
    return task["id"]


def update_task(d: dict, b: OpBuilder, task_id: str, changes: dict) -> None:
    state = _state(d)
    task = _task(state, task_id)
    changes = dict(changes)
    if "tagIds" in changes and TODAY_TAG_ID in changes["tagIds"]:
        raise MutationError("'TODAY' must never appear in task.tagIds")

    # An explicit due change replaces the other mechanism (CLI intent) — the
    # dueDay+same-day-dueWithTime pair is only ever produced by planning paths.
    if changes.get("dueDay") is not None:
        changes.setdefault("dueWithTime", None)
        changes.setdefault("remindAt", None)
    if changes.get("dueWithTime") is not None:
        changes.setdefault("dueDay", None)

    b.op("HU", "UPD", "TASK", task_id, {"task": {"id": task_id, "changes": copy.deepcopy(changes)}})

    if "tagIds" in changes:
        old_tags = set(task.get("tagIds", []))
        new_tags = set(changes["tagIds"])
        for tag_id in new_tags - old_tags:
            tag = _tag(state, tag_id)
            if task_id not in tag["taskIds"]:
                tag["taskIds"].append(task_id)
        for tag_id in old_tags - new_tags:
            tag = state["tag"]["entities"].get(tag_id)
            if tag:
                _list_remove(tag["taskIds"], task_id)

    task.update(changes)
    task["modified"] = now_ms()

    # Keep the TODAY ordering coherent with due changes.
    if "dueDay" in changes or "dueWithTime" in changes:
        today = today_str()
        member = (
            task.get("dueWithTime") is not None
            and day_of_ms(task["dueWithTime"]) == today
        ) or (task.get("dueWithTime") is None and task.get("dueDay") == today)
        if member:
            if task_id not in _today_order(state):
                _today_prepend(state, [task_id])
        else:
            _list_remove(_today_order(state), task_id)


def complete_task(d: dict, b: OpBuilder, task_id: str) -> None:
    update_task(d, b, task_id, {"isDone": True, "doneOn": now_ms()})


def reopen_task(d: dict, b: OpBuilder, task_id: str) -> None:
    update_task(d, b, task_id, {"isDone": False, "doneOn": None})


def _clear_current_task_refs(state: dict, task_ids: list[str]) -> None:
    """Null `task.currentTaskId` / `lastCurrentTaskId` when they point at a
    task that no longer exists.

    Cosmetic and device-local (the running timer never travels through the
    sync file), but leaving a dangling id in the snapshot we PUT is untidy
    and would make another device's task bar reference a phantom task.
    """
    reg = state.get("task")
    if not isinstance(reg, dict):
        return
    gone = set(task_ids)
    for key in ("currentTaskId", "lastCurrentTaskId"):
        if reg.get(key) in gone:
            reg[key] = None


def _cascade_remove_task(state: dict, task_id: str) -> None:
    """Remove a task (and its subtasks) from every referencing structure."""
    task = state["task"]["entities"].get(task_id)
    if task is None:
        return
    all_ids = [task_id] + list(task.get("subTaskIds", []))

    parent_id = task.get("parentId")
    if parent_id:
        parent = state["task"]["entities"].get(parent_id)
        if parent:
            _list_remove(parent.get("subTaskIds", []), task_id)

    for tid in all_ids:
        _reg_remove(state["task"], tid)
    for project in state["project"]["entities"].values():
        for tid in all_ids:
            _list_remove(project.get("taskIds", []), tid)
            _list_remove(project.get("backlogTaskIds", []), tid)
    for tag in state["tag"]["entities"].values():
        for tid in all_ids:
            _list_remove(tag.get("taskIds", []), tid)
    _planner_purge(state, all_ids)
    # handleTaskRemoval: the ids leave EVERY section, whatever its context.
    _section_remove_task_ids(state, all_ids)
    _clear_current_task_refs(state, all_ids)


def delete_task(d: dict, b: OpBuilder, task_id: str) -> None:
    state = _state(d)
    _task(state, task_id)
    snapshot = task_with_subtasks(state, task_id)
    b.op("HD", "DEL", "TASK", task_id, {"task": snapshot})
    _cascade_remove_task(state, task_id)


def delete_tasks(d: dict, b: OpBuilder, task_ids: list[str]) -> None:
    state = _state(d)
    # Native SP passes the flattened list of ALL deleted ids — every task
    # plus each task's subTaskIds (the state cascade removes them anyway).
    all_ids: dict[str, None] = {}
    for tid in task_ids:
        task = _task(state, tid)
        all_ids[tid] = None
        for sid in task.get("subTaskIds", []):
            all_ids[sid] = None
    flat = list(all_ids)
    b.op("HDM", "DEL", "TASK", task_ids[0], {"taskIds": flat}, ds=flat)
    for tid in task_ids:
        _cascade_remove_task(state, tid)


def add_subtask(d: dict, b: OpBuilder, parent_id: str, subtask: dict) -> str:
    state = _state(d)
    parent = _task(state, parent_id)
    if parent.get("parentId"):
        raise MutationError("cannot nest subtasks (parent is itself a subtask)")
    subtask = dict(subtask)
    subtask["parentId"] = parent_id
    subtask["projectId"] = parent["projectId"]

    b.op(
        "TA",
        "CRT",
        "TASK",
        subtask["id"],
        {"task": copy.deepcopy(subtask), "parentId": parent_id},
    )

    _reg_add(state["task"], subtask)
    parent["subTaskIds"].append(subtask["id"])
    return subtask["id"]


def move_to_project(d: dict, b: OpBuilder, task_id: str, target_project_id: str) -> None:
    state = _state(d)
    task = _task(state, task_id)
    if task.get("parentId"):
        raise MutationError("cannot move a subtask; move its parent")
    target = _project(state, target_project_id)
    old_project = state["project"]["entities"].get(task["projectId"])
    snapshot = task_with_subtasks(state, task_id)

    b.op(
        "HMP",
        "UPD",
        "TASK",
        task_id,
        {"task": snapshot, "targetProjectId": target_project_id},
    )

    task["projectId"] = target_project_id
    for sid in task.get("subTaskIds", []):
        sub = state["task"]["entities"].get(sid)
        if sub:
            sub["projectId"] = target_project_id
    if old_project:
        _list_remove(old_project["taskIds"], task_id)
        _list_remove(old_project.get("backlogTaskIds", []), task_id)
        # handleMoveToOtherProject: membership in the OLD project's sections
        # is stale once the task (and its subtasks) left the project.
        _section_remove_task_ids(
            state,
            [task_id] + list(task.get("subTaskIds", [])),
            context_type="PROJECT",
            context_id=old_project["id"],
        )
    if task_id not in target["taskIds"]:
        target["taskIds"].append(task_id)


def reorder_project(d: dict, b: OpBuilder, project_id: str, ordered: list[str]) -> None:
    state = _state(d)
    project = _project(state, project_id)
    if sorted(ordered) != sorted(project["taskIds"]):
        raise MutationError(
            "reorder: id list must be a permutation of the project's taskIds"
        )
    b.op(
        "PU",
        "UPD",
        "PROJECT",
        project_id,
        {"project": {"id": project_id, "changes": {"taskIds": list(ordered)}}},
    )
    project["taskIds"] = list(ordered)


# ------------------------------------------------- reordering & conversion

DIRECTIONS = ("up", "down", "top", "bottom")

_WM_ACTIONS = {"up": "WMU", "down": "WMD", "top": "WMT", "bottom": "WMB"}
_TM_ACTIONS = {"up": "TMU", "down": "TMD", "top": "TMT", "bottom": "TMB"}


def _array_move(lst: list, from_i: int, to_i: int) -> None:
    """SP's `arrayMove` (splice out, splice in) — in place."""
    lst.insert(to_i, lst.pop(from_i))


def _move_left_until(lst: list, value: str, skip) -> None:
    """SP's `arrayMoveLeftUntil`: step left over every neighbour `skip()`
    accepts (the DONE ones), stopping at index 0."""
    if value not in lst:
        return
    old = lst.index(value)
    new = old - 1
    while new > 0 and skip(lst[new]):
        new -= 1
    if new >= 0:
        _array_move(lst, old, new)


def _move_right_until(lst: list, value: str, skip) -> None:
    """SP's `arrayMoveRightUntil` (mirror of `_move_left_until`)."""
    if value not in lst:
        return
    old = lst.index(value)
    new = old + 1
    while new < len(lst) and skip(lst[new]):
        new += 1
    if new < len(lst):
        _array_move(lst, old, new)


def _move_in_list(lst: list, value: str, direction: str, undone: list[str]) -> None:
    """The four WM*/TM* shifts. `undone` is the NOT-done id list: up/down step
    OVER done neighbours (SP skips every id absent from it)."""
    if value not in lst:
        return
    undone_set = set(undone)

    def _skip(item: str) -> bool:
        return item not in undone_set

    if direction == "up":
        _move_left_until(lst, value, _skip)
    elif direction == "down":
        _move_right_until(lst, value, _skip)
    elif direction == "top":
        _array_move(lst, lst.index(value), 0)
    elif direction == "bottom":
        _array_move(lst, lst.index(value), len(lst) - 1)
    else:
        raise MutationError(f"unknown direction: {direction}")


def _move_item_before_anchor(lst: list, from_id: str, to_id: str) -> None:
    """SP's `moveItemBeforeItem` — in place, callers guard membership."""
    to_index = lst.index(to_id)
    from_index = lst.index(from_id)
    adjusted = to_index - 1 if from_index < to_index else to_index
    _array_move(lst, from_index, adjusted)


def _undone_ids(state: dict, ids: list[str]) -> list[str]:
    entities = state["task"]["entities"]
    return [tid for tid in ids if not (entities.get(tid) or {}).get("isDone")]


def _recalc_parent_times(state: dict, parent_id: str) -> None:
    """SP's `reCalcTimesForParentIfParent`: a parent task's times are pure
    aggregates over its CURRENT subtasks.

    - `timeSpentOnDay` = per-day sum over the subtasks (falsy values skipped)
    - `timeSpent`      = sum of that map
    - `timeEstimate`   = `sumSubTaskTimeLeft`: the work still OUTSTANDING, i.e.
      sum of `max(0, estimate - spent)` over the NOT-done subtasks (a done
      subtask contributes nothing) — 0 when the last subtask leaves.
    """
    parent = state["task"]["entities"].get(parent_id)
    if parent is None:
        return
    entities = state["task"]["entities"]
    subs = [
        entities[sid]
        for sid in parent.get("subTaskIds", [])
        if sid in entities
    ]
    per_day: dict[str, int] = {}
    for sub in subs:
        for day, value in (sub.get("timeSpentOnDay") or {}).items():
            if not value:
                continue
            per_day[day] = per_day.get(day, 0) + int(value)
    parent["timeSpentOnDay"] = per_day
    parent["timeSpent"] = sum(per_day.values())
    parent["timeEstimate"] = sum(
        0
        if sub.get("isDone")
        else max(0, int(sub.get("timeEstimate") or 0) - int(sub.get("timeSpent") or 0))
        for sub in subs
    )


def today_move_before(d: dict, b: OpBuilder, task_id: str, to_task_id: str) -> None:
    """HMT: put `task_id` directly BEFORE `to_task_id` in the TODAY ordering.

    A bulk op whose `ds` is **[toTaskId, fromTaskId]** — target first. The
    receiver silently ignores the move unless BOTH ids are in the TODAY list,
    so membership is checked here instead of shipping a dead op.
    """
    state = _state(d)
    _task(state, task_id)
    _task(state, to_task_id)
    if task_id == to_task_id:
        raise MutationError("today move: a task cannot be moved before itself")
    order = _today_order(state)
    for tid in (task_id, to_task_id):
        if tid not in order:
            raise MutationError(
                f"task {tid} is not in today's list "
                "(both tasks must be in it: 'sp today add' first)"
            )

    b.op(
        "HMT",
        "MOV",
        "TASK",
        to_task_id,
        {"toTaskId": to_task_id, "fromTaskId": task_id},
        ds=[to_task_id, task_id],
    )

    _move_item_before_anchor(order, task_id, to_task_id)


def today_move(d: dict, b: OpBuilder, task_id: str, direction: str) -> None:
    """WMU/WMD/WMT/WMB on the TODAY tag list.

    ⚠️ `doneTaskIds` is a wire-frozen misnomer: it carries the NOT-done ids of
    the context, and up/down hop OVER every neighbour missing from it.
    """
    if direction not in DIRECTIONS:
        raise MutationError(f"unknown direction: {direction}")
    state = _state(d)
    _task(state, task_id)
    order = _today_order(state)
    if task_id not in order:
        raise MutationError(
            f"task {task_id} is not in today's list ('sp today add' first)"
        )

    undone = _undone_ids(state, order)
    b.op(
        _WM_ACTIONS[direction],
        "MOV",
        "TAG",
        TODAY_TAG_ID,
        {
            "taskId": task_id,
            "workContextId": TODAY_TAG_ID,
            "doneTaskIds": undone,
            "workContextType": "TAG",
        },
    )

    _move_in_list(order, task_id, direction, undone)
    # Same op, same pass: the section holding the task for this context is
    # reordered with the very same closure (payload `doneTaskIds`, i.e. the
    # context's NOT-done ids — a section id absent from it is stepped over).
    _section_reorder_task(
        state,
        "TAG",
        TODAY_TAG_ID,
        task_id,
        lambda ids: _move_in_list(ids, task_id, direction, undone),
    )


def _project_list_task(state: dict, task_id: str) -> tuple[dict, dict]:
    task = _task(state, task_id)
    if task.get("parentId"):
        raise MutationError(
            "cannot reorder a subtask in the project list; use 'sp subtask move'"
        )
    project_id = task.get("projectId")
    if not project_id:
        raise MutationError(f"task {task_id} has no project to reorder it in")
    project = _project(state, project_id)
    if task_id in project.get("backlogTaskIds", []):
        raise MutationError(
            f"task {task_id} is in the backlog of {project['id']} "
            "(use 'sp backlog rm' first)"
        )
    if task_id not in project.get("taskIds", []):
        raise MutationError(
            f"task {task_id} is not in project {project['id']}'s task list"
        )
    return task, project


def project_move(d: dict, b: OpBuilder, task_id: str, direction: str) -> str:
    """WMU/WMD/WMT/WMB in the task's own project list. Returns the project id."""
    if direction not in DIRECTIONS:
        raise MutationError(f"unknown direction: {direction}")
    state = _state(d)
    _task_, project = _project_list_task(state, task_id)
    project_id = project["id"]
    task_ids = project["taskIds"]

    undone = _undone_ids(state, task_ids)
    b.op(
        _WM_ACTIONS[direction],
        "MOV",
        "PROJECT",
        project_id,
        {
            "taskId": task_id,
            "workContextId": project_id,
            "doneTaskIds": undone,
            "workContextType": "PROJECT",
        },
    )

    _move_in_list(task_ids, task_id, direction, undone)
    # reorderTaskInContextSections, with the closure the reducer uses.
    _section_reorder_task(
        state,
        "PROJECT",
        project_id,
        task_id,
        lambda ids: _move_in_list(ids, task_id, direction, undone),
    )
    return project_id


def project_move_after(
    d: dict, b: OpBuilder, task_id: str, after_task_id: str | None
) -> str:
    """WM: anchor-based move inside the project list (`after_task_id` None
    prepends). Returns the project id."""
    state = _state(d)
    _task_, project = _project_list_task(state, task_id)
    project_id = project["id"]
    if after_task_id is not None:
        if after_task_id == task_id:
            raise MutationError("move: a task cannot be moved after itself")
        if after_task_id not in project.get("taskIds", []):
            raise MutationError(
                f"anchor {after_task_id} is not in project {project_id}'s task list"
            )

    b.op(
        "WM",
        "MOV",
        "PROJECT",
        project_id,
        {
            "taskId": task_id,
            "afterTaskId": after_task_id,
            "workContextType": "PROJECT",
            "workContextId": project_id,
            "src": "UNDONE",
            "target": "UNDONE",
        },
    )

    _move_item_after_anchor(project["taskIds"], task_id, after_task_id)
    return project_id


def subtask_move(d: dict, b: OpBuilder, task_id: str, direction: str) -> str:
    """TMU/TMD/TMT/TMB: shift a subtask inside its parent. Returns parent id.

    The op writes ONLY `parent.subTaskIds` and is a silent no-op on the
    receivers when the parent is gone or the id is not among its subtasks.
    """
    if direction not in DIRECTIONS:
        raise MutationError(f"unknown direction: {direction}")
    state = _state(d)
    task = _task(state, task_id)
    parent_id = task.get("parentId")
    if not parent_id:
        raise MutationError(f"task {task_id} is not a subtask")
    parent = _task(state, parent_id)
    sub_ids = parent.setdefault("subTaskIds", [])
    if task_id not in sub_ids:
        raise MutationError(
            f"task {task_id} is not listed in parent {parent_id}'s subTaskIds"
        )

    b.op(
        _TM_ACTIONS[direction],
        "MOV",
        "TASK",
        task_id,
        {"id": task_id, "parentId": parent_id},
    )

    # Subtask lists have no done/undone skipping: every sibling counts.
    _move_in_list(sub_ids, task_id, direction, list(sub_ids))
    return parent_id


def subtask_reparent(
    d: dict,
    b: OpBuilder,
    task_id: str,
    target_parent_id: str,
    after_task_id: str | None = None,
) -> str:
    """TMS: move a subtask under another parent. Returns the old parent id.

    Both parents' aggregate times are recomputed, exactly as the reducer does.
    """
    state = _state(d)
    task = _task(state, task_id)
    src_id = task.get("parentId")
    if not src_id:
        raise MutationError(
            f"task {task_id} is not a subtask (use 'sp demote' to nest it)"
        )
    src = _task(state, src_id)
    target = _task(state, target_parent_id)
    if task_id in (src_id, target_parent_id):
        raise MutationError("reparent: a task cannot be its own parent")
    if target.get("parentId"):
        raise MutationError(
            f"{target_parent_id} is itself a subtask; SuperProductivity nests "
            "only two levels"
        )
    if target_parent_id in task.get("subTaskIds", []):
        raise MutationError("reparent: that would create a circular reference")
    if after_task_id is not None and after_task_id not in target.get("subTaskIds", []):
        raise MutationError(
            f"anchor {after_task_id} is not a subtask of {target_parent_id}"
        )

    b.op(
        "TMS",
        "MOV",
        "TASK",
        task_id,
        {
            "taskId": task_id,
            "srcTaskId": src_id,
            "targetTaskId": target_parent_id,
            "afterTaskId": after_task_id,
        },
    )

    _list_remove(src.setdefault("subTaskIds", []), task_id)
    _recalc_parent_times(state, src_id)
    _move_item_after_anchor(
        target.setdefault("subTaskIds", []), task_id, after_task_id
    )
    task["parentId"] = target_parent_id
    task["projectId"] = target["projectId"]
    _recalc_parent_times(state, target_parent_id)
    return src_id


# The eligibility rule of SP's `canConvertTaskToSubTask`, field by field, with
# the message explaining why the app would refuse. Replicated here because HCS
# is a SILENT no-op on the receivers when it does not hold.
_DEMOTE_BLOCKERS = (
    ("parentId", "it is already a subtask"),
    ("subTaskIds", "it has subtasks of its own (only two levels are nested)"),
    ("repeatCfgId", "it is a repeating task"),
    ("issueId", "it comes from an issue provider"),
    ("issueProviderId", "it comes from an issue provider"),
    ("issueType", "it comes from an issue provider"),
    ("dueWithTime", "it is scheduled at a time ('sp unschedule' first)"),
    ("reminderId", "it has a reminder ('sp unschedule' first)"),
    ("remindAt", "it has a reminder ('sp unschedule' first)"),
)


def demote(
    d: dict,
    b: OpBuilder,
    task_id: str,
    target_parent_id: str,
    after_task_id: str | None = None,
) -> None:
    """HCS convertToSubTask: turn a main task into a subtask of another task.

    Every eligibility condition is checked here: the receivers apply the op
    only when it holds, and a rejected op would leave the CLI's snapshot and
    the devices permanently out of step.

    Effects mirror the reducer: the task leaves the project's list AND backlog,
    the TODAY ordering and every planner day; it is anchor-inserted into the
    parent's subTaskIds; `parentId`/`projectId` are set, `dueDay` cleared,
    `modified` bumped. `tagIds` and the deadline fields are left alone.
    """
    state = _state(d)
    task = _task(state, task_id)
    target = _task(state, target_parent_id)
    if task_id == target_parent_id:
        raise MutationError("demote: a task cannot be its own parent")
    if target.get("parentId"):
        raise MutationError(
            f"{target_parent_id} is itself a subtask; SuperProductivity nests "
            "only two levels"
        )
    for field, reason in _DEMOTE_BLOCKERS:
        if task.get(field):
            raise MutationError(f"cannot convert {task_id} to a subtask: {reason}")
    if after_task_id is not None and after_task_id not in target.get("subTaskIds", []):
        raise MutationError(
            f"anchor {after_task_id} is not a subtask of {target_parent_id}"
        )

    b.op(
        "HCS",
        "UPD",
        "TASK",
        task_id,
        {
            "taskId": task_id,
            "targetParentId": target_parent_id,
            "afterTaskId": after_task_id,
        },
    )

    project = state["project"]["entities"].get(task.get("projectId"))
    if project is not None:
        _list_remove(project.setdefault("taskIds", []), task_id)
        _list_remove(project.setdefault("backlogTaskIds", []), task_id)
    _list_remove(_today_order(state), task_id)
    _planner_purge(state, [task_id])
    # HCS runs handleTaskRemoval on the section slice: a task that became a
    # subtask is no longer a member of ANY section (project or TODAY).
    _section_remove_task_ids(state, [task_id])
    _move_item_after_anchor(
        target.setdefault("subTaskIds", []), task_id, after_task_id
    )
    task["parentId"] = target_parent_id
    task["projectId"] = target["projectId"]
    task["dueDay"] = None
    task["modified"] = now_ms()
    _recalc_parent_times(state, target_parent_id)


def promote(d: dict, b: OpBuilder, task_id: str, plan_for_today: bool = False) -> str:
    """HC convertToMainTask: lift a subtask back to a top-level task. Returns
    the former parent id.

    The receiver THROWS when it cannot resolve the parent, so the payload
    carries the task snapshot taken BEFORE the change and every optional field
    explicitly (`today`, `doneOn`, `modified`) — replay must not depend on the
    receiving device's clock.

    Effects mirror the reducer: the task keeps its own tags (TODAY filtered) or
    inherits the parent's when it has none; the old parent loses it from
    `subTaskIds` and has its times recomputed; the task is inserted right after
    its former parent in the project list and in each of its tags; TODAY gets
    it when `plan_for_today`. The planner is NOT touched.
    """
    state = _state(d)
    task = _task(state, task_id)
    parent_id = task.get("parentId")
    if not parent_id:
        raise MutationError(f"task {task_id} is not a subtask")
    parent = _task(state, parent_id)

    snapshot = copy.deepcopy(task)  # BEFORE the change
    today = logical_today_str(d)
    is_done = bool(task.get("isDone"))
    modified = now_ms()
    own_tags = [t for t in (task.get("tagIds") or []) if t != TODAY_TAG_ID]
    parent_tags = [t for t in (parent.get("tagIds") or []) if t != TODAY_TAG_ID]
    kept_tags = own_tags if own_tags else list(parent_tags)

    payload = {
        "task": snapshot,
        "parentTagIds": list(parent.get("tagIds") or []),
        "isPlanForToday": bool(plan_for_today),
        "afterTaskId": parent_id,
        "isDone": is_done,
        "today": today,
        "modified": modified,
    }
    if is_done:
        # Only consulted for a done task (`capturedDoneOn ?? Date.now()`), but
        # then it must be OURS, not the receiving device's clock.
        payload["doneOn"] = task.get("doneOn") or modified
    b.op("HC", "UPD", "TASK", task_id, payload)

    # old parent: unlink + recompute its aggregates
    _list_remove(parent.setdefault("subTaskIds", []), task_id)
    _recalc_parent_times(state, parent_id)

    task.pop("parentId", None)
    task["tagIds"] = list(kept_tags)
    task["modified"] = modified
    if plan_for_today and not task.get("dueWithTime"):
        task["dueDay"] = today
    if is_done:
        task["doneOn"] = payload["doneOn"]
        task["dueDay"] = today
        task["dueWithTime"] = None
    else:
        task["doneOn"] = None

    # position: right after the former parent, in the project and in the tags
    project = state["project"]["entities"].get(task.get("projectId"))
    if project is not None:
        _move_item_after_anchor(project.setdefault("taskIds", []), task_id, parent_id)
    tag_ids = list(kept_tags) + ([TODAY_TAG_ID] if plan_for_today else [])
    for tag_id in tag_ids:
        tag = state["tag"]["entities"].get(tag_id)
        if tag is None:
            continue
        _move_item_after_anchor(tag.setdefault("taskIds", []), task_id, parent_id)
    return parent_id


def planner_move_before(d: dict, b: OpBuilder, task_id: str, to_task_id: str) -> None:
    """LB: drop a task right before another one in the planner.

    The anchor decides the day: the task leaves every planner day, is inserted
    before the anchor in the anchor's day, and inherits the anchor's `dueDay`
    (its `dueWithTime` is dropped). TODAY membership follows the anchor.
    """
    state = _state(d)
    task = _task(state, task_id)
    target = _task(state, to_task_id)
    if task_id == to_task_id:
        raise MutationError("plan move: a task cannot be moved before itself")
    days_map = (state.get("planner") or {}).get("days") or {}
    anchor_day = next(
        (day for day, day_list in days_map.items() if to_task_id in (day_list or [])),
        None,
    )
    if anchor_day is None and not target.get("dueDay"):
        raise MutationError(
            f"anchor {to_task_id} is not planned for any day and has no dueDay: "
            "the move would take the task out of every planner day and give it "
            f"the anchor's (empty) dueDay. Plan {to_task_id} first "
            f"('sp plan {to_task_id} --day <day>') or use 'sp today move --before'."
        )

    b.op(
        "LB",
        "MOV",
        "PLANNER",
        task_id,
        {"fromTask": copy.deepcopy(task), "toTaskId": to_task_id},
    )

    days = state.setdefault("planner", {"days": {}}).setdefault("days", {})
    for day, day_list in list(days.items()):
        _list_remove(day_list, task_id)
        if to_task_id in day_list:
            day_list.insert(day_list.index(to_task_id), task_id)
        if not day_list:
            del days[day]

    task["dueDay"] = target.get("dueDay")
    task["dueWithTime"] = None

    order = _today_order(state)
    if to_task_id in order:
        _list_remove(order, task_id)
        order.insert(order.index(to_task_id), task_id)
    elif task_id in order:
        _list_remove(order, task_id)


# ---------------------------------------------------------------- backlog

def backlog_add(
    d: dict, b: OpBuilder, task_id: str, after_task_id: str | None = None
) -> str:
    """PRB: move a top-level task from the project list into its backlog."""
    state = _state(d)
    task = _task(state, task_id)
    if task.get("parentId"):
        raise MutationError(
            "cannot move a subtask to the backlog; move its parent instead"
        )
    project = _project(state, task["projectId"])
    _require_backlog(project)
    project_id = project["id"]

    b.op(
        "PRB",
        "MOV",
        "TASK",
        task_id,
        {
            "taskId": task_id,
            "afterTaskId": after_task_id,
            "workContextId": project_id,
        },
    )

    _list_remove(project.setdefault("taskIds", []), task_id)
    _move_item_after_anchor(
        project.setdefault("backlogTaskIds", []), task_id, after_task_id
    )
    return project_id


def backlog_remove(
    d: dict, b: OpBuilder, task_id: str, after_task_id: str | None = None
) -> str:
    """PBR: move a task back out of the backlog into the project list."""
    state = _state(d)
    task = _task(state, task_id)
    project = _project(state, task["projectId"])
    project_id = project["id"]
    if task_id not in project.get("backlogTaskIds", []):
        raise MutationError(
            f"task {task_id} is not in the backlog of project {project_id}"
        )

    b.op(
        "PBR",
        "MOV",
        "TASK",
        task_id,
        {
            "taskId": task_id,
            "afterTaskId": after_task_id,
            "workContextId": project_id,
            "src": "BACKLOG",
            "target": "UNDONE",
        },
    )

    _list_remove(project.setdefault("backlogTaskIds", []), task_id)
    _move_item_after_anchor(
        project.setdefault("taskIds", []), task_id, after_task_id
    )
    return project_id


def backlog_clear(d: dict, b: OpBuilder, project_id: str) -> list[str]:
    """PBA: move the WHOLE backlog back into the project list. Returns the
    moved ids ([] when the backlog was already empty — the op is emitted
    either way, it is an idempotent no-op on the receivers)."""
    state = _state(d)
    project = _project(state, project_id)
    moved = list(project.get("backlogTaskIds", []))

    b.op("PBA", "UPD", "PROJECT", project_id, {"projectId": project_id})

    task_ids = project.setdefault("taskIds", [])
    for tid in moved:
        if tid not in task_ids:
            task_ids.append(tid)
    project["backlogTaskIds"] = []
    return moved


def project_set_backlog(
    d: dict, b: OpBuilder, project_id: str, enabled: bool
) -> list[str]:
    """Toggle project.isEnableBacklog. Disabling emits PU **and** PBA: SP's
    own effect flushes the backlog when the setting goes off, so the file has
    to carry the same pair or the tasks would be stranded in a list the app
    no longer shows."""
    state = _state(d)
    _project(state, project_id)
    project_update(d, b, project_id, {"isEnableBacklog": bool(enabled)})
    if enabled:
        return []
    return backlog_clear(d, b, project_id)


# ---------------------------------------------------------------- planning

def plan_today(d: dict, b: OpBuilder, task_ids: list[str]) -> None:
    state = _state(d)
    today = logical_today_str(d)
    offset = start_of_next_day_diff_ms(d)
    for tid in task_ids:
        _task(state, tid)

    b.op(
        "HPT",
        "UPD",
        "TASK",
        task_ids[0],
        {
            "taskIds": list(task_ids),
            "today": today,
            "startOfNextDayDiffMs": offset,
        },
        ds=list(task_ids),
    )

    for tid in task_ids:
        _plan_for_today(_task(state, tid), today, d)
    _today_prepend(state, task_ids)
    _planner_purge(state, task_ids)


def remove_from_today(d: dict, b: OpBuilder, task_id: str) -> None:
    """Also `unschedule` — HSX clears every due/reminder field."""
    state = _state(d)
    task = _task(state, task_id)
    b.op("HSX", "UPD", "TASK", task_id, {"id": task_id, "today": today_str()})
    task["dueDay"] = None
    _clear_due_with_time(task)
    _list_remove(_today_order(state), task_id)


unschedule = remove_from_today


def plan_for_day(d: dict, b: OpBuilder, task_id: str, day: str) -> None:
    state = _state(d)
    task = _task(state, task_id)
    if day < today_str():
        raise MutationError(
            f"cannot plan for a past day ({day}); "
            "for today use 'sp today add'"
        )
    snapshot = copy.deepcopy(task)

    b.op(
        "LP",
        "UPD",
        "PLANNER",
        task_id,
        {"task": snapshot, "day": day, "isAddToTop": False},
    )

    task["dueDay"] = day
    _clear_due_with_time(task)
    if day == today_str():
        _today_prepend(state, [task_id])
        _planner_purge(state, [task_id])
    else:
        _list_remove(_today_order(state), task_id)
        _planner_purge(state, [task_id])
        days = state.setdefault("planner", {"days": {}}).setdefault("days", {})
        days.setdefault(day, []).append(task_id)


def schedule_task(
    d: dict, b: OpBuilder, task_id: str, due_with_time: int, remind_at: int | None = None
) -> None:
    state = _state(d)
    task = _task(state, task_id)
    snapshot = copy.deepcopy(task)  # BEFORE the change
    action = "HSR" if task.get("dueWithTime") is not None else "HS"

    payload = {
        "task": snapshot,
        "dueWithTime": due_with_time,
        "isMoveToBacklog": False,
    }
    if remind_at is not None:
        payload["remindAt"] = remind_at
    b.op(action, "UPD", "TASK", task_id, payload)

    task["dueWithTime"] = due_with_time
    task["dueDay"] = None
    task["remindAt"] = remind_at
    if day_of_ms(due_with_time) == today_str():
        _today_prepend(state, [task_id])
    else:
        _list_remove(_today_order(state), task_id)


def set_deadline(
    d: dict,
    b: OpBuilder,
    task_id: str,
    deadline_day: str | None = None,
    deadline_with_time: int | None = None,
    deadline_remind_at: int | None = None,
) -> None:
    state = _state(d)
    task = _task(state, task_id)
    if (deadline_day is None) == (deadline_with_time is None):
        raise MutationError("deadline: exactly one of day / with-time required")

    payload: dict = {"taskId": task_id}
    if deadline_day is not None:
        payload["deadlineDay"] = deadline_day
    else:
        payload["deadlineWithTime"] = deadline_with_time
    if deadline_remind_at is not None:
        payload["deadlineRemindAt"] = deadline_remind_at
    b.op("HDL", "UPD", "TASK", task_id, payload)

    if deadline_day is not None:
        task["deadlineDay"] = deadline_day
        task["deadlineWithTime"] = None
    else:
        task["deadlineWithTime"] = deadline_with_time
        task["deadlineDay"] = None
    if deadline_remind_at is not None:
        task["deadlineRemindAt"] = deadline_remind_at


# ---------------------------------------------------------------- tracking

def _rollup_parent_time(state: dict, task: dict, date: str, delta: int) -> None:
    """Mirror SP's `updateParentTimeSpentIncremental`.

    A subtask's time is aggregated onto its parent: the parent's value for
    the day moves by the same delta (the key is dropped once it reaches 0,
    exactly as SP does) and `timeSpent` is the sum over `timeSpentOnDay`.
    State-only — the KT/TR payload never mentions the parent, every device
    derives the rollup itself.
    """
    parent_id = task.get("parentId")
    if not parent_id or not delta:
        return
    parent = state["task"]["entities"].get(parent_id)
    if parent is None:
        return
    tsod = parent.setdefault("timeSpentOnDay", {})
    value = int(tsod.get(date, 0)) + delta
    if value > 0:
        tsod[date] = value
    else:
        tsod.pop(date, None)
    parent["timeSpent"] = sum(int(v) for v in tsod.values())


def track_time(d: dict, b: OpBuilder, task_id: str, date: str, duration: int) -> None:
    state = _state(d)
    task = _task(state, task_id)
    if not isinstance(duration, int) or duration < 0:
        raise MutationError("track: duration must be a non-negative integer (ms)")

    changes = {"taskId": task_id, "date": date, "duration": duration}
    b.op(
        "KT",
        "UPD",
        "TASK",
        task_id,
        dict(changes),
        entity_changes=[
            {
                "entityType": "TASK",
                "entityId": task_id,
                "opType": "UPD",
                "changes": dict(changes),
            }
        ],
    )

    tsod = task.setdefault("timeSpentOnDay", {})
    tsod[date] = int(tsod.get(date, 0)) + duration
    task["timeSpent"] = sum(int(v) for v in tsod.values())
    _rollup_parent_time(state, task, date, duration)


def untrack_time(d: dict, b: OpBuilder, task_id: str, date: str, duration: int) -> int:
    """Remove tracked time (correction). TR payload key is `id`, not `taskId`;
    the reducer clamps at zero (max(x - duration, 0)).

    Returns the ms actually removed, which is less than `duration` when the
    clamp bites.
    """
    state = _state(d)
    task = _task(state, task_id)
    if not isinstance(duration, int) or duration < 0:
        raise MutationError("untrack: duration must be a non-negative integer (ms)")

    # No entityChanges: SP's TR reducer recomputes everything (including the
    # parent rollup) from {id, date, duration}, and a stale entity snapshot
    # here would fight the clamp on replay. Deliberately payload-only.
    b.op("TR", "UPD", "TASK", task_id, {"id": task_id, "date": date, "duration": duration})

    tsod = task.setdefault("timeSpentOnDay", {})
    before = int(tsod.get(date, 0))
    tsod[date] = max(before - duration, 0)
    task["timeSpent"] = sum(int(v) for v in tsod.values())
    removed = before - tsod[date]
    _rollup_parent_time(state, task, date, -removed)
    return removed


# ---------------------------------------------------------------- repeat

def repeat_add(
    d: dict,
    b: OpBuilder,
    task_id: str,
    cfg_id: str,
    repeat_cycle: str,
    repeat_every: int = 1,
    days: list[str] | None = None,
    start_date: str | None = None,
    start_time: str | None = None,
    remind_at: str | None = None,
) -> str:
    state = _state(d)
    task = _task(state, task_id)
    cfg = make_repeat_cfg(
        cfg_id,
        title=task["title"],
        project_id=task.get("projectId"),
        repeat_cycle=repeat_cycle,
        repeat_every=repeat_every,
        days=days,
        start_date=start_date,
        start_time=start_time,
        remind_at=remind_at,
        default_estimate=task.get("timeEstimate") or None,
    )

    payload = {"taskId": task_id, "taskRepeatCfg": copy.deepcopy(cfg)}
    if start_time is not None:
        payload["startTime"] = start_time
    if remind_at is not None:
        payload["remindAt"] = remind_at
    b.op("RA", "CRT", "TASK_REPEAT_CFG", cfg_id, payload)

    _reg_add(state["taskRepeatCfg"], cfg)
    task["repeatCfgId"] = cfg_id
    return cfg_id


# ---------------------------------------------------------------- projects/tags

def project_add(d: dict, b: OpBuilder, project: dict) -> str:
    state = _state(d)
    b.op("PA", "CRT", "PROJECT", project["id"], {"project": copy.deepcopy(project)})
    _reg_add(state["project"], project)
    return project["id"]


def project_update(d: dict, b: OpBuilder, project_id: str, changes: dict) -> None:
    state = _state(d)
    project = _project(state, project_id)
    b.op(
        "PU",
        "UPD",
        "PROJECT",
        project_id,
        {"project": {"id": project_id, "changes": copy.deepcopy(changes)}},
    )
    project.update(changes)


def tag_add(d: dict, b: OpBuilder, tag: dict) -> str:
    state = _state(d)
    b.op("GA", "CRT", "TAG", tag["id"], {"tag": copy.deepcopy(tag)})
    _reg_add(state["tag"], tag)
    return tag["id"]


def tag_update(d: dict, b: OpBuilder, tag_id: str, changes: dict) -> None:
    state = _state(d)
    tag = _tag(state, tag_id)
    b.op(
        "GU",
        "UPD",
        "TAG",
        tag_id,
        {"tag": {"id": tag_id, "changes": copy.deepcopy(changes)}},
    )
    tag.update(changes)


def add_tag_to_task(d: dict, b: OpBuilder, task_id: str, tag_id: str) -> bool:
    """Returns False (and emits NO op) when the tag is already on the task."""
    state = _state(d)
    task = _task(state, task_id)
    tag = _tag(state, tag_id)
    if tag_id == TODAY_TAG_ID:
        raise MutationError("'TODAY' must never appear in task.tagIds")
    if tag_id in task.get("tagIds", []):
        return False
    b.op(
        "HGT",
        "UPD",
        "TASK",
        task_id,
        {"tagId": tag_id, "taskId": task_id},
        ds=[task_id, tag_id],
    )
    task.setdefault("tagIds", []).append(tag_id)
    if task_id not in tag["taskIds"]:
        tag["taskIds"].append(task_id)
    return True


def tag_task(
    d: dict,
    b: OpBuilder,
    task_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    """HGT per added tag; one HU bulk tagIds replace for removals."""
    state = _state(d)
    task = _task(state, task_id)
    for tag_id in add or []:
        add_tag_to_task(d, b, task_id, tag_id)
    remove = [t for t in (remove or []) if t in task.get("tagIds", [])]
    if remove:
        new_tags = [t for t in task["tagIds"] if t not in remove]
        update_task(d, b, task_id, {"tagIds": new_tags})


# ----------------------------------------------------------- full deletes

# Tags SP creates itself and relies on; deleting one breaks the app's own
# views (Today list, Eisenhower board, Kanban board).
SYSTEM_TAG_IDS = (
    TODAY_TAG_ID,
    "EM_URGENT",
    "EM_IMPORTANT",
    "KANBAN_IN_PROGRESS",
)


def _prune_menu_nodes(nodes: list, kind: str, entity_id: str) -> list:
    """Drop every `{k: kind, id: entity_id}` node, recursing into folders."""
    out: list = []
    for node in nodes:
        if not isinstance(node, dict):
            out.append(node)
            continue
        if node.get("k") == "f":
            children = node.get("children")
            node["children"] = _prune_menu_nodes(
                children if isinstance(children, list) else [], kind, entity_id
            )
            out.append(node)
            continue
        if node.get("k") == kind and node.get("id") == entity_id:
            continue
        out.append(node)
    return out


def _menu_tree_prune(state: dict, kind: str, entity_id: str) -> None:
    """Prune the sidebar tree ('p' = projectTree, 't' = tagTree)."""
    tree = state.get("menuTree")
    if not isinstance(tree, dict):
        return
    key = "projectTree" if kind == "p" else "tagTree"
    nodes = tree.get(key)
    if isinstance(nodes, list):
        tree[key] = _prune_menu_nodes(nodes, kind, entity_id)


def _time_tracking_maps(d: dict) -> list[dict]:
    """Every timeTracking map in the file: live plus both archive blobs
    (top-level and under `state`, exactly like archive_task_blobs)."""
    state = d["state"] if isinstance(d.get("state"), dict) else {}
    maps: list[dict] = []
    for holder in (state, d):
        tt = holder.get("timeTracking")
        if isinstance(tt, dict) and not any(tt is m for m in maps):
            maps.append(tt)
    for _key, blob in _archive_blobs(d):
        tt = blob.get("timeTracking")
        if isinstance(tt, dict) and not any(tt is m for m in maps):
            maps.append(tt)
    return maps


def _archive_blobs(d: dict) -> list[tuple[str, dict]]:
    """The archiveYoung/archiveOld containers themselves (not their task
    registries) — top-level and under `state`, both kept."""
    state = d["state"] if isinstance(d.get("state"), dict) else {}
    out: list[tuple[str, dict]] = []
    for key in ("archiveYoung", "archiveOld"):
        for blob in (d.get(key), state.get(key)):
            if isinstance(blob, dict):
                out.append((key, blob))
    return out


def project_delete(d: dict, b: OpBuilder, project_id: str) -> tuple[list, list]:
    """HPD: delete a project outright — tasks and notes go with it, NOT to
    the archive. Returns (deleted task ids, deleted note ids).

    The payload carries `projectDeleteWins: True` as a TOP-LEVEL key: without
    it a concurrent project update from another device resurrects the project
    during conflict resolution.

    allTaskIds is taskIds + backlogTaskIds + their subTaskIds, as SP builds
    it, plus any live task that claims the project only via `projectId` —
    including those keeps the receivers' cascade identical to ours instead of
    stranding a task whose project no longer exists. Same for noteIds.

    That widening is deliberate and is a SUPERSET of SP's own payload: SP
    trusts its two order lists, we also sweep by `projectId`. Caveat: a task
    another device added to the project concurrently is only in *our* copy of
    the lists if we already pulled it, so the sweep improves — but cannot
    guarantee — completeness under concurrent edits. The stray tasks we do
    catch are named in allTaskIds, so receivers delete exactly what we did.

    Beyond the tasks/notes cascade this mirrors SP's project-shared reducer:
    repeat configs whose `projectId` is the deleted project are REMOVED
    outright (regardless of their tags — cleanupTaskRepeatCfgsForProject),
    `timeTracking.project[pid]` is dropped (live + both archive blobs), and
    the two globalConfig fields that can point at a project are healed the
    way project.effects.ts `deleteProjectRelatedData` heals them.
    """
    state = _state(d)
    project = _project(state, project_id)
    if project_id == INBOX_PROJECT_ID:
        raise MutationError("the Inbox project cannot be deleted")

    tasks = state["task"]["entities"]
    all_task_ids: list[str] = []

    def _push(tid: str) -> None:
        if tid not in all_task_ids:
            all_task_ids.append(tid)

    for tid in list(project.get("taskIds", [])) + list(
        project.get("backlogTaskIds", [])
    ):
        _push(tid)
        for sid in (tasks.get(tid) or {}).get("subTaskIds", []):
            _push(sid)
    for tid in list(state["task"]["ids"]):
        task = tasks.get(tid) or {}
        if task.get("projectId") != project_id:
            continue
        _push(tid)
        for sid in task.get("subTaskIds", []):
            _push(sid)

    note_reg = _note_reg(state)
    note_ids = list(project.get("noteIds", []))
    for nid in note_reg["ids"]:
        note = note_reg["entities"].get(nid) or {}
        if note.get("projectId") == project_id and nid not in note_ids:
            note_ids.append(nid)

    b.op(
        "HPD",
        "DEL",
        "PROJECT",
        project_id,
        {
            "projectId": project_id,
            "noteIds": list(note_ids),
            "allTaskIds": list(all_task_ids),
            "projectDeleteWins": True,
        },
    )

    # tasks: registry + every list that could reference them
    for tid in all_task_ids:
        _reg_remove(state["task"], tid)
    for proj in state["project"]["entities"].values():
        for tid in all_task_ids:
            _list_remove(proj.setdefault("taskIds", []), tid)
            _list_remove(proj.setdefault("backlogTaskIds", []), tid)
    for tag in state["tag"]["entities"].values():
        for tid in all_task_ids:
            _list_remove(tag.setdefault("taskIds", []), tid)
    _planner_purge(state, all_task_ids)
    _clear_current_task_refs(state, all_task_ids)

    # notes
    for nid in note_ids:
        _reg_remove(note_reg, nid)
        _list_remove(note_reg["todayOrder"], nid)

    # sections: `removeProjectSections` drops the ones OWNED by the project
    # (a Section is keyed by contextType/contextId — it has no `projectId`),
    # then `cleanupSectionTaskIds` strips the dead task ids from whatever
    # sections remain (the TODAY-tag ones can hold them too).
    sections = state.get("section")
    if isinstance(sections, dict) and isinstance(sections.get("entities"), dict):
        sections.setdefault("ids", [])
        for sid, section in list(sections["entities"].items()):
            if (
                isinstance(section, dict)
                and section.get("contextType") == "PROJECT"
                and section.get("contextId") == project_id
            ):
                _reg_remove(sections, sid)
    _section_remove_task_ids(state, all_task_ids)

    _menu_tree_prune(state, "p", project_id)

    # repeat cfgs of the project: removed outright, tags or no tags
    # (project-shared.reducer.ts cleanupTaskRepeatCfgsForProject). Their tasks
    # are in the project and die with it, so SP never clears `repeatCfgId`
    # refs here either — we mirror that exactly.
    cfg_reg = state.get("taskRepeatCfg")
    if isinstance(cfg_reg, dict) and isinstance(cfg_reg.get("entities"), dict):
        for cid, cfg in list(cfg_reg["entities"].items()):
            if isinstance(cfg, dict) and cfg.get("projectId") == project_id:
                _reg_remove(cfg_reg, cid)

    # time tracking: live bucket (cleanupTimeTrackingForProject) plus both
    # archive blobs (ArchiveOperationHandler._handleDeleteProject)
    for tt in _time_tracking_maps(d):
        by_project = tt.get("project")
        if isinstance(by_project, dict):
            by_project.pop(project_id, None)

    # globalConfig healing — project.effects.ts deleteProjectRelatedData:
    # defaultProjectId falls back to the Inbox (the canonical "unset" target),
    # defaultStartPage back to 0 so nobody lands on a dead route. This touches
    # only these two fields; the `sync` section stays device-local.
    cfg = state.get("globalConfig")
    if isinstance(cfg, dict):
        tasks_cfg = cfg.get("tasks")
        if isinstance(tasks_cfg, dict) and tasks_cfg.get("defaultProjectId") == project_id:
            tasks_cfg["defaultProjectId"] = INBOX_PROJECT_ID
        misc_cfg = cfg.get("misc")
        if isinstance(misc_cfg, dict) and misc_cfg.get("defaultStartPage") == project_id:
            misc_cfg["defaultStartPage"] = 0

    for provider in ((state.get("issueProvider") or {}).get("entities") or {}).values():
        if provider.get("defaultProjectId") == project_id:
            provider["defaultProjectId"] = None

    # archived tasks of the project (the archive handler does this on devices)
    for _key, reg in archive_task_blobs(d):
        for tid, task in list(reg["entities"].items()):
            if task.get("projectId") == project_id:
                reg["entities"].pop(tid, None)
                _list_remove(reg.setdefault("ids", []), tid)

    _reg_remove(state["project"], project_id)
    return all_task_ids, note_ids


def tag_delete(d: dict, b: OpBuilder, tag_id: str) -> list[str]:
    """GD: delete a tag. ONE op — the whole cascade is re-derived on every
    receiver, so companion ops would double-apply. Returns the ids of the
    tasks the orphan rule hard-deleted along with it.

    Cascade order is SP's: strip the tag off tasks; hard-delete a task left
    with no tags, no project and no parent (with its subtasks); filter repeat
    cfgs (dropping one left with neither tags nor project); drop the tag's
    time-tracking bucket; filter issue-provider defaults; remove the entity;
    prune the sidebar tree; strip the tag out of the archives too.
    """
    state = _state(d)
    _tag(state, tag_id)
    if tag_id in SYSTEM_TAG_IDS:
        raise MutationError(f"'{tag_id}' is a built-in tag and cannot be deleted")

    b.op("GD", "DEL", "TAG", tag_id, {"id": tag_id})

    orphans: list[str] = []
    for tid in list(state["task"]["ids"]):
        task = state["task"]["entities"].get(tid)
        if task is None or tag_id not in task.get("tagIds", []):
            continue
        task["tagIds"] = [t for t in task["tagIds"] if t != tag_id]
        if not task["tagIds"] and not task.get("projectId") and not task.get("parentId"):
            orphans.append(tid)
    for tid in orphans:
        _cascade_remove_task(state, tid)

    cfg_reg = state.get("taskRepeatCfg")
    if isinstance(cfg_reg, dict) and isinstance(cfg_reg.get("entities"), dict):
        for cid, cfg in list(cfg_reg["entities"].items()):
            if tag_id not in (cfg.get("tagIds") or []):
                continue
            cfg["tagIds"] = [t for t in cfg["tagIds"] if t != tag_id]
            if not cfg["tagIds"] and not cfg.get("projectId"):
                _reg_remove(cfg_reg, cid)

    for tt in _time_tracking_maps(d):
        by_tag = tt.get("tag")
        if isinstance(by_tag, dict):
            by_tag.pop(tag_id, None)

    for provider in ((state.get("issueProvider") or {}).get("entities") or {}).values():
        defaults = provider.get("defaultTagIds")
        if isinstance(defaults, list) and tag_id in defaults:
            provider["defaultTagIds"] = [t for t in defaults if t != tag_id]

    _reg_remove(state["tag"], tag_id)
    _menu_tree_prune(state, "t", tag_id)

    # archives: strip the tag, then apply the SAME orphan rule the live state
    # got — task-archive.service.ts _removeTagsFromAllTasks hard-deletes an
    # archived task left with no tags, no project and no parent, along with
    # everything in its subTaskIds.
    for _key, reg in archive_task_blobs(d):
        entities = reg["entities"]
        for task in entities.values():
            if tag_id in (task.get("tagIds") or []):
                task["tagIds"] = [t for t in task["tagIds"] if t != tag_id]
        doomed: list[str] = []
        for tid in list(reg.get("ids") or []):
            task = entities.get(tid)
            if task is None:
                continue
            if task.get("tagIds") or task.get("projectId") or task.get("parentId"):
                continue
            doomed.append(tid)
            doomed.extend(task.get("subTaskIds") or [])
        for tid in doomed:
            entities.pop(tid, None)
            _list_remove(reg.setdefault("ids", []), tid)

    return orphans


CLEARABLE_REPEAT_CFG_FIELDS = (
    "startTime",
    "remindAt",
    "defaultEstimate",
    "notes",
    # SP's MONTHLY_ANCHOR_RESET: absence is what these three mean, so a cadence
    # change has to unset them rather than write a falsy value.
    *MONTHLY_ANCHOR_FIELDS,
)
MAX_CLEARED_FIELDS = 32  # SP's applyClearedFields caps the wire list at 32


def _repeat_cfg(state: dict, cfg_id: str) -> dict:
    reg = state.get("taskRepeatCfg")
    entities = (reg or {}).get("entities") or {}
    if not isinstance(reg, dict) or cfg_id not in entities:
        raise MutationError(f"repeat config not found: {cfg_id}")
    return entities[cfg_id]


def repeat_update(
    d: dict,
    b: OpBuilder,
    cfg_id: str,
    changes: dict,
    cleared_fields: list[str] | None = None,
) -> None:
    """RU: update a repeat config; `cleared_fields` unsets optional fields.

    Clearing MUST go through the `clearedFields` sibling of the actionPayload:
    `changes: {startTime: undefined}` survives locally but every JSON hop drops
    the key, so the clear replays as a no-op elsewhere (SP issue #9776).
    Mirrors applyClearedFields: cleared keys are REMOVED from the entity, never
    written as null, and are never listed in `changes`.
    """
    state = _state(d)
    cfg = _repeat_cfg(state, cfg_id)
    cleared = list(dict.fromkeys(cleared_fields or []))
    for field in cleared:
        if field not in CLEARABLE_REPEAT_CFG_FIELDS:
            raise MutationError(
                f"field is not clearable: {field} "
                f"(allowed: {', '.join(CLEARABLE_REPEAT_CFG_FIELDS)})"
            )
        if field in changes:
            raise MutationError(f"field both set and cleared: {field}")
    if len(cleared) > MAX_CLEARED_FIELDS:
        raise MutationError(f"too many cleared fields (max {MAX_CLEARED_FIELDS})")
    if not changes and not cleared:
        raise MutationError("repeat update: nothing to change")

    payload = {
        "taskRepeatCfg": {"id": cfg_id, "changes": copy.deepcopy(changes)},
    }
    if cleared:
        payload["clearedFields"] = list(cleared)
    b.op("RU", "UPD", "TASK_REPEAT_CFG", cfg_id, payload)

    cfg.update(copy.deepcopy(changes))
    for field in cleared:
        cfg.pop(field, None)


def repeat_skip_instance(d: dict, b: OpBuilder, cfg_id: str, date_str: str) -> bool:
    """RDI: suppress the instance of `date_str`. Returns False (and emits NO op)
    when the date is already suppressed — the list is an append-only set.

    Does NOT delete an already generated task for that date.
    """
    state = _state(d)
    cfg = _repeat_cfg(state, cfg_id)
    dates = list(cfg.get("deletedInstanceDates") or [])
    if date_str in dates:
        return False
    b.op(
        "RDI",
        "UPD",
        "TASK_REPEAT_CFG",
        cfg_id,
        {"repeatCfgId": cfg_id, "dateStr": date_str},
    )
    cfg["deletedInstanceDates"] = dates + [date_str]
    return True


def repeat_delete(d: dict, b: OpBuilder, cfg_id: str) -> None:
    """HRC: delete a repeat config and unlink every task pointing at it.

    Payload key is `taskRepeatCfgId`, not `id`. RD/RDM only drop the entity
    and would leave every generated task holding a dangling repeatCfgId.
    """
    state = _state(d)
    reg = state.get("taskRepeatCfg")
    if not isinstance(reg, dict) or cfg_id not in (reg.get("entities") or {}):
        raise MutationError(f"repeat config not found: {cfg_id}")

    b.op(
        "HRC",
        "DEL",
        "TASK_REPEAT_CFG",
        cfg_id,
        {"taskRepeatCfgId": cfg_id},
    )

    for task in state["task"]["entities"].values():
        if task.get("repeatCfgId") == cfg_id:
            task.pop("repeatCfgId", None)
    for entities in archive_task_entity_maps(d):
        for task in entities.values():
            if task.get("repeatCfgId") == cfg_id:
                task.pop("repeatCfgId", None)
    _reg_remove(reg, cfg_id)


# ---------------------------------------------------------------- attachments

def _attachment_list(task: dict) -> list:
    attachments = task.get("attachments")
    if not isinstance(attachments, list):
        attachments = []
        task["attachments"] = attachments
    return attachments


def _attachment(task: dict, attachment_id: str) -> dict:
    for attachment in _attachment_list(task):
        if attachment.get("id") == attachment_id:
            return attachment
    raise MutationError(
        f"attachment not found on task {task['id']}: {attachment_id}"
    )


def attachment_add(d: dict, b: OpBuilder, task_id: str, attachment: dict) -> str:
    """XA — append an attachment to a task (already built via make_attachment).

    All three attachment ops are e=TASK/o=UPD (even the delete) and the SP
    reducers THROW when the task is missing, so existence is checked here.
    None of them bumps task.modified.
    """
    state = _state(d)
    task = _task(state, task_id)
    attachments = _attachment_list(task)
    if any(a.get("id") == attachment["id"] for a in attachments):
        raise MutationError(f"attachment already exists: {attachment['id']}")

    b.op(
        "XA",
        "UPD",
        "TASK",
        task_id,
        {"taskId": task_id, "taskAttachment": copy.deepcopy(attachment)},
    )

    attachments.append(attachment)
    return attachment["id"]


def attachment_update(
    d: dict, b: OpBuilder, task_id: str, attachment_id: str, changes: dict
) -> None:
    """XU — shallow-merge changes into one attachment by id."""
    state = _state(d)
    task = _task(state, task_id)
    attachment = _attachment(task, attachment_id)
    if not changes:
        raise MutationError("attachment update: nothing to change")

    b.op(
        "XU",
        "UPD",
        "TASK",
        task_id,
        {
            "taskId": task_id,
            "taskAttachment": {"id": attachment_id, "changes": copy.deepcopy(changes)},
        },
    )

    attachment.update(copy.deepcopy(changes))


def attachment_delete(
    d: dict, b: OpBuilder, task_id: str, attachment_id: str
) -> None:
    """XD — drop one attachment by id (o=UPD: the entity is the TASK)."""
    state = _state(d)
    task = _task(state, task_id)
    _attachment(task, attachment_id)

    b.op("XD", "UPD", "TASK", task_id, {"taskId": task_id, "id": attachment_id})

    task["attachments"] = [
        a for a in _attachment_list(task) if a.get("id") != attachment_id
    ]


# ---------------------------------------------------------------- notes

def _note_reg(state: dict) -> dict:
    reg = state.setdefault("note", {"ids": [], "entities": {}, "todayOrder": []})
    reg.setdefault("ids", [])
    reg.setdefault("entities", {})
    reg.setdefault("todayOrder", [])
    return reg


def _note(state: dict, note_id: str) -> dict:
    try:
        return state["note"]["entities"][note_id]
    except KeyError:
        raise MutationError(f"note not found: {note_id}") from None


def _note_unlink(state: dict, note_id: str) -> None:
    """Drop the id from every project's noteIds (never leave dangling refs)."""
    for project in state["project"]["entities"].values():
        _list_remove(project.setdefault("noteIds", []), note_id)


def note_add(d: dict, b: OpBuilder, note: dict) -> str:
    """Create a note (already built via model.make_note)."""
    state = _state(d)
    reg = _note_reg(state)
    if note["id"] in reg["entities"]:
        raise MutationError(f"entity already exists: {note['id']}")
    project = None
    if note.get("projectId"):
        project = _project(state, note["projectId"])

    b.op("NA", "CRT", "NOTE", note["id"], {"note": copy.deepcopy(note)})

    # SP prepends everywhere: newest note first.
    reg["entities"][note["id"]] = note
    reg["ids"].insert(0, note["id"])
    if note.get("isPinnedToToday"):
        reg["todayOrder"].insert(0, note["id"])
    if project is not None:
        project.setdefault("noteIds", []).insert(0, note["id"])
    return note["id"]


def note_update(d: dict, b: OpBuilder, note_id: str, changes: dict) -> None:
    state = _state(d)
    reg = _note_reg(state)
    note = _note(state, note_id)
    changes = dict(changes)
    # SP's reducer prepends [id, ...todayOrder] with NO dedupe, so re-pinning an
    # already pinned note duplicates the id on the phone while our snapshot has
    # it once. A pin flag equal to the current value carries no information —
    # drop it (and its todayOrder churn) entirely.
    if "isPinnedToToday" in changes and bool(changes["isPinnedToToday"]) == bool(
        note.get("isPinnedToToday")
    ):
        del changes["isPinnedToToday"]
    if not changes:
        return  # nothing left to say: no op, no state change
    changes["modified"] = now_ms()

    b.op(
        "NU",
        "UPD",
        "NOTE",
        note_id,
        {"note": {"id": note_id, "changes": copy.deepcopy(changes)}},
    )

    # todayOrder moves ONLY when the pin flag is part of the patch.
    if "isPinnedToToday" in changes:
        if changes["isPinnedToToday"]:
            _list_remove(reg["todayOrder"], note_id)
            reg["todayOrder"].insert(0, note_id)
        else:
            _list_remove(reg["todayOrder"], note_id)
    note.update(changes)


def note_delete(d: dict, b: OpBuilder, note_id: str) -> None:
    state = _state(d)
    reg = _note_reg(state)
    note = _note(state, note_id)
    # SP's project reducer dereferences payload.projectId unguarded on replay,
    # so a dangling projectId would crash the app. Coerce it to null: the note
    # is not really in any project anyway.
    project_id = note.get("projectId")
    if project_id is not None and project_id not in state["project"]["entities"]:
        project_id = None
    # Payload carries the pre-delete linkage for the cross-slice reducers.
    b.op(
        "ND",
        "DEL",
        "NOTE",
        note_id,
        {
            "id": note_id,
            "projectId": project_id,
            "isPinnedToToday": bool(note.get("isPinnedToToday")),
        },
    )

    _reg_remove(reg, note_id)
    _list_remove(reg["todayOrder"], note_id)
    _note_unlink(state, note_id)


def note_move(d: dict, b: OpBuilder, note_id: str, target_project_id: str) -> None:
    state = _state(d)
    _note_reg(state)
    note = _note(state, note_id)
    target = _project(state, target_project_id)
    if note.get("projectId") == target_project_id:
        raise MutationError(f"note is already in project {target_project_id}")
    source_id = note.get("projectId")
    if source_id is not None and source_id not in state["project"]["entities"]:
        # NM replay dereferences the SOURCE project unguarded — refuse rather
        # than ship an op that crashes the app.
        raise MutationError(
            f"note {note_id}: source project '{source_id}' does not exist; "
            "run `sp doctor` and fix the note's project first"
        )

    # The payload note keeps the OLD projectId — that is the source list.
    b.op(
        "NM",
        "UPD",
        "NOTE",
        note_id,
        {"note": copy.deepcopy(note), "targetProjectId": target_project_id},
    )

    note["projectId"] = target_project_id
    _note_unlink(state, note_id)
    target.setdefault("noteIds", []).append(note_id)


# ---------------------------------------------------------------- boards

def _board_cfgs(state: dict) -> list:
    """state.boards is NOT an entity registry: a plain {boardCfgs: []} array."""
    boards = state.setdefault("boards", {"boardCfgs": []})
    if not isinstance(boards.get("boardCfgs"), list):
        boards["boardCfgs"] = []
    return boards["boardCfgs"]


def _board(state: dict, board_id: str) -> dict:
    for board in _board_cfgs(state):
        if board.get("id") == board_id:
            return board
    raise MutationError(f"board not found: {board_id}")


def find_panel(state: dict, panel_id: str) -> tuple[dict, int]:
    """(board, index-in-board.panels) of the FIRST panel with that id —
    the same lookup SP's reducer does."""
    for board in _board_cfgs(state):
        for i, panel in enumerate(board.get("panels") or []):
            if panel.get("id") == panel_id:
                return board, i
    raise MutationError(f"panel not found: {panel_id}")


def _check_panels(state: dict, board_id: str, panels: list[dict]) -> list[dict]:
    """Sanitize + validate a whole panels array before it is written."""
    clean = [sanitize_panel(p) for p in panels]
    seen: set[str] = set()
    for panel in clean:
        if not panel.get("id"):
            raise MutationError("panel id must be a non-empty string")
        if panel["id"] in seen:
            raise MutationError(f"duplicate panel id: {panel['id']}")
        seen.add(panel["id"])
        if TODAY_TAG_ID in panel["includedTagIds"]:
            raise MutationError("'TODAY' must never appear in panel.includedTagIds")
    # No tag/project existence checks here: seeded boards (Eisenhower/Kanban)
    # reference tag ids that only exist once the app creates them, and every
    # edit rewrites the WHOLE panels array. Refs from the CLI are resolved
    # (and thus validated) before they reach this point.
    # Panel ids must be unique GLOBALLY (a load-time dedup pass renames repeats).
    for other in _board_cfgs(state):
        if other.get("id") == board_id:
            continue
        for panel in other.get("panels") or []:
            if panel.get("id") in seen:
                raise MutationError(
                    f"panel id {panel['id']} already used on board "
                    f"{other.get('id')} — run `sp doctor` and give one of them "
                    "a fresh id"
                )
    return clean


def board_add(d: dict, b: OpBuilder, board: dict) -> str:
    """Create a board (already built via model.make_board)."""
    state = _state(d)
    cfgs = _board_cfgs(state)
    if any(cfg.get("id") == board["id"] for cfg in cfgs):
        raise MutationError(f"board already exists: {board['id']}")
    board = dict(board)
    board["panels"] = _check_panels(state, board["id"], board.get("panels") or [])

    b.op("BA", "CRT", "BOARD", board["id"], {"board": copy.deepcopy(board)})

    cfgs.append(board)
    return board["id"]


def board_update(d: dict, b: OpBuilder, board_id: str, updates: dict) -> None:
    """Shallow merge on the board. The ONLY way to edit panels (whole array)."""
    state = _state(d)
    board = _board(state, board_id)
    updates = dict(updates)
    if "panels" in updates:
        updates["panels"] = _check_panels(state, board_id, updates["panels"])
    if "cols" in updates:
        updates["cols"] = int(updates["cols"])

    b.op(
        "BU",
        "UPD",
        "BOARD",
        board_id,
        {"id": board_id, "updates": copy.deepcopy(updates)},
    )

    board.update(updates)


def board_delete(d: dict, b: OpBuilder, board_id: str) -> None:
    state = _state(d)
    _board(state, board_id)
    b.op("BD", "DEL", "BOARD", board_id, {"id": board_id})
    cfgs = _board_cfgs(state)
    cfgs[:] = [cfg for cfg in cfgs if cfg.get("id") != board_id]


def panel_add(d: dict, b: OpBuilder, board_id: str, panel: dict) -> str:
    """Append a panel — emitted as a BU rewriting the whole panels array."""
    state = _state(d)
    board = _board(state, board_id)
    panels = list(board.get("panels") or []) + [panel]
    board_update(d, b, board_id, {"panels": panels})
    return panel["id"]


def panel_update(d: dict, b: OpBuilder, panel_id: str, changes: dict) -> str:
    """Patch one panel; rewrites its board's panels array via BU."""
    state = _state(d)
    board, index = find_panel(state, panel_id)
    panels = [dict(p) for p in board["panels"]]
    panels[index] = {**panels[index], **changes}
    board_update(d, b, board["id"], {"panels": panels})
    return board["id"]


def panel_remove(d: dict, b: OpBuilder, panel_id: str) -> str:
    state = _state(d)
    board, index = find_panel(state, panel_id)
    panels = [dict(p) for i, p in enumerate(board["panels"]) if i != index]
    board_update(d, b, board["id"], {"panels": panels})
    return board["id"]


def panel_task_order(
    d: dict, b: OpBuilder, panel_id: str, task_ids: list[str]
) -> None:
    """BT: manual ORDERING only — membership stays derived from the filters."""
    state = _state(d)
    board, index = find_panel(state, panel_id)
    panel = board["panels"][index]
    if panel.get("sortBy"):
        raise MutationError(
            f"panel {panel_id} is sorted by '{panel['sortBy']}': sorted panels "
            "ignore manual order — run `sp board panel edit "
            f"{panel_id} --sort manual` first"
        )
    for tid in task_ids:
        _task(state, tid)

    b.op(
        "BT",
        "UPD",
        "BOARD",
        panel_id,
        {"panelId": panel_id, "taskIds": list(task_ids)},
    )

    board["panels"][index]["taskIds"] = list(task_ids)


def boards_sort(d: dict, b: OpBuilder, board_ids: list[str]) -> None:
    """BS: listed boards first, in the given order; the rest keep their tail."""
    state = _state(d)
    if not board_ids:
        raise MutationError("board sort: no board ids given")
    for bid in board_ids:
        _board(state, bid)
    if len(set(board_ids)) != len(board_ids):
        raise MutationError("board sort: duplicate ids")

    b.op(
        "BS",
        "MOV",
        "BOARD",
        board_ids[0],
        {"ids": list(board_ids)},
        ds=list(board_ids),
    )

    cfgs = _board_cfgs(state)
    # Entries without an id can only come from a foreign writer: keep them in
    # the tail rather than blowing up the reorder.
    by_id = {cfg.get("id"): cfg for cfg in cfgs if cfg.get("id")}
    cfgs[:] = [by_id[bid] for bid in board_ids] + [
        cfg for cfg in cfgs if cfg.get("id") not in board_ids
    ]


# ---------------------------------------------------------------- counters

def _counter_reg(state: dict) -> dict:
    reg = state.setdefault("simpleCounter", {"ids": [], "entities": {}})
    reg.setdefault("ids", [])
    reg.setdefault("entities", {})
    return reg


def _day_key(value: object, what: str) -> str:
    """A day key (countOnDay key, metric id) must be plain 'YYYY-MM-DD'."""
    if not isinstance(value, str):
        raise MutationError(f"{what}: date must be a 'YYYY-MM-DD' string")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.isoformat() != value:
        raise MutationError(f"{what}: invalid date {value!r} (use YYYY-MM-DD)")
    return value


def _counter(state: dict, counter_id: str) -> dict:
    try:
        return state["simpleCounter"]["entities"][counter_id]
    except KeyError:
        raise MutationError(f"counter not found: {counter_id}") from None


def counter_add(d: dict, b: OpBuilder, counter: dict) -> str:
    """Create a simple counter (already built via model.make_simple_counter)."""
    state = _state(d)
    reg = _counter_reg(state)
    counter = dict(counter)
    counter["isOn"] = False  # device-local, never synced as True
    if counter["id"] in reg["entities"]:
        raise MutationError(f"entity already exists: {counter['id']}")

    b.op(
        "SA",
        "CRT",
        "SIMPLE_COUNTER",
        counter["id"],
        {"simpleCounter": copy.deepcopy(counter)},
    )

    _reg_add(reg, counter)
    return counter["id"]


def counter_update(d: dict, b: OpBuilder, counter_id: str, changes: dict) -> None:
    state = _state(d)
    _counter_reg(state)
    counter = _counter(state, counter_id)
    changes = dict(changes)
    if "isOn" in changes:
        raise MutationError("isOn is device-local and must not be synced")
    if "countOnDay" in changes:
        raise MutationError("use counter_set / counter_log_time to change countOnDay")
    if not changes:
        return

    b.op(
        "SU",
        "UPD",
        "SIMPLE_COUNTER",
        counter_id,
        {"simpleCounter": {"id": counter_id, "changes": copy.deepcopy(changes)}},
    )

    counter.update(changes)


def counter_delete(d: dict, b: OpBuilder, counter_id: str) -> None:
    state = _state(d)
    reg = _counter_reg(state)
    _counter(state, counter_id)
    b.op("SD", "DEL", "SIMPLE_COUNTER", counter_id, {"id": counter_id})
    _reg_remove(reg, counter_id)


def counter_set(
    d: dict, b: OpBuilder, counter_id: str, value: int, date: str | None = None
) -> int:
    """Absolute value for a day: ST for today, SFD otherwise. Clamped to >= 0."""
    state = _state(d)
    _counter_reg(state)
    counter = _counter(state, counter_id)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MutationError("counter set: value must be an integer")
    new_val = max(0, value)
    date = date or today_str()

    if date == today_str():
        b.op(
            "ST",
            "UPD",
            "SIMPLE_COUNTER",
            counter_id,
            {"id": counter_id, "newVal": new_val, "today": date},
        )
    else:
        b.op(
            "SFD",
            "UPD",
            "SIMPLE_COUNTER",
            counter_id,
            {"id": counter_id, "date": date, "newVal": new_val},
        )

    counter.setdefault("countOnDay", {})[date] = new_val
    return new_val


def counter_inc(
    d: dict, b: OpBuilder, counter_id: str, by: int = 1, date: str | None = None
) -> int:
    """Increment by reading the current value and emitting an ABSOLUTE ST/SFD
    (SI/SX are not persistent and must never be emitted)."""
    state = _state(d)
    _counter_reg(state)
    counter = _counter(state, counter_id)
    date = date or today_str()
    current = int((counter.get("countOnDay") or {}).get(date, 0))
    return counter_set(d, b, counter_id, current + int(by), date)


def counter_log_time(
    d: dict, b: OpBuilder, counter_id: str, date: str, duration: int
) -> int:
    """SC: an ADDITIVE delta (ms) for StopWatch counters. SP's reducer is
    isRemote-gated, so the CLI applies the += to its own state copy itself."""
    state = _state(d)
    _counter_reg(state)
    counter = _counter(state, counter_id)
    if counter.get("type") != "StopWatch":
        raise MutationError(
            f"counter log: {counter_id} is not a StopWatch counter "
            "(use counter_set / counter_inc)"
        )
    date = _day_key(date, "counter log")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        raise MutationError("counter log: duration must be a non-negative integer (ms)")

    b.op(
        "SC",
        "UPD",
        "SIMPLE_COUNTER",
        counter_id,
        {"id": counter_id, "date": date, "duration": duration},
    )

    per_day = counter.setdefault("countOnDay", {})
    per_day[date] = int(per_day.get(date, 0)) + duration
    return per_day[date]


def counter_order(d: dict, b: OpBuilder, counter_ids: list[str]) -> None:
    """SM: the payload replaces the whole ids array, so it must list every
    counter exactly once."""
    state = _state(d)
    reg = _counter_reg(state)
    if not counter_ids:
        raise MutationError("counter order: no counter ids given")
    if len(set(counter_ids)) != len(counter_ids):
        raise MutationError("counter order: duplicate ids")
    if sorted(counter_ids) != sorted(reg["ids"]):
        raise MutationError(
            "counter order: id list must be a permutation of all counter ids"
        )

    b.op(
        "SM",
        "MOV",
        "SIMPLE_COUNTER",
        counter_ids[0],
        {"ids": list(counter_ids)},
        ds=list(counter_ids),
    )

    reg["ids"] = list(counter_ids)


# ---------------------------------------------------------------- metrics

def _metric_reg(state: dict) -> dict:
    reg = state.setdefault("metric", {"ids": [], "entities": {}})
    reg.setdefault("ids", [])
    reg.setdefault("entities", {})
    return reg


def _metric_int(value: object, field: str, lo: int, hi: int | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MutationError(f"metric {field}: must be an integer")
    if value < lo or (hi is not None and value > hi):
        bounds = f"{lo}-{hi}" if hi is not None else f">= {lo}"
        raise MutationError(f"metric {field}: must be {bounds} (got {value})")
    return value


def _metric_changes(changes: dict) -> dict:
    """Validate a metric patch: known fields only, no resurrected dead ones."""
    clean = copy.deepcopy(dict(changes))
    clean.pop("id", None)  # the id is the day and is never patched
    for field in clean:
        if field in METRIC_DEAD_FIELDS:
            raise MutationError(
                f"metric {field}: this field no longer exists in "
                "SuperProductivity and must never be written"
            )
        if field == "focusSessions":
            raise MutationError(
                "use metric_log_focus to add a focus session "
                "(a patch would overwrite the whole array)"
            )
        if field not in METRIC_FIELDS:
            raise MutationError(f"metric: unknown field '{field}'")
    if clean.get("impactOfWork") is not None:
        _metric_int(clean["impactOfWork"], "impactOfWork", 1, 4)
    if clean.get("energyCheckin") is not None:
        _metric_int(clean["energyCheckin"], "energyCheckin", 1, 3)
    for field in ("totalWorkMinutes", "completedTasks", "plannedTasks"):
        if clean.get(field) is not None:
            _metric_int(clean[field], field, 0, None)
    if "notes" in clean and not isinstance(clean["notes"], (str, type(None))):
        raise MutationError("metric notes: must be a string")
    if "remindTomorrow" in clean and not isinstance(clean["remindTomorrow"], bool):
        raise MutationError("metric remindTomorrow: must be a boolean")
    if "reflections" in clean:
        reflections = clean["reflections"]
        if not isinstance(reflections, list):
            raise MutationError("metric reflections: must be an array")
        for item in reflections:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise MutationError(
                    "metric reflections: each entry needs a 'text' string"
                )
    return clean


def metric_update(d: dict, b: OpBuilder, day: str, changes: dict) -> str:
    """EU: a self-creating patch — SP upserts {id, ...DEFAULT, ...changes}
    when the day has no metric yet, so the CLI mirrors that locally."""
    state = _state(d)
    reg = _metric_reg(state)
    day = _day_key(day, "metric set")
    changes = _metric_changes(changes)
    if not changes:
        return day

    b.op(
        "EU",
        "UPD",
        "METRIC",
        day,
        {"metric": {"id": day, "changes": copy.deepcopy(changes)}},
    )

    metric = reg["entities"].get(day)
    if metric is None:
        _reg_add(reg, make_metric(day, **changes))
    else:
        metric.update(copy.deepcopy(changes))
    return day


def metric_log_focus(d: dict, b: OpBuilder, day: str, duration: int) -> int:
    """EL: an ADDITIVE focus session (ms) appended to the day's
    focusSessions. Also self-creating; duration must be positive (SP's
    reducer treats <= 0 as a no-op)."""
    state = _state(d)
    reg = _metric_reg(state)
    day = _day_key(day, "metric focus")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise MutationError("metric focus: duration must be a positive number (ms)")

    b.op("EL", "UPD", "METRIC", day, {"day": day, "duration": duration})

    metric = reg["entities"].get(day)
    if metric is None:
        metric = make_metric(day)
        _reg_add(reg, metric)
    sessions = metric.setdefault("focusSessions", [])
    sessions.append(duration)
    return len(sessions)


def metric_delete(d: dict, b: OpBuilder, day: str) -> None:
    state = _state(d)
    reg = _metric_reg(state)
    if day not in reg["entities"]:
        raise MutationError(f"metric not found: {day}")
    b.op("ED", "DEL", "METRIC", day, {"id": day})
    _reg_remove(reg, day)


# ------------------------------------------------------- issue providers

def _provider_reg(state: dict) -> dict:
    reg = state.setdefault("issueProvider", {"ids": [], "entities": {}})
    reg.setdefault("ids", [])
    reg.setdefault("entities", {})
    return reg


def _provider(state: dict, provider_id: str) -> dict:
    try:
        return state["issueProvider"]["entities"][provider_id]
    except KeyError:
        raise MutationError(f"issue provider not found: {provider_id}") from None


def provider_add(d: dict, b: OpBuilder, provider: dict) -> str:
    """IA: create an issue provider (built via model.make_issue_provider).

    The payload carries the FULL provider object — SP validates built-in
    provider cfgs with typia and rejects a partial one.
    """
    state = _state(d)
    reg = _provider_reg(state)
    provider = copy.deepcopy(provider)
    pid = provider.get("id")
    if not pid:
        raise MutationError("provider add: missing id")
    if pid in reg["entities"]:
        raise MutationError(f"entity already exists: {pid}")

    b.op("IA", "CRT", "ISSUE_PROVIDER", pid, {"issueProvider": copy.deepcopy(provider)})

    _reg_add(reg, provider)
    return pid


def provider_update(d: dict, b: OpBuilder, provider_id: str, changes: dict) -> None:
    state = _state(d)
    _provider_reg(state)
    provider = _provider(state, provider_id)
    changes = dict(changes)
    if "id" in changes or "issueProviderKey" in changes:
        raise MutationError("provider edit: id / issueProviderKey are immutable")
    if not changes:
        return

    b.op(
        "IU",
        "UPD",
        "ISSUE_PROVIDER",
        provider_id,
        {"issueProvider": {"id": provider_id, "changes": copy.deepcopy(changes)}},
    )

    provider.update(copy.deepcopy(changes))


def _unlink_issue_fields(task: dict) -> None:
    for field in ISSUE_TASK_FIELDS:
        task.pop(field, None)


def provider_delete(d: dict, b: OpBuilder, provider_id: str) -> list[str]:
    """HID: delete a provider and unlink every task that referenced it.

    taskIdsToUnlink is collected from live tasks AND every archive blob
    (archiveYoung/archiveOld, top-level and under `state`); the issue fields
    are cleared in all of them. Returns the unlinked ids.
    """
    state = _state(d)
    reg = _provider_reg(state)
    _provider(state, provider_id)

    live = [
        tid
        for tid in state["task"]["ids"]
        if state["task"]["entities"].get(tid, {}).get("issueProviderId")
        == provider_id
    ]
    archives = archive_task_entity_maps(d)
    task_ids = list(live)
    for entities in archives:
        for tid, task in entities.items():
            if task.get("issueProviderId") == provider_id and tid not in task_ids:
                task_ids.append(tid)

    b.op(
        "HID",
        "DEL",
        "ISSUE_PROVIDER",
        provider_id,
        {"issueProviderId": provider_id, "taskIdsToUnlink": list(task_ids)},
    )

    for tid in live:
        _unlink_issue_fields(state["task"]["entities"][tid])
    for entities in archives:
        for task in entities.values():
            if task.get("issueProviderId") == provider_id:
                _unlink_issue_fields(task)
    _reg_remove(reg, provider_id)
    return task_ids


def provider_order(d: dict, b: OpBuilder, provider_ids: list[str]) -> None:
    """IS: listed providers first, in the given order; the rest keep their tail."""
    state = _state(d)
    reg = _provider_reg(state)
    if not provider_ids:
        raise MutationError("provider order: no provider ids given")
    if len(set(provider_ids)) != len(provider_ids):
        raise MutationError("provider order: duplicate ids")
    for pid in provider_ids:
        _provider(state, pid)

    b.op(
        "IS",
        "MOV",
        "ISSUE_PROVIDER",
        provider_ids[0],
        {"ids": list(provider_ids)},
        ds=list(provider_ids),
    )

    reg["ids"] = list(provider_ids) + [
        pid for pid in reg["ids"] if pid not in provider_ids
    ]


# ---------------------------------------------------------------- archive

def archive_done(d: dict, b: OpBuilder) -> list[str]:
    """Move done top-level tasks (with their subtasks) into top-level
    archiveYoung. Returns archived parent ids ([] and no op when none)."""
    state = _state(d)
    reg = state["task"]
    parent_ids = [
        tid
        for tid in reg["ids"]
        if reg["entities"][tid].get("isDone")
        and not reg["entities"][tid].get("parentId")
    ]
    if not parent_ids:
        return []

    snapshots = [task_with_subtasks(state, tid) for tid in parent_ids]
    b.op(
        "HX",
        "UPD",
        "TASK",
        parent_ids[0],
        {"tasks": copy.deepcopy(snapshots)},
        ds=list(parent_ids),
    )

    archive = d.get("archiveYoung")
    if not isinstance(archive, dict):
        archive = {
            "task": {"ids": [], "entities": {}},
            "timeTracking": {"project": {}, "tag": {}},
            "lastTimeTrackingFlush": now_ms(),
        }
        d["archiveYoung"] = archive
    arch_reg = archive.setdefault("task", {"ids": [], "entities": {}})
    arch_reg.setdefault("ids", [])
    arch_reg.setdefault("entities", {})

    for snapshot in snapshots:
        subtasks = snapshot.pop("subTasks")
        # Archived entities are plain Tasks: no 'subTasks' key at all
        # (subtasks are stored flattened alongside their parent).
        parent_entity = dict(snapshot)
        for entity in [parent_entity] + [dict(s) for s in subtasks]:
            eid = entity["id"]
            if eid not in arch_reg["entities"]:
                arch_reg["ids"].append(eid)
            arch_reg["entities"][eid] = entity

    for tid in parent_ids:
        _cascade_remove_task(state, tid)
    return parent_ids


def _archive_find(d: dict, task_id: str) -> dict | None:
    for entities in archive_task_entity_maps(d):
        task = entities.get(task_id)
        if task is not None:
            return task
    return None


def _archive_remove(d: dict, task_ids: list[str]) -> None:
    """Drop the ids from EVERY archive blob (young and old, top-level and
    under `state`) — a task can sit in more than one of them."""
    for _key, reg in archive_task_blobs(d):
        for tid in task_ids:
            reg["entities"].pop(tid, None)
            _list_remove(reg.setdefault("ids", []), tid)


def _materialize_restore_today(
    root: dict, subs: list[dict], today: str, d: dict | None
) -> None:
    """The `restoreToToday` transform SP's action creator applies BEFORE the
    op leaves the device — so the payload must already carry it.

    Same rule as plan_today: dueDay=today always, remindAt gone, dueWithTime
    kept when it already falls on today's logical day."""
    root.pop("remindAt", None)
    root["dueDay"] = today
    if _should_clear_due_time_for_today(root.get("dueWithTime"), today, d):
        root.pop("dueWithTime", None)
    for sub in subs:
        for field in ("dueDay", "dueWithTime", "remindAt"):
            sub.pop(field, None)


def _normalize_restored(state: dict, root: dict, subs: list[dict]) -> None:
    """The receiver-side normalization of a restored task (HR reducer):
    live again, no doneOn, a project that exists, no TODAY tag, no dangling
    repeatCfgId; subtasks re-pointed at the parent.

    (Section membership is NOT a task field — see `_sections` — so nothing is
    stripped from the entity here; `restore_task` clears the section refs.)"""
    projects = state["project"]["entities"]
    tags = state["tag"]["entities"]
    cfgs = (state.get("taskRepeatCfg") or {}).get("entities") or {}

    if root.get("projectId") not in projects:
        # No Inbox in this file? Degrade instead of exploding: the task comes
        # back holding its (dangling) projectId and `doctor` flags it.
        if INBOX_PROJECT_ID in projects:
            root["projectId"] = INBOX_PROJECT_ID
    root["isDone"] = False
    root.pop("doneOn", None)
    for entity in [root] + subs:
        if entity.get("repeatCfgId") and entity["repeatCfgId"] not in cfgs:
            entity.pop("repeatCfgId", None)
    for entity in [root] + subs:
        # TODAY is never a stored tag, and a tag deleted while the task sat
        # in the archive would leave a dangling reference.
        entity["tagIds"] = [
            t for t in entity.get("tagIds", []) if t != TODAY_TAG_ID and t in tags
        ]
    for sub in subs:
        sub["parentId"] = root["id"]
        sub["projectId"] = root["projectId"]


def restore_task(
    d: dict, b: OpBuilder, task_id: str, to_today: bool = False
) -> list[str]:
    """Bring an archived top-level task (with its subtasks) back to life.

    Emits one HR whose payload is already materialized (`restoreToToday`
    transforms applied), then reproduces the reducer's state effects:
    the ids leave every archive blob, the normalized entities are added back,
    the root is unique-appended to `project.taskIds` (NEVER the backlog) and
    to each of its tags. Returns `[root, *subtasks]`.

    Archive maintenance (young→old flush AF, compaction AC) is the app's job
    and is deliberately never emitted here.
    """
    state = _state(d)
    if task_id in state["task"]["entities"]:
        raise MutationError(f"task {task_id} is already active")
    archived = _archive_find(d, task_id)
    if archived is None:
        raise MutationError(f"task not in the archive: {task_id}")
    if archived.get("parentId"):
        raise MutationError(
            f"{task_id} is an archived subtask; restore its parent "
            f"{archived['parentId']} instead"
        )

    root = copy.deepcopy(archived)
    root.pop("subTasks", None)
    subs: list[dict] = []
    adopted: list[str] = []
    sub_ids: list[str] = []
    for sid in list(root.get("subTaskIds") or []):
        if sid in state["task"]["entities"]:
            # Already live (a partial restore, or an out-of-band edit): keep
            # the link and adopt it rather than refusing the whole restore.
            adopted.append(sid)
            sub_ids.append(sid)
            continue
        sub = _archive_find(d, sid)
        if sub is None:
            continue  # gone from the archive: prune the dangling reference
        sub = copy.deepcopy(sub)
        sub.pop("subTasks", None)
        subs.append(sub)
        sub_ids.append(sid)
    # The reducer reads task.subTaskIds to clean the archive — keep it exact.
    root["subTaskIds"] = sub_ids

    today = logical_today_str(d)
    if to_today:
        _materialize_restore_today(root, subs, today, d)

    payload = {"task": copy.deepcopy(root), "subTasks": copy.deepcopy(subs)}
    if to_today:
        payload["restoreToToday"] = {
            "today": today,
            "startOfNextDayDiffMs": start_of_next_day_diff_ms(d),
        }
    b.op("HR", "UPD", "TASK", task_id, payload)

    _normalize_restored(state, root, subs)
    project = state["project"]["entities"].get(root.get("projectId"))
    all_ids = [task_id] + list(sub_ids)

    _archive_remove(d, all_ids)
    _reg_add(state["task"], root)
    for sub in subs:
        _reg_add(state["task"], sub)
    for sid in adopted:
        live = state["task"]["entities"][sid]
        live["parentId"] = root["id"]
        live["projectId"] = root["projectId"]

    # removeTasksFromAllProjects: no stale membership may survive anywhere.
    for other in state["project"]["entities"].values():
        for key in ("taskIds", "backlogTaskIds"):
            for tid in all_ids:
                _list_remove(other.get(key) or [], tid)
    # HR's section handler is `removeTaskIdsFromProjectSections` with no
    # project filter: a restored task comes back section-less, so any ref a
    # pre-fix archive left in a PROJECT section is stripped.
    _section_remove_task_ids(state, all_ids, context_type="PROJECT")
    if project is not None:
        project.setdefault("taskIds", []).append(task_id)
    for entity in [root] + subs:
        for tag_id in entity["tagIds"]:
            tag = _tag(state, tag_id)
            if entity["id"] not in tag.setdefault("taskIds", []):
                tag["taskIds"].append(entity["id"])
    if to_today:
        _today_prepend(state, [task_id])
        _planner_purge(state, all_ids)
    return all_ids
