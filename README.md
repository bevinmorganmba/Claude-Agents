# Hermes fleet — Phase 0

Five task-specific agents sharing one Markdown brain, behind a broker that
enforces permissions in code. Runs entirely on your machine against fixture
data: **no network, no OAuth, no VPS, nothing purchased or connected.**

The point of Phase 0 is to watch the agents behave and prove the guardrails
hold *before* any credential exists.

## Run it

```bash
python3 -m hermes doctor    # check the setup
python3 -m hermes drill     # prove the policy engine — no API key needed
python3 -m unittest discover -s tests
```

Those three need nothing installed but PyYAML. To actually talk to the fleet:

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m hermes chat
```

```
you  what's on today?
       olivia → holt: list today's calendar commitments
       holt → calendar.list_events({"day_offset": 0})

Holt  Five things today. Gym at 8, Tardus standup at 10, lunch with
           Marcus 12:30 in person at Sweet Maple, pipeline review at 2,
           school pickup at 4. Your only real work block is 10:30–12:30.
```

Other commands: `hermes board`, `hermes pending`, `hermes log`,
`hermes call <agent> <tool> k=v`, `hermes approve <code>`.

## The roster

| Agent | Owns | Model | Tools |
|---|---|---|---|
| **Olivia** | Routing only | Haiku 4.5 | **none, by design** |
| **Holt** | Calendars, availability | Sonnet 5 | 5 read + 1 approval-gated |
| **Bailey** | What to work on now | Sonnet 5 | 4, local board only |
| **Danbury** | Fit against the five-year plan | Opus 5 | 3 read, never acts |
| **Barbara** | Context repo, board, Drive structure | Sonnet 5 | 5 + 1 approval-gated |

Each agent's personality, scope, and refusals live in
`context/agents/<name>/charter.md`. Those files are the spec — edit them and
behaviour changes.

## How permission actually works

Agents hold no credentials and never call a tool. They ask the **broker** to
perform a named action; the broker checks agent × tool × arguments against
`policy.yml`, records the decision, and only then executes.

```
1. deny rules       →  refused, terminal, not overridable by any approval
2. agent allow list →  absent means refused (default deny)
3. agent approve    →  queued for you, with a code
4. otherwise        →  allowed
```

Your five hard rules are in `policy.yml` and cannot be unlocked by approving
anything: no external email, no deletes, nothing under legal/financial/tax/
contracts, no Substack publishing, and no mail scopes at all in this phase.

`hermes drill` demonstrates all of it in one screen, including that the router
holds nothing and that approvals are re-checked against policy at redemption.

## Approvals

A write queues instead of executing. You get a three-character code, valid
once, for fifteen minutes, bound to a hash of that exact action — approving
"create the 9:30 Halcyon call" cannot be replayed to create anything else.

```
you  holt, book 30 min with Halcyon tomorrow at 9:30

Holt  I've queued a 30-minute "Halcyon follow-up" for Wednesday 9:30.
           Reply 'yes bgp' to confirm — nothing has happened yet.

you  yes bgp
```

## Audit

Every decision — allowed, denied, queued, rejected — appends to
`var/audit.jsonl`, hash-chained. `hermes log --verify` walks the chain and
names the first entry that was edited or removed.

## What is fake, and what is real

Real and shipping unchanged to the VPS: the broker, policy engine, approval
flow, audit chain, context repo, charters, agent loop.

Fixtures: Drive metadata, and calendars *until* you run Phase 1 — see
docs/PHASE1.md. The bus is a directory instead
of WhatsApp — Phase 2 swaps `hermes/bus.py` and nothing else moves.

## Before Phase 1

The scaffolds in `context/identity/` are marked TODO on purpose — Danbury
reasons against `five-year-plan.md`, so her answers are only as good as what
you put there. Those three files are the highest-value thing you can spend
thirty minutes on.

Still open from the proposal: whether connecting the Tardus Outlook tenant is
permitted (Holt runs Google + Calendly only until it is), and the actual
folder names you consider off-limits so they can go in the deny list.

## Layout

```
hermes/           broker, policy, approvals, audit, bus, tools, agents, cli
policy.yml        the permission rules — the file that decides
context/          the shared Markdown brain (identity, charters, board)
fixtures/         stand-in calendar and Drive data
tests/            22 guardrail tests, stdlib unittest, no install needed
var/              audit log, approvals, bus messages (gitignored)
```
