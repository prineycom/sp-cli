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


def make_note(
    note_id: str,
    content: str,
    project_id: str | None = None,
    is_pinned_to_today: bool = False,
    created: int | None = None,
) -> dict:
    ts = created if created is not None else now_ms()
    return {
        "id": note_id,
        "projectId": project_id,
        "isPinnedToToday": is_pinned_to_today,
        "content": content,
        "created": ts,
        "modified": ts,
    }


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


# ---------------------------------------------------------------- boards

# BoardPanelCfg enums are numbers in the sync file (TaskDoneState etc.).
TASK_DONE_STATE = {"all": 1, "done": 2, "undone": 3}
SCHEDULED_STATE = {"all": 1, "scheduled": 2, "not": 3}
BACKLOG_STATE = {"all": 1, "no": 2, "only": 3}
PANEL_SORT_BY = ("dueDate", "created", "title", "timeEstimate")
PANEL_SORT_DIR = ("asc", "desc")
PANEL_TAGS_MATCH = ("all", "any")

DEFAULT_PANEL_CFG = {
    "id": "",
    "title": "",
    "taskIds": [],
    "taskDoneState": 1,
    "excludedTagIds": [],
    "includedTagIds": [],
    "scheduledState": 1,
    "backlogState": 1,
    "isParentTasksOnly": False,
    "projectIds": [""],
}


def _clean_project_ids(value) -> list[str]:
    """'' means 'all projects' and is exclusive: mixing it with real ids
    DROPS the real ids in SP's sanitizer, so canonicalize to exactly ['']."""
    if not isinstance(value, list):
        return [""]
    ids = [str(v) for v in value]
    if not ids or "" in ids:
        return [""]
    out: list[str] = []
    for pid in ids:
        if pid not in out:
            out.append(pid)
    return out


def _clean_tag_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for tid in value:
        if tid and tid not in out:
            out.append(str(tid))
    return out


def _enum_value(value, default: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return num if num in (1, 2, 3) else default


def sanitize_panel(panel: dict) -> dict:
    """Normalize a BoardPanelCfg for persistence — mirrors SP's sanitizePanelCfg.

    MIGRATING, not a whitelist: every unknown key is preserved. Panels are only
    ever written as a whole array (BU), so a whitelist rebuild would silently
    strip fields a newer client wrote onto the untouched sibling panels.

    Migrates legacy `projectId` → `projectIds` and `sortByDue` → `sortBy`/
    `sortDir`; drops null match/sortDir values and unknown `sortBy`;
    canonicalizes projectIds and the enum fields. Idempotent."""
    out = dict(panel)

    # Legacy `projectId` → `projectIds`. The legacy value wins over a
    # default-looking [''] (SP: don't lose an explicit older-version choice).
    if "projectId" in out:
        legacy = out["projectId"]
        current = out.get("projectIds")
        if current is None or (isinstance(current, list) and current == [""]):
            out["projectIds"] = [str(legacy) if legacy else ""]
        del out["projectId"]

    out["projectIds"] = _clean_project_ids(out.get("projectIds"))

    # Legacy `sortByDue: 'asc'|'desc'` → sortBy/sortDir; the key always goes.
    if out.get("sortByDue") in PANEL_SORT_DIR:
        out["sortBy"] = "dueDate"
        out["sortDir"] = out["sortByDue"]
    out.pop("sortByDue", None)

    if out.get("sortBy") in PANEL_SORT_BY:
        if out.get("sortDir") not in PANEL_SORT_DIR:
            out.pop("sortDir", None)
    else:
        out.pop("sortBy", None)
        out.pop("sortDir", None)
    for key in ("includedTagsMatch", "excludedTagsMatch"):
        if out.get(key) not in PANEL_TAGS_MATCH:
            out.pop(key, None)

    out["title"] = out.get("title") or ""
    out["taskIds"] = [str(t) for t in (out.get("taskIds") or [])]
    out["includedTagIds"] = _clean_tag_ids(out.get("includedTagIds"))
    out["excludedTagIds"] = _clean_tag_ids(out.get("excludedTagIds"))
    out["taskDoneState"] = _enum_value(out.get("taskDoneState"), 1)
    out["scheduledState"] = _enum_value(out.get("scheduledState"), 1)
    out["backlogState"] = _enum_value(out.get("backlogState"), 1)
    out["isParentTasksOnly"] = bool(out.get("isParentTasksOnly"))
    return out


def make_panel(panel_id: str, title: str, **overrides) -> dict:
    """A BoardPanelCfg built from DEFAULT_PANEL_CFG, sanitized."""
    panel = copy.deepcopy(DEFAULT_PANEL_CFG)
    panel["id"] = panel_id
    panel["title"] = title
    for key, value in overrides.items():
        if value is not None:
            panel[key] = value
    return sanitize_panel(panel)


def make_board(
    board_id: str, title: str, cols: int = 2, panels: list[dict] | None = None
) -> dict:
    return {
        "id": board_id,
        "title": title,
        "cols": int(cols),
        "panels": [sanitize_panel(p) for p in (panels or [])],
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


# ---------------------------------------------------------------- counters

# SimpleCounterType values as stored in the sync file.
SIMPLE_COUNTER_TYPES = {
    "click": "ClickCounter",
    "stopwatch": "StopWatch",
    "countdown": "RepeatedCountdownReminder",
}

# Mon-Fri, as in EMPTY_SIMPLE_COUNTER (JSON keys are strings '0'..'6').
DEFAULT_STREAK_WEEK_DAYS = {
    "0": False,
    "1": True,
    "2": True,
    "3": True,
    "4": True,
    "5": True,
    "6": False,
}

# CLI weekday abbreviations -> streakWeekDays key (0 = Sunday, as in SP).
STREAK_DAY_KEYS = {
    "sun": "0",
    "mon": "1",
    "tue": "2",
    "wed": "3",
    "thu": "4",
    "fri": "5",
    "sat": "6",
}


def streak_week_days(days: list[str] | None = None) -> dict:
    """{'0'..'6': bool}; `days` are streakWeekDays keys ('0'...'6')."""
    if days is None:
        return copy.deepcopy(DEFAULT_STREAK_WEEK_DAYS)
    wanted = set(days)
    return {k: (k in wanted) for k in DEFAULT_STREAK_WEEK_DAYS}


def make_simple_counter(
    counter_id: str,
    title: str,
    counter_type: str = "ClickCounter",
    icon: str | None = None,
    is_enabled: bool = True,
    is_track_streaks: bool = True,
    streak_min_value: int = 1,
    week_days: list[str] | None = None,
    countdown_duration: int | None = None,
) -> dict:
    """A SimpleCounter shaped like EMPTY_SIMPLE_COUNTER.

    isOn is device-local and is always written as False (loadAllData forces it).
    """
    if counter_type not in SIMPLE_COUNTER_TYPES.values():
        raise ValueError(f"invalid simple counter type: {counter_type}")
    counter = {
        "id": counter_id,
        "title": title,
        "isEnabled": bool(is_enabled),
        "icon": icon,
        "type": counter_type,
        "countOnDay": {},
        "isOn": False,
        "isTrackStreaks": bool(is_track_streaks),
        "streakMinValue": int(streak_min_value),
        "streakMode": "specific-days",
        "streakWeekDays": streak_week_days(week_days),
    }
    if counter_type == "RepeatedCountdownReminder":
        counter["countdownDuration"] = int(
            countdown_duration if countdown_duration is not None else 1800000
        )
    elif countdown_duration is not None:
        raise ValueError("countdownDuration only applies to countdown counters")
    return counter


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
