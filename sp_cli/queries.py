"""Read-only layer: id resolution, filters, today/agenda, worklog, doctor."""

from __future__ import annotations

import datetime

from sp_cli.model import PANEL_SORT_BY, TODAY_TAG_ID, day_of_ms, today_str


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


def resolve_note(d: dict, ref: str) -> str:
    ids = (d["state"].get("note") or {}).get("ids", [])
    if ref in ids:
        return ref
    matches = [nid for nid in ids if nid.startswith(ref)]
    if not matches:
        matches = [nid for nid in ids if nid.lstrip("-_").startswith(ref)]
    if not matches:
        raise NotFoundError(f"no note with id (prefix) '{ref}'")
    if len(matches) > 1:
        raise AmbiguousIdError(ref, matches)
    return matches[0]


def all_boards(d: dict) -> list[dict]:
    boards = d["state"].get("boards") or {}
    cfgs = boards.get("boardCfgs")
    return list(cfgs) if isinstance(cfgs, list) else []


def all_panels(d: dict) -> list[tuple[dict, dict]]:
    return [(b, p) for b in all_boards(d) for p in (b.get("panels") or [])]


def resolve_board(d: dict, ref: str) -> str:
    boards = all_boards(d)
    ids = [b["id"] for b in boards]
    if ref in ids:
        return ref
    by_title = [b["id"] for b in boards if (b.get("title") or "").lower() == ref.lower()]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_title) > 1:
        raise AmbiguousIdError(ref, by_title)
    matches = [bid for bid in ids if bid.startswith(ref)]
    if not matches:
        matches = [bid for bid in ids if bid.lstrip("-_").startswith(ref)]
    if not matches:
        raise NotFoundError(f"no board with id or title '{ref}'")
    if len(matches) > 1:
        raise AmbiguousIdError(ref, matches)
    return matches[0]


def resolve_panel(d: dict, ref: str, board_id: str | None = None) -> str:
    """Panel id (exact / title / id prefix). Panel ids are globally unique."""
    pairs = [
        (b, p)
        for b, p in all_panels(d)
        if board_id is None or b["id"] == board_id
    ]
    ids = [p["id"] for _, p in pairs]
    if ref in ids:
        return ref
    by_title = [
        p["id"] for _, p in pairs if (p.get("title") or "").lower() == ref.lower()
    ]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_title) > 1:
        raise AmbiguousIdError(ref, by_title)
    matches = [pid for pid in ids if pid.startswith(ref)]
    if not matches:
        matches = [pid for pid in ids if pid.lstrip("-_").startswith(ref)]
    if not matches:
        raise NotFoundError(f"no board panel with id or title '{ref}'")
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


# ---------------------------------------------------------------- notes

def all_notes(d: dict) -> list[dict]:
    reg = d["state"].get("note") or {}
    entities = reg.get("entities") or {}
    return [entities[nid] for nid in reg.get("ids", []) if nid in entities]


def list_notes(
    d: dict, project: str | None = None, today: bool = False
) -> list[dict]:
    """Notes in state order; `today` = pinned ones in todayOrder order."""
    reg = d["state"].get("note") or {}
    entities = reg.get("entities") or {}
    if today:
        order = reg.get("todayOrder") or []
        notes = [entities[nid] for nid in order if nid in entities]
    else:
        notes = all_notes(d)
    if project is not None:
        pid = resolve_project(d, project)
        notes = [n for n in notes if n.get("projectId") == pid]
        if not today:
            # The app shows a project's notes in project.noteIds order; any
            # note missing from that list keeps its note.ids position at the end.
            proj = (d["state"]["project"]["entities"].get(pid) or {})
            order = proj.get("noteIds") or []
            rank = {nid: i for i, nid in enumerate(order)}
            notes.sort(key=lambda n: rank.get(n["id"], len(order)))
    return notes


# ---------------------------------------------------------------- counters

def all_counters(d: dict) -> list[dict]:
    reg = d["state"].get("simpleCounter") or {}
    entities = reg.get("entities") or {}
    return [entities[cid] for cid in reg.get("ids", []) if cid in entities]


def resolve_counter(d: dict, ref: str) -> str:
    """Counter by exact id, exact title (case-insensitive) or id prefix."""
    counters = all_counters(d)
    ids = [c["id"] for c in counters]
    if ref in ids:
        return ref
    by_title = [
        c["id"] for c in counters if (c.get("title") or "").lower() == ref.lower()
    ]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_title) > 1:
        raise AmbiguousIdError(ref, by_title)
    matches = [cid for cid in ids if cid.startswith(ref)]
    if not matches:
        matches = [cid for cid in ids if cid.lstrip("-_").startswith(ref)]
    if not matches:
        raise NotFoundError(f"no counter with id or title '{ref}'")
    if len(matches) > 1:
        raise AmbiguousIdError(ref, matches)
    return matches[0]


def counter_value(counter: dict, date: str | None = None) -> int:
    """Clicks (or ms for StopWatch) counted on `date` (default today)."""
    date = date or today_str()
    return int((counter.get("countOnDay") or {}).get(date, 0))


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

    for name in ("task", "project", "tag", "taskRepeatCfg", "note", "simpleCounter"):
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

    note_reg = state.get("note") or {}
    notes = note_reg.get("entities") or {}
    note_ids = set(notes)
    today_order = note_reg.get("todayOrder") or []
    for nid in today_order:
        if nid not in note_ids:
            problems.append(f"note todayOrder: references missing note {nid}")
    if len(set(today_order)) != len(today_order):
        problems.append("note todayOrder: duplicate ids")
    for nid, note in notes.items():
        pid = note.get("projectId")
        if pid is not None:
            if pid not in state["project"]["entities"]:
                problems.append(f"note {nid}: projectId '{pid}' does not exist")
            elif nid not in state["project"]["entities"][pid].get("noteIds", []):
                problems.append(
                    f"note {nid}: not in project {pid}.noteIds (membership desync)"
                )
        if note.get("isPinnedToToday") and nid not in today_order:
            problems.append(f"note {nid}: pinned to today but not in todayOrder")
        if not note.get("isPinnedToToday") and nid in today_order:
            problems.append(f"note {nid}: in todayOrder but not pinned")

    for pid, project in state["project"]["entities"].items():
        note_id_list = project.get("noteIds", [])
        if len(set(note_id_list)) != len(note_id_list):
            problems.append(f"project {pid}: noteIds has duplicate ids")
        for nid in note_id_list:
            if nid not in note_ids:
                problems.append(f"project {pid}: noteIds references missing note {nid}")
            elif notes[nid].get("projectId") != pid:
                problems.append(f"project {pid}: note {nid} projectId mismatch")

    board_ids: set[str] = set()
    panel_ids: set[str] = set()
    for board in all_boards(d):
        bid = board.get("id")
        if not bid:
            problems.append("boards: board without an id")
        elif bid in board_ids:
            problems.append(f"boards: duplicate board id '{bid}'")
        board_ids.add(bid)
        for panel in board.get("panels") or []:
            pid = panel.get("id")
            if not pid:
                problems.append(f"board {bid}: panel without an id")
            elif pid in panel_ids:
                problems.append(f"boards: duplicate panel id '{pid}'")
            panel_ids.add(pid)
            # An absent or empty projectIds is VALID: it means "All Projects",
            # same as the '' sentinel. Only a non-list or a mixed array is off.
            project_ids = panel.get("projectIds")
            if project_ids is not None and not isinstance(project_ids, list):
                problems.append(f"panel {pid}: projectIds must be an array")
            elif isinstance(project_ids, list) and "" in project_ids and (
                project_ids != [""]
            ):
                problems.append(f"panel {pid}: projectIds mixes '' with real ids")
            for legacy in ("projectId", "sortByDue"):
                if legacy in panel:
                    problems.append(
                        f"panel {pid}: leftover legacy key '{legacy}' "
                        "(rewrite the panel to migrate it)"
                    )
            if "sortBy" in panel and panel["sortBy"] not in PANEL_SORT_BY:
                problems.append(f"panel {pid}: invalid sortBy {panel['sortBy']!r}")
            for key in ("sortDir", "includedTagsMatch", "excludedTagsMatch"):
                if key in panel and panel[key] is None:
                    problems.append(f"panel {pid}: {key} is null (must be absent)")
            for key in ("taskDoneState", "scheduledState", "backlogState"):
                if key in panel and (
                    isinstance(panel[key], bool) or not isinstance(panel[key], int)
                ):
                    problems.append(
                        f"panel {pid}: {key} = {panel[key]!r} is not a number"
                    )
            if not isinstance(panel.get("taskIds"), list):
                problems.append(f"panel {pid}: taskIds must be an array")
            if TODAY_TAG_ID in (panel.get("includedTagIds") or []):
                problems.append(f"panel {pid}: 'TODAY' in includedTagIds")

    for counter in all_counters(d):
        cid = counter["id"]
        if counter.get("isOn"):
            problems.append(f"counter {cid}: isOn is true (must be device-local false)")
        for day, val in (counter.get("countOnDay") or {}).items():
            if not isinstance(val, (int, float)) or val < 0:
                problems.append(f"counter {cid}: countOnDay[{day}] = {val!r} is invalid")

    for day, ids in (state.get("planner", {}).get("days") or {}).items():
        for tid in ids:
            if tid not in task_ids:
                problems.append(f"planner {day}: references missing task {tid}")

    return problems
