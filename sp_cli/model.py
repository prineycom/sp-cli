"""Entity factories with SuperProductivity defaults."""

from __future__ import annotations

import copy
import datetime
import re
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


# `misc.startOfNextDayTime` is a "HH:MM" string (canonical since v18.5.0);
# `misc.startOfNextDay` is the deprecated hour-only number kept for older data.
_START_OF_NEXT_DAY_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def start_of_next_day_diff_ms(d: dict | None = None) -> int:
    """The logical-day offset in ms (SP's `getStartOfNextDayDiffMs`).

    Mirrors src/app/util/start-of-next-day.util.ts:
    - a `startOfNextDayTime` string wins outright; when it is present but
      malformed the whole pair is untrustworthy and the offset resets to 0
      (SP #7645) — it does NOT fall back to the legacy hour;
    - only when no time string exists at all is the numeric
      `startOfNextDay` (whole hours, 0-23) honoured;
    - anything else (absent, 0, out of range, wrong type) means 0, which
      keeps plain calendar days.

    Accepts either a whole sync file or a bare `state` dict.
    """
    if not isinstance(d, dict):
        return 0
    state = d["state"] if isinstance(d.get("state"), dict) else d
    misc = (state.get("globalConfig") or {}).get("misc")
    if not isinstance(misc, dict):
        return 0
    time_str = misc.get("startOfNextDayTime")
    if isinstance(time_str, str):
        m = _START_OF_NEXT_DAY_RE.match(time_str)
        if not m:
            return 0
        return (int(m.group(1)) * 60 + int(m.group(2))) * 60_000
    hour = misc.get("startOfNextDay")
    if isinstance(hour, bool) or not isinstance(hour, (int, float)):
        return 0
    if hour < 0 or hour > 23:
        return 0
    return int(hour) * 3_600_000


def logical_day_of_ms(ts: int, d: dict | None = None) -> str:
    """The day a moment BELONGS to, honouring `misc.startOfNextDay*`.

    With the default offset of 0 this is exactly `day_of_ms`.
    """
    return day_of_ms(int(ts) - start_of_next_day_diff_ms(d))


def logical_today_str(d: dict | None = None) -> str:
    """Today as SP sees it (now shifted back by the start-of-next-day offset)."""
    return logical_day_of_ms(now_ms(), d)


ARCHIVE_KEYS = ("archiveYoung", "archiveOld")


def archive_task_blobs(d: dict) -> list[tuple[str, dict]]:
    """Every archived-task registry in the file as `(archive key, registry)`.

    `archiveYoung` / `archiveOld` live at the TOP level in current files, but
    older (and partially-migrated) ones keep them under `state` — and a file
    can carry BOTH at once. Every blob is returned so a scan can never
    short-circuit on the first one it finds and miss the tasks in the other.

    The registry itself is handed back (not a copy) so writers can prune both
    its `ids` list and its `entities` map; young blobs come before old ones.
    """
    state = d.get("state") if isinstance(d.get("state"), dict) else {}
    blobs: list[tuple[str, dict]] = []
    for key in ARCHIVE_KEYS:
        for blob in (d.get(key), state.get(key)):
            if not isinstance(blob, dict):
                continue
            reg = blob.get("task")
            if isinstance(reg, dict) and isinstance(reg.get("entities"), dict):
                blobs.append((key, reg))
    return blobs


def archive_task_entity_maps(d: dict) -> list[dict]:
    """Every archived-task `entities` map in the file (see archive_task_blobs)."""
    return [reg["entities"] for _key, reg in archive_task_blobs(d)]


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


ATTACHMENT_TYPES = ("FILE", "LINK", "IMG", "COMMAND", "NOTE")

# SP's TaskAttachment icons (material icon names) — one per supported type.
ATTACHMENT_ICONS = {
    "FILE": "insert_drive_file",
    "LINK": "bookmark",
    "IMG": "image",
}

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".avif",
    ".ico",
    ".tif",
    ".tiff",
)


def _attachment_basename(path: str) -> str:
    """Last path segment, query/fragment stripped — SP's default title."""
    cleaned = (path or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")
    name = cleaned.rsplit("/", 1)[-1]
    return name or (path or "")


def guess_attachment_type(path: str) -> str:
    """IMG for image extensions, FILE for local paths, LINK otherwise."""
    cleaned = (path or "").split("#", 1)[0].split("?", 1)[0]
    if cleaned.lower().endswith(IMAGE_EXTENSIONS):
        return "IMG"
    lowered = (path or "").lower()
    if lowered.startswith("file://") or lowered.startswith("/") or lowered.startswith(
        "~"
    ):
        return "FILE"
    return "LINK"


def make_attachment(
    attachment_id: str,
    path: str,
    attachment_type: str | None = None,
    title: str | None = None,
) -> dict:
    """Build a TaskAttachment: type auto-detected, title defaults to basename,
    icon derived from the type."""
    a_type = (attachment_type or guess_attachment_type(path)).upper()
    if a_type not in ATTACHMENT_TYPES:
        raise ValueError(f"unknown attachment type: {attachment_type}")
    attachment = {
        "id": attachment_id,
        "type": a_type,
        "path": path,
        "title": title if title else _attachment_basename(path),
    }
    icon = ATTACHMENT_ICONS.get(a_type)
    if icon:
        attachment["icon"] = icon
    return attachment


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


WORKDAY_KEYS = frozenset(WEEKDAY_KEYS[:5])

# The monthly anchors SP's MONTHLY_ANCHOR_RESET wipes when a monthly preset is
# (re)applied: their presence is what discriminates the monthly variants, so a
# stale Nth-weekday / last-day flag would silently take effect.
MONTHLY_ANCHOR_FIELDS = ("monthlyWeekOfMonth", "monthlyWeekday", "monthlyLastDay")


def repeat_cadence_fields(
    repeat_cycle: str,
    repeat_every: int = 1,
    days: list[str] | None = None,
) -> dict:
    """The cadence half of a TaskRepeatCfg: quickSetting + cycle + weekdays.

    Shared by cfg creation and `sp repeat edit` so an edited cadence lands on
    exactly the same field set SP would have written itself.
    days: weekday keys ('monday'...) — overrides the default mon-fri pattern.

    quickSetting must agree with the weekday booleans, because SP re-derives the
    cadence from the preset on every dialog save (getQuickSettingUpdates): a
    cfg tagged WEEKLY_CURRENT_WEEKDAY but carrying mon-fri would be rewritten to
    a single weekday behind the user's back. Hence, for a 1-week cycle:
    mon-fri → MONDAY_TO_FRIDAY, exactly one day → WEEKLY_CURRENT_WEEKDAY,
    anything else → CUSTOM. Every non-1 interval is CUSTOM (no preset has one).
    """
    if repeat_cycle not in _QUICK_SETTING:
        raise ValueError(f"invalid repeat cycle: {repeat_cycle}")
    if repeat_every < 1:
        raise ValueError(f"repeat interval must be >= 1: {repeat_every}")
    weekdays = {
        k: (k in days) if days is not None else (k in WORKDAY_KEYS) for k in WEEKDAY_KEYS
    }
    if repeat_every != 1:
        quick = "CUSTOM"
    elif repeat_cycle == "WEEKLY":
        on = {k for k, v in weekdays.items() if v}
        if on == WORKDAY_KEYS:
            quick = "MONDAY_TO_FRIDAY"
        elif len(on) == 1:
            quick = "WEEKLY_CURRENT_WEEKDAY"
        else:
            quick = "CUSTOM"
    else:
        quick = _QUICK_SETTING[repeat_cycle]
    return {
        "quickSetting": quick,
        "repeatCycle": repeat_cycle,
        "repeatEvery": repeat_every,
        **weekdays,
    }


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
    cadence = repeat_cadence_fields(repeat_cycle, repeat_every, days)
    cfg = {
        "id": cfg_id,
        "projectId": project_id,
        "title": title,
        "tagIds": [],
        "order": 0,
        "isPaused": False,
        "startDate": start_date or today_str(),
        "lastTaskCreationDay": today_str(),
        "skipOverdue": False,
        "waitForCompletion": False,
        "repeatFromCompletionDate": False,
        "shouldInheritSubtasks": False,
        **cadence,
    }
    if default_estimate is not None:
        cfg["defaultEstimate"] = default_estimate
    if start_time is not None:
        cfg["startTime"] = start_time
    if remind_at is not None:
        cfg["remindAt"] = remind_at
    return cfg


# ---------------------------------------------------------------- metrics

# DEFAULT_METRIC_FOR_DAY as SP builds it. The metric's id IS the day
# ('YYYY-MM-DD'). mood / productivity / obstruction / improvement no longer
# exist in SuperProductivity and must never be written.
DEFAULT_METRIC = {
    "focusSessions": [],
    "remindTomorrow": False,
    "reflections": [],
}

# Fields the CLI is allowed to write via a metric patch.
METRIC_FIELDS = (
    "impactOfWork",
    "energyCheckin",
    "notes",
    "remindTomorrow",
    "reflections",
    "totalWorkMinutes",
    "completedTasks",
    "plannedTasks",
)

# Removed from SP; writing them resurrects dead fields in everyone's file.
METRIC_DEAD_FIELDS = (
    "mood",
    "productivity",
    "obstructions",
    "improvements",
    "improvementsTomorrow",
    "obstruction",
    "improvement",
)


def make_metric(day: str, **changes) -> dict:
    """A Metric for `day` shaped like DEFAULT_METRIC_FOR_DAY + changes."""
    metric = {"id": day}
    metric.update(copy.deepcopy(DEFAULT_METRIC))
    metric.update(copy.deepcopy(changes))
    return metric


def make_reflection(text: str, created: int | None = None) -> dict:
    return {"text": text, "created": created if created is not None else now_ms()}


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


# ------------------------------------------------------- issue providers

# ISSUE_PROVIDER_DEFAULT_COMMON_CFG — shared by every provider key.
ISSUE_PROVIDER_COMMON_CFG = {
    "isAutoPoll": True,
    "isAutoAddToBacklog": False,
    "isIntegratedAddTaskBar": False,
    "defaultProjectId": None,
    "pinnedSearch": None,
    "pollingMode": "whenProjectOpen",
    "defaultTagIds": [],
    "defaultNote": None,
}

# Per-key cfg defaults. Fields are FLATTENED onto the provider object
# (discriminated union on issueProviderKey), and a built-in provider must be
# COMPLETE for its key or SP's typia validation rejects the whole file.
DEFAULT_CALENDAR_CFG = {
    "isEnabled": False,
    "icalUrl": "",
    "isAutoImportForCurrentDay": False,
    "isReferenceCalendar": False,
    "checkUpdatesEvery": 7200000,
    "showBannerBeforeThreshold": 7200000,
    "isDisabledForWebApp": False,
    "filterIncludeRegex": None,
    "filterExcludeRegex": None,
}

DEFAULT_CALDAV_CFG = {
    "isEnabled": False,
    "caldavUrl": None,
    "resourceName": None,
    "username": None,
    "password": None,
    "categoryFilter": None,
    "isAddSubTasks": False,
    "twoWaySync": {"isDone": "pullOnly", "title": "pullOnly", "notes": "off"},
}

ISSUE_PROVIDER_DEFAULT_CFG = {
    "ICAL": DEFAULT_CALENDAR_CFG,
    "CALDAV": DEFAULT_CALDAV_CFG,
}

# The provider url field per key (for listing / resolution).
ISSUE_PROVIDER_URL_FIELD = {"ICAL": "icalUrl", "CALDAV": "caldavUrl"}

# Issue-link fields cleared on every task when its provider is deleted.
ISSUE_TASK_FIELDS = (
    "issueId",
    "issueProviderId",
    "issueType",
    "issueWasUpdated",
    "issueLastUpdated",
    "issueAttachmentNr",
    "issueTimeTracked",
    "issuePoints",
)


def make_issue_provider(provider_id: str, key: str, **overrides) -> dict:
    """A full IssueProvider: COMMON cfg + the key's default cfg + overrides.

    Always emits the COMPLETE cfg for the key — a partial built-in provider
    fails SP's typia validation.
    """
    if key not in ISSUE_PROVIDER_DEFAULT_CFG:
        raise ValueError(f"unsupported issue provider key: {key}")
    provider = copy.deepcopy(ISSUE_PROVIDER_COMMON_CFG)
    provider.update(copy.deepcopy(ISSUE_PROVIDER_DEFAULT_CFG[key]))
    provider["id"] = provider_id
    provider["issueProviderKey"] = key
    provider["isEnabled"] = True
    # Overrides may only touch fields this key actually has: a stray field
    # (a CalDAV one on an ICAL provider, or a typo) would sail into the sync
    # file and fail SP's typia validation for the whole discriminated union.
    for name, value in overrides.items():
        if name not in provider:
            raise ValueError(f"issue provider {key}: unknown field '{name}'")
        if value is not None:
            provider[name] = value
    return provider


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
