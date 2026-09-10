"""argparse CLI for sp-cli."""

from __future__ import annotations

import argparse
import datetime
import re
import sys

from sp_cli import mutations as mut
from sp_cli import queries as q
from sp_cli import render
from sp_cli import shortsyntax
from sp_cli import timer
from sp_cli.config import ConfigError, init_config, load_config
from sp_cli.ids import nanoid
from sp_cli.model import (
    ATTACHMENT_ICONS,
    BACKLOG_STATE,
    INBOX_PROJECT_ID,
    ISSUE_PROVIDER_DEFAULT_CFG,
    ISSUE_PROVIDER_URL_FIELD,
    MONTHLY_ANCHOR_FIELDS,
    PANEL_SORT_BY,
    SCHEDULED_STATE,
    SIMPLE_COUNTER_TYPES,
    STREAK_DAY_KEYS,
    TASK_DONE_STATE,
    TODAY_TAG_ID,
    WEEKDAY_KEYS,
    logical_today_str,
    make_attachment,
    make_board,
    make_issue_provider,
    make_note,
    make_panel,
    make_reflection,
    make_project,
    make_simple_counter,
    make_tag,
    make_task,
    now_ms,
    repeat_cadence_fields,
    start_of_next_day_diff_ms,
    streak_week_days,
    today_str,
)
from sp_cli.store import SyncStore
from sp_cli.timer import TimerError
from sp_cli.webdav import ConflictError, SyncFileClient, WebDavError


class CliError(Exception):
    pass


# ---------------------------------------------------------------- helpers

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# CLI exposes only the three attachment types a terminal can meaningfully
# create; SP's union also has COMMAND and NOTE (dialog-only).
_ATTACH_TYPES = {"link": "LINK", "img": "IMG", "file": "FILE"}


def _ctx() -> tuple[SyncFileClient, SyncStore]:
    cfg = load_config()
    client = SyncFileClient(
        cfg.url, cfg.folder, cfg.user, cfg.password, cfg.backup_dir
    )
    return client, SyncStore(client, cfg.client_id)


def _parse_day(text: str, d: dict | None = None) -> str:
    """Resolve a day expression. With `d`, 'today' is SP's LOGICAL today
    (misc.startOfNextDay*) instead of the wall-clock date."""
    text = text.strip()
    today = (
        datetime.date.fromisoformat(logical_today_str(d))
        if d is not None
        else datetime.date.today()
    )
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
                ("--backlog", args.backlog),
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

    # Short syntax runs only for top-level tasks: a subtask carries no project,
    # scheduling or tags of its own, so there would be nothing to parse into.
    parsed = None
    if not args.no_parse and not args.parent:
        parsed = shortsyntax.parse(
            args.title, d, force_deadline=bool(args.parse_deadline)
        )
        if parsed.touched:
            print("parsed: " + " ".join(parsed.summary), file=sys.stderr)

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
        # Explicit flags always win over what short syntax found.
        title = parsed.clean_title if parsed else args.title
        if args.project:
            project_id = q.resolve_project(d, args.project)
        elif parsed and parsed.project_id:
            project_id = parsed.project_id
        else:
            project_id = INBOX_PROJECT_ID
        if args.tag or not parsed:
            tag_ids, tag_muts = _resolve_tag_refs(d, args.tag or [], args.create_tags)
        else:
            tag_ids = list(parsed.tag_ids)
            tag_muts = []
            for tag_title in parsed.new_tag_titles:
                new_id = nanoid()
                tag_ids.append(new_id)

                def _create(dd, b, _id=new_id, _title=tag_title):
                    mut.tag_add(dd, b, make_tag(_id, _title))

                tag_muts.append(_create)
        muts.extend(tag_muts)
        if not args.est and parsed and parsed.time_estimate_ms:
            est = parsed.time_estimate_ms

        due_day = _parse_day(args.due) if args.due else None
        at_ts = _parse_dt(args.at) if args.at else None
        if due_day and at_ts:
            raise CliError("--due and --at are mutually exclusive")
        if args.backlog and (due_day or at_ts):
            raise CliError(
                "--backlog is incompatible with --due / --at: a backlog task "
                "is explicitly not scheduled"
            )
        # A backlog task is explicitly unscheduled — a parsed `@due` is dropped
        # rather than fought over (only the explicit flags are an error).
        if parsed and not args.backlog and not due_day and at_ts is None:
            due_day = parsed.due_day
            at_ts = parsed.due_with_time
        spent_ms = parsed.time_spent_ms if parsed else None
        deadline_day = parsed.deadline_day if parsed else None
        deadline_ts = parsed.deadline_with_time if parsed else None
        repeat = parsed.repeat if parsed else None

        def _add(dd, b):
            task = make_task(
                task_id,
                title,
                project_id,
                time_estimate=est,
                tag_ids=tag_ids,
                notes=args.notes,
                due_day=due_day,
            )
            mut.add_task(dd, b, task, to_backlog=bool(args.backlog))
            if at_ts is not None:
                offset = render.parse_offset(args.remind) if args.remind else 0
                mut.schedule_task(dd, b, task_id, at_ts, remind_at=at_ts - offset)
            if deadline_day is not None or deadline_ts is not None:
                mut.set_deadline(
                    dd,
                    b,
                    task_id,
                    deadline_day=deadline_day,
                    deadline_with_time=deadline_ts,
                )
            if spent_ms:
                mut.track_time(dd, b, task_id, logical_today_str(dd), spent_ms)
            if repeat:
                mut.repeat_add(dd, b, task_id, nanoid(), **repeat)

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

    def _reorder(dd, b):
        # Recomputed inside the closure: on a 412 retry the project's taskIds
        # may differ, and a stale permutation would abort the commit.
        current = list(dd["state"]["project"]["entities"][pid]["taskIds"])
        for tid in listed:
            if tid not in current:
                raise CliError(f"task {tid} is not a top-level task of project {pid}")
        ordered = listed + [t for t in current if t not in listed]
        mut.reorder_project(dd, b, pid, ordered)

    store.commit([_reorder], initial=d)
    print(f"reordered {pid}")
    return 0


def _direction(args) -> str | None:
    """The single --up/--down/--top/--bottom flag, or None."""
    chosen = [name for name in mut.DIRECTIONS if getattr(args, name, False)]
    if len(chosen) > 1:
        raise CliError(
            "pick exactly one direction (--up / --down / --top / --bottom)"
        )
    return chosen[0] if chosen else None


def cmd_today_move(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    direction = _direction(args)
    if args.before and direction:
        raise CliError("today move: --before cannot be combined with a direction")
    if not args.before and not direction:
        raise CliError(
            "today move: pass --before ID or --up / --down / --top / --bottom"
        )
    if args.before:
        anchor = q.resolve_task(d, args.before)
        store.commit(
            [lambda dd, b: mut.today_move_before(dd, b, tid, anchor)], initial=d
        )
        print(f"{render.short_id(tid)} moved before {render.short_id(anchor)} in today")
        return 0
    store.commit([lambda dd, b: mut.today_move(dd, b, tid, direction)], initial=d)
    print(f"{render.short_id(tid)} moved {direction} in today")
    return 0


def cmd_move_in_project(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    direction = _direction(args)
    if args.after and direction:
        raise CliError("move-in-project: --after cannot be combined with a direction")
    if not args.after and not direction:
        raise CliError(
            "move-in-project: pass --after ID or --up / --down / --top / --bottom"
        )
    if args.after:
        anchor = q.resolve_task(d, args.after)
        store.commit(
            [lambda dd, b: mut.project_move_after(dd, b, tid, anchor)], initial=d
        )
        print(f"{render.short_id(tid)} moved after {render.short_id(anchor)}")
        return 0
    store.commit([lambda dd, b: mut.project_move(dd, b, tid, direction)], initial=d)
    print(f"{render.short_id(tid)} moved {direction} in its project")
    return 0


def cmd_subtask_move(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    direction = _direction(args)
    if not direction:
        raise CliError("subtask move: pass --up / --down / --top / --bottom")
    store.commit([lambda dd, b: mut.subtask_move(dd, b, tid, direction)], initial=d)
    print(f"{render.short_id(tid)} moved {direction} among its siblings")
    return 0


def cmd_subtask_reparent(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    parent_id = q.resolve_task(d, args.parent)
    after = q.resolve_task(d, args.after) if args.after else None
    store.commit(
        [lambda dd, b: mut.subtask_reparent(dd, b, tid, parent_id, after)], initial=d
    )
    print(f"{render.short_id(tid)} is now a subtask of {render.short_id(parent_id)}")
    return 0


def cmd_demote(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    parent_id = q.resolve_task(d, args.parent)
    after = q.resolve_task(d, args.after) if args.after else None
    store.commit(
        [lambda dd, b: mut.demote(dd, b, tid, parent_id, after)], initial=d
    )
    print(f"{render.short_id(tid)} is now a subtask of {render.short_id(parent_id)}")
    return 0


def cmd_promote(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    store.commit(
        [lambda dd, b: mut.promote(dd, b, tid, plan_for_today=args.today)], initial=d
    )
    print(f"{render.short_id(tid)} is now a main task")
    return 0


def cmd_plan_move(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    anchor = q.resolve_task(d, args.before)
    store.commit(
        [lambda dd, b: mut.planner_move_before(dd, b, tid, anchor)], initial=d
    )
    print(f"{render.short_id(tid)} moved before {render.short_id(anchor)}")
    return 0


def cmd_backlog(args) -> int:
    client, _ = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.project)
    tasks = q.backlog_list(d, pid)
    if args.json:
        render.print_json(tasks)
    else:
        render.print_tasks(d, tasks)
    return 0


def cmd_backlog_add(args) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    muts = [(lambda dd, b, _t=tid: mut.backlog_add(dd, b, _t)) for tid in tids]
    store.commit(muts, initial=d)
    print(f"moved to backlog: {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_backlog_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    tids = _resolve_tasks(d, args.ids)
    muts = [(lambda dd, b, _t=tid: mut.backlog_remove(dd, b, _t)) for tid in tids]
    store.commit(muts, initial=d)
    print(f"moved out of backlog: {', '.join(render.short_id(t) for t in tids)}")
    return 0


def cmd_backlog_clear(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.project)
    if not d["state"]["project"]["entities"][pid].get("backlogTaskIds"):
        raise CliError(f"project {pid}: the backlog is already empty")
    moved: list[str] = []

    def _clear(dd, b):
        moved[:] = mut.backlog_clear(dd, b, pid)

    store.commit([_clear], initial=d)
    print(f"backlog cleared: {len(moved)} task(s) back in {pid}")
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
    if args.clear or args.clear_reminder:
        if args.clear and args.clear_reminder:
            raise CliError("deadline: --clear and --clear-reminder are exclusive")
        if args.day or args.at or args.remind:
            raise CliError("deadline: --clear* takes no --day / --at / --remind")
        if args.clear:
            store.commit([lambda dd, b: mut.remove_deadline(dd, b, tid)], initial=d)
            print(f"deadline cleared on {render.short_id(tid)}")
        else:
            store.commit(
                [lambda dd, b: mut.clear_deadline_reminder(dd, b, tid)], initial=d
            )
            print(f"deadline reminder cleared on {render.short_id(tid)}")
        return 0
    if bool(args.day) == bool(args.at):
        raise CliError("deadline: give exactly one of --day / --at")
    day = _parse_day(args.day) if args.day else None
    ts = _parse_dt(args.at) if args.at else None
    remind = None
    drop_reminder = False
    if args.remind:
        # `--remind none` = set the deadline and explicitly drop its reminder.
        # With --day it is redundant (a day deadline never carries one in SP)
        # but accepted; any other offset with --day is an error.
        if args.remind.lower() == "none":
            drop_reminder = True
        elif ts is None:
            raise CliError(
                "deadline: --remind requires --at — a day deadline (--day) "
                "carries no reminder in SP (use --at, or --remind none)"
            )
        else:
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
                drop_reminder=drop_reminder,
            )
        ],
        initial=d,
    )
    print(f"deadline set on {render.short_id(tid)}")
    return 0


def cmd_dismiss(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    store.commit([lambda dd, b: mut.dismiss_reminder(dd, b, tid)], initial=d)
    print(f"reminder dismissed on {render.short_id(tid)}")
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
    # The default day is SP's LOGICAL today (misc.startOfNextDay*), so time
    # tracked just after midnight still lands on the day the user is working.
    date = _parse_day(args.date) if args.date else logical_today_str(d)
    store.commit([lambda dd, b: mut.track_time(dd, b, tid, date, ms)], initial=d)
    print(f"tracked {render.format_duration(ms)} on {render.short_id(tid)} ({date})")
    return 0


def cmd_untrack(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    ms = render.parse_duration(args.duration)
    date = _parse_day(args.date) if args.date else logical_today_str(d)
    removed: list[int] = []

    def _untrack(dd, b):
        removed.append(mut.untrack_time(dd, b, tid, date, ms))

    store.commit([_untrack], initial=d)
    # SP's reducer clamps at zero, so report what actually came off.
    actual = removed[0] if removed else 0
    print(
        f"untracked {render.format_duration(actual)} on "
        f"{render.short_id(tid)} ({date})"
    )
    return 0


# ---------------------------------------------------------------- live timer

def _print_timer(running: dict) -> None:
    elapsed = max(now_ms() - int(running["started_at"]), 0)
    print(
        f"{render.short_id(running['task_id'])} {running.get('title', '')}  "
        f"{render.format_duration(elapsed)} "
        f"(since {render.format_ts(running['started_at'])})"
    )


def _stop_timer(store, running: dict, d: dict) -> int | None:
    """Write the accrued time (one KT op per day, single batch), clear the
    timer file, print the summary. Returns the tracked ms — or None when the
    task has vanished and the segment was dropped.

    A negative elapsed (the clock moved backwards, or the file was hand-edited
    with a future `started_at`) is refused and the timer file is KEPT, so
    nothing is silently lost.
    """
    tid = running["task_id"]
    started = int(running["started_at"])
    elapsed = now_ms() - started
    if elapsed < 0:
        raise CliError(
            f"timer on {render.short_id(tid)} started in the future "
            f"({render.format_ts(started)}); refusing to track negative time. "
            "Fix the clock, or run `sp stop --discard`."
        )
    if tid not in d["state"]["task"]["entities"]:
        # Deleted on another device while the timer ran: there is no task for
        # a KT to land on, so drop the segment rather than emit a dangling op.
        print(
            f"warning: task {render.short_id(tid)} no longer exists "
            "(deleted elsewhere?)",
            file=sys.stderr,
        )
        timer.clear_timer()
        print(
            f"discarded {render.format_duration(max(elapsed, 0))} on "
            f"{render.short_id(tid)} {running.get('title', '')}: "
            "the task is gone"
        )
        return None
    if elapsed < timer.MIN_TRACK_MS:
        print(
            f"warning: elapsed {max(elapsed, 0) // 1000}s is under 1m; tracking 1m",
            file=sys.stderr,
        )
        elapsed = timer.MIN_TRACK_MS
    segments = timer.split_by_day(
        started, started + elapsed, start_of_next_day_diff_ms(d)
    )
    store.commit(
        [
            (lambda dd, b, day=day, ms=ms: mut.track_time(dd, b, tid, day, ms))
            for day, ms in segments
        ],
        initial=d,
    )
    timer.clear_timer()
    total = sum(ms for _, ms in segments)
    print(
        f"stopped {render.short_id(tid)} {running.get('title', '')}: "
        f"{render.format_duration(total)}"
    )
    if len(segments) > 1:
        for day, ms in segments:
            print(f"  {day}  {render.format_duration(ms)}")
    return total


def cmd_start(args) -> int:
    running = timer.read_timer()
    if not args.id:
        # Bare `sp start` is an alias of `sp current`.
        if not running:
            print("no timer")
            return 1
        _print_timer(running)
        return 0
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    task = d["state"]["task"]["entities"][tid]
    if task.get("isDone"):
        raise CliError(f"task {render.short_id(tid)} is done; reopen it first")
    if running:
        if running["task_id"] == tid:
            _print_timer(running)
            return 0
        # A vanished previous task only warns — the new timer still starts.
        _stop_timer(store, running, d)
    timer.write_timer(tid, task.get("title", ""), now_ms())
    print(f"started {render.short_id(tid)} {task.get('title', '')}")
    return 0


def cmd_stop(args) -> int:
    if args.discard:
        # Blind clear: --discard is the escape hatch for a corrupt timer
        # file, so it must never parse one.
        if timer.clear_timer():
            print("discarded the running timer")
        else:
            print("no timer running")
        return 0
    running = timer.read_timer()
    if not running:
        raise CliError("no timer running")
    client, store = _ctx()
    d = client.get()
    _stop_timer(store, running, d)
    return 0


def cmd_current(args) -> int:
    running = timer.read_timer()
    if not running:
        if args.json:
            render.print_json(None)
        else:
            print("no timer")
        return 1
    if args.json:
        render.print_json(
            {**running, "elapsed": max(now_ms() - int(running["started_at"]), 0)}
        )
        return 0
    _print_timer(running)
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
    _check_interval(args.interval)
    days = _parse_days(args.days) if args.days else None
    if days is not None and cycle != "WEEKLY":
        raise CliError("--days only applies to a weekly cadence (use --every week)")
    start_time = _parse_clock(args.start_time)
    if args.remind and not start_time:
        raise CliError("--remind needs a start time (pass --start-time HH:MM)")
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
                start_time=start_time,
                remind_at=args.remind,
            )
        ],
        initial=d,
    )
    print(cfg_id)
    return 0


def _parse_days(text: str) -> list[str]:
    try:
        return [_DAY_KEYS[p.strip().lower()] for p in text.split(",")]
    except KeyError as e:
        raise CliError(f"invalid weekday {e} (use mon,tue,...)") from None


def _check_interval(interval: int | None) -> None:
    if interval is not None and interval < 1:
        raise CliError("--interval must be >= 1")


def _parse_clock(text: str | None) -> str | None:
    """Normalize a HH:MM clock string ('8:00' -> '08:00'); None passes through.
    SP stores startTime as a zero-padded clock string and compares it as text."""
    if text is None:
        return None
    try:
        return datetime.datetime.strptime(text.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise CliError(f"invalid time '{text}' (use HH:MM, 24h)") from None


def _cadence_changes(cfg: dict, args) -> dict:
    """Recompute quickSetting/repeatCycle/repeatEvery/weekdays for an edit.

    Unspecified parts fall back to the config's current cadence, and that
    includes the weekday booleans: SP-authored cfgs express their weekdays
    through the MONDAY_TO_FRIDAY / WEEKLY_CURRENT_WEEKDAY presets just as much
    as through CUSTOM, so `--interval 2` on a Wednesday-only cfg must stay
    Wednesday-only whatever its quickSetting says. Only an actual cycle switch
    (no --days given) falls back to SP's mon-fri default.
    """
    cycle = _CYCLES[args.every] if args.every else cfg.get("repeatCycle", "DAILY")
    _check_interval(args.interval)
    every = args.interval if args.interval is not None else int(cfg.get("repeatEvery", 1))
    if args.days:
        days = _parse_days(args.days)
        if cycle != "WEEKLY":
            raise CliError("--days only applies to a weekly cadence (use --every week)")
    elif cycle == cfg.get("repeatCycle"):
        days = [k for k in WEEKDAY_KEYS if cfg.get(k)]
    else:
        days = None
    try:
        fields = repeat_cadence_fields(cycle, every, days)
    except ValueError as e:
        raise CliError(str(e)) from None
    if cycle != "WEEKLY":
        # Weekday booleans are inert outside a weekly cycle — don't clobber the
        # ones the cfg carries for a cadence the user may switch back to.
        for k in WEEKDAY_KEYS:
            fields.pop(k, None)
    return fields


def cmd_repeat_edit(args) -> int:
    if args.pause and args.resume:
        raise CliError("--pause and --resume are mutually exclusive")
    client, store = _ctx()
    d = client.get()
    cfg_id = q.resolve_repeat_cfg(d, args.id)
    cfg = d["state"]["taskRepeatCfg"]["entities"][cfg_id]

    changes: dict = {}
    anchor_reset: list[str] = []
    if args.every or args.days or args.interval is not None:
        changes.update(_cadence_changes(cfg, args))
        if changes["repeatCycle"] != cfg.get("repeatCycle"):
            # SP's MONTHLY_ANCHOR_RESET: the monthly anchors mean something only
            # for the cadence they were picked for, and their presence is the
            # discriminator — a cycle switch has to unset them, not zero them.
            anchor_reset = [f for f in MONTHLY_ANCHOR_FIELDS if f in cfg]
    if args.title is not None:
        if not args.title.strip():
            raise CliError("empty title")
        changes["title"] = args.title
    start_time = _parse_clock(args.start_time)
    if start_time:
        changes["startTime"] = start_time
    if args.remind:
        changes["remindAt"] = args.remind
    if args.est:
        changes["defaultEstimate"] = render.parse_duration(args.est)
    if args.notes is not None:
        changes["notes"] = args.notes
    if args.start_date:
        changes["startDate"] = _parse_day(args.start_date)
    if args.pause:
        changes["isPaused"] = True
    if args.resume:
        changes["isPaused"] = False

    cleared: list[str] = []
    if args.clear:
        for name in args.clear.split(","):
            name = name.strip()
            if not name:
                continue
            if name not in mut.CLEARABLE_REPEAT_CFG_FIELDS:
                raise CliError(
                    f"cannot clear '{name}' (allowed: "
                    f"{','.join(mut.CLEARABLE_REPEAT_CFG_FIELDS)})"
                )
            if name in changes:
                raise CliError(f"'{name}' is both set and cleared")
            cleared.append(name)

    # A reminder is anchored to the start time: SP's dialog drops remindAt
    # whenever the cfg has no startTime, so clearing one clears the other.
    if "startTime" in cleared and "remindAt" not in cleared:
        if "remindAt" in changes:
            raise CliError("'remindAt' is both set and cleared (clearing startTime)")
        cleared.append("remindAt")
    if args.remind and "startTime" not in changes and not cfg.get("startTime"):
        raise CliError("--remind needs a start time (pass --start-time HH:MM)")

    for field in anchor_reset:
        if field not in cleared and field not in changes:
            cleared.append(field)

    if not changes and not cleared:
        raise CliError("nothing to change")

    store.commit(
        [lambda dd, b: mut.repeat_update(dd, b, cfg_id, changes, cleared)],
        initial=d,
    )
    print(cfg_id)
    return 0


def cmd_repeat_skip(args) -> int:
    client, store = _ctx()
    d = client.get()
    cfg_id = q.resolve_repeat_cfg(d, args.id)
    # 'today' is SP's logical today — a skip typed after midnight must land on
    # the day the user is still working, like `sp track` does.
    day = _parse_day(args.date, d)
    skipped: list[bool] = []
    store.commit(
        [lambda dd, b: skipped.append(mut.repeat_skip_instance(dd, b, cfg_id, day))],
        initial=d,
    )
    # commit may retry the closure on a conflict — the last run is the one that
    # actually landed.
    if skipped and not skipped[-1]:
        print(f"{day} is already skipped")
        return 0
    print(f"skipped {day}")
    inst_id = f"rpt_{cfg_id}_{day}"
    print(
        f"note: an already created instance is not removed — "
        f"run 'sp delete {inst_id}' if it exists"
    )
    return 0


def cmd_repeats(args) -> int:
    client, _ = _ctx()
    d = client.get()
    reg = d["state"].get("taskRepeatCfg", {"ids": [], "entities": {}})
    cfgs = [reg["entities"][cid] for cid in reg["ids"] if cid in reg["entities"]]
    if args.json:
        render.print_json(cfgs)
        return 0
    rows = []
    for c in cfgs:
        skipped = list(c.get("deletedInstanceDates") or [])
        skip_col = ""
        if skipped:
            skip_col = f"{len(skipped)} ({', '.join(sorted(skipped)[-2:])})"
        rows.append(
            [
                render.short_id(c["id"]),
                render.truncate(c.get("title", ""), 40),
                c.get("repeatCycle", "?"),
                str(c.get("repeatEvery", "?")),
                c.get("startDate", "-"),
                c.get("startTime") or "-",
                c.get("remindAt") or "-",
                skip_col,
                "paused" if c.get("isPaused") else "",
            ]
        )
    render.print_table(
        ["id", "title", "cycle", "every", "start", "time", "remind", "skipped", ""],
        rows,
    )
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
    if args.enable_backlog and args.disable_backlog:
        raise CliError(
            "--enable-backlog and --disable-backlog are mutually exclusive"
        )
    backlog = True if args.enable_backlog else (False if args.disable_backlog else None)

    def _edit(dd, b):
        project = dd["state"]["project"]["entities"][pid]
        changes: dict = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.color is not None:
            changes["theme"] = {**project["theme"], "primary": args.color}
        if args.hide:
            changes["isHiddenFromMenu"] = True
        if changes:
            mut.project_update(dd, b, pid, changes)
        if backlog is not None:
            mut.project_set_backlog(dd, b, pid, backlog)
        elif not changes:
            raise CliError("project edit: nothing to change")

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


def cmd_project_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_project(d, args.id)
    if pid == INBOX_PROJECT_ID:
        raise CliError("the Inbox project cannot be deleted")
    project = d["state"]["project"]["entities"][pid]
    tasks = [t for t in q.all_tasks(d) if t.get("projectId") == pid]
    notes = [
        n
        for n in q.all_notes(d)
        if n.get("projectId") == pid or n["id"] in project.get("noteIds", [])
    ]
    archived = [
        tid
        for tid in q.archived_task_ids(d)
        if (q.archived_task(d, tid) or {}).get("projectId") == pid
    ]
    what = (
        f"DELETE project '{project.get('title', pid)}' with "
        f"{len(tasks)} task(s), {len(archived)} archived task(s) and "
        f"{len(notes)} note(s)? Tasks are deleted, NOT archived"
    )
    if not _confirm(what, args.yes):
        print("aborted", file=sys.stderr)
        return 1
    result: list[tuple[list, list]] = []

    def _rm(dd, b):
        result.append(mut.project_delete(dd, b, pid))

    store.commit([_rm], initial=d)
    # commit() may retry the mutation on a conflict — the LAST run is the
    # one that landed.
    deleted_tasks, deleted_notes = result[-1]
    print(
        f"deleted {pid} ({len(deleted_tasks)} task(s), "
        f"{len(deleted_notes)} note(s))"
    )
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


def cmd_tag_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    tag_id = q.resolve_tag(d, args.id)
    if tag_id in mut.SYSTEM_TAG_IDS:
        raise CliError(f"'{tag_id}' is a built-in tag and cannot be deleted")
    tag = d["state"]["tag"]["entities"][tag_id]
    tagged = [t for t in q.all_tasks(d) if tag_id in (t.get("tagIds") or [])]
    what = (
        f"delete tag '{tag.get('title', tag_id)}' and remove it from "
        f"{len(tagged)} task(s)?"
    )
    if not _confirm(what, args.yes):
        print("aborted", file=sys.stderr)
        return 1
    orphaned: list[list[str]] = []

    def _rm(dd, b):
        orphaned.append(mut.tag_delete(dd, b, tag_id))

    store.commit([_rm], initial=d)
    # last run == the one that landed (commit() retries on conflict)
    extra = f", deleted {len(orphaned[-1])} orphaned task(s)" if orphaned[-1] else ""
    print(f"deleted {tag_id}{extra}")
    return 0


def cmd_repeat_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    cfg_id = q.resolve_repeat_cfg(d, args.id)
    cfg = d["state"]["taskRepeatCfg"]["entities"][cfg_id]
    linked = [
        t for t in q.all_tasks(d) if t.get("repeatCfgId") == cfg_id
    ]
    what = (
        f"delete repeat config '{cfg.get('title', cfg_id)}' and unlink "
        f"{len(linked)} task(s)? (existing tasks are kept)"
    )
    if not _confirm(what, args.yes):
        print("aborted", file=sys.stderr)
        return 1
    store.commit([lambda dd, b: mut.repeat_delete(dd, b, cfg_id)], initial=d)
    print(f"deleted {cfg_id}")
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


def cmd_attach(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    a_type = _ATTACH_TYPES[args.type] if args.type else None
    attachment_id = nanoid()
    try:
        attachment = make_attachment(
            attachment_id, args.path, attachment_type=a_type, title=args.title
        )
    except ValueError as e:
        raise CliError(str(e)) from None

    store.commit(
        [lambda dd, b: mut.attachment_add(dd, b, tid, attachment)], initial=d
    )
    print(attachment_id)
    return 0


def cmd_attachments(args) -> int:
    client, _ = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    attachments = q.task_attachments(d, tid)
    if args.json:
        render.print_json(attachments)
    else:
        render.print_attachments(attachments)
    return 0


def cmd_attach_edit(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    aid = q.resolve_attachment(d, tid, args.attachment)
    changes: dict = {}
    if args.title is not None:
        changes["title"] = args.title
    if args.path is not None:
        changes["path"] = args.path
    if args.type is not None:
        changes["type"] = _ATTACH_TYPES[args.type]
        changes["icon"] = ATTACHMENT_ICONS[changes["type"]]
    if not changes:
        raise CliError("attach edit: nothing to change")

    store.commit(
        [lambda dd, b: mut.attachment_update(dd, b, tid, aid, changes)], initial=d
    )
    print(f"updated {render.short_id(aid)}")
    return 0


def cmd_attach_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_task(d, args.id)
    aid = q.resolve_attachment(d, tid, args.attachment)
    store.commit(
        [lambda dd, b: mut.attachment_delete(dd, b, tid, aid)], initial=d
    )
    print(f"deleted {render.short_id(aid)}")
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
    try:
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
    except ValueError as e:
        raise CliError(f"counter add: {e}") from None

    store.commit([lambda dd, b: mut.counter_add(dd, b, counter)], initial=d)
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
    if counter.get("type") == "StopWatch":
        raise CliError(
            f"counter inc: '{counter.get('title')}' is a stopwatch counter "
            "(it counts time, not clicks); use 'sp counter log ID 30m' "
            "or 'sp counter set ID 30m'"
        )
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
    final: list[str] = []

    def _order(dd, b):
        # Complete the permutation from the *fresh* state so a 412 retry
        # against a changed registry still produces a valid id list.
        current = [c["id"] for c in q.all_counters(dd)]
        ordered = listed + [cid for cid in current if cid not in listed]
        final[:] = ordered
        mut.counter_order(dd, b, ordered)

    store.commit([_order], initial=d)
    print(f"reordered counters: {', '.join(final)}")
    return 0


# ------------------------------------------------------- issue providers

_PLAINTEXT_WARNING = (
    "CalDAV credentials are stored in PLAIN TEXT inside the sync file "
    "(and in every backup of it). Pass --store-plaintext-credentials to "
    "confirm you accept that."
)


def _require_writable_provider(key: str | None, what: str) -> None:
    """The CLI only owns the two providers it can build a complete cfg for.

    Jira/GitHub/GitLab/plugin providers carry cfg shapes (and secrets) the
    CLI does not model; rewriting or deleting one from here would corrupt it.
    """
    if key not in ISSUE_PROVIDER_DEFAULT_CFG:
        raise CliError(
            f"provider {what}: {key or 'this provider'} is managed by the app "
            "and its plugins; only ICAL and CALDAV providers can be edited or "
            "removed from the CLI"
        )


def _provider_common(args, d: dict) -> tuple[dict, list]:
    """Shared add-flags → cfg fields; returns (fields, extra tag mutations)."""
    fields: dict = {}
    extra: list = []
    if getattr(args, "project", None):
        fields["defaultProjectId"] = q.resolve_project(d, args.project)
    if getattr(args, "tag", None):
        tag_ids, extra = _resolve_tag_refs(d, args.tag, args.create_tags)
        fields["defaultTagIds"] = tag_ids
    return fields, extra


def cmd_providers(args) -> int:
    client, _ = _ctx()
    d = client.get()
    providers = q.all_providers(d)
    if args.json:
        render.print_json(providers)
        return 0
    projects = d["state"]["project"]["entities"]
    names = [
        (projects.get(p.get("defaultProjectId")) or {}).get(
            "title", p.get("defaultProjectId") or ""
        )
        for p in providers
    ]
    render.print_providers(providers, [q.provider_url(p) for p in providers], names)
    return 0


def cmd_provider_add_ical(args) -> int:
    client, store = _ctx()
    d = client.get()
    fields, extra = _provider_common(args, d)
    fields["icalUrl"] = args.url
    fields["isAutoImportForCurrentDay"] = bool(args.auto_import)
    if args.check_every:
        fields["checkUpdatesEvery"] = render.parse_duration(args.check_every)
    if args.banner_before:
        fields["showBannerBeforeThreshold"] = render.parse_duration(args.banner_before)
    if args.include_regex is not None:
        fields["filterIncludeRegex"] = args.include_regex or None
    if args.exclude_regex is not None:
        fields["filterExcludeRegex"] = args.exclude_regex or None
    provider_id = nanoid()
    provider = make_issue_provider(provider_id, "ICAL", **fields)
    store.commit(
        extra + [lambda dd, b: mut.provider_add(dd, b, provider)], initial=d
    )
    print(provider_id)
    return 0


def cmd_provider_add_caldav(args) -> int:
    if not args.store_plaintext_credentials:
        raise CliError(_PLAINTEXT_WARNING)
    client, store = _ctx()
    d = client.get()
    fields, extra = _provider_common(args, d)
    fields["caldavUrl"] = args.url
    fields["resourceName"] = args.resource
    fields["username"] = args.username
    fields["password"] = args.password
    if args.category_filter is not None:
        fields["categoryFilter"] = args.category_filter or None
    provider_id = nanoid()
    provider = make_issue_provider(provider_id, "CALDAV", **fields)
    store.commit(
        extra + [lambda dd, b: mut.provider_add(dd, b, provider)], initial=d
    )
    print(provider_id)
    return 0


def cmd_provider_edit(args) -> int:
    if args.enable and args.disable:
        raise CliError("provider edit: --enable and --disable are mutually exclusive")
    if args.auto_import and args.no_auto_import:
        raise CliError(
            "provider edit: --auto-import and --no-auto-import are mutually exclusive"
        )
    if args.project and args.no_project:
        raise CliError("provider edit: --project and --no-project are mutually exclusive")
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_provider(d, args.id)
    provider = d["state"]["issueProvider"]["entities"][pid]
    key = provider.get("issueProviderKey")
    _require_writable_provider(key, "edit")

    changes: dict = {}
    if args.enable:
        changes["isEnabled"] = True
    if args.disable:
        changes["isEnabled"] = False
    if args.url is not None:
        field = ISSUE_PROVIDER_URL_FIELD.get(key)
        if not field:
            raise CliError(f"provider edit: --url is not supported for key {key}")
        if not args.url.strip():
            # An empty url silently disables polling in SP; make it explicit.
            raise CliError(
                "provider edit: --url cannot be empty "
                "(use --disable to turn the provider off)"
            )
        changes[field] = args.url
    if args.auto_import:
        changes["isAutoImportForCurrentDay"] = True
    if args.no_auto_import:
        changes["isAutoImportForCurrentDay"] = False
    if args.project:
        changes["defaultProjectId"] = q.resolve_project(d, args.project)
    if args.no_project:
        changes["defaultProjectId"] = None
    if args.check_every:
        changes["checkUpdatesEvery"] = render.parse_duration(args.check_every)
    if args.banner_before:
        changes["showBannerBeforeThreshold"] = render.parse_duration(args.banner_before)
    if args.include_regex is not None:
        changes["filterIncludeRegex"] = args.include_regex or None
    if args.exclude_regex is not None:
        changes["filterExcludeRegex"] = args.exclude_regex or None
    if args.username is not None:
        changes["username"] = args.username
    if args.password is not None:
        if not args.store_plaintext_credentials:
            raise CliError(_PLAINTEXT_WARNING)
        changes["password"] = args.password
    if args.category_filter is not None:
        changes["categoryFilter"] = args.category_filter or None

    ical_only = {
        "isAutoImportForCurrentDay",
        "checkUpdatesEvery",
        "showBannerBeforeThreshold",
        "filterIncludeRegex",
        "filterExcludeRegex",
    }
    caldav_only = {"username", "password", "categoryFilter"}
    if ical_only & set(changes) and key != "ICAL":
        raise CliError(
            "provider edit: --auto-import / --check-every / --banner-before / "
            "--include-regex / --exclude-regex only apply to ICAL providers"
        )
    if caldav_only & set(changes) and key != "CALDAV":
        raise CliError(
            "provider edit: --username / --password / --category-filter "
            "only apply to CalDAV providers"
        )
    if not changes:
        raise CliError("provider edit: nothing to change")

    store.commit([lambda dd, b: mut.provider_update(dd, b, pid, changes)], initial=d)
    print(f"updated {pid}")
    return 0


def cmd_provider_rm(args) -> int:
    client, store = _ctx()
    d = client.get()
    pid = q.resolve_provider(d, args.id)
    _require_writable_provider(
        d["state"]["issueProvider"]["entities"][pid].get("issueProviderKey"), "rm"
    )
    linked = q.tasks_of_provider(d, pid)
    what = f"delete provider {pid} and unlink {len(linked)} task(s)?"
    if not _confirm(what, args.yes):
        print("aborted", file=sys.stderr)
        return 1
    unlinked: list[list[str]] = []

    def _rm(dd, b):
        unlinked.append(mut.provider_delete(dd, b, pid))

    store.commit([_rm], initial=d)
    print(f"deleted {pid} (unlinked {len(unlinked[0])} task(s))")
    return 0


def cmd_provider_order(args) -> int:
    client, store = _ctx()
    d = client.get()
    listed = [q.resolve_provider(d, ref) for ref in args.ids]
    store.commit([lambda dd, b: mut.provider_order(dd, b, listed)], initial=d)
    print(f"reordered providers: {', '.join(listed)}")
    return 0


# ---------------------------------------------------------------- metrics

def cmd_metrics(args) -> int:
    client, _ = _ctx()
    d = client.get()
    day_from = _parse_day(getattr(args, "from")) if getattr(args, "from") else None
    day_to = _parse_day(args.to) if args.to else None
    metrics = q.list_metrics(d, day_from, day_to)
    if args.json:
        render.print_json(metrics)
        return 0
    render.print_metrics(metrics)
    return 0


def cmd_metric_set(args) -> int:
    if args.remind_tomorrow and args.no_remind_tomorrow:
        raise CliError(
            "metric set: --remind-tomorrow and --no-remind-tomorrow are "
            "mutually exclusive"
        )
    base: dict = {}
    if args.impact is not None:
        base["impactOfWork"] = args.impact
    if args.energy is not None:
        base["energyCheckin"] = args.energy
    if args.notes is not None:
        base["notes"] = args.notes or None
    if args.completed is not None:
        base["completedTasks"] = args.completed
    if args.planned is not None:
        base["plannedTasks"] = args.planned
    if args.remind_tomorrow:
        base["remindTomorrow"] = True
    if args.no_remind_tomorrow:
        base["remindTomorrow"] = False
    if not base and not args.reflect:
        raise CliError("metric set: nothing to change")

    day = _parse_day(args.day) if args.day else today_str()
    client, store = _ctx()
    d = client.get()

    def _set(dd, b):
        changes = dict(base)
        if args.reflect:
            # Reflections are a whole-array field: read the CURRENT array from
            # the fresh state so a 412 retry still appends instead of dropping.
            reg = (dd["state"].get("metric") or {}).get("entities") or {}
            current = (reg.get(day) or {}).get("reflections") or []
            changes["reflections"] = list(current) + [make_reflection(args.reflect)]
        mut.metric_update(dd, b, day, changes)

    result = store.commit([_set], initial=d)
    metric = result["state"]["metric"]["entities"][day]
    print(render.metric_card(metric))
    return 0


def cmd_metric_focus(args) -> int:
    ms = render.parse_duration(args.duration)
    if ms <= 0:
        raise CliError("metric focus: duration must be positive")
    day = _parse_day(args.day) if args.day else today_str()
    client, store = _ctx()
    d = client.get()
    count: list[int] = []

    def _log(dd, b):
        count.append(mut.metric_log_focus(dd, b, day, ms))

    result = store.commit([_log], initial=d)
    _, total = q.focus_sessions(result["state"]["metric"]["entities"][day])
    print(
        f"{day}: focus session {render.format_duration(ms)} "
        f"(#{count[0]}, total {render.format_duration(total)})"
    )
    return 0


def cmd_metric_rm(args) -> int:
    day = _parse_day(args.day)
    client, store = _ctx()
    d = client.get()
    if day not in ((d["state"].get("metric") or {}).get("entities") or {}):
        raise CliError(f"no metric for {day}")
    if not _confirm(f"delete the metric for {day}?", args.yes):
        print("aborted", file=sys.stderr)
        return 1
    store.commit([lambda dd, b: mut.metric_delete(dd, b, day)], initial=d)
    print(f"deleted {day}")
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
        archived[:] = mut.archive_done(dd, b)  # a retried closure must not double-count

    store.commit([_arch], initial=d)
    print(f"archived {len(archived)} task(s)")
    return 0


def cmd_archived(args) -> int:
    client, _ = _ctx()
    d = client.get()
    tasks = q.archived_tasks(
        d, search=args.search, include_subtasks=args.subtasks
    )
    if args.json:
        render.print_json(tasks)
    else:
        render.print_archived_tasks(d, tasks)
    return 0


def cmd_restore(args) -> int:
    client, store = _ctx()
    d = client.get()
    tid = q.resolve_archived_task(d, args.id)
    restored: list[str] = []

    def _restore(dd, b):
        # commit() may retry the closure on a sync race — report the LAST run.
        restored[:] = mut.restore_task(dd, b, tid, to_today=args.today)

    store.commit([_restore], initial=d)
    subs = len(restored) - 1
    extra = f" (+{subs} subtask(s))" if subs else ""
    print(f"restored {tid}{extra}" + (" to today" if args.today else ""))
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
    print(f"providers:    {len(q.all_providers(d))}")
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
    ("today", "move"): "today-move",
    ("plan", "show"): "plan-show",
    ("plan", "move"): "plan-move",
    ("subtask", "move"): "subtask-move",
    ("subtask", "reparent"): "subtask-reparent",
    ("project", "add"): "project-add",
    ("project", "edit"): "project-edit",
    ("project", "archive"): "project-archive",
    ("project", "rm"): "project-rm",
    ("tag", "new"): "tag-new",
    ("tag", "edit"): "tag-edit",
    ("tag", "rm"): "tag-rm",
    ("repeat", "rm"): "repeat-rm",
    ("repeat", "edit"): "repeat-edit",
    ("repeat", "skip"): "repeat-skip",
    ("backlog", "add"): "backlog-add",
    ("backlog", "rm"): "backlog-rm",
    ("backlog", "clear"): "backlog-clear",
    ("attach", "edit"): "attach-edit",
    ("attach", "rm"): "attach-rm",
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
    ("provider", "add-ical"): "provider-add-ical",
    ("provider", "add-caldav"): "provider-add-caldav",
    ("provider", "edit"): "provider-edit",
    ("provider", "rm"): "provider-rm",
    ("provider", "order"): "provider-order",
    ("metric", "set"): "metric-set",
    ("metric", "focus"): "metric-focus",
    ("metric", "rm"): "metric-rm",
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
    # `sp attach TASK` (no path) == `sp attachments TASK`
    if argv[:1] == ["attach"] and len(argv) >= 2 and not argv[1].startswith("-") and (
        len(argv) == 2 or all(a.startswith("-") for a in argv[2:])
    ):
        return ["attachments"] + argv[1:]
    # bare `sp note [--flags]` == `sp notes`
    if argv[:1] == ["note"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["notes"] + argv[1:]
    # bare `sp counter [--flags]` == `sp counters`
    if argv[:1] == ["counter"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["counters"] + argv[1:]
    # bare `sp provider [--flags]` == `sp providers`
    if argv[:1] == ["provider"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["providers"] + argv[1:]
    # bare `sp metric [--flags]` == `sp metrics`
    if argv[:1] == ["metric"] and (len(argv) == 1 or argv[1].startswith("-")):
        return ["metrics"] + argv[1:]
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


_PROVIDER_USAGE = """usage: sp provider <subcommand> ...

  sp providers [--json]                 list issue providers / calendars
  sp provider add-ical URL [--auto-import] [--project P] [--tag T]
  sp provider add-caldav --url U --resource R --username U --password P \\
      --store-plaintext-credentials
  sp provider edit ID [--enable|--disable] [--url U] [--project P] ...
                                        (ICAL/CALDAV only)
  sp provider rm ID [--yes]
  sp provider order ID [ID ...]         listed providers first

Run `sp provider-add-ical --help` (etc.) for the full flags."""


def _provider_group_help(argv: list[str]) -> str | None:
    """`sp provider <typo>` gets the group usage, not argparse's
    "invalid choice: 'provider'"."""
    if not argv or argv[0] != "provider" or len(argv) < 2:
        return None
    return (
        f"unknown provider subcommand: {argv[1]}\n\n{_PROVIDER_USAGE}"
    )


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
    s.add_argument(
        "--backlog",
        action="store_true",
        help="create in the project's backlog (needs --enable-backlog)",
    )
    s.add_argument(
        "--no-parse",
        action="store_true",
        help="do not parse short syntax (+project #tag @due !deadline 30m)",
    )
    s.add_argument(
        "--parse-deadline",
        action="store_true",
        help="parse '!<date>' as a deadline even when shortSyntax.isEnableDeadline is off",
    )

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

    s = add(
        "reorder",
        cmd_reorder,
        "rewrite a project's whole task order (prefer 'move-in-project')",
    )
    s.add_argument("--project", required=True)
    s.add_argument("ids", nargs="+")

    def _directions(sp):
        for name in mut.DIRECTIONS:
            sp.add_argument(f"--{name}", action="store_true")
        return sp

    s = _directions(add("today-move", cmd_today_move, "reorder today's list"))
    s.add_argument("id")
    s.add_argument("--before", help="put it directly before this task")

    s = _directions(
        add("move-in-project", cmd_move_in_project, "reorder a task in its project")
    )
    s.add_argument("id")
    s.add_argument("--after", help="put it directly after this task")

    s = _directions(add("subtask-move", cmd_subtask_move, "reorder a subtask"))
    s.add_argument("id")

    s = add("subtask-reparent", cmd_subtask_reparent, "move a subtask to another parent")
    s.add_argument("id")
    s.add_argument("--parent", required=True)
    s.add_argument("--after", help="put it after this subtask of the new parent")

    s = add("demote", cmd_demote, "turn a task into a subtask")
    s.add_argument("id")
    s.add_argument("--parent", required=True)
    s.add_argument("--after", help="put it after this subtask of the parent")

    s = add(
        "promote",
        cmd_promote,
        "turn a subtask into a main task (a DONE one is rescheduled to today)",
    )
    s.add_argument("id")
    s.add_argument("--today", action="store_true", help="also plan it for today")

    s = add("plan-move", cmd_plan_move, "move a task before another in the planner")
    s.add_argument("id")
    s.add_argument(
        "--before",
        required=True,
        help="anchor task; it decides the day, so it must be planned or have a due day",
    )

    s = add("backlog", cmd_backlog, "list a project's backlog")
    s.add_argument("--project", required=True)
    s.add_argument("--json", action="store_true")
    s = add("backlog-add", cmd_backlog_add, "move tasks into their backlog")
    s.add_argument("ids", nargs="+")
    s = add("backlog-rm", cmd_backlog_rm, "move tasks out of the backlog")
    s.add_argument("ids", nargs="+")
    s = add("backlog-clear", cmd_backlog_clear, "move the whole backlog back")
    s.add_argument("--project", required=True)

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
    s.add_argument(
        "--remind",
        help="offset before --at, e.g. 1h; 'none' drops the reminder",
    )
    s.add_argument("--clear", action="store_true", help="remove the deadline")
    s.add_argument(
        "--clear-reminder",
        action="store_true",
        help="keep the deadline, drop its reminder",
    )

    s = add("dismiss", cmd_dismiss, "drop a task's reminder, keep its schedule")
    s.add_argument("id")

    s = add("agenda", cmd_agenda, "overdue / today / scheduled / deadlines")
    s.add_argument("--json", action="store_true")

    s = add("track", cmd_track, "add tracked time")
    s.add_argument("id")
    s.add_argument("duration", help="e.g. 30m, 1.5h")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")

    s = add("untrack", cmd_untrack, "remove tracked time (correction)")
    s.add_argument("id")
    s.add_argument("duration", help="e.g. 30m, 1.5h")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")

    s = add("start", cmd_start, "start the local timer on a task")
    s.add_argument("id", nargs="?", help="omit to show the running timer")
    s = add("stop", cmd_stop, "stop the timer and track the elapsed time")
    s.add_argument("--discard", action="store_true", help="drop it without tracking")
    s = add("current", cmd_current, "show the running timer")
    s.add_argument("--json", action="store_true")

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

    s = add("repeat-edit", cmd_repeat_edit, "edit / pause a repeat config")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--every", choices=sorted(_CYCLES))
    s.add_argument("--interval", type=int)
    s.add_argument("--days", help="mon,tue,...")
    s.add_argument("--start-time", help="HH:MM")
    s.add_argument("--remind", choices=["AtStart", "m5", "m10", "m15", "m30", "h1"])
    s.add_argument("--est", help="default estimate, e.g. 30m")
    s.add_argument("--notes")
    s.add_argument("--start-date")
    s.add_argument("--pause", action="store_true")
    s.add_argument("--resume", action="store_true")
    s.add_argument(
        "--clear",
        help="comma-separated fields to unset: "
        + ",".join(mut.CLEARABLE_REPEAT_CFG_FIELDS),
    )

    s = add("repeat-skip", cmd_repeat_skip, "skip one instance of a repeat config")
    s.add_argument("id")
    s.add_argument("--date", required=True, help="YYYY-MM-DD | today | tomorrow")

    s = add("repeat-rm", cmd_repeat_rm, "delete a repeat config (tasks are kept)")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")

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
    s.add_argument("--enable-backlog", action="store_true")
    s.add_argument(
        "--disable-backlog",
        action="store_true",
        help="turn the backlog off; its tasks move back into the project list",
    )
    s = add("project-archive", cmd_project_archive, "archive a project")
    s.add_argument("id")
    s = add(
        "project-rm",
        cmd_project_rm,
        "DELETE a project with all its tasks and notes (not archived)",
    )
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")

    s = add("tags", cmd_tags, "list tags")
    s.add_argument("--json", action="store_true")
    s = add("tag-new", cmd_tag_new, "create a tag")
    s.add_argument("title")
    s.add_argument("--color")
    s = add("tag-edit", cmd_tag_edit, "edit a tag")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--color")
    s = add("tag-rm", cmd_tag_rm, "delete a tag (removed from every task)")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")

    s = add("attach", cmd_attach, "attach a link/file/image to a task")
    s.add_argument("id")
    s.add_argument("path", help="url or file path")
    s.add_argument("--title")
    s.add_argument(
        "--type",
        choices=sorted(_ATTACH_TYPES),
        help="force the type (default: guessed from the path)",
    )
    s = add("attachments", cmd_attachments, "list a task's attachments")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = add("attach-edit", cmd_attach_edit, "edit a task attachment")
    s.add_argument("id")
    s.add_argument("attachment")
    s.add_argument("--title")
    s.add_argument("--path")
    s.add_argument("--type", choices=sorted(_ATTACH_TYPES))
    s = add("attach-rm", cmd_attach_rm, "remove a task attachment")
    s.add_argument("id")
    s.add_argument("attachment")

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
    s.add_argument("--by", help="clicks (default 1); not for stopwatch counters")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s = add("counter-log", cmd_counter_log, "add time to a stopwatch counter")
    s.add_argument("id")
    s.add_argument("duration", help="e.g. 30m, 1.5h")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s = add("counter-order", cmd_counter_order, "reorder counters (listed first)")
    s.add_argument("ids", nargs="+")

    s = add("providers", cmd_providers, "list issue providers / calendars")
    s.add_argument("--json", action="store_true")
    s = add("provider-add-ical", cmd_provider_add_ical, "connect an ICAL calendar")
    s.add_argument("url", help="the calendar's .ics URL")
    s.add_argument(
        "--auto-import",
        action="store_true",
        help="auto-import today's events as tasks (the app creates them)",
    )
    s.add_argument("--project", help="default project for imported tasks")
    s.add_argument("--tag", action="append", help="default tag (repeatable)")
    s.add_argument("--create-tags", action="store_true")
    s.add_argument("--check-every", help="poll interval, e.g. 2h (default 2h)")
    s.add_argument("--banner-before", help="banner lead time, e.g. 2h")
    s.add_argument("--include-regex", help="only import matching events")
    s.add_argument("--exclude-regex", help="skip matching events")
    s = add("provider-add-caldav", cmd_provider_add_caldav, "connect a CalDAV server")
    s.add_argument("--url", required=True)
    s.add_argument("--resource", required=True, help="calendar/resource name")
    s.add_argument("--username", required=True)
    s.add_argument("--password", required=True)
    s.add_argument("--category-filter")
    s.add_argument("--project", help="default project for imported tasks")
    s.add_argument("--tag", action="append", help="default tag (repeatable)")
    s.add_argument("--create-tags", action="store_true")
    s.add_argument(
        "--store-plaintext-credentials",
        action="store_true",
        help="required: the password is stored in plain text in the sync file",
    )
    s = add("provider-edit", cmd_provider_edit, "edit an issue provider")
    s.add_argument("id")
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.add_argument("--url")
    s.add_argument("--auto-import", action="store_true")
    s.add_argument("--no-auto-import", action="store_true")
    s.add_argument("--project")
    s.add_argument("--no-project", action="store_true")
    s.add_argument("--check-every", help="poll interval, e.g. 2h (ICAL)")
    s.add_argument("--banner-before", help="banner lead time, e.g. 2h (ICAL)")
    s.add_argument("--include-regex", help="only import matching events (ICAL)")
    s.add_argument("--exclude-regex", help="skip matching events (ICAL)")
    s.add_argument("--username", help="CalDAV user")
    s.add_argument("--password", help="CalDAV password (needs --store-plaintext-credentials)")
    s.add_argument("--category-filter", help="CalDAV category filter")
    s.add_argument(
        "--store-plaintext-credentials",
        action="store_true",
        help="required with --password: it is stored in plain text in the sync file",
    )
    s = add("provider-rm", cmd_provider_rm, "delete a provider, unlinking its tasks")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true")
    s = add("provider-order", cmd_provider_order, "reorder providers (listed first)")
    s.add_argument("ids", nargs="+")

    s = add("metrics", cmd_metrics, "daily metrics / day rating")
    s.add_argument("--from", dest="from", help="YYYY-MM-DD | today | +N")
    s.add_argument("--to", help="YYYY-MM-DD | today | +N")
    s.add_argument("--json", action="store_true")
    s = add("metric-set", cmd_metric_set, "rate a day")
    s.add_argument("--day", help="YYYY-MM-DD | today | +N (default today)")
    s.add_argument("--impact", type=int, help="impact of work, 1-4")
    s.add_argument("--energy", type=int, help="energy check-in, 1-3")
    s.add_argument("--notes", help="day notes (empty string clears)")
    s.add_argument("--reflect", help="append a reflection")
    s.add_argument("--remind-tomorrow", action="store_true")
    s.add_argument("--no-remind-tomorrow", action="store_true")
    s.add_argument("--completed", type=int, help="completed task count")
    s.add_argument("--planned", type=int, help="planned task count")
    s = add("metric-focus", cmd_metric_focus, "log a focus session")
    s.add_argument("duration", help="e.g. 25m, 1.5h")
    s.add_argument("--day", help="YYYY-MM-DD (default today)")
    s = add("metric-rm", cmd_metric_rm, "delete a day's metric")
    s.add_argument("day")
    s.add_argument("--yes", action="store_true")

    s = add("archive", cmd_archive, "archive done tasks")
    s.add_argument("--yes", action="store_true")

    s = add("archived", cmd_archived, "list archived tasks")
    s.add_argument("--search")
    s.add_argument(
        "--subtasks", action="store_true", help="also list archived subtasks"
    )
    s.add_argument("--json", action="store_true")

    s = add("restore", cmd_restore, "restore a task from the archive")
    s.add_argument("id")
    s.add_argument("--today", action="store_true", help="plan it for today")

    s = add("pull", cmd_pull, "download and summarize the sync file")
    s.add_argument("--raw", action="store_true")

    add("doctor", cmd_doctor, "validate state invariants")
    add("backup", cmd_backup, "download and store a backup")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = _rewrite_argv(list(sys.argv[1:] if argv is None else argv))
    group_help = _board_group_help(argv) or _provider_group_help(argv)
    if group_help:
        print(group_help, file=sys.stderr)
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
        TimerError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
