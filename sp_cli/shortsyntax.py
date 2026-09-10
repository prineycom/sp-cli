"""SuperProductivity short-syntax parser (`+project #tag @due !deadline 30m`).

SP parses short syntax locally in the app and then emits ordinary, already
resolved actions — there is no "short syntax" op on the wire (HSS exists but
buys nothing, and the new-tag dialog action TGS is not persistent). So the CLI
does the same: this module is a pure function over the title string plus the
sync-file snapshot, and `sp add` turns the result into the normal HA / GA / HS /
HDL / KT / RA ops.

The regexes and the stage order are ported verbatim from SP's
`features/tasks/short-syntax.ts` (see
`.yoke/ai/batch2-research/research-task-methods.md` §9). The date grammar is a
small deterministic subset of what SP gets from chrono — English words only,
documented in the README.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from sp_cli.model import TODAY_TAG_ID

# ---------------------------------------------------------------- result


@dataclass
class ParseResult:
    """Everything `sp add` needs; every field is optional but `clean_title`."""

    clean_title: str
    project_id: str | None = None
    project_title: str | None = None
    tag_ids: list[str] = field(default_factory=list)
    new_tag_titles: list[str] = field(default_factory=list)
    time_estimate_ms: int | None = None
    time_spent_ms: int | None = None
    due_day: str | None = None
    due_with_time: int | None = None
    deadline_day: str | None = None
    deadline_with_time: int | None = None
    repeat: dict | None = None
    summary: list[str] = field(default_factory=list)
    #: the parse was thrown away because it would have left an empty title
    refused: bool = False

    @property
    def touched(self) -> bool:
        return bool(self.summary)


# ---------------------------------------------------------------- time

# SHORT_SYNTAX_TIME_REG_EX, verbatim. `t` prefix optional, clusters may repeat,
# an optional `/` splits "time spent / estimate".
_TIME_RE = re.compile(
    r"(?:\s|^)t?((?:\d+(?:\.\d+)?[mh]\s*)+)(?:\s*/((?:\s*\d+(?:\.\d+)?[mh])+)?)?(?=\s|$)"
)

_MS = {"s": 1000, "m": 60_000, "h": 3_600_000}
_ATOM_RE = re.compile(r"(\d*\.?\d+)\s*([smh]?)")
_HMM_RE = re.compile(r"^(\d+):([0-5]\d)$")


def string_to_ms(text: str) -> int:
    """SP's `stringToMs`: '30m', '1.5h', '1h 30m', '2:30', bare numbers.

    A bare (unit-less) number is hours when it is fractional or <= 8, and
    minutes when it is an integer > 8 — SP's heuristic for "8" meaning a
    working day and "45" meaning three quarters of an hour.
    """
    text = (text or "").strip().lower()
    if not text:
        return 0
    hmm = _HMM_RE.match(text)
    if hmm:
        return int(hmm.group(1)) * _MS["h"] + int(hmm.group(2)) * _MS["m"]
    total = 0.0
    found = False
    for value, unit in _ATOM_RE.findall(text):
        found = True
        num = float(value)
        if unit:
            total += num * _MS[unit]
        elif num != int(num) or num <= 8:
            total += num * _MS["h"]
        else:
            total += num * _MS["m"]
    return int(round(total)) if found else 0


def _sum_clusters(text: str) -> int:
    return sum(string_to_ms(part) for part in re.findall(r"\d+(?:\.\d+)?[smh]", text))


# ---------------------------------------------------------------- dates

_WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DOT_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?\.?$")
_SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")
_CLOCK_RE = re.compile(r"^(\d{1,2}):([0-5]\d)$")
_AMPM_RE = re.compile(r"^(\d{1,2})(?::([0-5]\d))?\s*(am|pm)$")
_BARE_HOUR_RE = re.compile(r"^(\d{1,2})$")


def _next_weekday(today: datetime.date, weekday: int) -> datetime.date:
    """Next occurrence, today included (chrono's forwardDate behaviour)."""
    return today + datetime.timedelta(days=(weekday - today.weekday()) % 7)


def _roll_year(day: int, month: int, today: datetime.date) -> datetime.date | None:
    for year in (today.year, today.year + 1):
        try:
            cand = datetime.date(year, month, day)
        except ValueError:
            continue
        if cand >= today:
            return cand
    return None


def parse_date_text(text: str, today: datetime.date) -> datetime.date | None:
    """A date word/number without a time-of-day, or None."""
    t = text.strip().lower().rstrip(",;:!?")
    if not t:
        return None
    if t == "today":
        return today
    if t == "tomorrow":
        return today + datetime.timedelta(days=1)
    if t in _WEEKDAY_NAMES:
        return _next_weekday(today, _WEEKDAY_NAMES[t])
    m = _ISO_RE.match(t)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    for rx in (_DOT_RE, _SLASH_RE):
        m = rx.match(t)
        if not m:
            continue
        day, month = int(m.group(1)), int(m.group(2))
        year = m.group(3)
        if not 1 <= month <= 12:
            return None
        if year is None:
            return _roll_year(day, month, today)
        y = int(year)
        if y < 100:
            y += 2000
        try:
            return datetime.date(y, month, day)
        except ValueError:
            return None
    return None


def parse_time_text(text: str) -> tuple[int, int] | None:
    """A time of day as (hour, minute), or None. Bare hours must be <= 24."""
    t = text.strip().lower().rstrip(",;:!?")
    if not t:
        return None
    m = _CLOCK_RE.match(t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        return (hour, minute) if hour <= 23 else None
    m = _AMPM_RE.match(t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if not 1 <= hour <= 12:
            return None
        if m.group(3) == "pm" and hour != 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
        return hour, minute
    m = _BARE_HOUR_RE.match(t)
    if m:
        hour = int(m.group(1))
        if hour <= 24:
            return hour % 24, 0
    return None


def parse_when(text: str, now: datetime.datetime) -> tuple[str | None, int | None] | None:
    """Resolve a date/time expression to `(day, None)` or `(None, epoch_ms)`.

    Accepts a bare date, a bare time (today, rolled to tomorrow when the hour
    has passed) or a "<date> <time>" combination. Returns None when nothing in
    `text` parses.
    """
    text = text.strip()
    if not text:
        return None
    today = now.date()

    date_only = parse_date_text(text, today)
    if date_only is not None:
        return date_only.isoformat(), None

    clock = parse_time_text(text)
    if clock is not None:
        when = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if when <= now:
            when += datetime.timedelta(days=1)
        return None, int(when.timestamp() * 1000)

    parts = text.split()
    for i in range(len(parts) - 1, 0, -1):
        date_part = parse_date_text(" ".join(parts[:i]), today)
        time_part = parse_time_text(" ".join(parts[i:]))
        if date_part is not None and time_part is not None:
            when = datetime.datetime.combine(
                date_part, datetime.time(time_part[0], time_part[1])
            )
            return None, int(when.timestamp() * 1000)
    return None


def _consume_when(
    text: str, now: datetime.datetime
) -> tuple[tuple[str | None, int | None], int] | None:
    """Longest leading date/time expression in `text` (max 3 words).

    Returns ((day, ms), consumed_chars) so the caller can leave the rest of the
    segment in the title, the way chrono only strips what it recognised.
    """
    stripped = text.lstrip()
    lead = len(text) - len(stripped)
    tokens = list(re.finditer(r"\S+", stripped))
    for k in range(min(3, len(tokens)), 0, -1):
        end = tokens[k - 1].end()
        got = parse_when(stripped[:end], now)
        if got is not None:
            return got, lead + end
    return None


def _consume_clock(text: str) -> tuple[tuple[int, int], int] | None:
    """Leading time-of-day in `text` → ((hour, minute), consumed chars).

    Only a time is accepted, and only directly after whatever came before —
    the adjacency rule SP applies to the chrono remainder of a recurrence
    phrase ('@every friday 3pm' absorbs the time, 'Standup @every monday and
    friday' must not swallow the words in between).
    """
    stripped = text.lstrip()
    lead = len(text) - len(stripped)
    tokens = list(re.finditer(r"\S+", stripped))
    for k in range(min(2, len(tokens)), 0, -1):
        end = tokens[k - 1].end()
        got = parse_time_text(stripped[:end])
        if got is not None:
            return got, lead + end
    return None


# ---------------------------------------------------------------- repeat

_WEEKDAY_KEYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
_WORKDAYS = _WEEKDAY_KEYS[:5]

_UNIT_CYCLE = {
    "day": "DAILY",
    "week": "WEEKLY",
    "month": "MONTHLY",
    "year": "YEARLY",
}

# SP's WEEKDAY_UNIT_SOURCE, verbatim: full names before abbreviations, because
# alternation is leftmost-first and a leading `fri` would leave the "day" of
# "friday" behind and fail the phrase-end lookahead.
_WEEKDAY_ALT = (
    "mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?"
    "|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun"
)
_SINGLE_UNIT_ALT = "days?|weeks?|months?|years?|weekdays?|workdays?"
# `weekday(s)`/`workday(s)` are deliberately absent from the *interval* units:
# "every 2 weekdays" means every other workday, which a weekly cycle cannot
# express (five weekday flags plus an interval means every other *week*, all
# five days), so it stays a plain date rather than becoming a wrong schedule.
_INTERVAL_UNIT_ALT = f"days?|weeks?|months?|years?|{_WEEKDAY_ALT}"

_REPEAT_RE = re.compile(
    r"^(?:(daily|weekly|monthly|yearly|annually)"
    rf"|every\s+({_SINGLE_UNIT_ALT}|{_WEEKDAY_ALT}|\d{{1,2}}(?:st|nd|rd|th))"
    rf"|every\s+([1-9]\d{{0,2}})\s+({_INTERVAL_UNIT_ALT}))"
    r"(?=[\s.,;:!?]|$)",
    re.IGNORECASE,
)


def _weekday_key(word: str) -> str | None:
    w = word.lower().rstrip("s")
    for key in _WEEKDAY_KEYS:
        if key.startswith(w) and len(w) >= 3:
            return key
    return None


def _day_of_month_start(day: int, today: datetime.date) -> str | None:
    """First date on or after today whose day-of-month is `day`."""
    if not 1 <= day <= 31:
        return None
    year, month = today.year, today.month
    for _ in range(14):
        try:
            cand = datetime.date(year, month, day)
        except ValueError:
            cand = None
        if cand is not None and cand >= today:
            return cand.isoformat()
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def parse_repeat(text: str, today: datetime.date) -> tuple[dict, int] | None:
    """`@every ...` / `@daily` → (repeat spec, consumed chars) or None.

    The spec is fed straight to `mutations.repeat_add`:
    `{repeat_cycle, repeat_every, days, start_date, label}`.
    """
    stripped = text.lstrip()
    lead = len(text) - len(stripped)
    m = _REPEAT_RE.match(stripped)
    if not m:
        return None
    preset, single, interval, interval_unit = m.groups()
    spec: dict = {"repeat_every": 1, "days": None, "start_date": None}

    def weekly_current() -> list[str]:
        return [_WEEKDAY_KEYS[today.weekday()]]

    if preset:
        p = preset.lower()
        if p == "daily":
            spec["repeat_cycle"] = "DAILY"
        elif p == "weekly":
            spec["repeat_cycle"] = "WEEKLY"
            spec["days"] = weekly_current()
        elif p == "monthly":
            spec["repeat_cycle"] = "MONTHLY"
        else:  # yearly | annually
            spec["repeat_cycle"] = "YEARLY"
    elif single:
        s = single.lower()
        nth = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", s)
        if nth:
            start = _day_of_month_start(int(nth.group(1)), today)
            if start is None:
                return None
            spec["repeat_cycle"] = "MONTHLY"
            spec["start_date"] = start
        elif s.rstrip("s") in ("weekday", "workday"):
            spec["repeat_cycle"] = "WEEKLY"
            spec["days"] = list(_WORKDAYS)
        elif s.rstrip("s") in _UNIT_CYCLE:
            spec["repeat_cycle"] = _UNIT_CYCLE[s.rstrip("s")]
            if spec["repeat_cycle"] == "WEEKLY":
                spec["days"] = weekly_current()
        else:
            key = _weekday_key(s)
            if key is None:
                return None
            spec["repeat_cycle"] = "WEEKLY"
            spec["days"] = [key]
            spec["start_date"] = _next_weekday(
                today, _WEEKDAY_KEYS.index(key)
            ).isoformat()
    else:
        unit = interval_unit.lower()
        # An interval of 1 collapses onto the plain preset (SP never stores a
        # CUSTOM cadence that a quick setting already expresses) — which the
        # field-level spec below expresses by itself.
        spec["repeat_every"] = int(interval)
        key = _weekday_key(unit)
        if key is not None:
            # "@every 2 fridays" — a weekly interval that names its own weekday
            # instead of taking today's.
            spec["repeat_cycle"] = "WEEKLY"
            spec["days"] = [key]
            spec["start_date"] = _next_weekday(
                today, _WEEKDAY_KEYS.index(key)
            ).isoformat()
        else:
            spec["repeat_cycle"] = _UNIT_CYCLE[unit.rstrip("s")]
            # A WEEKLY interval always pins the weekday flags to its first
            # occurrence (SP's getIntervalRepeatUpdates): the flags are an
            # independent filter, and the cfg default is mon-fri.
            if spec["repeat_cycle"] == "WEEKLY":
                spec["days"] = weekly_current()
    spec["label"] = m.group(0).strip()
    return spec, lead + m.end()


def _skip_weekend(day: datetime.date) -> datetime.date:
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def _roll_one_period(day: datetime.date, spec: dict) -> datetime.date:
    """Advance a first occurrence whose time of day has already passed.

    SP's applyRepeatSyntax roll-forward: an anchored cycle advances a whole
    period (so '@every friday 3pm' typed on a Friday afternoon lands on the
    next Friday), an unanchored one (DAILY, mon-fri) just moves to the next
    day, the way chrono's forwardDate already would.
    """
    cycle = spec["repeat_cycle"]
    days = spec.get("days") or []
    if cycle == "WEEKLY" and len(days) == 1:
        return day + datetime.timedelta(days=7)
    if cycle == "MONTHLY":
        nxt = _day_of_month_start(day.day, day + datetime.timedelta(days=1))
        return datetime.date.fromisoformat(nxt) if nxt else day
    if cycle == "YEARLY":
        try:
            return day.replace(year=day.year + 1)
        except ValueError:  # Feb 29
            return day.replace(year=day.year + 1, day=28)
    return day + datetime.timedelta(days=1)


def anchor_first_occurrence(
    spec: dict, clock: tuple[int, int], now: datetime.datetime
) -> datetime.datetime:
    """First occurrence of `spec` at `clock`, today or later.

    Mutates nothing; the caller writes the result back into the spec's
    `start_date` / `start_time`.
    """
    today = now.date()
    day = (
        datetime.date.fromisoformat(spec["start_date"])
        if spec.get("start_date")
        else today
    )
    when = datetime.datetime.combine(day, datetime.time(clock[0], clock[1]))
    if when <= now:
        when = datetime.datetime.combine(
            _roll_one_period(when.date(), spec), when.time()
        )
    if sorted(spec.get("days") or []) == sorted(_WORKDAYS):
        # A mon-fri schedule has no weekend occurrence: the roll above (or a
        # weekend `start_date`) would advertise one the task never gets.
        when = datetime.datetime.combine(_skip_weekend(when.date()), when.time())
    return when


# ---------------------------------------------------------------- projects/tags

_PROJECT_RE = re.compile(r"\+(?!\s)((?:(?!\s+(?:#|@|t?\d+[mh]\b)).)+)")
# SP's class is `[^(+|#|@|!)|\s]+` — a JS character class, so the literal
# parentheses and pipes are excluded characters too. Kept faithful.
_TAG_RE = re.compile(r"#[^()+|#@!\s]+")
_DUE_RE = re.compile(r"@[^+#@!]+")
_DEADLINE_RE = re.compile(r"![^+#@!]+")


def _preceded_by_space(text: str, index: int) -> bool:
    return index == 0 or text[index - 1].isspace()


def _visible_projects(d: dict) -> list[dict]:
    reg = (d.get("state") or {}).get("project") or {}
    entities = reg.get("entities") or {}
    out = []
    for pid in reg.get("ids", []):
        p = entities.get(pid)
        if not p or p.get("isArchived") or p.get("isHiddenFromMenu"):
            continue
        out.append(p)
    return out


def _matchable_projects(d: dict) -> list[dict]:
    """Visible projects as SP's `MatchableProject`, shortest title first."""
    out = []
    for project in _visible_projects(d):
        title = (project.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "project": project,
                "words": title.lower().split(),
                "squashed": title.replace(" ", "").lower(),
                "length": len(project["title"]),
            }
        )
    out.sort(key=lambda m: m["length"])
    return out


def _is_fully_typed(m: dict, typed: list[str]) -> bool:
    return m["words"] == typed


def _is_partially_typed(m: dict, typed: list[str]) -> bool:
    """A partial title may only shorten its LAST word.

    Otherwise task content would get pulled into the project title
    ('+Home work on taxes' must not match a project 'Homework', '+Workshop'
    must not match 'Work'). A single word may also be typed without any
    whitespace at all ('+SomePro' → 'Some Pro').
    """
    if len(typed) == 1:
        return m["squashed"].startswith(typed[0])
    if len(typed) > len(m["words"]):
        return False
    last = len(typed) - 1
    return all(
        m["words"][i].startswith(word) if i == last else m["words"][i] == word
        for i, word in enumerate(typed)
    )


def match_project(text: str, d: dict) -> tuple[dict, int] | None:
    """Word-boundary project match on `text` → (project, matched chars).

    `text` is everything the `+` token swallowed, so it usually carries task
    words after the project title: walk its word prefixes from the longest
    down and consume only the words that belong to a title. A fully typed
    title always wins over a partial one (SP's two passes), and among equal
    word counts the shortest project title does.
    """
    projects = _matchable_projects(d)
    if not projects:
        return None
    max_words = max(len(m["words"]) for m in projects)
    word_ends = [w.end() for w in re.finditer(r"\S+", text)]
    for is_match in (_is_fully_typed, _is_partially_typed):
        for count in range(min(len(word_ends), max_words), 0, -1):
            typed_title = text[: word_ends[count - 1]]
            typed = typed_title.lower().split()
            for m in projects:
                if is_match(m, typed):
                    return m["project"], len(typed_title)
    return None


def _tag_index(d: dict) -> dict[str, str]:
    reg = (d.get("state") or {}).get("tag") or {}
    entities = reg.get("entities") or {}
    out: dict[str, str] = {}
    for tid in reg.get("ids", []):
        tag = entities.get(tid)
        # TODAY is not a user tag: it must never land in task.tagIds (the
        # mutation layer rejects it), so short syntax cannot select it.
        if not tag or tid == TODAY_TAG_ID:
            continue
        out[(tag.get("title") or "").lower()] = tid
    return out


# ---------------------------------------------------------------- config

_SHORT_SYNTAX_DEFAULTS = {
    "isEnableProject": True,
    "isEnableDue": True,
    "isEnableTag": True,
    "isEnableDeadline": False,
}


def config_gates(d: dict) -> dict:
    """`globalConfig.shortSyntax` with SP's defaults filled in."""
    cfg = ((d.get("state") or {}).get("globalConfig") or {}).get("shortSyntax") or {}
    return {
        key: bool(cfg.get(key, default))
        for key, default in _SHORT_SYNTAX_DEFAULTS.items()
    }


# ---------------------------------------------------------------- parse

def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return text


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse(
    title: str,
    d: dict,
    *,
    force_deadline: bool = False,
    now: datetime.datetime | None = None,
) -> ParseResult:
    """Parse a task title. Pure: reads `d`, mutates nothing, does no I/O.

    Stage order is SP's: time → repeat → due → deadline → project → tags, and
    whatever is left over is the title.
    """
    now = now or datetime.datetime.now()
    today = now.date()
    gates = config_gates(d)
    res = ParseResult(clean_title=title)
    text = title

    # --- time (SP wraps parseTimeSpentTracked in the isEnableDue gate) ---
    if gates["isEnableDue"]:
        m = _TIME_RE.search(text)
        if m:
            pre, post = m.group(1), m.group(2)
            # The `/` decides, not the presence of a second cluster: '30m/' is
            # half an hour spent and no estimate at all (SP tests matchSpan
            # for the separator). A leading '/1h' cannot match the regex at
            # all — the pre-cluster is mandatory — and stays in the title.
            spent, estimate = (pre, post) if "/" in m.group(0) else (None, pre)
            parts = []
            if spent is not None:
                res.time_spent_ms = _sum_clusters(spent)
                parts.append(f"spent={spent.strip()}")
            if estimate is not None:
                res.time_estimate_ms = _sum_clusters(estimate)
                parts.append(f"est={estimate.strip()}")
            res.summary.append(" ".join(parts))
            start = m.start() + (1 if m.group(0)[:1].isspace() else 0)
            text = _cut(text, [(start, m.end())])

    # --- due (repeat is anchored at the start of the due text) -----------
    if gates["isEnableDue"]:
        for dm in _DUE_RE.finditer(text):
            if not _preceded_by_space(text, dm.start()):
                continue
            body = dm.group(0)[1:]
            body_at = dm.start() + 1
            rep = parse_repeat(body, today)
            if rep is not None:
                spec, used = rep
                label = spec.pop("label")
                # '@every monday 9:00' — absorb the adjacent time remainder
                # into the schedule instead of leaving it in the title.
                clock = _consume_clock(body[used:])
                if clock is not None:
                    (hour, minute), clock_used = clock
                    when = anchor_first_occurrence(spec, (hour, minute), now)
                    spec["start_date"] = when.date().isoformat()
                    spec["start_time"] = f"{hour:02d}:{minute:02d}"
                    res.due_with_time = int(when.timestamp() * 1000)
                    used += clock_used
                    label += f" {spec['start_time']}"
                res.repeat = spec
                res.summary.append(f"repeat={label}")
                text = _cut(text, [(dm.start(), body_at + used)])
                break
            got = _consume_when(body, now)
            if got is not None:
                (day, ms), used = got
                res.due_day, res.due_with_time = day, ms
                res.summary.append(f"due={day or _fmt_ts(ms)}")
                text = _cut(text, [(dm.start(), body_at + used)])
                break

    # --- deadline -------------------------------------------------------
    if gates["isEnableDeadline"] or force_deadline:
        for dm in _DEADLINE_RE.finditer(text):
            if not _preceded_by_space(text, dm.start()):
                continue
            got = _consume_when(dm.group(0)[1:], now)
            if got is not None:
                (day, ms), used = got
                res.deadline_day, res.deadline_with_time = day, ms
                res.summary.append(f"deadline={day or _fmt_ts(ms)}")
                text = _cut(text, [(dm.start(), dm.start() + 1 + used)])
                break

    # --- project --------------------------------------------------------
    if gates["isEnableProject"]:
        for pm in _PROJECT_RE.finditer(text):
            if not _preceded_by_space(text, pm.start()):
                continue
            found = match_project(pm.group(1), d)
            if found is not None:
                project, used = found
                res.project_id = project["id"]
                res.project_title = project.get("title")
                res.summary.append(f"project={project.get('title')}")
                text = _cut(text, [(pm.start(), pm.start(1) + used)])
            break

    # --- tags -----------------------------------------------------------
    if gates["isEnableTag"]:
        index = _tag_index(d)
        spans: list[tuple[int, int]] = []
        seen: set[str] = set()
        # SP rejects a numeric-only tag only at index 0 of the *trimmed* title
        # ('#123 fix' is an issue reference, 'Fix bug #123' is a tag).
        title_start = len(text) - len(text.lstrip())
        for tm in _TAG_RE.finditer(text):
            if not _preceded_by_space(text, tm.start()):
                continue
            name = tm.group(0)[1:]
            if tm.start() == title_start and re.fullmatch(r"\d+", name):
                continue
            key = name.lower()
            if key == "today":  # reserved: TODAY is not a user tag
                continue
            if key in seen:
                spans.append(tm.span())
                continue
            seen.add(key)
            if key in index:
                res.tag_ids.append(index[key])
            else:
                res.new_tag_titles.append(name)
            spans.append(tm.span())
        if spans:
            res.summary.append(
                "tags=" + ",".join("#" + n for n in _tag_names(res, index))
            )
            text = _cut(text, spans)

    clean = _collapse(text)
    if not clean:
        # Everything was syntax. A task with no title is worse than an
        # unparsed one, so the whole parse is thrown away — no project, no
        # tags, no schedule, no new tags created (SP keeps the title because
        # its add bar can show the parse and let the user fix it; the CLI has
        # one shot and prefers the literal title).
        return ParseResult(clean_title=_collapse(title), refused=True)
    res.clean_title = clean
    return res


def _tag_names(res: ParseResult, index: dict[str, str]) -> list[str]:
    by_id = {tid: name for name, tid in index.items()}
    return [by_id.get(tid, tid) for tid in res.tag_ids] + list(res.new_tag_titles)


def _fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
