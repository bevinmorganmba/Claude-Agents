"""Tool implementations, backed by fixtures.

Phase 0 has no network and no OAuth. Every tool below reads from
`fixtures/` or the local context repo. The three deny-listed tools at the
bottom are registered deliberately: an agent must be *able* to attempt them
for the drill to prove the broker refuses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from . import config

# ---------------------------------------------------------------- helpers


def _today() -> date:
    return datetime.now(config.TZ).date()


def _at(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=config.TZ)


def _fixture(name: str) -> dict:
    return json.loads((config.FIXTURES / name).read_text(encoding="utf-8"))


def _all_events(day_from: int = 0, day_to: int = 0) -> list[dict]:
    """Merged, resolved events across every calendar source in fixtures/."""
    out: list[dict] = []
    for fname in ("calendar_google.json", "calendar_outlook.json", "calendly.json"):
        doc = _fixture(fname)
        for ev in doc["events"]:
            off = ev["day_offset"]
            if not (day_from <= off <= day_to):
                continue
            day = _today() + timedelta(days=off)
            out.append({
                "source": doc["source"],
                "day_offset": off,
                "date": day.isoformat(),
                "start": _at(day, ev["start"]),
                "end": _at(day, ev["end"]),
                "title": ev["title"],
                "location": ev.get("location", ""),
                "in_person": bool(ev.get("in_person", False)),
                "external": bool(ev.get("external", False)),
            })
    return sorted(out, key=lambda e: e["start"])


def _fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _busy_blocks(day_from: int, day_to: int, travel: bool) -> list[tuple[datetime, datetime]]:
    """Busy intervals, padded with travel buffer around in-person commitments."""
    blocks: list[tuple[datetime, datetime]] = []
    for ev in _all_events(day_from, day_to):
        pad = timedelta(minutes=config.TRAVEL_BUFFER_MIN) if (travel and ev["in_person"]) else timedelta()
        blocks.append((ev["start"] - pad, ev["end"] + pad))
    blocks.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in blocks:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------- calendar


def calendar_list_events(day_offset: int = 0) -> str:
    """List every calendar commitment on a given day, across all connected calendars.

    Args:
        day_offset: 0 for today, 1 for tomorrow, -1 for yesterday.
    """
    events = _all_events(day_offset, day_offset)
    day = _today() + timedelta(days=day_offset)
    if not events:
        return f"{day:%A %-d %B}: nothing scheduled."
    lines = [f"{day:%A %-d %B} — {len(events)} commitment(s):"]
    for ev in events:
        tags = []
        if ev["in_person"]:
            tags.append(f"in person, {ev['location']}")
        if ev["external"]:
            tags.append("external attendee")
        suffix = f"  [{'; '.join(tags)}]" if tags else ""
        lines.append(f"  {_fmt(ev['start'])}–{_fmt(ev['end'])}  {ev['title']} ({ev['source']}){suffix}")
    return "\n".join(lines)


def calendar_free_busy(day_offset_start: int = 0, day_offset_end: int = 0) -> str:
    """Return merged busy blocks across all calendars for a range of days.

    Args:
        day_offset_start: First day, 0 being today.
        day_offset_end: Last day inclusive, 0 being today.
    """
    blocks = _busy_blocks(day_offset_start, day_offset_end, travel=False)
    if not blocks:
        return "No busy blocks in that range."
    return "\n".join(f"{s:%a %d %b} {_fmt(s)}–{_fmt(e)}" for s, e in blocks)


def calendar_find_slots(duration_minutes: int, day_offset_start: int = 0,
                        day_offset_end: int = 4, in_person: bool = False) -> str:
    """Find open slots of a given length, honouring working hours and travel time.

    Args:
        duration_minutes: How long the slot needs to be.
        day_offset_start: First day to search, 0 being today.
        day_offset_end: Last day to search inclusive.
        in_person: True if the meeting is in person, which adds travel buffer
            either side of any adjacent in-person commitment.
    """
    need = timedelta(minutes=duration_minutes)
    found: list[str] = []
    for off in range(day_offset_start, day_offset_end + 1):
        day = _today() + timedelta(days=off)
        if day.weekday() >= 5:
            continue
        cursor = _at(day, config.WORKDAY_START)
        close = _at(day, config.WORKDAY_END)
        if off == 0:
            now = datetime.now(config.TZ)
            cursor = max(cursor, now.replace(second=0, microsecond=0))
        for bs, be in _busy_blocks(off, off, travel=in_person):
            if bs > cursor and bs - cursor >= need:
                found.append(f"{day:%a %d %b} {_fmt(cursor)}–{_fmt(cursor + need)}")
            cursor = max(cursor, be)
        if close > cursor and close - cursor >= need:
            found.append(f"{day:%a %d %b} {_fmt(cursor)}–{_fmt(cursor + need)}")
    if not found:
        return (f"No {duration_minutes}-minute slot available between day {day_offset_start} "
                f"and day {day_offset_end} within {config.WORKDAY_START}–{config.WORKDAY_END}.")
    header = f"Open {duration_minutes}-minute slots"
    if in_person:
        header += f" (with {config.TRAVEL_BUFFER_MIN}min travel buffer)"
    return header + ":\n" + "\n".join("  " + f for f in found)


def calendar_check_slot(day_offset: int, start_time: str, duration_minutes: int,
                        in_person: bool = False) -> str:
    """Check whether one specific proposed slot is actually free.

    Args:
        day_offset: 0 for today, 1 for tomorrow, and so on.
        start_time: Proposed start in 24-hour HH:MM form.
        duration_minutes: Length of the proposed meeting.
        in_person: True if in person, which requires travel buffer to clear too.
    """
    day = _today() + timedelta(days=day_offset)
    start = _at(day, start_time)
    end = start + timedelta(minutes=duration_minutes)
    conflicts = []
    for ev in _all_events(day_offset, day_offset):
        pad = timedelta(minutes=config.TRAVEL_BUFFER_MIN) if (in_person and ev["in_person"]) else timedelta()
        if start < ev["end"] + pad and end > ev["start"] - pad:
            why = "overlaps" if start < ev["end"] and end > ev["start"] else "too tight for travel"
            conflicts.append(f"{why}: {ev['title']} {_fmt(ev['start'])}–{_fmt(ev['end'])}")
    window_ok = (start >= _at(day, config.WORKDAY_START) and end <= _at(day, config.WORKDAY_END))
    weekend = day.weekday() >= 5
    verdict = "FREE" if (not conflicts and window_ok and not weekend) else "NOT FREE"
    lines = [f"{verdict}: {day:%A %-d %B} {_fmt(start)}–{_fmt(end)}"]
    if weekend:
        # find_slots already skips weekends. Without this, checking one
        # specific Saturday slot would come back FREE and contradict it.
        lines.append("  weekend — not available unless you say otherwise")
    if not window_ok:
        lines.append(f"  outside working hours ({config.WORKDAY_START}–{config.WORKDAY_END})")
    lines.extend("  " + c for c in conflicts)
    return "\n".join(lines)


def calendar_create_event(day_offset: int, start_time: str, duration_minutes: int,
                          title: str, attendees: str = "") -> str:
    """Create a calendar event. Requires your approval before it is written.

    Args:
        day_offset: 0 for today, 1 for tomorrow.
        start_time: Start in 24-hour HH:MM form.
        duration_minutes: Length of the event.
        title: Event title.
        attendees: Comma-separated email addresses. Leave empty for a solo block.
    """
    day = _today() + timedelta(days=day_offset)
    return (f"[fixture] Would create '{title}' on {day:%a %d %b} at {start_time} "
            f"for {duration_minutes}min. Attendees: {attendees or 'none'}.")


# ---------------------------------------------------------------- kanban

_TASK_RE = re.compile(r"^-\s*\[( |x)\]\s*(?P<id>t-\d+)\s*\|\s*(?P<title>[^|]+?)\s*\|(?P<rest>.*)$")


@dataclass
class Task:
    id: str
    title: str
    status: str
    est_min: int
    energy: str
    blocks: str
    serves: str      # which venture — this is what makes the priority ladder applicable
    due: str
    done: bool


def _parse_board() -> list[Task]:
    tasks: list[Task] = []
    status = "Unsorted"
    for line in (config.CONTEXT / "kanban" / "board.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            status = line[3:].strip()
            continue
        # Match the raw line, not a stripped copy: the schema example in the
        # board's header is indented, and stripping it made the documentation
        # parse as a real task. Also require a column heading first — anything
        # above the first `## ` is prose, not the board.
        m = _TASK_RE.match(line)
        if not m or status == "Unsorted":
            continue
        meta = dict(
            part.strip().split(":", 1)
            for part in m.group("rest").split("|") if ":" in part
        )
        est = meta.get("est", "0m").strip()
        minutes = int(est[:-1]) * (60 if est.endswith("h") else 1) if est[:-1].isdigit() else 0
        tasks.append(Task(
            id=m.group("id"), title=m.group("title").strip(), status=status,
            est_min=minutes, energy=meta.get("energy", "any").strip(),
            blocks=meta.get("blocks", "none").strip(),
            serves=meta.get("serves", "unknown").strip(),
            due=meta.get("due", "").strip(), done=m.group(1) == "x",
        ))
    return tasks


def kanban_list_tasks(max_minutes: int = 0, energy: str = "", serves: str = "") -> str:
    """List tasks from the Kanban board, filtered by time, energy, or venture.

    Args:
        max_minutes: Only return tasks estimated at or below this. 0 means no limit.
        energy: Filter to one of deep, shallow, admin, or social. Empty means any.
        serves: Filter to one venture, for example cfo-sales, options, salon,
            tardus, s-dekalb, substack, personal, family. Empty means any.
    """
    tasks = [t for t in _parse_board() if not t.done]
    if max_minutes:
        tasks = [t for t in tasks if 0 < t.est_min <= max_minutes]
    if energy:
        tasks = [t for t in tasks if t.energy == energy.strip().lower()]
    if serves:
        tasks = [t for t in tasks if t.serves == serves.strip().lower()]
    if not tasks:
        return "No matching tasks on the board."
    lines = []
    for t in sorted(tasks, key=lambda t: (t.status != "Now", t.est_min)):
        bits = [f"[{t.status}]", t.id, t.title, "·", f"{t.est_min}min",
                "·", t.energy, "·", t.serves]
        if t.blocks != "none":
            bits.append(f"← blocks {t.blocks}")
        if t.due:
            bits.append(f"← due {t.due}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def kanban_set_status(task_id: str, status: str) -> str:
    """Move a task to a different column on the local Kanban board.

    Args:
        task_id: The task identifier, for example t-014.
        status: Target column: Now, Next, Waiting, or Done.
    """
    path = config.CONTEXT / "kanban" / "board.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    moved = None
    kept: list[str] = []
    for line in lines:
        m = _TASK_RE.match(line.strip())
        if m and m.group("id") == task_id:
            moved = line
            continue
        kept.append(line)
    if moved is None:
        return f"No task {task_id} on the board."
    if status.lower() == "done":
        moved = moved.replace("- [ ]", "- [x]", 1)
    out: list[str] = []
    placed = False
    for line in kept:
        out.append(line)
        if line.strip() == f"## {status}" and not placed:
            out.append(moved)
            placed = True
    if not placed:
        return f"No column called '{status}' on the board."
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f"Moved {task_id} to {status}."


# ---------------------------------------------------------------- context / drive


def context_read(path: str) -> str:
    """Read a Markdown file from the shared context repository.

    Args:
        path: Repo-relative path, for example identity/five-year-plan.md.
    """
    target = (config.CONTEXT / path).resolve()
    if not str(target).startswith(str(config.CONTEXT.resolve())):
        return "Refused: path escapes the context repository."
    if not target.exists():
        available = sorted(p.relative_to(config.CONTEXT).as_posix()
                           for p in config.CONTEXT.rglob("*.md"))
        return f"No such file. Available:\n" + "\n".join("  " + a for a in available)
    return target.read_text(encoding="utf-8")


def context_write(path: str, content: str) -> str:
    """Write a Markdown file in the shared context repository.

    Args:
        path: Repo-relative path.
        content: Full new contents of the file.
    """
    target = (config.CONTEXT / path).resolve()
    if not str(target).startswith(str(config.CONTEXT.resolve())):
        return "Refused: path escapes the context repository."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {path} ({len(content)} chars)."


# Defence in depth. The policy engine already refuses drive calls aimed at
# these prefixes, but a listing of "/" is aimed at nothing in particular and
# would otherwise hand back legal and financial *filenames*, which are
# themselves disclosive. Filtered here as well as refused there.
PROTECTED_PREFIXES = ("/Legal/", "/Financial/", "/Tax/", "/Contracts/")


def drive_list_metadata(path_prefix: str = "/") -> str:
    """List Drive file metadata: names, types, dates. Never file contents.

    Args:
        path_prefix: Only return files under this path, for example /Clients/.
    """
    files = _fixture("drive_metadata.json")["files"]
    hits = [f for f in files if f["path"].startswith(path_prefix)]
    redacted = [f for f in hits if f["path"].startswith(PROTECTED_PREFIXES)]
    hits = [f for f in hits if not f["path"].startswith(PROTECTED_PREFIXES)]
    if not hits and not redacted:
        return f"Nothing under {path_prefix}."
    lines = [f"{len(hits)} file(s) under {path_prefix}:"]
    for f in sorted(hits, key=lambda f: f["path"]):
        lines.append(f"  {f['path']:<52} {f['type']:<12} {f['modified_days_ago']:>4}d ago")
    if redacted:
        lines.append(f"  ({len(redacted)} file(s) withheld: protected folders)")
    return "\n".join(lines)


def drive_move(source_path: str, destination_path: str) -> str:
    """Move or rename a Drive file. Requires your approval.

    Args:
        source_path: Current full path.
        destination_path: Desired full path.
    """
    return f"[fixture] Would move {source_path} -> {destination_path}."


# ------------------------------------------------- deny-listed, on purpose


def gmail_send(to: str, subject: str, body: str) -> str:
    """Send an email. Blocked for external recipients by policy.

    Args:
        to: Recipient address.
        subject: Subject line.
        body: Message body.
    """
    return f"[fixture] Would email {to}: {subject}"


def drive_delete(path: str) -> str:
    """Delete a Drive file. Blocked by policy under all circumstances.

    Args:
        path: Full path of the file to delete.
    """
    return f"[fixture] Would delete {path}"


def substack_publish(title: str, body: str) -> str:
    """Publish a Substack post. Blocked by policy under all circumstances.

    Args:
        title: Post title.
        body: Post body.
    """
    return f"[fixture] Would publish {title}"


# ---------------------------------------------------------------- registry


@dataclass
class Tool:
    name: str
    fn: Callable[..., str]
    description: str
    schema: dict


def _tool(name: str, fn: Callable[..., str], schema: dict) -> Tool:
    doc = (fn.__doc__ or "").strip()
    return Tool(name=name, fn=fn, description=doc.split("\n\n")[0], schema=schema)


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}

REGISTRY: dict[str, Tool] = {t.name: t for t in [
    _tool("calendar.list_events", calendar_list_events,
          _obj({"day_offset": _INT}, [])),
    _tool("calendar.free_busy", calendar_free_busy,
          _obj({"day_offset_start": _INT, "day_offset_end": _INT}, [])),
    _tool("calendar.find_slots", calendar_find_slots,
          _obj({"duration_minutes": _INT, "day_offset_start": _INT,
                "day_offset_end": _INT, "in_person": _BOOL}, ["duration_minutes"])),
    _tool("calendar.check_slot", calendar_check_slot,
          _obj({"day_offset": _INT, "start_time": _STR,
                "duration_minutes": _INT, "in_person": _BOOL},
               ["day_offset", "start_time", "duration_minutes"])),
    _tool("calendar.create_event", calendar_create_event,
          _obj({"day_offset": _INT, "start_time": _STR, "duration_minutes": _INT,
                "title": _STR, "attendees": _STR},
               ["day_offset", "start_time", "duration_minutes", "title"])),
    _tool("kanban.list_tasks", kanban_list_tasks,
          _obj({"max_minutes": _INT, "energy": _STR, "serves": _STR}, [])),
    _tool("kanban.set_status", kanban_set_status,
          _obj({"task_id": _STR, "status": _STR}, ["task_id", "status"])),
    _tool("context.read", context_read, _obj({"path": _STR}, ["path"])),
    _tool("context.write", context_write,
          _obj({"path": _STR, "content": _STR}, ["path", "content"])),
    _tool("drive.list_metadata", drive_list_metadata,
          _obj({"path_prefix": _STR}, [])),
    _tool("drive.move", drive_move,
          _obj({"source_path": _STR, "destination_path": _STR},
               ["source_path", "destination_path"])),
    _tool("gmail.send", gmail_send,
          _obj({"to": _STR, "subject": _STR, "body": _STR}, ["to", "subject", "body"])),
    _tool("drive.delete", drive_delete, _obj({"path": _STR}, ["path"])),
    _tool("substack.publish", substack_publish,
          _obj({"title": _STR, "body": _STR}, ["title", "body"])),
]}


def summarise(tool: str, args: dict[str, Any]) -> str:
    """One-line, human-readable description of a pending action."""
    if tool == "calendar.create_event":
        day = _today() + timedelta(days=int(args.get("day_offset", 0)))
        who = args.get("attendees") or "no attendees"
        return (f"Create '{args.get('title')}' on {day:%a %d %b} at {args.get('start_time')} "
                f"for {args.get('duration_minutes')}min ({who})")
    if tool == "drive.move":
        return f"Move {args.get('source_path')} -> {args.get('destination_path')}"
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{tool}({rendered})"
