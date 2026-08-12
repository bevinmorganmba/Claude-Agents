"""Agent turns.

A manual tool-use loop rather than the SDK's beta tool runner, for one
reason: every tool call has to pass through the broker, and the broker's
verdict — not the model's intent — decides what happens. Owning the loop
keeps that boundary obvious in the code.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import config, tools
from .broker import Broker

ROSTER = {
    "olivia": "routes incoming messages; answers nothing herself",
    "holt": "calendars, availability, what's on today, when a meeting can land",
    "bailey": "what to work on right now, given the time and energy you have",
    "danbury": "whether an idea, task or opportunity fits the five-year plan",
    "barbara": "the shared context repo, the Kanban board, and Drive structure",
}

# Applied to every agent. Opus 5 and Sonnet 5 both default to longer replies
# than a phone conversation wants, so the brevity instruction is explicit.
HOUSE_STYLE = """
## How you talk

You are answering on WhatsApp, on a phone. Lead with the answer in the first
sentence — the thing Bevin would ask for if he said "just give me the short
version". Supporting detail comes after, only if it changes what he does next.
No preamble, no restating the question, no sign-off.

Plain sentences. No markdown headers, no bullet lists unless you are genuinely
enumerating three or more things. Times as "2:30", days as "Thursday".

If you don't have what you need, say which tool call failed or which fact is
missing. Never invent a calendar entry, a task, or a document.

## What you may do

You have no credentials. Every tool call goes through the broker, which
enforces policy independently of anything in this prompt. If the broker denies
something, that is final: say so plainly and do not try a different phrasing of
the same action. If the broker queues something for approval, tell Bevin what
you want to do and give him the code.
""".strip()


def _charter(agent: str) -> str:
    path = config.CONTEXT / "agents" / agent / "charter.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _memory(agent: str) -> str:
    path = config.CONTEXT / "agents" / agent / "memory.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def system_prompt(agent: str) -> str:
    now = datetime.now(config.TZ)
    others = "\n".join(f"- {n}: {d}" for n, d in ROSTER.items() if n != agent)
    return f"""{_charter(agent)}

{HOUSE_STYLE}

## Right now

It is {now:%A %-d %B %Y, %-I:%M %p} ({config.TZ}). Bevin's working day runs
{config.WORKDAY_START}–{config.WORKDAY_END}. Day offset 0 means today.

## The rest of the fleet

{others}

If a question belongs to another agent, say whose it is in one line rather
than answering outside your scope.

## Your standing notes

{_memory(agent) or "(nothing recorded yet)"}"""


def tool_defs(agent: str, policy) -> list[dict]:
    names = policy.tools_for(agent)
    out = []
    for name in names:
        spec = tools.REGISTRY.get(name)
        if spec:
            out.append({"name": spec.name.replace(".", "__"),
                        "description": spec.description,
                        "input_schema": spec.schema})
    return out


def _request_kwargs(agent: str) -> dict[str, Any]:
    """Haiku 4.5 predates adaptive thinking and the effort parameter; sending
    either returns a 400. Sonnet 5 and Opus 5 take both."""
    model = config.MODELS[agent]
    kwargs: dict[str, Any] = {"model": model, "max_tokens": 8000}
    if model in ("claude-sonnet-5", "claude-opus-5"):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": config.EFFORT[agent]}
        if agent == "danbury":
            kwargs["max_tokens"] = 16000
    return kwargs


class Fleet:
    def __init__(self, broker: Broker | None = None):
        self.broker = broker or Broker()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise SystemExit(
                    "The anthropic SDK is not installed. Run:\n"
                    "    pip install -r requirements.txt\n"
                    "Everything except `chat` works without it — try `hermes drill`."
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------ routing

    def route(self, text: str) -> tuple[str, str]:
        """Olivia. Returns (agent, restated task). Cheap model, no tools, no
        credentials — she is the only component exposed to the outside world
        and she holds nothing."""
        schema = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": list(k for k in ROSTER if k != "olivia")},
                "task": {"type": "string"},
            },
            "required": ["agent", "task"],
            "additionalProperties": False,
        }
        roster = "\n".join(f"- {n}: {d}" for n, d in ROSTER.items() if n != "olivia")
        resp = self.client.messages.create(
            model=config.MODELS["olivia"],
            max_tokens=400,
            system=(
                "You are Olivia, the switchboard for a small fleet of assistants. "
                "Pick exactly one agent to handle the message and restate the task "
                "in one clear sentence for them. You never answer questions yourself "
                "and you have no tools.\n\n" + roster
            ),
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": text}],
        )
        payload = json.loads(next(b.text for b in resp.content if b.type == "text"))
        return payload["agent"], payload["task"]

    # ---------------------------------------------------------- agent turn

    def ask(self, agent: str, text: str, history: list | None = None,
            on_tool=None) -> tuple[str, list]:
        """Run one agent turn to completion. Returns (reply, new history)."""
        messages = list(history or []) + [{"role": "user", "content": text}]
        defs = tool_defs(agent, self.broker.policy)
        kwargs = _request_kwargs(agent)

        for _ in range(12):  # generous ceiling; a turn that needs more is stuck
            resp = self.client.messages.create(
                system=system_prompt(agent),
                messages=messages,
                tools=defs,
                **kwargs,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "refusal":
                return "(the model declined to answer that)", messages
            if resp.stop_reason != "tool_use":
                text_out = "\n".join(b.text for b in resp.content if b.type == "text").strip()
                return text_out or "(no reply)", messages

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                tool_name = block.name.replace("__", ".")
                args = dict(block.input)
                if on_tool:
                    on_tool(agent, tool_name, args)
                outcome = self.broker.execute(agent, tool_name, args)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.text,
                    "is_error": outcome.status == "denied",
                })
            messages.append({"role": "user", "content": results})

        return "(gave up after 12 tool rounds)", messages
