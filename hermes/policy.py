"""Policy engine.

The whole point of this module: permission decisions are made here, in code,
against a static file — never by a model reasoning about its own instructions.
An agent cannot argue with this, and no approval code unlocks a deny rule.

Evaluation order is strict:
    1. deny rules   -> DENY, terminal, not overridable
    2. agent allow  -> absent means DENY (default-deny)
    3. agent approve-> APPROVE (queued for the human)
    4. otherwise    -> ALLOW
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import config

ALLOW, DENY, APPROVE = "allow", "deny", "approve"


@dataclass(frozen=True)
class Decision:
    verdict: str
    rule: str
    reason: str

    @property
    def denied(self) -> bool:
        return self.verdict == DENY


def _matches_tool(patterns: list[str], tool: str) -> bool:
    return any(fnmatch.fnmatch(tool, p) for p in patterns)


def _when_matches(when: dict | None, args: dict[str, Any]) -> bool:
    """A rule with no `when` always applies. Otherwise every named argument is
    tested against a regex, in whichever direction the rule specifies, and the
    rule fires if *any* of them matches.

    `arg` may name one argument or several — a single tool often carries the
    same sensitive value under different names (path, source_path, prefix),
    and a rule that guarded only one of them would be trivial to sidestep.
    """
    if not when:
        return True
    names = when["arg"] if isinstance(when["arg"], list) else [when["arg"]]
    present = [str(args[n]) for n in names if args.get(n) is not None]
    if not present:
        # Every guarded argument is absent. Treat that as a match so a caller
        # cannot dodge a rule by omitting the field it is keyed on.
        return True
    if "matching" in when:
        return any(re.search(when["matching"], t) for t in present)
    if "not_matching" in when:
        return any(re.search(when["not_matching"], t) is None for t in present)
    return True


class Policy:
    def __init__(self, doc: dict):
        self.deny_rules: list[dict] = doc.get("deny", []) or []
        self.agents: dict[str, dict] = doc.get("agents", {}) or {}

    @classmethod
    def load(cls, path: Path | None = None) -> "Policy":
        path = path or config.POLICY_FILE
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def evaluate(self, agent: str, tool: str, args: dict[str, Any]) -> Decision:
        for rule in self.deny_rules:
            if _matches_tool(rule["tools"], tool) and _when_matches(rule.get("when"), args):
                return Decision(DENY, rule["id"], rule["reason"])

        profile = self.agents.get(agent)
        if profile is None:
            return Decision(DENY, "unknown-agent", f"No policy entry for agent '{agent}'.")

        if _matches_tool(profile.get("approve", []) or [], tool):
            return Decision(APPROVE, "approval-required",
                            f"{agent} may request {tool}, but you approve each one.")

        if _matches_tool(profile.get("allow", []) or [], tool):
            return Decision(ALLOW, "allowed", f"{tool} is within {agent}'s scope.")

        return Decision(DENY, "default-deny",
                        f"{tool} is not in {agent}'s allow list. Default is deny.")

    def tools_for(self, agent: str) -> list[str]:
        """Tool patterns an agent may attempt — allowed plus approval-gated."""
        profile = self.agents.get(agent, {})
        return list(profile.get("allow", []) or []) + list(profile.get("approve", []) or [])
