"""State mutations. Each function applies a logical change to d['state']
(and top-level archives where relevant) AND appends the matching op(s) via
the OpBuilder — keeping the snapshot contract: state always reflects the
effect of every op in recentOps."""

from __future__ import annotations

import copy

from sp_cli.model import (
    TODAY_TAG_ID,
    day_of_ms,
    make_repeat_cfg,
    now_ms,
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


def _today_order(state: dict) -> list:
    return _tag(state, TODAY_TAG_ID)["taskIds"]


def _today_prepend(state: dict, task_ids: list[str]) -> None:
    order = _today_order(state)
    for tid in reversed(task_ids):
        _list_remove(order, tid)
        order.insert(0, tid)


def _planner_purge(state: dict, task_ids: list[str]) -> None:
    days = state.get("planner", {}).get("days", {})
    for day_list in days.values():
        for tid in task_ids:
            _list_remove(day_list, tid)


def _clear_due_with_time(task: dict, changes: dict | None = None) -> None:
    task["dueWithTime"] = None
    task["remindAt"] = None
    if changes is not None:
        changes["dueWithTime"] = None
        changes["remindAt"] = None


# ---------------------------------------------------------------- tasks

def add_task(d: dict, b: OpBuilder, task: dict) -> str:
    """Create a top-level task (already built via model.make_task)."""
    state = _state(d)
    if TODAY_TAG_ID in task.get("tagIds", []):
        raise MutationError("'TODAY' must never appear in task.tagIds")
    if task.get("dueDay") is not None and task.get("dueWithTime") is not None:
        raise MutationError("dueDay and dueWithTime are mutually exclusive")
    project = _project(state, task["projectId"])
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
            "isAddToBacklog": False,
            "isAddToBottom": True,
        },
    )

    _reg_add(state["task"], task)
    project["taskIds"].append(task["id"])
    for tag_id in task.get("tagIds", []):
        tag = _tag(state, tag_id)
        if task["id"] not in tag["taskIds"]:
            tag["taskIds"].append(task["id"])
    if task.get("dueDay") == today_str():
        _today_prepend(state, [task["id"]])
    return task["id"]


def update_task(d: dict, b: OpBuilder, task_id: str, changes: dict) -> None:
    state = _state(d)
    task = _task(state, task_id)
    changes = dict(changes)
    if "tagIds" in changes and TODAY_TAG_ID in changes["tagIds"]:
        raise MutationError("'TODAY' must never appear in task.tagIds")

    # XOR invariant: setting one due mechanism clears the other, explicitly.
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


def delete_task(d: dict, b: OpBuilder, task_id: str) -> None:
    state = _state(d)
    _task(state, task_id)
    snapshot = task_with_subtasks(state, task_id)
    b.op("HD", "DEL", "TASK", task_id, {"task": snapshot})
    _cascade_remove_task(state, task_id)


def delete_tasks(d: dict, b: OpBuilder, task_ids: list[str]) -> None:
    state = _state(d)
    for tid in task_ids:
        _task(state, tid)
    b.op("HDM", "DEL", "TASK", task_ids[0], {"taskIds": list(task_ids)}, ds=list(task_ids))
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


# ---------------------------------------------------------------- planning

def plan_today(d: dict, b: OpBuilder, task_ids: list[str]) -> None:
    state = _state(d)
    today = today_str()
    for tid in task_ids:
        _task(state, tid)

    b.op(
        "HPT",
        "UPD",
        "TASK",
        task_ids[0],
        {"taskIds": list(task_ids), "today": today},
        ds=list(task_ids),
    )

    for tid in task_ids:
        task = _task(state, tid)
        task["dueDay"] = today
        _clear_due_with_time(task)
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
        parent_entity = dict(snapshot)
        parent_entity["subTasks"] = []  # flattened: subtasks stored separately
        for entity in [parent_entity] + [dict(s) for s in subtasks]:
            eid = entity["id"]
            if eid not in arch_reg["entities"]:
                arch_reg["ids"].append(eid)
            arch_reg["entities"][eid] = entity

    for tid in parent_ids:
        _cascade_remove_task(state, tid)
    return parent_ids
