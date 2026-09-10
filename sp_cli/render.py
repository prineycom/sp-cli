"""Output helpers: durations, dates, tables, task rendering."""

from __future__ import annotations

import datetime
import json
import re

SHORT_ID_LEN = 8
TITLE_WIDTH = 42


class RenderError(Exception):
    pass


_DURATION_RE = re.compile(
    r"^\s*(?:(?P<h>\d+(?:\.\d+)?)\s*h)?\s*(?:(?P<m>\d+(?:\.\d+)?)\s*m)?\s*$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> int:
    """'30m', '1h', '1.5h', '90m', '1h30m', bare '90' (minutes) → ms."""
    if re.fullmatch(r"\d+", text.strip()):
        return int(text.strip()) * 60_000
    m = _DURATION_RE.match(text)
    if not m or (m.group("h") is None and m.group("m") is None):
        raise RenderError(f"cannot parse duration '{text}' (use e.g. 30m, 1.5h, 1h30m)")
    hours = float(m.group("h") or 0)
    minutes = float(m.group("m") or 0)
    return int(round((hours * 60 + minutes) * 60_000))


def format_duration(ms: int | None) -> str:
    if not ms:
        return "-"
    total_min = int(round(ms / 60_000))
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def parse_offset(text: str) -> int:
    """Reminder offset like 10m / 1h / 0m → ms."""
    return parse_duration(text)


def format_ts(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def format_time(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def short_id(task_id: str) -> str:
    return task_id[:SHORT_ID_LEN]


def truncate(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        print("(none)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def _due_str(task: dict) -> str:
    if task.get("dueWithTime") is not None:
        return format_ts(task["dueWithTime"])
    return task.get("dueDay") or "-"


def task_rows(d: dict, tasks: list[dict]) -> list[list[str]]:
    projects = d["state"]["project"]["entities"]
    tags = d["state"]["tag"]["entities"]
    rows = []
    for t in tasks:
        tag_names = ",".join(
            tags[tg]["title"] for tg in t.get("tagIds", []) if tg in tags
        )
        project = projects.get(t.get("projectId"), {}).get("title", "?")
        est = format_duration(t.get("timeEstimate"))
        spent = format_duration(t.get("timeSpent"))
        rows.append(
            [
                short_id(t["id"]),
                ("x" if t.get("isDone") else " "),
                truncate(t.get("title", ""), TITLE_WIDTH),
                project,
                _due_str(t),
                f"{est}/{spent}",
                tag_names,
            ]
        )
    return rows


def print_tasks(d: dict, tasks: list[dict]) -> None:
    print_table(["id", "✓", "title", "project", "due", "est/spent", "tags"], task_rows(d, tasks))


def archived_task_rows(d: dict, tasks: list[dict]) -> list[list[str]]:
    projects = d["state"]["project"]["entities"]
    rows = []
    for t in tasks:
        rows.append(
            [
                short_id(t["id"]),
                t.get("age", "-"),
                truncate(t.get("title", ""), TITLE_WIDTH),
                projects.get(t.get("projectId"), {}).get("title", "?"),
                format_ts(t.get("doneOn")),
                str(len(t.get("subTaskIds") or [])),
                format_duration(t.get("timeSpent")),
            ]
        )
    return rows


def print_archived_tasks(d: dict, tasks: list[dict]) -> None:
    print_table(
        ["id", "age", "title", "project", "done", "subs", "spent"],
        archived_task_rows(d, tasks),
    )


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def note_rows(d: dict, notes: list[dict]) -> list[list[str]]:
    projects = d["state"]["project"]["entities"]
    rows = []
    for n in notes:
        pid = n.get("projectId")
        project = projects.get(pid, {}).get("title", "-") if pid else "-"
        rows.append(
            [
                short_id(n["id"]),
                ("📌" if n.get("isPinnedToToday") else " "),
                truncate(first_line(n.get("content")), TITLE_WIDTH),
                project,
            ]
        )
    return rows


def attachment_rows(attachments: list[dict]) -> list[list[str]]:
    return [
        [
            short_id(a.get("id") or ""),
            a.get("type") or "?",
            truncate(a.get("title") or "", TITLE_WIDTH),
            truncate(a.get("path") or "", 60),
        ]
        for a in attachments
    ]


def print_attachments(attachments: list[dict]) -> None:
    print_table(["id", "type", "title", "path"], attachment_rows(attachments))


def print_notes(d: dict, notes: list[dict]) -> None:
    print_table(["id", "pin", "content", "project"], note_rows(d, notes))


def note_card(d: dict, note: dict) -> str:
    projects = d["state"]["project"]["entities"]
    pid = note.get("projectId")
    project = f"{projects.get(pid, {}).get('title', '?')} ({pid})" if pid else "-"
    lines = [
        f"id:       {note['id']}",
        f"project:  {project}",
        f"pinned:   {'yes' if note.get('isPinnedToToday') else 'no'}",
        f"created:  {format_ts(note.get('created'))}",
        f"modified: {format_ts(note.get('modified'))}",
    ]
    if note.get("backgroundColor"):
        lines.append(f"color:    {note['backgroundColor']}")
    lines.append("content:")
    for ln in (note.get("content") or "").splitlines():
        lines.append(f"  {ln}")
    return "\n".join(lines)


_COUNTER_TYPE_LABEL = {
    "ClickCounter": "click",
    "StopWatch": "stopwatch",
    "RepeatedCountdownReminder": "countdown",
}

_WEEK_DAY_LABEL = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def counter_value_str(counter: dict, value: int) -> str:
    """StopWatch counts milliseconds; everything else counts clicks."""
    if counter.get("type") == "StopWatch":
        return format_duration(value) if value else "0m"
    return str(value)


def counter_streak_str(counter: dict) -> str:
    if not counter.get("isTrackStreaks"):
        return "-"
    week = counter.get("streakWeekDays") or {}
    days = ",".join(
        _WEEK_DAY_LABEL[i] for i in range(7) if week.get(str(i)) or week.get(i)
    )
    minimum = counter_value_str(counter, int(counter.get("streakMinValue") or 0))
    return f">={minimum} on {days or '-'}"


def counter_rows(counters: list[dict], values: list[int]) -> list[list[str]]:
    return [
        [
            c["id"],
            truncate(c.get("title", ""), TITLE_WIDTH),
            _COUNTER_TYPE_LABEL.get(c.get("type"), c.get("type") or "?"),
            "on" if c.get("isEnabled") else "off",
            counter_value_str(c, value),
            counter_streak_str(c),
        ]
        for c, value in zip(counters, values)
    ]


def print_counters(counters: list[dict], values: list[int]) -> None:
    print_table(
        ["id", "title", "type", "enabled", "today", "streak"],
        counter_rows(counters, values),
    )


_IMPACT_LABEL = {1: "1 low", 2: "2 some", 3: "3 good", 4: "4 high"}
_ENERGY_LABEL = {1: "1 low", 2: "2 ok", 3: "3 high"}


def _or_dash(value) -> str:
    return "-" if value is None else str(value)


def metric_rows(metrics: list[dict]) -> list[list[str]]:
    rows = []
    for m in metrics:
        sessions = [int(s) for s in (m.get("focusSessions") or [])]
        focus = (
            f"{len(sessions)}x {format_duration(sum(sessions))}" if sessions else "-"
        )
        done = m.get("completedTasks")
        planned = m.get("plannedTasks")
        tasks = (
            "-"
            if done is None and planned is None
            else f"{_or_dash(done)}/{_or_dash(planned)}"
        )
        reflections = len(m.get("reflections") or [])
        rows.append(
            [
                m["id"],
                _IMPACT_LABEL.get(m.get("impactOfWork"), "-"),
                _ENERGY_LABEL.get(m.get("energyCheckin"), "-"),
                focus,
                tasks,
                str(reflections) if reflections else "-",
                truncate(first_line(m.get("notes")), TITLE_WIDTH) or "-",
            ]
        )
    return rows


def print_metrics(metrics: list[dict]) -> None:
    print_table(
        ["day", "impact", "energy", "focus", "done/plan", "refl", "notes"],
        metric_rows(metrics),
    )


def metric_card(metric: dict) -> str:
    sessions = [int(s) for s in (metric.get("focusSessions") or [])]
    lines = [
        f"day:       {metric['id']}",
        f"impact:    {_IMPACT_LABEL.get(metric.get('impactOfWork'), '-')}",
        f"energy:    {_ENERGY_LABEL.get(metric.get('energyCheckin'), '-')}",
        f"focus:     {len(sessions)} session(s), {format_duration(sum(sessions))}",
        f"done/plan: {_or_dash(metric.get('completedTasks'))}/"
        f"{_or_dash(metric.get('plannedTasks'))}",
        f"remind:    {'yes' if metric.get('remindTomorrow') else 'no'}",
    ]
    if metric.get("notes"):
        lines.append("notes:")
        for ln in metric["notes"].splitlines():
            lines.append(f"  {ln}")
    for reflection in metric.get("reflections") or []:
        lines.append(f"reflection ({format_ts(reflection.get('created'))}):")
        for ln in (reflection.get("text") or "").splitlines():
            lines.append(f"  {ln}")
    return "\n".join(lines)


_DONE_STATE = {1: "", 2: "done", 3: "undone"}
_SCHEDULED_STATE = {1: "", 2: "scheduled", 3: "not-scheduled"}
_BACKLOG_STATE = {1: "", 2: "no-backlog", 3: "only-backlog"}


def panel_filters(d: dict, panel: dict) -> str:
    """Compact one-line summary of a board panel's filters."""
    tags = d["state"]["tag"]["entities"]
    projects = d["state"]["project"]["entities"]

    def tag_names(ids: list[str]) -> str:
        return ",".join(tags.get(t, {}).get("title", t) for t in ids)

    parts: list[str] = []
    if panel.get("includedTagIds"):
        parts.append("+" + tag_names(panel["includedTagIds"]))
    if panel.get("excludedTagIds"):
        parts.append("-" + tag_names(panel["excludedTagIds"]))
    project_ids = panel.get("projectIds") or [""]
    if project_ids != [""]:
        parts.append(
            "project="
            + ",".join(projects.get(p, {}).get("title", p) for p in project_ids)
        )
    for key, table in (
        ("taskDoneState", _DONE_STATE),
        ("scheduledState", _SCHEDULED_STATE),
        ("backlogState", _BACKLOG_STATE),
    ):
        label = table.get(panel.get(key), "")
        if label:
            parts.append(label)
    if panel.get("isParentTasksOnly"):
        parts.append("parents-only")
    if panel.get("sortBy"):
        parts.append(f"sort={panel['sortBy']}/{panel.get('sortDir', 'asc')}")
    return " ".join(parts) or "-"


def print_boards(d: dict, boards: list[dict]) -> None:
    if not boards:
        print("(none)")
        return
    for board in boards:
        panels = board.get("panels") or []
        print(
            f"{board['id']}  {board.get('title', '')}  "
            f"(cols: {board.get('cols', '?')}, panels: {len(panels)})"
        )
        for panel in panels:
            print(
                f"  {panel['id']}  {truncate(panel.get('title', ''), TITLE_WIDTH)}"
                f"  {panel_filters(d, panel)}"
            )


def provider_rows(
    providers: list[dict], urls: list[str], project_names: list[str]
) -> list[list[str]]:
    return [
        [
            p["id"],
            p.get("issueProviderKey", "?"),
            "on" if p.get("isEnabled") else "off",
            truncate(url, TITLE_WIDTH),
            project or "-",
            "yes" if p.get("isAutoImportForCurrentDay") else "no",
        ]
        for p, url, project in zip(providers, urls, project_names)
    ]


def print_providers(
    providers: list[dict], urls: list[str], project_names: list[str]
) -> None:
    print_table(
        ["id", "key", "enabled", "url", "project", "auto-import"],
        provider_rows(providers, urls, project_names),
    )


def print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def task_card(d: dict, task: dict) -> str:
    state = d["state"]
    projects = state["project"]["entities"]
    tags = state["tag"]["entities"]
    cfgs = state.get("taskRepeatCfg", {}).get("entities", {})
    lines = [
        f"id:       {task['id']}",
        f"title:    {task.get('title', '')}",
        f"project:  {projects.get(task.get('projectId'), {}).get('title', '?')} ({task.get('projectId')})",
        f"done:     {'yes (' + format_ts(task.get('doneOn')) + ')' if task.get('isDone') else 'no'}",
        f"created:  {format_ts(task.get('created'))}",
        f"estimate: {format_duration(task.get('timeEstimate'))}",
        f"spent:    {format_duration(task.get('timeSpent'))}",
    ]
    if task.get("tagIds"):
        names = ", ".join(tags[t]["title"] for t in task["tagIds"] if t in tags)
        lines.append(f"tags:     {names}")
    if task.get("dueDay"):
        lines.append(f"due:      {task['dueDay']}")
    if task.get("dueWithTime") is not None:
        lines.append(f"due at:   {format_ts(task['dueWithTime'])}")
    if task.get("remindAt") is not None:
        lines.append(f"remind:   {format_ts(task['remindAt'])}")
    if task.get("deadlineDay"):
        lines.append(f"deadline: {task['deadlineDay']}")
    if task.get("deadlineWithTime") is not None:
        lines.append(f"deadline: {format_ts(task['deadlineWithTime'])}")
    if task.get("repeatCfgId"):
        cfg = cfgs.get(task["repeatCfgId"], {})
        lines.append(
            f"repeat:   {cfg.get('repeatCycle', '?')} every {cfg.get('repeatEvery', '?')}"
        )
    if task.get("parentId"):
        lines.append(f"parent:   {task['parentId']}")
    if task.get("timeSpentOnDay"):
        per_day = ", ".join(
            f"{day}: {format_duration(ms)}"
            for day, ms in sorted(task["timeSpentOnDay"].items())
        )
        lines.append(f"per day:  {per_day}")
    if task.get("notes"):
        lines.append("notes:")
        for ln in task["notes"].splitlines():
            lines.append(f"  {ln}")
    attachments = task.get("attachments")
    if isinstance(attachments, list) and attachments:
        lines.append("attachments:")
        for a in attachments:
            title = a.get("title") or a.get("path") or ""
            lines.append(
                f"  {short_id(a.get('id') or '')} "
                f"[{a.get('type') or '?'}] {title} — {a.get('path') or ''}"
            )
    subs = task.get("subTaskIds", [])
    if subs:
        lines.append("subtasks:")
        entities = state["task"]["entities"]
        for sid in subs:
            sub = entities.get(sid)
            if sub:
                mark = "x" if sub.get("isDone") else " "
                lines.append(f"  [{mark}] {short_id(sid)} {sub.get('title', '')}")
    return "\n".join(lines)
