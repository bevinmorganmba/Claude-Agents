"""Approval queue.

A pending approval is bound to a hash of the exact action. Approving
"create the 10:00 call with Ana" cannot be replayed to create a different
event: the code carries no authority of its own, only a pointer to one
frozen action, once, within fifteen minutes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from . import config

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no l/o/0/1 — these get typed on a phone


@dataclass
class Pending:
    code: str
    agent: str
    tool: str
    args: dict[str, Any]
    fingerprint: str
    created: float
    summary: str

    @property
    def expired(self) -> bool:
        return time.time() - self.created > config.APPROVAL_TTL_SECONDS

    @property
    def seconds_left(self) -> int:
        return max(0, int(config.APPROVAL_TTL_SECONDS - (time.time() - self.created)))


def fingerprint(agent: str, tool: str, args: dict) -> str:
    payload = json.dumps([agent, tool, args], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _load() -> dict[str, dict]:
    if not config.APPROVALS_DB.exists():
        return {}
    return json.loads(config.APPROVALS_DB.read_text(encoding="utf-8"))


def _save(db: dict[str, dict]) -> None:
    config.ensure_dirs()
    config.APPROVALS_DB.write_text(json.dumps(db, indent=2, default=str), encoding="utf-8")


def enqueue(agent: str, tool: str, args: dict, summary: str) -> Pending:
    db = _load()
    code = "".join(secrets.choice(_ALPHABET) for _ in range(3))
    while code in db:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(3))
    pending = Pending(
        code=code,
        agent=agent,
        tool=tool,
        args=args,
        fingerprint=fingerprint(agent, tool, args),
        created=time.time(),
        summary=summary,
    )
    db[code] = asdict(pending)
    _save(db)
    return pending


def get(code: str) -> Pending | None:
    row = _load().get(code.lower().strip())
    return Pending(**row) if row else None


def consume(code: str) -> Pending | None:
    """Single use: the code is removed whether or not it turned out valid."""
    db = _load()
    row = db.pop(code.lower().strip(), None)
    if row is None:
        return None
    _save(db)
    return Pending(**row)


def outstanding() -> list[Pending]:
    live = [Pending(**row) for row in _load().values()]
    return sorted((p for p in live if not p.expired), key=lambda p: p.created)


def sweep() -> int:
    """Drop expired codes. Returns how many were removed."""
    db = _load()
    dead = [c for c, row in db.items() if Pending(**row).expired]
    for c in dead:
        db.pop(c)
    if dead:
        _save(db)
    return len(dead)
