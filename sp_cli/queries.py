"""Read-only layer: id resolution, filters, today/agenda, worklog, doctor."""

from __future__ import annotations

import datetime

from sp_cli.model import TODAY_TAG_ID, day_of_ms, today_str


class QueryError(Exception):
    pass


class AmbiguousIdError(QueryError):
    def __init__(self, prefix: str, candidates: list[str]):
        self.candidates = candidates
        super().__init__(
            f"ambiguous id prefix '{prefix}'; candidates: {', '.join(candidates)}"
        )


class NotFoundError(QueryError):
    pass


# ---------------------------------------------------------------- resolution

def resolve_task(d: dict, ref: str) -> str:
    ids = d["state"]["task"]["ids"]
    if ref in ids:
        return ref
    matches = [tid for tid in ids if tid.startswith(ref)]
    if not matches:
        # Ids may start with '-'/'_' which is awkward to type as a CLI
        # argument; allow a prefix with those leading chars omitted.
        matches = [tid for tid in ids if tid.lstrip("-_").startswith(ref)]
    if not matches:
        raise NotFoundError(f"no task with id (prefix) '{ref}'")
    if len(matches) > 1:
        raise AmbiguousIdError(ref, matches)
    return matches[0]


def _resolve_named(d: dict, registry: str, ref: str, kind: str) -> str:
    reg = d["state"][registry]
    if ref in reg["entities"]:
        return ref
    by_title = [
        eid
        for eid, e in reg["entities"].items()
        if e.get("title", "").lower() == ref.lower()
    ]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_title) > 1:
        raise AmbiguousIdError(ref, by_title)
    raise NotFoundError(f"no {kind} with id or title '{ref}'")


def resolve_project(d: dict, ref: str) -> str:
    return _resolve_named(d, "project", ref, "project")


def resolve_tag(d: dict, ref: str) -> str:
    return _resolve_named(d, "tag", ref, "tag")


# ---------------------------------------------------------------- membership

def is_today_member(task: dict, today: str | None = None) -> bool:
    today = today or today_str()
    if task.get("dueWithTime") is not None:
        return day_of_ms(task["dueWithTime"]) == today
    return task.get("dueDay") == today


def is_overdue(task: dict, today: str | None = None, now: int | None = None) -> bool:
    """Overdue = due strictly before today. Tasks scheduled earlier TODAY are
    today-members, not overdue. (`now` kept for signature compatibility.)"""
    if task.get("isDone"):
        return False
    today = today or today_str()
    if task.get("dueWithTime") is not None:
        return day_of_ms(task["dueWithTime"]) < today
    due_day = task.get("dueDay")
    return due_day is not None and due_day < today


def is_unscheduled(task: dict) -> bool:
    return task.get("dueDay") is None and task.get("dueWithTime") is None


# ---------------------------------------------------------------- listing

def all_tasks(d: dict) -> list[dict]:
    reg = d["state"]["task"]
    return [reg["entities"][tid] for tid in reg["ids"] if tid in reg["entities"]]


def list_tasks(
    d: dict,
    project: str | None = None,
    tag: str | None = None,
    include_done: bool = False,
    only_done: bool = False,
    overdue: bool = False,
    today: bool = False,
    unscheduled: bool = False,
    search: str | None = None,
    parents_only: bool = False,
) -> list[dict]:
    tasks = all_tasks(d)
    today_s = today_str()
    if project is not None:
        pid = resolve_project(d, project)
        tasks = [t for t in tasks if t.get("projectId") == pid]
    if tag is not None:
        tgid = resolve_tag(d, tag)
        tasks = [t for t in tasks if tgid in t.get("tagIds", [])]
    if only_done:
        tasks = [t for t in tasks if t.get("isDone")]
    elif not include_done:
        tasks = [t for t in tasks if not t.get("isDone")]
    if overdue:
        tasks = [t for t in tasks if is_overdue(t, today_s)]
    if today:
        tasks = [t for t in tasks if is_today_member(t, today_s)]
    if unscheduled:
        tasks = [t for t in tasks if is_unscheduled(t)]
    if search:
        needle = search.lower()
        tasks = [
            t
            for t in tasks
            if needle in t.get("title", "").lower()
            or needle in (t.get("notes") or "").lower()
        ]
    if parents_only:
        tasks = [t for t in tasks if not t.get("parentId")]
    return tasks


def today_list(d: dict) -> list[dict]:
    """Members per the rule, ordered by TODAY tag ordering, extras appended."""
    state = d["state"]
    today_s = today_str()
    entities = state["task"]["entities"]
    members = [t["id"] for t in all_tasks(d) if is_today_member(t, today_s)]
    member_set = set(members)
    order = state["tag"]["entities"].get(TODAY_TAG_ID, {}).get("taskIds", [])
    ordered = [tid for tid in order if tid in member_set]
    ordered += [tid for tid in members if tid not in ordered]
    return [entities[tid] for tid in ordered]


def agenda(d: dict) -> dict:
    today_s = today_str()
    now = int(datetime.datetime.now().timestamp() * 1000)
    horizon = (
        datetime.date.fromisoformat(today_s) + datetime.timedelta(days=7)
    ).isoformat()
    tasks = [t for t in all_tasks(d) if not t.get("isDone")]

    overdue = [t for t in tasks if is_overdue(t, today_s, now)]
    today_tasks = today_list(d)
    today_tasks = [t for t in today_tasks if not t.get("isDone")]
    scheduled_today = sorted(
        (
            t
            for t in tasks
            if t.get("dueWithTime") is not None
            and day_of_ms(t["dueWithTime"]) == today_s
        ),
        key=lambda t: t["dueWithTime"],
    )

    def deadline_day(t: dict) -> str | None:
        if t.get("deadlineDay"):
            return t["deadlineDay"]
        if t.get("deadlineWithTime") is not None:
            return day_of_ms(t["deadlineWithTime"])
        return None

    deadlines = sorted(
        (t for t in tasks if (dd := deadline_day(t)) is not None and dd <= horizon),
        key=lambda t: deadline_day(t) or "",
    )
    return {
        "overdue": overdue,
        "today": today_tasks,
        "scheduled_today": scheduled_today,
        "deadlines": deadlines,
    }


# ---------------------------------------------------------------- worklog

def _archive_tasks(d: dict) -> list[dict]:
    tasks = []
    for key in ("archiveYoung", "archiveOld"):
        blob = d.get(key) or d["state"].get(key) or {}
        reg = blob.get("task") or {}
        tasks.extend((reg.get("entities") or {}).values())
    return tasks


def worklog(d: dict, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Aggregate timeSpentOnDay over live + archived tasks.

    Returns {days: {date: ms}, projects: {projectId: ms},
             estimate: {estimated, spent}}.
    """
    days: dict[str, int] = {}
    projects: dict[str, int] = {}
    estimated = 0
    spent_on_estimated = 0
    for task in all_tasks(d) + _archive_tasks(d):
        task_total = 0
        for date, ms in (task.get("timeSpentOnDay") or {}).items():
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue
            ms = int(ms)
            days[date] = days.get(date, 0) + ms
            pid = task.get("projectId") or "?"
            projects[pid] = projects.get(pid, 0) + ms
            task_total += ms
        if task.get("timeEstimate") and task_total:
            estimated += int(task["timeEstimate"])
            spent_on_estimated += task_total
    return {
        "days": dict(sorted(days.items())),
        "projects": projects,
        "estimate": {"estimated": estimated, "spent": spent_on_estimated},
    }


# ---------------------------------------------------------------- doctor

def doctor(d: dict) -> list[str]:
    """Validate state invariants; returns a list of problem descriptions."""
    problems: list[str] = []
    state = d["state"]

    for name in ("task", "project", "tag", "taskRepeatCfg"):
        reg = state.get(name)
        if not reg:
            continue
        ids, entities = reg.get("ids", []), reg.get("entities", {})
        for eid in ids:
            if eid not in entities:
                problems.append(f"{name}: id '{eid}' in ids but not in entities")
        for eid in entities:
            if eid not in ids:
                problems.append(f"{name}: entity '{eid}' not listed in ids")
        for eid, e in entities.items():
            if e.get("id") != eid:
                problems.append(f"{name}: entity key '{eid}' != entity.id '{e.get('id')}'")
        if len(set(ids)) != len(ids):
            problems.append(f"{name}: duplicate ids")

    tasks = state["task"]["entities"]
    task_ids = set(tasks)

    for pid, project in state["project"]["entities"].items():
        for tid in project.get("taskIds", []):
            if tid not in task_ids:
                problems.append(f"project {pid}: taskIds references missing task {tid}")
            elif tasks[tid].get("parentId"):
                problems.append(f"project {pid}: subtask {tid} listed in project.taskIds")
        for tid in project.get("backlogTaskIds", []):
            if tid not in task_ids:
                problems.append(
                    f"project {pid}: backlogTaskIds references missing task {tid}"
                )

    tags = state["tag"]["entities"]
    for tid, task in tasks.items():
        if TODAY_TAG_ID in task.get("tagIds", []):
            problems.append(f"task {tid}: 'TODAY' in tagIds")
        if task.get("dueDay") is not None and task.get("dueWithTime") is not None:
            problems.append(f"task {tid}: both dueDay and dueWithTime set")
        pid = task.get("projectId")
        if pid not in state["project"]["entities"]:
            problems.append(f"task {tid}: projectId '{pid}' does not exist")
        for tg in task.get("tagIds", []):
            if tg not in tags:
                problems.append(f"task {tid}: tagIds references missing tag {tg}")
            elif tid not in tags[tg].get("taskIds", []):
                problems.append(f"task {tid}: not in tag {tg}.taskIds (membership desync)")
        parent_id = task.get("parentId")
        if parent_id:
            if parent_id not in task_ids:
                problems.append(f"task {tid}: parentId '{parent_id}' does not exist")
            elif tid not in tasks[parent_id].get("subTaskIds", []):
                problems.append(f"task {tid}: not in parent's subTaskIds")
        for sid in task.get("subTaskIds", []):
            if sid not in task_ids:
                problems.append(f"task {tid}: subTaskIds references missing task {sid}")
            elif tasks[sid].get("parentId") != tid:
                problems.append(f"task {tid}: subtask {sid} parentId mismatch")

    for tg, tag in tags.items():
        if tg == TODAY_TAG_ID:
            continue
        for tid in tag.get("taskIds", []):
            if tid not in task_ids:
                problems.append(f"tag {tg}: taskIds references missing task {tid}")
            elif tg not in tasks[tid].get("tagIds", []):
                problems.append(f"tag {tg}: task {tid} lacks the tag (membership desync)")

    for day, ids in (state.get("planner", {}).get("days") or {}).items():
        for tid in ids:
            if tid not in task_ids:
                problems.append(f"planner {day}: references missing task {tid}")

    return problems
