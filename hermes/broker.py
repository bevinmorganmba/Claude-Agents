"""The broker: the only component that executes anything.

Agents do not hold credentials and do not call tools. They ask the broker to
perform a named action; the broker decides, records, and only then executes.
In Phase 0 there are no real credentials to hold, but the shape is the one
that ships to the VPS unchanged — which is the point of building it now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import approvals, audit, tools
from .policy import ALLOW, APPROVE, DENY, Policy


@dataclass
class Result:
    status: str          # "ok" | "denied" | "pending"
    text: str            # what the agent sees
    rule: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Broker:
    def __init__(self, policy: Policy | None = None):
        self.policy = policy or Policy.load()

    # ---------------------------------------------------------------- exec

    def execute(self, agent: str, tool: str, args: dict[str, Any]) -> Result:
        decision = self.policy.evaluate(agent, tool, args)

        if tool not in tools.REGISTRY:
            audit.append("tool.unknown", agent=agent, tool=tool, args=args)
            return Result("denied", f"No such tool: {tool}", "unknown-tool")

        if decision.verdict == DENY:
            audit.append("tool.denied", agent=agent, tool=tool, args=args,
                         rule=decision.rule, reason=decision.reason)
            return Result(
                "denied",
                f"DENIED by policy rule '{decision.rule}': {decision.reason} "
                f"This is not overridable — do not retry, and tell the user plainly "
                f"that you are not permitted to do this.",
                decision.rule,
            )

        if decision.verdict == APPROVE:
            summary = tools.summarise(tool, args)
            pending = approvals.enqueue(agent, tool, args, summary)
            audit.append("tool.queued", agent=agent, tool=tool, args=args,
                         code=pending.code, fingerprint=pending.fingerprint)
            return Result(
                "pending",
                f"QUEUED FOR APPROVAL (code {pending.code}). Nothing has happened yet. "
                f"Tell the user exactly what you want to do and that they can reply "
                f"'yes {pending.code}' within 15 minutes to authorise it.",
                decision.rule,
            )

        if decision.verdict == ALLOW:
            return self._run(agent, tool, args, via="direct")

        return Result("denied", "Unrecognised policy verdict.", "internal")

    def _run(self, agent: str, tool: str, args: dict[str, Any], via: str) -> Result:
        spec = tools.REGISTRY[tool]
        try:
            output = spec.fn(**args)
        except TypeError as exc:
            audit.append("tool.badargs", agent=agent, tool=tool, args=args, error=str(exc))
            return Result("denied", f"Bad arguments for {tool}: {exc}", "bad-arguments")
        except Exception as exc:  # a failing tool must not take the fleet down
            audit.append("tool.error", agent=agent, tool=tool, args=args, error=str(exc))
            return Result("denied", f"{tool} failed: {exc}", "tool-error")
        audit.append("tool.executed", agent=agent, tool=tool, args=args, via=via)
        return Result("ok", output)

    # ------------------------------------------------------------ approval

    def redeem(self, code: str) -> Result:
        """Consume an approval code and run the exact action it was bound to."""
        approvals.sweep()
        pending = approvals.consume(code)
        if pending is None:
            audit.append("approval.unknown", code=code)
            return Result("denied", f"No pending action with code '{code}'.", "no-such-code")

        if pending.expired:
            audit.append("approval.expired", code=code, tool=pending.tool)
            return Result("denied",
                          f"Code '{code}' expired. Ask again and I will re-queue it.",
                          "expired")

        if approvals.fingerprint(pending.agent, pending.tool, pending.args) != pending.fingerprint:
            audit.append("approval.tampered", code=code, tool=pending.tool)
            return Result("denied", "Action no longer matches what you approved.", "tampered")

        # Re-check policy at redemption. An approval is permission to run one
        # action, never permission to bypass a deny rule added since it queued.
        decision = self.policy.evaluate(pending.agent, pending.tool, pending.args)
        if decision.verdict == DENY:
            audit.append("approval.denied_at_redemption", code=code,
                         tool=pending.tool, rule=decision.rule)
            return Result("denied",
                          f"Policy now refuses this: {decision.reason}", decision.rule)

        audit.append("approval.granted", code=code, agent=pending.agent,
                     tool=pending.tool, summary=pending.summary)
        return self._run(pending.agent, pending.tool, pending.args, via=f"approval:{code}")

    def reject(self, code: str) -> Result:
        pending = approvals.consume(code)
        if pending is None:
            return Result("denied", f"No pending action with code '{code}'.", "no-such-code")
        audit.append("approval.rejected", code=code, tool=pending.tool,
                     summary=pending.summary)
        return Result("ok", f"Dropped: {pending.summary}")
