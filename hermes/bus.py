"""File-based message bus — the stand-in for WhatsApp.

Phase 2 replaces this module with a Cloud API webhook handler and nothing
else changes: agents never learn where a message came from. Keeping the
seam here in Phase 0 is what makes that swap cheap.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

# In Phase 2 this becomes the allow-list of WhatsApp numbers. Every other
# sender is silently dropped and logged, per the approved design.
AUTHORISED = {"local"}


@dataclass
class Message:
    direction: str   # "in" | "out"
    sender: str
    agent: str
    text: str
    ts: float

    @property
    def path(self) -> Path:
        folder = config.BUS / ("inbound" if self.direction == "in" else "outbound")
        return folder / f"{self.ts:.6f}-{self.agent}.json"


def publish(direction: str, sender: str, agent: str, text: str) -> Message:
    config.ensure_dirs()
    msg = Message(direction=direction, sender=sender, agent=agent, text=text, ts=time.time())
    msg.path.write_text(json.dumps(asdict(msg), indent=2), encoding="utf-8")
    return msg


def authorised(sender: str) -> bool:
    return sender in AUTHORISED


def transcript() -> list[Message]:
    out: list[Message] = []
    for folder in ("inbound", "outbound"):
        d = config.BUS / folder
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            out.append(Message(**json.loads(f.read_text(encoding="utf-8"))))
    return sorted(out, key=lambda m: m.ts)
