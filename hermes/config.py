"""Paths and tunables. Phase 0 keeps everything under the repo root."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

CONTEXT = ROOT / "context"
FIXTURES = ROOT / "fixtures"
POLICY_FILE = ROOT / "policy.yml"

VAR = ROOT / "var"
BUS = VAR / "bus"
AUDIT_LOG = VAR / "audit.jsonl"
APPROVALS_DB = VAR / "approvals.json"

TZ = ZoneInfo(os.environ.get("HERMES_TZ", "America/New_York"))

# Holt's assumptions when computing availability. Deliberately explicit:
# an agent that guesses at your working day is worse than one that states it.
WORKDAY_START = "08:30"
WORKDAY_END = "18:00"
TRAVEL_BUFFER_MIN = 30  # applied either side of an in-person commitment
MIN_GAP_MIN = 15  # gaps shorter than this are not real availability

APPROVAL_TTL_SECONDS = 15 * 60

# Model assignment per the approved design. Olivia only routes, so she runs on
# the cheapest model; Danbury reasons against the five-year plan and gets Opus.
MODELS = {
    "olivia": "claude-haiku-4-5",
    "holt": "claude-sonnet-5",
    "bailey": "claude-sonnet-5",
    "barbara": "claude-sonnet-5",
    "danbury": "claude-opus-5",
}

EFFORT = {
    "olivia": "low",
    "holt": "medium",
    "bailey": "medium",
    "barbara": "medium",
    "danbury": "high",
}


def ensure_dirs() -> None:
    for d in (VAR, BUS / "inbound", BUS / "outbound"):
        d.mkdir(parents=True, exist_ok=True)
