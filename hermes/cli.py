"""Terminal front end for the Phase 0 fleet."""

from __future__ import annotations

import argparse
import json
import re
import sys

from . import approvals, audit, bus, config, tools
from .agents import Fleet
from .broker import Broker
from .policy import Policy

DIM, BOLD, RED, GREEN, YELL, CYAN, OFF = (
    "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
)

APPROVE_RE = re.compile(r"^\s*(yes|ok|approve|go)\s+([a-z0-9]{3})\s*$", re.I)
REJECT_RE = re.compile(r"^\s*(no|nope|reject|drop)\s+([a-z0-9]{3})\s*$", re.I)


def _say(agent: str, text: str) -> None:
    print(f"\n{BOLD}{agent.capitalize()}{OFF}  {text}\n")


# ----------------------------------------------------------------- chat


def cmd_chat(args) -> int:
    fleet = Fleet(Broker(Policy.load()))
    histories: dict[str, list] = {}
    print(f"{BOLD}Hermes fleet — Phase 0{OFF} {DIM}(fixtures only, nothing connected){OFF}")
    print(f"{DIM}Address an agent by name, or just ask and Uhura will route.")
    print(f"Approve with 'yes <code>'.  /pending  /board  /log  /quit{OFF}\n")

    while True:
        try:
            line = input(f"{CYAN}you{OFF}  ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/q", "exit"):
            return 0
        if line == "/pending":
            _print_pending()
            continue
        if line == "/board":
            print(tools.kanban_list_tasks())
            continue
        if line == "/log":
            _print_log(12)
            continue

        bus.publish("in", "local", "uhura", line)

        m = APPROVE_RE.match(line)
        if m:
            result = fleet.broker.redeem(m.group(2))
            colour = GREEN if result.ok else RED
            _say("broker", f"{colour}{result.text}{OFF}")
            continue
        m = REJECT_RE.match(line)
        if m:
            result = fleet.broker.reject(m.group(2))
            _say("broker", result.text)
            continue

        # Explicit addressing beats routing: "katherine, what's on today"
        agent, task = None, line
        first = line.split(",")[0].split()[0].lower().lstrip("@")
        if first in config.MODELS and first != "uhura":
            agent = first
            task = line[len(line.split(",")[0]) + 1:].strip() if "," in line else \
                " ".join(line.split()[1:])
        if agent is None:
            try:
                agent, task = fleet.route(line)
                print(f"{DIM}  uhura → {agent}: {task}{OFF}")
            except Exception as exc:
                _say("uhura", f"{RED}routing failed: {exc}{OFF}")
                continue

        def trace(a, tool, targs):
            print(f"{DIM}  {a} → {tool}({json.dumps(targs, default=str)}){OFF}")

        try:
            reply, histories[agent] = fleet.ask(
                agent, task, histories.get(agent), on_tool=trace)
        except Exception as exc:
            _say(agent, f"{RED}{type(exc).__name__}: {exc}{OFF}")
            continue

        bus.publish("out", agent, agent, reply)
        _say(agent, reply)


# ----------------------------------------------------------------- drill


DRILLS = [
    ("katherine", "calendar.list_events", {"day_offset": 0},
     "allow", "in-scope read"),
    ("katherine", "calendar.find_slots", {"duration_minutes": 45},
     "allow", "in-scope read"),
    ("katherine", "calendar.create_event",
     {"day_offset": 1, "start_time": "10:00", "duration_minutes": 30, "title": "Test"},
     "pending", "write, so it queues for approval"),
    ("boone", "calendar.create_event",
     {"day_offset": 1, "start_time": "10:00", "duration_minutes": 30, "title": "Test"},
     "denied", "not in Boone's allow list — default deny"),
    ("uhura", "calendar.list_events", {"day_offset": 0},
     "denied", "the router holds nothing at all"),
    ("dorothy", "gmail.send",
     {"to": "client@example.com", "subject": "hi", "body": "hi"},
     "denied", "hard deny: never email an external client"),
    ("dorothy", "drive.delete", {"path": "/Clients/old.pdf"},
     "denied", "hard deny: never delete anything"),
    ("guinan", "context.read", {"path": "identity/five-year-plan.md"},
     "allow", "the plan is what Guinan reasons against"),
    ("dorothy", "drive.list_metadata", {"path_prefix": "/Financial/"},
     "denied", "hard deny: never touch financial docs"),
    ("dorothy", "drive.list_metadata", {"path_prefix": "/Legal/"},
     "denied", "hard deny: never touch legal docs"),
    ("dorothy", "substack.publish", {"title": "Draft", "body": "..."},
     "denied", "hard deny: never publish to Substack"),
    ("dorothy", "drive.move",
     {"source_path": "/Inbox/a.pdf", "destination_path": "/Clients/a.pdf"},
     "pending", "Drive writes queue for approval"),
]


def cmd_drill(args) -> int:
    """Prove the guardrails without spending a token."""
    broker = Broker(Policy.load())
    print(f"\n{BOLD}Policy drill{OFF} {DIM}— no API key needed, no model involved{OFF}\n")
    failures = 0
    # The drill queues real approvals as it goes. Track what was already
    # outstanding so the drill's own can be dropped afterwards — otherwise
    # `hermes pending` fills up with actions nobody actually asked for.
    before = {p.code for p in approvals.outstanding()}
    for agent, tool, targs, expected, why in DRILLS:
        result = broker.execute(agent, tool, targs)
        got = {"ok": "allow", "denied": "denied", "pending": "pending"}[result.status]
        good = got == expected
        failures += 0 if good else 1
        mark = f"{GREEN}pass{OFF}" if good else f"{RED}FAIL{OFF}"
        colour = {"allow": GREEN, "denied": RED, "pending": YELL}[got]
        print(f"  {mark}  {agent:<10} {tool:<24} {colour}{got:<8}{OFF} {DIM}{why}{OFF}")
        if not good:
            print(f"        expected {expected}, got {got}: {result.text}")

    approvals.sweep()
    for pending in approvals.outstanding():
        if pending.code not in before:
            broker.reject(pending.code)

    ok, msg = audit.verify()
    print(f"\n  audit chain: {GREEN if ok else RED}{msg}{OFF}")
    print(f"\n{BOLD}{len(DRILLS) - failures}/{len(DRILLS)} drills passed{OFF}\n")
    return 1 if failures else 0


# ----------------------------------------------------------------- misc


def _coerce(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def cmd_call(args) -> int:
    """Invoke one tool as one agent, straight through the broker."""
    targs = {}
    for pair in args.kwargs:
        if "=" not in pair:
            print(f"{RED}expected key=value, got {pair!r}{OFF}")
            return 2
        k, v = pair.split("=", 1)
        targs[k] = _coerce(v)
    result = Broker(Policy.load()).execute(args.agent, args.tool, targs)
    colour = {"ok": GREEN, "denied": RED, "pending": YELL}[result.status]
    print(f"{colour}[{result.status}]{OFF} {result.text}")
    return 0 if result.status != "denied" else 1


def _print_pending() -> None:
    approvals.sweep()
    live = approvals.outstanding()
    if not live:
        print(f"{DIM}nothing awaiting approval{OFF}")
        return
    for p in live:
        print(f"  {YELL}{p.code}{OFF}  {p.summary}  {DIM}({p.agent}, {p.seconds_left}s left){OFF}")


def cmd_pending(args) -> int:
    _print_pending()
    return 0


def cmd_approve(args) -> int:
    result = Broker(Policy.load()).redeem(args.code)
    print(f"{GREEN if result.ok else RED}{result.text}{OFF}")
    return 0 if result.ok else 1


def cmd_reject(args) -> int:
    print(Broker(Policy.load()).reject(args.code).text)
    return 0


def _print_log(limit: int) -> None:
    entries = list(audit.read())[-limit:]
    if not entries:
        print(f"{DIM}audit log is empty{OFF}")
        return
    for e in entries:
        colour = RED if "denied" in e["event"] else (YELL if "queued" in e["event"] else "")
        detail = e.get("tool", e.get("code", ""))
        rule = f" {DIM}({e['rule']}){OFF}" if e.get("rule") else ""
        print(f"  {DIM}{e['ts']}{OFF} {colour}{e['event']:<26}{OFF} "
              f"{e.get('agent', '-'):<10} {detail}{rule}")


def cmd_log(args) -> int:
    if args.verify:
        ok, msg = audit.verify()
        print(f"{GREEN if ok else RED}{msg}{OFF}")
        return 0 if ok else 1
    _print_log(args.limit)
    return 0


def cmd_board(args) -> int:
    print(tools.kanban_list_tasks(max_minutes=args.minutes or 0, energy=args.energy or ""))
    return 0


def cmd_doctor(args) -> int:
    print(f"{BOLD}Hermes Phase 0 — preflight{OFF}\n")
    rows = []
    rows.append(("policy.yml", config.POLICY_FILE.exists()))
    rows.append(("context repo", (config.CONTEXT / "identity").is_dir()))
    rows.append(("fixtures", (config.FIXTURES / "calendar_google.json").exists()))
    charters = all((config.CONTEXT / "agents" / a / "charter.md").exists()
                   for a in config.MODELS)
    rows.append(("five charters", charters))
    try:
        import anthropic  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    rows.append(("anthropic SDK (chat only)", sdk))
    import os
    rows.append(("ANTHROPIC_API_KEY set (chat only)", bool(os.environ.get("ANTHROPIC_API_KEY"))))

    for label, ok in rows:
        print(f"  {GREEN + 'ok  ' + OFF if ok else YELL + 'miss' + OFF}  {label}")

    ok, msg = audit.verify()
    print(f"\n  audit chain: {GREEN if ok else RED}{msg}{OFF}")
    print(f"\n{DIM}Everything except `chat` runs without an API key.{OFF}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes", description="Hermes fleet, Phase 0")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("chat", help="talk to the fleet (needs ANTHROPIC_API_KEY)").set_defaults(fn=cmd_chat)
    sub.add_parser("drill", help="prove the policy engine, no API key needed").set_defaults(fn=cmd_drill)
    sub.add_parser("doctor", help="check the setup").set_defaults(fn=cmd_doctor)
    sub.add_parser("pending", help="list actions awaiting approval").set_defaults(fn=cmd_pending)

    p = sub.add_parser("call", help="invoke one tool as one agent")
    p.add_argument("agent")
    p.add_argument("tool")
    p.add_argument("kwargs", nargs="*", help="key=value pairs")
    p.set_defaults(fn=cmd_call)

    p = sub.add_parser("approve", help="redeem an approval code")
    p.add_argument("code")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("reject", help="drop a pending action")
    p.add_argument("code")
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("log", help="show the audit log")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--verify", action="store_true", help="check the hash chain")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("board", help="show the Kanban board")
    p.add_argument("--minutes", type=int, help="only tasks fitting in this long")
    p.add_argument("--energy", help="deep, shallow, admin, social")
    p.set_defaults(fn=cmd_board)

    args = parser.parse_args(argv)
    config.ensure_dirs()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
