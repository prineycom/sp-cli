"""argparse CLI for sp-cli."""

from __future__ import annotations

import argparse
import datetime
import re
import sys

from sp_cli import mutations as mut
from sp_cli import queries as q
from sp_cli import render
from sp_cli.config import ConfigError, init_config, load_config
from sp_cli.ids import nanoid
from sp_cli.model import (
    BACKLOG_STATE,
    INBOX_PROJECT_ID,
    PANEL_SORT_BY,
    SCHEDULED_STATE,
    SIMPLE_COUNTER_TYPES,
    STREAK_DAY_KEYS,
    TASK_DONE_STATE,
    TODAY_TAG_ID,
    make_board,
    make_note,
    make_panel,
    make_project,
    make_simple_counter,
    make_tag,
    make_task,
    now_ms,
    streak_week_days,
    today_str,
)
from sp_cli.store import SyncStore
from sp_cli.webdav import ConflictError, SyncFileClient, WebDavError


class CliError(Exception):
    pass


# ---------------------------------------------------------------- helpers

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _ctx() -> tuple[SyncFileClient, SyncStore]:
    cfg = load_config()
    client = SyncFileClient(
        cfg.url, cfg.folder, cfg.user, cfg.password, cfg.backup_dir
    )
    return client, SyncStore(client, cfg.client_id)


def _parse_day(text: str) -> str:
    text = text.strip()
    today = datetime.date.today()
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + datetime.timedelta(days=1)).isoformat()
    if text.startswith("+") and text[1:].isdigit():
        return (today + datetime.timedelta(days=int(text[1:]))).isoformat()
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise CliError(
            f"invalid day '{text}' (use YYYY-MM-DD, today, tomorrow, +N)"
        ) from None


def _parse_dt(text: str) -> int:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return int(datetime.datetime.strptime(text.strip(), fmt).timestamp() * 1000)
        except ValueError:
            continue
    raise CliError(f"invalid datetime '{text}' (use 'YYYY-MM-DD HH:MM')")


def _resolve_tasks(d: dict, refs: list[str]) -> list[str]:
    return [q.resolve_task(d, ref) for ref in refs]


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _resolve_tag_refs(
    d: dict, refs: list[str], create_missing: bool
) -> tuple[list[str], list]:
    """Returns (tag_ids, extra_mutations creating missing tags)."""
    tag_ids: list[str] = []
    extra = []
    missing: dict[str, str] = {}  # lowercased ref -> new id (dedupe creation)
    for ref in refs:
        try:
            tag_id = q.resolve_tag(d, ref)
        except q.NotFoundError:
            if not create_missing:
                raise CliError(
                    f"tag '{ref}' not found (use --create-tags to create it)"
                ) from None
            key = ref.lower()
            if key in missing:
                tag_id = missing[key]
            else:
                tag_id = nanoid()
                missing[key] = tag_id
                title = ref

                def _create(dd, b, _id=tag_id, _title=title):
                    mut.tag_add(dd, b, make_tag(_id, _title))

                extra.append(_create)
        if tag_id not in tag_ids:
            tag_ids.append(tag_id)
    return tag_ids, extra


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    path = init_config(
        url=args.url,
        user=args.user,
        password=args.password,
        password_file=args.password_file,
        folder=args.folder,
        backup_dir=args.backup_dir,
    )
    print(f"config written to {path}")
    return 0


def cmd_list(args) -> int:
    client, _ = _ctx()
    d = client.get()
    tasks = q.list_tasks(
        d,
        project=args.project,
        tag=args.tag,
        include_done=args.all,
        only_done=args.done,
        overdue=args.overdue,
        today=args.today,
        unscheduled=args.unscheduled,
        search=args.search,
        parents_only=args.parents_only,
    )
    if args.json:
        render.print_json(tasks)
    else:
        render.print_tasks(d, tasks)
    return 0


def cmd_show(args) -> int:
    client, _ = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    task = d["state"]["task"]["entities"][tid]
    if args.json:
        render.print_json(task)
    else:
        print(render.task_card(d, task))
    return 0


def cmd_add(args) -> int:
    if args.parent:
        incompatible = [
            flag
            for flag, value in (
                ("--project", args.project),
                ("--due", args.due),
                ("--at", args.at),
                ("--remind", args.remind),
                ("--tag", args.tag),
            )
            if value
        ]
        if incompatible:
            raise CliError(
                f"--parent is incompatible with {', '.join(incompatible)}: "
                "subtasks inherit the parent's project and carry no own "
                "scheduling or tags"
            )
    client, store = _ctx()
    d = client.get()
    est = render.parse_duration(args.est) if args.est else 0
    task_id = nanoid()
    muts = []

    if args.parent:
        parent_id = q.resolve_task(d, args.parent)

        def _sub(dd, b):
            parent = dd["state"]["task"]["entities"][parent_id]
            sub = make_task(
                task_id,
                args.title,
                parent["projectId"],
                time_estimate=est,
                notes=args.notes,
                parent_id=parent_id,
            )
            mut.add_subtask(dd, b, parent_id, sub)

        muts.append(_sub)
    else:
        project_id = (
            q.resolve_project(d, args.project) if args.project else INBOX_PROJECT_ID
        )
        tag_ids, tag_muts = _resolve_tag_refs(d, args.tag or [], args.create_tags)
        muts.extend(tag_muts)

        due_day = _parse_day(args.due) if args.due else None
        at_ts = _parse_dt(args.at) if args.at else None
        if due_day and at_ts:
            raise CliError("--due and --at are mutually exclusive")

        def _add(dd, b):
            task = make_task(
                task_id,
                args.title,
                project_id,
                time_estimate=est,
                tag_ids=tag_ids,
                notes=args.notes,
                due_day=due_day,
            )
            mut.add_task(dd, b, task)
            if at_ts is not None:
                offset = render.parse_offset(args.remind) if args.remind else 0
                mut.schedule_task(dd, b, task_id, at_ts, remind_at=at_ts - offset)

        muts.append(_add)

    store.commit(muts, initial=d)
    print(task_id)
    return 0


def cmd_edit(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    est = render.parse_duration(args.est) if args.est else None

    def _edit(dd, b):
        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.notes is not None:
            changes["notes"] = args.notes
        if args.append_notes is not None:
            current = dd["state"]["task"]["entities"][tid].get("notes") or ""
            changes["notes"] = (
                current + "\n" + args.append_notes if current else args.append_notes
            )
        if est is not None:
            changes["timeEstimate"] = est
        if args.due is not None:
            if args.due == "":
                changes["dueDay"] = None
            else:
                changes["dueDay"] = _parse_day(args.due)
        if not changes:
            raise CliError("edit: nothing to change")
        mut.update_task(dd, b, tid, changes)

    store.commit([_edit], initial=d)
    print(f"updated {render.short_id(tid)}")
    return 0


def _cmd_toggle_done(args, done: bool) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    fn = mut.complete_task if done else mut.reopen_task
    muts = [
        (lambda dd, b, _t=tid: fn(dd, b, _t))
        for tid in tids
    ]
    store.commit(muts, initial=d)
    verb = "completed" if done else "reopened"
    print(f"{verb} {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_complete(args) -> int:
    return _cmd_toggle_done(args, True)


def cmd_reopen(args) -> int:
    return _cmd_toggle_done(args, False)


def cmd_delete(args) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    titles = [d["state"]["task"]["entities"][t].get("title", "") for t in tids]
    if not _confirm(
        f"delete {len(tids)} task(s): {', '.join(titles)}?", args.yes
    ):
        print("aborted", file=sys.stderr)
        return 1
    if len(tids) == 1:
        muts = [lambda dd, b: mut.delete_task(dd, b, tids[0])]
    else:
        muts = [lambda dd, b: mut.delete_tasks(dd, b, tids)]
    store.commit(muts, initial=d)
    print(f"deleted {len(tids)} task(s)")
    return 0


def cmd_subtask(args) -> int:
    client, store = _ctx()
    d = client.get()
    parent_id = q.resolve_task(d, args.parent)
    est = render.parse_duration(args.est) if args.est else 0
    sub_id = nanoid()

    def _sub(dd, b):
        parent = dd["state"]["task"]["entities"][parent_id]
        sub = make_task(
            sub_id,
            args.title,
            parent["projectId"],
            time_estimate=est,
            notes=args.notes,
            parent_id=parent_id,
        )
        mut.add_subtask(dd, b, parent_id, sub)

    store.commit([_sub], initial=d)
    print(sub_id)
    return 0


def cmd_move(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    pid = q.resolve_project(d, args.project)
    store.commit([lambda dd, b: mut.move_to_project(dd, b, tid, pid)], initial=d)
    print(f"moved {render.short_id(tid)} to {pid}")
    return 0


def cmd_tag_task(args) -> int:
    if not args.add and not args.remove:
        raise CliError("tag: nothing to do (use --add / --remove)")
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    add_ids = [q.resolve_tag(d, ref) for ref in (args.add or [])]
    rm_ids = [q.resolve_tag(d, ref) for ref in (args.remove or [])]
    store.commit(
        [lambda dd, b: mut.tag_task(dd, b, tid, add=add_ids, remove=rm_ids)],
        initial=d,
    )
    print(f"tags updated on {render.short_id(tid)}")
    return 0


def cmd_reorder(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.project)
    listed = _resolve_tasks(d, args.ids)
    current = list(d["state"]["project"]["entities"][pid]["taskIds"])
    for tid in listed:
        if tid not in current:
            raise CliError(f"task {tid} is not a top-level task of project {pid}")
    ordered = listed + [t for t in current if t not in listed]
    store.commit([lambda dd, b: mut.reorder_project(dd, b, pid, ordered)], initial=d)
    print(f"reordered {pid}")
    return 0


def cmd_today(args) -> int:
    client, _ = _ctx()
    d = client.get()
    tasks = q.today_list(d)
    if args.json:
        render.print_json(tasks)
    else:
        render.print_tasks(d, tasks)
    return 0


def cmd_today_add(args) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    store.commit([lambda dd, b: mut.plan_today(dd, b, tids)], initial=d)
    print(f"planned for today: {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_today_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    muts = [(lambda dd, b, _t=tid: mut.remove_from_today(dd, b, _t)) for tid in tids]
    store.commit(muts, initial=d)
    print(f"removed from today: {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_plan(args) -> int:
    client, store = _ctx()
    d = client.get()
    day = _parse_day(args.day)
    tids = _resolve_tasks(d, args.ids)
    muts = [(lambda dd, b, _t=tid: mut.plan_for_day(dd, b, _t, day)) for tid in tids]
    store.commit(muts, initial=d)
    print(f"planned for {day}: {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_plan_show(args) -> int:
    client, _ = _ctx()
    d = client.get()
    days = d["state"].get("planner", {}).get("days", {})
    entities = d["state"]["task"]["entities"]
    if args.day:
        day_filter = _parse_day(args.day)
        days = {k: v for k, v in days.items() if k == day_filter}
    out = {"today": [t["id"] for t in q.today_list(d)], "days": days}
    if args.json:
        render.print_json(out)
        return 0
    print(f"today ({today_str()}):")
    for t in q.today_list(d):
        print(f"  {render.short_id(t['id'])} {t.get('title', '')}")
    for day in sorted(days):
        print(f"{day}:")
        for tid in days[day]:
            title = entities.get(tid, {}).get("title", "?")
            print(f"  {render.short_id(tid)} {title}")
    return 0


def cmd_schedule(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    ts = _parse_dt(args.at)
    remind = ts - render.parse_offset(args.remind) if args.remind else None
    store.commit(
        [lambda dd, b: mut.schedule_task(dd, b, tid, ts, remind_at=remind)],
        initial=d,
    )
    print(f"scheduled {render.short_id(tid)} at {render.format_ts(ts)}")
    return 0


def cmd_unschedule(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    store.commit([lambda dd, b: mut.unschedule(dd, b, tid)], initial=d)
    print(f"unscheduled {render.short_id(tid)}")
    return 0


def cmd_deadline(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    if bool(args.day) == bool(args.at):
        raise CliError("deadline: give exactly one of --day / --at")
    day = _parse_day(args.day) if args.day else None
    ts = _parse_dt(args.at) if args.at else None
    remind = None
    if args.remind:
        if ts is None:
            raise CliError("deadline: --remind requires --at")
        remind = ts - render.parse_offset(args.remind)
    store.commit(
        [
            lambda dd, b: mut.set_deadline(
                dd,
                b,
                tid,
                deadline_day=day,
                deadline_with_time=ts,
                deadline_remind_at=remind,
            )
        ],
        initial=d,
    )
    print(f"deadline set on {render.short_id(tid)}")
    return 0


def cmd_agenda(args) -> int:
    client, _ = _ctx()
    d = client.get()
    a = q.agenda(d)
    if args.json:
        render.print_json(a)
        return 0
    print(f"# agenda {today_str()}")
    if a["overdue"]:
        print("\n## overdue")
        render.print_tasks(d, a["overdue"])
    print("\n## today")
    render.print_tasks(d, a["today"])
    if a["scheduled_today"]:
        print("\n## scheduled today")
        for t in a["scheduled_today"]:
            print(
                f"  {render.format_time(t['dueWithTime'])}  "
                f"{render.short_id(t['id'])} {t.get('title', '')}"
            )
    if a["deadlines"]:
        print("\n## deadlines (7d)")
        for t in a["deadlines"]:
            dd = t.get("deadlineDay") or render.format_ts(t.get("deadlineWithTime"))
            print(f"  {dd}  {render.short_id(t['id'])} {t.get('title', '')}")
    return 0


def cmd_track(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    ms = render.parse_duration(args.duration)
    date = _parse_day(args.date) if args.date else today_str()
    store.commit([lambda dd, b: mut.track_time(dd, b, tid, date, ms)], initial=d)
    print(f"tracked {render.format_duration(ms)} on {render.short_id(tid)} ({date})")
    return 0


def cmd_worklog(args) -> int:
    client, _ = _ctx()
    d = client.get()
    date_from = _parse_day(getattr(args, "from")) if getattr(args, "from") else None
    date_to = _parse_day(args.to) if args.to else None
    log = q.worklog(d, date_from, date_to)
    if args.json:
        render.print_json(log)
        return 0
    projects = d["state"]["project"]["entities"]
    print("per day:")
    for day, ms in log["days"].items():
        print(f"  {day}  {render.format_duration(ms)}")
    print("per project:")
    for pid, ms in sorted(log["projects"].items(), key=lambda kv: -kv[1]):
        title = projects.get(pid, {}).get("title", pid)
        print(f"  {title}  {render.format_duration(ms)}")
    est = log["estimate"]
    if est["estimated"]:
        pct = round(100 * est["spent"] / est["estimated"])
        print(
            f"estimate accuracy: spent {render.format_duration(est['spent'])} "
            f"of {render.format_duration(est['estimated'])} estimated ({pct}%)"
        )
    return 0


_CYCLES = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}
_DAY_KEYS = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}


def cmd_repeat(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    cycle = _CYCLES[args.every]
    days = None
    if args.days:
        try:
            days = [_DAY_KEYS[p.strip().lower()] for p in args.days.split(",")]
        except KeyError as e:
            raise CliError(f"invalid weekday {e} (use mon,tue,...)") from None
    start_date = _parse_day(args.start_date) if args.start_date else None
    cfg_id = nanoid()
    store.commit(
        [
            lambda dd, b: mut.repeat_add(
                dd,
                b,
                tid,
                cfg_id,
                repeat_cycle=cycle,
                repeat_every=args.interval,
                days=days,
                start_date=start_date,
                start_time=args.start_time,
                remind_at=args.remind,
            )
        ],
        initial=d,
    )
    print(cfg_id)
    return 0


def cmd_repeats(args) -> int:
    client, _ = _ctx()
    d = client.get()
    reg = d["state"].get("taskRepeatCfg", {"ids": [], "entities": {}})
    cfgs = [reg["entities"][cid] for cid in reg["ids"] if cid in reg["entities"]]
    if args.json:
        render.print_json(cfgs)
        return 0
    rows = [
        [
            render.short_id(c["id"]),
            render.truncate(c.get("title", ""), 40),
            c.get("repeatCycle", "?"),
            str(c.get("repeatEvery", "?")),
            c.get("startDate", "-"),
            "paused" if c.get("isPaused") else "",
        ]
        for c in cfgs
    ]
    render.print_table(["id", "title", "cycle", "every", "start", ""], rows)
    return 0


def cmd_projects(args) -> int:
    client, _ = _ctx()
    d = client.get()
    reg = d["state"]["project"]
    projects = [reg["entities"][pid] for pid in reg["ids"] if pid in reg["entities"]]
    if not args.all:
        projects = [p for p in projects if not p.get("isArchived")]
    if args.json:
        render.print_json(projects)
        return 0
    rows = [
        [
            p["id"],
            p.get("title", ""),
            str(len(p.get("taskIds", []))),
            "archived" if p.get("isArchived") else "",
        ]
        for p in projects
    ]
    render.print_table(["id", "title", "tasks", ""], rows)
    return 0


def cmd_project_add(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = nanoid()
    store.commit(
        [lambda dd, b: mut.project_add(dd, b, make_project(pid, args.title, args.color))],
        initial=d,
    )
    print(pid)
    return 0


def cmd_project_edit(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.id)

    def _edit(dd, b):
        project = dd["state"]["project"]["entities"][pid]
        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.color is not None:
            changes["theme"] = {**project["theme"], "primary": args.color}
        if args.hide:
            changes["isHiddenFromMenu"] = True
        if not changes:
            raise CliError("project edit: nothing to change")
        mut.project_update(dd, b, pid, changes)

    store.commit([_edit], initial=d)
    print(f"updated {pid}")
    return 0


def cmd_project_archive(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.id)
    store.commit(
        [lambda dd, b: mut.project_update(dd, b, pid, {"isArchived": True})],
        initial=d,
    )
    print(f"archived {pid}")
    return 0


def cmd_tags(args) -> int:
    client, _ = _ctx()
    d = client.get()
    reg = d["state"]["tag"]
    tags = [reg["entities"][tid] for tid in reg["ids"] if tid in reg["entities"]]
    if args.json:
        render.print_json(tags)
        return 0
    rows = [
        [t["id"], t.get("title", ""), t.get("color") or "-", str(len(t.get("taskIds", [])))]
        for t in tags
    ]
    render.print_table(["id", "title", "color", "tasks"], rows)
    return 0


def cmd_tag_new(args) -> int:
    client, store = _ctx()
    d = client.get()
    tag_id = nanoid()
    store.commit(
        [lambda dd, b: mut.tag_add(dd, b, make_tag(tag_id, args.title, args.color))],
        initial=d,
    )
    print(tag_id)
    return 0


def cmd_tag_edit(args) -> int:
    client, store = _ctx()
    d = client.get()
    tag_id = q.resolve_tag(d, args.id)

    def _edit(dd, b):
        tag = dd["state"]["tag"]["entities"][tag_id]
        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.color is not None:
            changes["color"] = args.color
            changes["theme"] = {**tag["theme"], "primary": args.color}
        if not changes:
            raise CliError("tag edit: nothing to change")
        mut.tag_update(dd, b, tag_id, changes)

    store.commit([_edit], initial=d)
    print(f"updated {tag_id}")
    return 0


def cmd_notes(args) -> int:
    client, _ = _ctx()
    d = client.get()
    notes = q.list_notes(d, project=args.project, today=args.today)
    if args.json:
        render.print_json(notes)
    else:
        render.print_notes(d, notes)
    return 0


def cmd_note_add(args) -> int:
    client, store = _ctx()
    d = client.get()
    project_id = q.resolve_project(d, args.project) if args.project else None
    note_id = nanoid()

    def _add(dd, b):
        note = make_note(
            note_id,
            args.content,
            project_id=project_id,
            is_pinned_to_today=bool(args.pin),
        )
        mut.note_add(dd, b, note)

    store.commit([_add], initial=d)
    print(note_id)
    return 0


def cmd_note_show(args) -> int:
    client, _ = _ctx()
    d = client.get()
    nid = q.resolve_note(d, args.id)
    note = d["state"]["note"]["entities"][nid]
    if args.json:
        render.print_json(note)
    else:
        print(render.note_card(d, note))
    return 0


def cmd_note_edit(args) -> int:
    if args.pin and args.unpin:
        raise CliError("note edit: --pin and --unpin are mutually exclusive")
    if args.content is not None and args.append is not None:
        raise CliError("note edit: --content and --append are mutually exclusive")
    if args.color is not None and not _HEX_COLOR_RE.match(args.color):
        raise CliError(
            f"note edit: --color must be a hex color like '#a05db1' (got {args.color!r})"
        )
    client, store = _ctx()
    d = client.get()
    nid = q.resolve_note(d, args.id)

    def _edit(dd, b):
        changes: dict = {}
        if args.content is not None:
            changes["content"] = args.content
        if args.append is not None:
            current = dd["state"]["note"]["entities"][nid].get("content") or ""
            changes["content"] = (
                current + "\n" + args.append if current else args.append
            )
        if args.color is not None:
            changes["backgroundColor"] = args.color
        if args.pin:
            changes["isPinnedToToday"] = True
        if args.unpin:
            changes["isPinnedToToday"] = False
        if not changes:
            raise CliError("note edit: nothing to change")
        mut.note_update(dd, b, nid, changes)

    store.commit([_edit], initial=d)
    print(f"updated {render.short_id(nid)}")
    return 0


def cmd_note_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    nid = q.resolve_note(d, args.id)
    preview = render.truncate(
        render.first_line(d["state"]["note"]["entities"][nid].get("content")), 60
    )
    if not _confirm(f"delete note '{preview}'?", args.yes):
        print("aborted", file=sys.stderr)
        return 1
    store.commit([lambda dd, b: mut.note_delete(dd, b, nid)], initial=d)
    print(f"deleted {render.short_id(nid)}")
    return 0


def cmd_note_move(args) -> int:
    client, store = _ctx()
    d = client.get()
    nid = q.resolve_note(d, args.id)
    pid = q.resolve_project(d, args.project)
    store.commit([lambda dd, b: mut.note_move(dd, b, nid, pid)], initial=d)
    print(f"moved {render.short_id(nid)} to {pid}")
    return 0


# ---------------------------------------------------------------- boards

def _tag_list(d: dict, text: str | None) -> list[str] | None:
    """'a,b' → resolved tag ids (empty string clears the filter)."""
    if text is None:
        return None
    refs = [r.strip() for r in text.split(",") if r.strip()]
    ids: list[str] = []
    for ref in refs:
        tag_id = q.resolve_tag(d, ref)
        if tag_id not in ids:
            ids.append(tag_id)
    return ids


def _panel_args(sp) -> None:
    sp.add_argument("--tags", help="comma-separated tags the task must have")
    sp.add_argument("--exclude-tags", help="comma-separated tags to exclude")
    sp.add_argument("--tags-match", choices=["all", "any"])
    sp.add_argument("--exclude-tags-match", choices=["all", "any"])
    sp.add_argument("--project", action="append", help="limit to project (repeatable)")
    sp.add_argument("--all-projects", action="store_true")
    sp.add_argument("--done", choices=sorted(TASK_DONE_STATE))
    sp.add_argument("--scheduled", choices=sorted(SCHEDULED_STATE))
    sp.add_argument("--backlog", choices=sorted(BACKLOG_STATE))
    sp.add_argument("--parents-only", action="store_true")
    sp.add_argument("--no-parents-only", action="store_true")
    sp.add_argument(
        "--sort",
        choices=[*PANEL_SORT_BY, "manual"],
        help="sort field, or 'manual' to drop sorting and use the panel's "
        "manual task order",
    )
    sp.add_argument(
        "--no-sort",
        action="store_true",
        help="alias for --sort manual",
    )
    sp.add_argument("--dir", choices=["asc", "desc"])


def _panel_changes(d: dict, args) -> dict:
    """Only the filter keys the user actually passed."""
    if args.project and args.all_projects:
        raise CliError("--project and --all-projects are mutually exclusive")
    if args.parents_only and args.no_parents_only:
        raise CliError("--parents-only and --no-parents-only are mutually exclusive")
    changes: dict = {}
    included = _tag_list(d, args.tags)
    if included is not None:
        if TODAY_TAG_ID in included:
            raise CliError("'TODAY' cannot be used as a board panel tag filter")
        changes["includedTagIds"] = included
    excluded = _tag_list(d, args.exclude_tags)
    if excluded is not None:
        changes["excludedTagIds"] = excluded
    if args.tags_match:
        changes["includedTagsMatch"] = args.tags_match
    if args.exclude_tags_match:
        changes["excludedTagsMatch"] = args.exclude_tags_match
    if args.all_projects:
        changes["projectIds"] = [""]
    elif args.project:
        changes["projectIds"] = [q.resolve_project(d, p) for p in args.project]
    if args.done:
        changes["taskDoneState"] = TASK_DONE_STATE[args.done]
    if args.scheduled:
        changes["scheduledState"] = SCHEDULED_STATE[args.scheduled]
    if args.backlog:
        changes["backlogState"] = BACKLOG_STATE[args.backlog]
    if args.parents_only:
        changes["isParentTasksOnly"] = True
    if args.no_parents_only:
        changes["isParentTasksOnly"] = False
    sort = "manual" if args.no_sort else args.sort
    if args.no_sort and args.sort and args.sort != "manual":
        raise CliError("--no-sort and --sort are mutually exclusive")
    if sort == "manual":
        if args.dir:
            raise CliError("--dir cannot be combined with --sort manual")
        # None drops both keys in the sanitizer: back to manual (BT) order.
        changes["sortBy"] = None
        changes["sortDir"] = None
    elif sort:
        changes["sortBy"] = sort
        changes["sortDir"] = args.dir or "asc"
    elif args.dir:
        raise CliError("--dir requires --sort")
    return changes


def cmd_boards(args) -> int:
    client, _ = _ctx()
    d = client.get()
    boards = q.all_boards(d)
    if args.json:
        render.print_json(boards)
    else:
        render.print_boards(d, boards)
    return 0


def _check_cols(cols) -> None:
    if cols is not None and int(cols) < 1:
        raise CliError("--cols must be 1 or more")


def cmd_board_add(args) -> int:
    _check_cols(args.cols)
    client, store = _ctx()
    d = client.get()
    board_id = nanoid()
    store.commit(
        [
            lambda dd, b: mut.board_add(
                dd, b, make_board(board_id, args.title, cols=args.cols)
            )
        ],
        initial=d,
    )
    print(board_id)
    return 0


def cmd_board_edit(args) -> int:
    _check_cols(args.cols)
    client, store = _ctx()
    d = client.get()
    board_id = q.resolve_board(d, args.id)
    updates: dict = {}
    if args.title is not None:
        updates["title"] = args.title
    if args.cols is not None:
        updates["cols"] = args.cols
    if not updates:
        raise CliError("board edit: nothing to change")
    store.commit(
        [lambda dd, b: mut.board_update(dd, b, board_id, updates)], initial=d
    )
    print(f"updated {board_id}")
    return 0


def cmd_board_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    board_id = q.resolve_board(d, args.id)
    title = next(b.get("title", "") for b in q.all_boards(d) if b["id"] == board_id)
    if not _confirm(f"delete board '{title}'?", args.yes):
        print("aborted", file=sys.stderr)
        return 1
    store.commit([lambda dd, b: mut.board_delete(dd, b, board_id)], initial=d)
    print(f"deleted {board_id}")
    return 0


def cmd_board_sort(args) -> int:
    client, store = _ctx()
    d = client.get()
    ids = [q.resolve_board(d, ref) for ref in args.ids]
    store.commit([lambda dd, b: mut.boards_sort(dd, b, ids)], initial=d)
    print(f"reordered boards: {', '.join(ids)}")
    return 0


def cmd_board_panel_add(args) -> int:
    client, store = _ctx()
    d = client.get()
    board_id = q.resolve_board(d, args.board)
    changes = _panel_changes(d, args)
    panel_id = nanoid()

    def _add(dd, b):
        mut.panel_add(dd, b, board_id, make_panel(panel_id, args.title, **changes))

    store.commit([_add], initial=d)
    print(panel_id)
    return 0


def cmd_board_panel_edit(args) -> int:
    client, store = _ctx()
    d = client.get()
    panel_id = q.resolve_panel(d, args.id)
    changes = _panel_changes(d, args)
    if args.title is not None:
        changes["title"] = args.title
    if not changes:
        raise CliError("board panel edit: nothing to change")
    store.commit(
        [lambda dd, b: mut.panel_update(dd, b, panel_id, changes)], initial=d
    )
    print(f"updated panel {panel_id}")
    return 0


def cmd_board_panel_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    panel_id = q.resolve_panel(d, args.id)
    store.commit([lambda dd, b: mut.panel_remove(dd, b, panel_id)], initial=d)
    print(f"deleted panel {panel_id}")
    return 0


def cmd_board_panel_order(args) -> int:
    client, store = _ctx()
    d = client.get()
    panel_id = q.resolve_panel(d, args.id)
    tids = _resolve_tasks(d, args.ids)
    store.commit(
        [lambda dd, b: mut.panel_task_order(dd, b, panel_id, tids)], initial=d
    )
    print(f"panel {panel_id}: order set for {len(tids)} task(s)")
    return 0


# ---------------------------------------------------------------- counters

def _counter_amount(counter: dict, text: str, what: str = "value") -> int:
    """StopWatch counters count milliseconds (durations), the rest clicks."""
    if counter.get("type") == "StopWatch":
        return render.parse_duration(text)
    try:
        return int(text)
    except ValueError:
        raise CliError(
            f"counter {what} must be a whole number for this counter type "
            f"(got {text!r})"
        ) from None


def _streak_days(text: str) -> list[str]:
    keys = []
    for part in text.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in STREAK_DAY_KEYS:
            raise CliError(f"invalid weekday '{part}' (use mon,tue,...)")
        keys.append(STREAK_DAY_KEYS[part])
    return keys


def cmd_counters(args) -> int:
    client, _ = _ctx()
    d = client.get()
    counters = q.all_counters(d)
    if args.json:
        render.print_json(counters)
        return 0
    values = [q.counter_value(c) for c in counters]
    render.print_counters(counters, values)
    return 0


def cmd_counter_add(args) -> int:
    counter_type = SIMPLE_COUNTER_TYPES[args.type]
    if args.countdown and counter_type != "RepeatedCountdownReminder":
        raise CliError("--countdown only applies to --type countdown")
    countdown = render.parse_duration(args.countdown) if args.countdown else None
    days = _streak_days(args.streak_days) if args.streak_days else None
    if args.streak_min is not None:
        streak_min = (
            render.parse_duration(args.streak_min)
            if counter_type == "StopWatch"
            else int(args.streak_min)
        )
    else:
        streak_min = 1
    client, store = _ctx()
    d = client.get()
    counter_id = nanoid()

    def _add(dd, b):
        counter = make_simple_counter(
            counter_id,
            args.title,
            counter_type=counter_type,
            icon=args.icon,
            is_track_streaks=not args.no_streak,
            streak_min_value=streak_min,
            week_days=days,
            countdown_duration=countdown,
        )
        mut.counter_add(dd, b, counter)

    store.commit([_add], initial=d)
    print(counter_id)
    return 0


def cmd_counter_edit(args) -> int:
    if args.enable and args.disable:
        raise CliError("counter edit: --enable and --disable are mutually exclusive")
    if args.streak and args.no_streak:
        raise CliError("counter edit: --streak and --no-streak are mutually exclusive")
    client, store = _ctx()
    d = client.get()
    cid = q.resolve_counter(d, args.id)
    counter = d["state"]["simpleCounter"]["entities"][cid]

    changes: dict = {}
    if args.title is not None:
        changes["title"] = args.title
    if args.icon is not None:
        changes["icon"] = args.icon or None
    if args.enable:
        changes["isEnabled"] = True
    if args.disable:
        changes["isEnabled"] = False
    if args.streak:
        changes["isTrackStreaks"] = True
    if args.no_streak:
        changes["isTrackStreaks"] = False
    if args.streak_min is not None:
        changes["streakMinValue"] = _counter_amount(
            counter, args.streak_min, "--streak-min"
        )
    if args.streak_days is not None:
        changes["streakWeekDays"] = streak_week_days(_streak_days(args.streak_days))
    if args.countdown is not None:
        if counter.get("type") != "RepeatedCountdownReminder":
            raise CliError("--countdown only applies to countdown counters")
        changes["countdownDuration"] = render.parse_duration(args.countdown)
    if not changes:
        raise CliError("counter edit: nothing to change")

    store.commit([lambda dd, b: mut.counter_update(dd, b, cid, changes)], initial=d)
    print(f"updated {cid}")
    return 0


def cmd_counter_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    cid = q.resolve_counter(d, args.id)
    title = d["state"]["simpleCounter"]["entities"][cid].get("title", "")
    if not _confirm(f"delete counter '{title}'?", args.yes):
        print("aborted", file=sys.stderr)
        return 1
    store.commit([lambda dd, b: mut.counter_delete(dd, b, cid)], initial=d)
    print(f"deleted {cid}")
    return 0


def cmd_counter_set(args) -> int:
    client, store = _ctx()
    d = client.get()
    cid = q.resolve_counter(d, args.id)
    counter = d["state"]["simpleCounter"]["entities"][cid]
    value = _counter_amount(counter, args.value)
    date = _parse_day(args.date) if args.date else today_str()
    store.commit([lambda dd, b: mut.counter_set(dd, b, cid, value, date)], initial=d)
    print(f"{cid} {date}: {render.counter_value_str(counter, max(0, value))}")
    return 0


def cmd_counter_inc(args) -> int:
    client, store = _ctx()
    d = client.get()
    cid = q.resolve_counter(d, args.id)
    counter = d["state"]["simpleCounter"]["entities"][cid]
    by = _counter_amount(counter, args.by, "--by") if args.by else 1
    date = _parse_day(args.date) if args.date else today_str()
    result: list[int] = []

    def _inc(dd, b):
        result.append(mut.counter_inc(dd, b, cid, by, date))

    store.commit([_inc], initial=d)
    print(f"{cid} {date}: {render.counter_value_str(counter, result[0])}")
    return 0


def cmd_counter_log(args) -> int:
    client, store = _ctx()
    d = client.get()
    cid = q.resolve_counter(d, args.id)
    counter = d["state"]["simpleCounter"]["entities"][cid]
    if counter.get("type") != "StopWatch":
        raise CliError(
            f"counter log: '{counter.get('title')}' is not a stopwatch counter; "
            "use 'sp counter inc' / 'sp counter set'"
        )
    ms = render.parse_duration(args.duration)
    date = _parse_day(args.date) if args.date else today_str()
    total: list[int] = []

    def _log(dd, b):
        total.append(mut.counter_log_time(dd, b, cid, date, ms))

    store.commit([_log], initial=d)
    print(
        f"logged {render.format_duration(ms)} on {cid} ({date}), "
        f"total {render.format_duration(total[0])}"
    )
    return 0


def cmd_counter_order(args) -> int:
    client, store = _ctx()
    d = client.get()
    listed = [q.resolve_counter(d, ref) for ref in args.ids]
    current = [c["id"] for c in q.all_counters(d)]
    ordered = listed + [cid for cid in current if cid not in listed]
    store.commit([lambda dd, b: mut.counter_order(dd, b, ordered)], initial=d)
    print(f"reordered counters: {', '.join(ordered)}")
    return 0


def cmd_archive(args) -> int:
    client, store = _ctx()
    d = client.get()
    done = [
        t
        for t in q.all_tasks(d)
        if t.get("isDone") and not t.get("parentId")
    ]
    if not done:
        print("nothing to archive")
        return 0
    if not _confirm(f"archive {len(done)} done task(s)?", args.yes):
        print("aborted", file=sys.stderr)
        return 1
    archived: list[str] = []

    def _arch(dd, b):
        archived.extend(mut.archive_done(dd, b))

    store.commit([_arch], initial=d)
    print(f"archived {len(archived)} task(s)")
    return 0


def cmd_pull(args) -> int:
    client, _ = _ctx()
    d = client.get()
    if args.raw:
        render.print_json(d)
        return 0
    state = d["state"]
    tasks = q.all_tasks(d)
    print(f"syncVersion:  {d.get('syncVersion')}")
    print(f"clientId:     {d.get('clientId')}")
    print(f"lastModified: {render.format_ts(d.get('lastModified'))}")
    print(f"vectorClock:  {d.get('vectorClock')}")
    print(f"recentOps:    {len(d.get('recentOps', []))}")
    print(f"tasks:        {len(tasks)} ({sum(1 for t in tasks if t.get('isDone'))} done)")
    print(f"projects:     {len(state['project']['ids'])}")
    print(f"tags:         {len(state['tag']['ids'])}")
    print(f"notes:        {len(q.all_notes(d))}")
    print(f"boards:       {len(q.all_boards(d))}")
    print(f"counters:     {len(q.all_counters(d))}")
    return 0


def cmd_doctor(args) -> int:
    client, _ = _ctx()
    d = client.get()
    problems = q.doctor(d)
    if not problems:
        print("ok: no problems found")
        return 0
    for p in problems:
        print(f"problem: {p}")
    return 1


def cmd_backup(args) -> int:
    client, _ = _ctx()
    client.get()
    path = client.save_backup()
    print(f"backup written to {path}")
    return 0


# ---------------------------------------------------------------- parser

_SUBCOMMAND_REWRITES = {
    ("today", "add"): "today-add",
    ("today", "rm"): "today-rm",
    ("plan", "show"): "plan-show",
    ("project", "add"): "project-add",
    ("project", "edit"): "project-edit",
    ("project", "archive"): "project-archive",
    ("tag", "new"): "tag-new",
    ("tag", "edit"): "tag-edit",
    ("note", "add"): "note-add",
    ("note", "show"): "note-show",
    ("note", "edit"): "note-edit",
    ("note", "rm"): "note-rm",
    ("note", "move"): "note-move",
    ("board", "add"): "board-add",
    ("board", "edit"): "board-edit",
    ("board", "rm"): "board-rm",
    ("board", "sort"): "board-sort",
    ("counter", "add"): "counter-add",
    ("counter", "edit"): "counter-edit",
    ("counter", "rm"): "counter-rm",
    ("counter", "set"): "counter-set",
    ("counter", "inc"): "counter-inc",
    ("counter", "log"): "counter-log",
    ("counter", "order"): "counter-order",
}

_SUBCOMMAND_REWRITES_3 = {
    ("board", "panel", "add"): "board-panel-add",
    ("board", "panel", "edit"): "board-panel-edit",
    ("board", "panel", "rm"): "board-panel-rm",
    ("board", "panel", "order"): "board-panel-order",
}


def _rewrite_argv(argv: list[str]) -> list[str]:
    if len(argv) >= 3 and tuple(argv[:3]) in _SUBCOMMAND_REWRITES_3:
        return [_SUBCOMMAND_REWRITES_3[tuple(argv[:3])]] + argv[3:]
    if len(argv) >= 2 and (argv[0], argv[1]) in _SUBCOMMAND_REWRITES:
        return [_SUBCOMMAND_REWRITES[(argv[0], argv[1])]] + argv[2:]
    # bare `sp note [--flags]` == `sp notes`
    if argv[:1] == ["note"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["notes"] + argv[1:]
    # bare `sp counter [--flags]` == `sp counters`
    if argv[:1] == ["counter"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["counters"] + argv[1:]
    return argv


_BOARD_USAGE = """usage: sp board <subcommand> ...

  sp boards [--json]                    list boards and their panels
  sp board add TITLE [--cols N]         create a board
  sp board edit ID [--title T] [--cols N]
  sp board rm ID [--yes]
  sp board sort ID [ID ...]             reorder boards (listed ones first)
  sp board panel add BOARD TITLE [filters]
  sp board panel edit PANEL [filters]
  sp board panel rm PANEL
  sp board panel order PANEL TASK [TASK ...]

Run `sp board-panel-add --help` (etc.) for the full filter flags."""


def _board_group_help(argv: list[str]) -> str | None:
    """`sp board` / `sp board panel` (+ typos) get the group usage instead of
    argparse's baffling "invalid choice: 'board'"."""
    if not argv or argv[0] != "board":
        return None
    if len(argv) >= 2 and argv[1] == "panel":
        bad = argv[2] if len(argv) > 2 else None
        what = f"unknown board panel subcommand: {bad}" if bad else (
            "sp board panel needs a subcommand (add, edit, rm, order)"
        )
    else:
        bad = argv[1] if len(argv) > 1 else None
        what = f"unknown board subcommand: {bad}" if bad else (
            "sp board needs a subcommand (add, edit, rm, sort, panel)"
        )
    return f"{what}\n\n{_BOARD_USAGE}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sp", description="SuperProductivity CLI")
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, fn, help_=""):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    s = add("init", cmd_init, "create the config file")
    s.add_argument("--url", required=True)
    s.add_argument("--user", required=True)
    s.add_argument("--password")
    s.add_argument("--password-file")
    s.add_argument("--folder", default="superproductivity")
    s.add_argument("--backup-dir")

    s = add("list", cmd_list, "list tasks")
    s.add_argument("--project")
    s.add_argument("--tag")
    s.add_argument("--done", action="store_true", help="only done tasks")
    s.add_argument("--all", action="store_true", help="include done tasks")
    s.add_argument("--overdue", action="store_true")
    s.add_argument("--today", action="store_true")
    s.add_argument("--unscheduled", action="store_true")
    s.add_argument("--search")
    s.add_argument("--parents-only", action="store_true")
    s.add_argument("--json", action="store_true")

    s = add("show", cmd_show, "show one task")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")

    s = add("add", cmd_add, "add a task")
    s.add_argument("title")
    s.add_argument("--project")
    s.add_argument("--tag", action="append")
    s.add_argument("--create-tags", action="store_true")
    s.add_argument("--due", help="YYYY-MM-DD | today | tomorrow | +N")
    s.add_argument("--at", help="'YYYY-MM-DD HH:MM' schedule with time")
    s.add_argument("--remind", help="offset before --at, e.g. 10m (default 0m)")
    s.add_argument("--est", help="e.g. 30m, 1.5h")
    s.add_argument("--notes")
    s.add_argument("--parent", help="create as subtask of this task")

    s = add("edit", cmd_edit, "edit a task")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--notes")
    s.add_argument("--append-notes")
    s.add_argument("--est")
    s.add_argument("--due", help="YYYY-MM-DD; empty string clears")

    s = add("complete", cmd_complete, "mark done")
    s.add_argument("ids", nargs="+")
    s = add("reopen", cmd_reopen, "mark not done")
    s.add_argument("ids", nargs="+")

    s = add("delete", cmd_delete, "delete tasks")
    s.add_argument("ids", nargs="+")
    s.add_argument("--yes", action="store_true")

    s = add("subtask", cmd_subtask, "add a subtask")
    s.add_argument("parent")
    s.add_argument("title")
    s.add_argument("--est")
    s.add_argument("--notes")

    s = add("move", cmd_move, "move task to another project")
    s.add_argument("id")
    s.add_argument("--project", required=True)

    s = add("tag", cmd_tag_task, "add/remove tags on a task")
    s.add_argument("id")
    s.add_argument("--add", action="append")
    s.add_argument("--remove", action="append")

    s = add("reorder", cmd_reorder, "reorder tasks in a project")
    s.add_argument("--project", required=True)
    s.add_argument("ids", nargs="+")

    s = add("today", cmd_today, "list today's tasks")
    s.add_argument("--json", action="store_true")
    s = add("today-add", cmd_today_add, "plan tasks for today")
    s.add_argument("ids", nargs="+")
    s = add("today-rm", cmd_today_rm, "remove tasks from today")
    s.add_argument("ids", nargs="+")

    s = add("plan", cmd_plan, "plan tasks for a day")
    s.add_argument("ids", nargs="+")
    s.add_argument("--day", required=True, help="YYYY-MM-DD | today | tomorrow | +N")
    s = add("plan-show", cmd_plan_show, "show planner")
    s.add_argument("--day")
    s.add_argument("--json", action="store_true")

    s = add("schedule", cmd_schedule, "schedule a task at a time")
    s.add_argument("id")
    s.add_argument("--at", required=True, help="'YYYY-MM-DD HH:MM'")
    s.add_argument("--remind", help="offset, e.g. 10m")
    s = add("unschedule", cmd_unschedule, "clear all scheduling")
    s.add_argument("id")

    s = add("deadline", cmd_deadline, "set a deadline")
    s.add_argument("id")
    s.add_argument("--day", help="YYYY-MM-DD")
    s.add_argument("--at", help="'YYYY-MM-DD HH:MM'")
    s.add_argument("--remind", help="offset before --at, e.g. 1h")

    s = add("agenda", cmd_agenda, "overdue / today / scheduled / deadlines")
    s.add_argument("--json", action="store_true")

    s = add("track", cmd_track, "add tracked time")
    s.add_argument("id")
    s.add_argument("duration", help="e.g. 30m, 1.5h")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")

    s = add("worklog", cmd_worklog, "time aggregation")
    s.add_argument("--from", dest="from")
    s.add_argument("--to")
    s.add_argument("--json", action="store_true")

    s = add("repeat", cmd_repeat, "attach a repeat config to a task")
    s.add_argument("id")
    s.add_argument("--every", required=True, choices=sorted(_CYCLES))
    s.add_argument("--interval", type=int, default=1)
    s.add_argument("--days", help="mon,tue,...")
    s.add_argument("--start-time", help="HH:MM")
    s.add_argument(
        "--remind", choices=["AtStart", "m5", "m10", "m15", "m30", "h1"]
    )
    s.add_argument("--start-date")

    s = add("repeats", cmd_repeats, "list repeat configs")
    s.add_argument("--json", action="store_true")

    s = add("projects", cmd_projects, "list projects")
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    s = add("project-add", cmd_project_add, "create a project")
    s.add_argument("title")
    s.add_argument("--color")
    s = add("project-edit", cmd_project_edit, "edit a project")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--color")
    s.add_argument("--hide", action="store_true")
    s = add("project-archive", cmd_project_archive, "archive a project")
    s.add_argument("id")

    s = add("tags", cmd_tags, "list tags")
    s.add_argument("--json", action="store_true")
    s = add("tag-new", cmd_tag_new, "create a tag")
    s.add_argument("title")
    s.add_argument("--color")
    s = add("tag-edit", cmd_tag_edit, "edit a tag")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--color")

    s = add("notes", cmd_notes, "list notes")
    s.add_argument("--project")
    s.add_argument("--today", action="store_true", help="only notes pinned to today")
    s.add_argument("--json", action="store_true")
    s = add("note-add", cmd_note_add, "create a note")
    s.add_argument("content")
    s.add_argument("--project")
    s.add_argument("--pin", action="store_true", help="pin to today")
    s = add("note-show", cmd_note_show, "show one note")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = add("note-edit", cmd_note_edit, "edit a note")
    s.add_argument("id")
    s.add_argument("--content")
    s.add_argument("--append")
    s.add_argument("--pin", action="store_true")
    s.add_argument("--unpin", action="store_true")
    s.add_argument("--color", help="background color, e.g. '#a05db1'")
    s = add("note-rm", cmd_note_rm, "delete a note")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")
    s = add("note-move", cmd_note_move, "move a note to another project")
    s.add_argument("id")
    s.add_argument("--project", required=True)

    s = add("boards", cmd_boards, "list boards and their panels")
    s.add_argument("--json", action="store_true")
    s = add("board-add", cmd_board_add, "create a board")
    s.add_argument("title")
    s.add_argument("--cols", type=int, default=2)
    s = add("board-edit", cmd_board_edit, "edit a board")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--cols", type=int)
    s = add("board-rm", cmd_board_rm, "delete a board")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")
    s = add("board-sort", cmd_board_sort, "reorder boards (listed ones first)")
    s.add_argument("ids", nargs="+")

    s = add("board-panel-add", cmd_board_panel_add, "add a panel to a board")
    s.add_argument("board")
    s.add_argument("title")
    _panel_args(s)
    s = add("board-panel-edit", cmd_board_panel_edit, "edit a board panel")
    s.add_argument("id")
    s.add_argument("--title")
    _panel_args(s)
    s = add("board-panel-rm", cmd_board_panel_rm, "remove a board panel")
    s.add_argument("id")
    s = add(
        "board-panel-order",
        cmd_board_panel_order,
        "set the manual task ORDER of a panel (membership stays "
        "derived from the panel's filters — listing a task here does "
        "not add it to the panel)",
    )
    s.add_argument("id")
    s.add_argument("ids", nargs="+", help="task ids in the desired order")

    s = add("counters", cmd_counters, "list simple counters / habits")
    s.add_argument("--json", action="store_true")
    s = add("counter-add", cmd_counter_add, "create a simple counter")
    s.add_argument("title")
    s.add_argument(
        "--type", choices=["click", "stopwatch", "countdown"], default="click"
    )
    s.add_argument("--icon", help="material icon name")
    s.add_argument("--countdown", help="countdown duration, e.g. 30m")
    s.add_argument("--no-streak", action="store_true", help="do not track streaks")
    s.add_argument("--streak-min", help="streak minimum (number, or duration)")
    s.add_argument("--streak-days", help="mon,tue,... (default mon-fri)")
    s = add("counter-edit", cmd_counter_edit, "edit a simple counter")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--icon")
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.add_argument("--streak", action="store_true")
    s.add_argument("--no-streak", action="store_true")
    s.add_argument("--streak-min")
    s.add_argument("--streak-days", help="mon,tue,...")
    s.add_argument("--countdown", help="countdown duration, e.g. 30m")
    s = add("counter-rm", cmd_counter_rm, "delete a simple counter")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")
    s = add("counter-set", cmd_counter_set, "set a counter's value for a day")
    s.add_argument("id")
    s.add_argument("value", help="clicks, or a duration for stopwatch counters")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s = add("counter-inc", cmd_counter_inc, "increment a counter")
    s.add_argument("id")
    s.add_argument("--by", help="amount (default 1; duration for stopwatch)")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s = add("counter-log", cmd_counter_log, "add time to a stopwatch counter")
    s.add_argument("id")
    s.add_argument("duration", help="e.g. 30m, 1.5h")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s = add("counter-order", cmd_counter_order, "reorder counters (listed first)")
    s.add_argument("ids", nargs="+")

    s = add("archive", cmd_archive, "archive done tasks")
    s.add_argument("--yes", action="store_true")

    s = add("pull", cmd_pull, "download and summarize the sync file")
    s.add_argument("--raw", action="store_true")

    add("doctor", cmd_doctor, "validate state invariants")
    add("backup", cmd_backup, "download and store a backup")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = _rewrite_argv(list(sys.argv[1:] if argv is None else argv))
    board_help = _board_group_help(argv)
    if board_help:
        print(board_help, file=sys.stderr)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    try:
        return args.fn(args)
    except (
        CliError,
        ConfigError,
        WebDavError,
        ConflictError,
        mut.MutationError,
        q.QueryError,
        render.RenderError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
