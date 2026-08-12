# Start here

Three steps. About ten minutes. Nothing to install.

You'll be talking to **Bailey** (what should I work on right now) and
**Danbury** (does this fit my plan) about your *actual* board and your *actual*
five-year plan. Holt needs calendar setup and can wait — skip him for now.

---

## 1. Open a terminal in your browser

On the Chromebook, go to **[shell.cloud.google.com](https://shell.cloud.google.com)**
and sign in with your Google account.

That's Google Cloud Shell. It's free, it's just a browser tab, and it already
has everything needed. Nothing gets installed on your Chromebook.

You'll see a black panel at the bottom with a blinking cursor. That's it.

> Phone only? This technically works in Chrome on the Pixel, but typing
> commands on a phone is miserable. Use the Chromebook for these ten minutes.
> Once it's set up you can go back to the phone.

---

## 2. Paste this

Copy the whole thing, paste it into the black panel, press Enter:

```bash
git clone https://github.com/bevinmorganmba/Claude-Agents.git && cd Claude-Agents && bash setup.sh
```

It'll churn for a minute, then ask for one thing: an **Anthropic API key**.

Get it from **[console.anthropic.com](https://console.anthropic.com)** →
API keys → Create key. Copy it, paste it in, press Enter. You won't see the
characters as you type — that's deliberate, it's a password.

*(This is the only thing that costs money. A conversation runs a few cents.)*

The script then runs the safety drill so you can see the rules working before
you say a word to anyone.

---

## 3. Talk to them

Setup starts the chat for you. **From then on, one command gets you back:**

```bash
./chat
```

You'll know you're in the right place when the prompt says `you`. If it says
something ending in `$`, you're still at the Cloud Shell prompt — type `./chat`
first.

Some things worth asking:

```
bailey, I have 40 minutes — what should I do?
bailey, I have 3 hours and good energy
bailey, I'm wiped. anything worth doing?

danbury, Farm School wants another workshop for $900 — worth it?
danbury, should I take on more at Tardus?
danbury, where does the options account fit right now?
```

Type `/quit` to leave. `/board` shows your tasks. Everything you type is
against your real board and real plan — those are already loaded.

Coming back later? Open shell.cloud.google.com and run:

```bash
cd Claude-Agents && ./chat
```

---

## What you're actually testing

Not whether it works. Whether **you'd take their advice.**

- Does Bailey pick the *right* one thing, or does she hedge?
- Is she too blunt? Not blunt enough?
- Does Danbury reason from *your* plan, or does she drift into generic
  business advice?
- Does she push back when she should?

Tell me what's off and I'll change it. The personalities live in plain text
files (`context/agents/<name>/charter.md`) and take minutes to adjust.

---

## If something breaks

Copy whatever red text you see and send it to me. Don't debug it.

Common ones:

| It says | Do |
|---|---|
| `bailey: command not found` | You're at the Cloud Shell prompt, not in the chat. Type `./chat` first. |
| `python3: command not found` | You're not in Cloud Shell. Go back to step 1. |
| `authentication_error` | Bad API key. Re-run `bash setup.sh` and paste it again. |
| `credit balance is too low` | Add a few dollars at console.anthropic.com → Billing. |
| Holt talks about a dentist and a gym | Expected. That's the fake calendar. See `docs/PHASE1.md` when you want the real one. |

---

## Later, only if you want it

- **`docs/PHASE1.md`** — connect your real Google Calendar so Holt is useful.
  ~30 minutes, mostly clicking through Google Cloud.
- **Phase 2** — put it on a server so it answers on WhatsApp and you stop
  opening a terminal at all. That's the version you actually want; this is how
  we find out whether it's worth building.
