"""Entity factories with SuperProductivity defaults."""

from __future__ import annotations

import copy
import datetime
import time

INBOX_PROJECT_ID = "INBOX_PROJECT"
TODAY_TAG_ID = "TODAY"

DEFAULT_PROJECT_COLOR = "#29a1aa"

DEFAULT_WORKLOG_EXPORT_SETTINGS = {
    "cols": ["DATE", "START", "END", "TIME_CLOCK", "TITLES_INCLUDING_SUB"],
    "roundWorkTimeTo": None,
    "roundStartTimeTo": None,
    "roundEndTimeTo": None,
    "separateTasksBy": " | ",
    "groupBy": "DATE",
}

# Full theme shape as in the TODAY tag of a stock sync file, primary swapped in.
_DEFAULT_THEME = {
    "isAutoContrast": True,
    "isDisableBackgroundTint": True,
    "primary": DEFAULT_PROJECT_COLOR,
    "huePrimary": "400",
    "accent": "#ff4081",
    "hueAccent": "500",
    "warn": "#e11826",
    "hueWarn": "500",
    "backgroundImageDark": "",
    "backgroundImageLight": None,
    "backgroundOverlayOpacity": 20,
    "backgroundImageBlur": 0,
}


def now_ms() -> int:
    return int(time.time() * 1000)


def today_str(dt: datetime.date | None = None) -> str:
    return (dt or datetime.date.today()).isoformat()


def day_of_ms(ts: int) -> str:
    """Local YYYY-MM-DD of an epoch-ms timestamp."""
    return datetime.datetime.fromtimestamp(ts / 1000).date().isoformat()


def default_theme(primary: str | None = None) -> dict:
    theme = copy.deepcopy(_DEFAULT_THEME)
    if primary:
        theme["primary"] = primary
    return theme


def default_advanced_cfg() -> dict:
    return {"worklogExportSettings": copy.deepcopy(DEFAULT_WORKLOG_EXPORT_SETTINGS)}


def make_task(
    task_id: str,
    title: str,
    project_id: str,
    created: int | None = None,
    time_estimate: int = 0,
    tag_ids: list[str] | None = None,
    notes: str | None = None,
    parent_id: str | None = None,
    due_day: str | None = None,
    due_with_time: int | None = None,
    remind_at: int | None = None,
) -> dict:
    task = {
        "id": task_id,
        "projectId": project_id,
        "subTaskIds": [],
        "timeSpentOnDay": {},
        "timeSpent": 0,
        "timeEstimate": time_estimate,
        "isDone": False,
        "title": title,
        "tagIds": list(tag_ids or []),
        "created": created if created is not None else now_ms(),
        "attachments": [],
    }
    if notes is not None:
        task["notes"] = notes
    if parent_id is not None:
        task["parentId"] = parent_id
    if due_day is not None:
        task["dueDay"] = due_day
    if due_with_time is not None:
        task["dueWithTime"] = due_with_time
    if remind_at is not None:
        task["remindAt"] = remind_at
    return task


def make_project(project_id: str, title: str, color: str | None = None) -> dict:
    return {
        "id": project_id,
        "title": title,
        "icon": None,
        "isArchived": False,
        "isDone": False,
        "doneOn": None,
        "isHiddenFromMenu": False,
        "isEnableBacklog": False,
        "taskIds": [],
        "backlogTaskIds": [],
        "noteIds": [],
        "advancedCfg": default_advanced_cfg(),
        "theme": default_theme(color or DEFAULT_PROJECT_COLOR),
    }


def make_tag(tag_id: str, title: str, color: str | None = None) -> dict:
    return {
        "id": tag_id,
        "title": title,
        "color": color,
        "created": now_ms(),
        "icon": None,
        "taskIds": [],
        "advancedCfg": default_advanced_cfg(),
        "theme": default_theme(color),
    }


_QUICK_SETTING = {
    "DAILY": "DAILY",
    "WEEKLY": "WEEKLY_CURRENT_WEEKDAY",
    "MONTHLY": "MONTHLY_CURRENT_DATE",
    "YEARLY": "YEARLY_CURRENT_DATE",
}

WEEKDAY_KEYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def make_repeat_cfg(
    cfg_id: str,
    title: str,
    project_id: str | None,
    repeat_cycle: str,
    repeat_every: int = 1,
    days: list[str] | None = None,
    start_date: str | None = None,
    start_time: str | None = None,
    remind_at: str | None = None,
    default_estimate: int | None = None,
) -> dict:
    """TaskRepeatCfg with SP defaults. repeat_cycle: DAILY|WEEKLY|MONTHLY|YEARLY.

    days: weekday keys ('monday'...) — overrides the default mon-fri pattern.
    remind_at: TaskReminderOptionId string (e.g. 'AtStart'), not a timestamp.
    """
    if repeat_cycle not in _QUICK_SETTING:
        raise ValueError(f"invalid repeat cycle: {repeat_cycle}")
    quick = _QUICK_SETTING[repeat_cycle]
    if repeat_every != 1 or (days is not None and repeat_cycle == "WEEKLY"):
        quick = "CUSTOM"
    weekdays = {
        k: (k in days) if days is not None else (k not in ("saturday", "sunday"))
        for k in WEEKDAY_KEYS
    }
    cfg = {
        "id": cfg_id,
        "projectId": project_id,
        "title": title,
        "tagIds": [],
        "order": 0,
        "isPaused": False,
        "quickSetting": quick,
        "repeatCycle": repeat_cycle,
        "startDate": start_date or today_str(),
        "repeatEvery": repeat_every,
        "lastTaskCreationDay": today_str(),
        "skipOverdue": False,
        "waitForCompletion": False,
        "repeatFromCompletionDate": False,
        "shouldInheritSubtasks": False,
        **weekdays,
    }
    if default_estimate is not None:
        cfg["defaultEstimate"] = default_estimate
    if start_time is not None:
        cfg["startTime"] = start_time
    if remind_at is not None:
        cfg["remindAt"] = remind_at
    return cfg


def task_with_subtasks(state: dict, task_id: str) -> dict:
    """Snapshot: the task entity plus 'subTasks': [sub entities]."""
    entities = state["task"]["entities"]
    task = entities[task_id]
    snapshot = copy.deepcopy(task)
    snapshot["subTasks"] = [
        copy.deepcopy(entities[sid])
        for sid in task.get("subTaskIds", [])
        if sid in entities
    ]
    return snapshot
