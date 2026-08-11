"""Hash-chained append-only audit log.

Every broker decision lands here, allowed or denied. Each entry carries the
hash of the one before it, so removing or editing a past entry breaks the
chain and `verify()` reports the first bad index.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

GENESIS = "0" * 64


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, body: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(body)).encode()).hexdigest()


def _last_hash() -> str:
    last = GENESIS
    for entry in read():
        last = entry.get("hash", GENESIS)
    return last


def append(event: str, **fields: Any) -> dict:
    """Append one entry and return it (including its hash)."""
    config.ensure_dirs()
    body = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    prev = _last_hash()
    entry = {**body, "prev": prev, "hash": _digest(prev, body)}
    with config.AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
    return entry


def read() -> Iterator[dict]:
    if not config.AUDIT_LOG.exists():
        return iter(())
    lines = config.AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    return (json.loads(line) for line in lines if line.strip())


def verify() -> tuple[bool, str]:
    """Walk the chain. Returns (ok, human-readable message)."""
    prev = GENESIS
    count = 0
    for i, entry in enumerate(read()):
        body = {k: v for k, v in entry.items() if k not in ("prev", "hash")}
        if entry.get("prev") != prev:
            return False, f"chain broken at entry {i}: prev hash does not match"
        if _digest(prev, body) != entry.get("hash"):
            return False, f"chain broken at entry {i}: entry has been modified"
        prev = entry["hash"]
        count += 1
    return True, f"{count} entries, chain intact"
