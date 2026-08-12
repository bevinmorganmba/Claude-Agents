# Phase 1 — connect your real calendars

Read-only. On your laptop. Nothing deployed, nothing exposed, no VPS.

When this is done, Holt answers about your actual week instead of fixtures.
Everything else — the broker, the policy, the approval flow — is unchanged.

Roughly 45 minutes, most of it clicking through Google Cloud.

---

## 0. Install

```bash
git clone https://github.com/bevinmorganmba/Claude-Agents.git
cd Claude-Agents
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # console.anthropic.com
python3 -m hermes doctor
```

---

## 1. Google Cloud — create an OAuth client

Google's console UI moves around; the names below are right as of writing but
trust the shape of the task over the exact labels.

1. **console.cloud.google.com** → create a new project. Call it anything.
2. **APIs & Services → Library** → search *Google Calendar API* → **Enable**.
3. **APIs & Services → OAuth consent screen**
   - User type: **External** (unless your Google account is Workspace, in which
     case Internal is simpler)
   - App name, your email for support and developer contact. Nothing else matters.
   - **Scopes:** add `.../auth/calendar.readonly`. Only that one.
   - **Test users:** add your own Gmail address.
4. **⚠️ Then set publishing status to "In production."** This is the step that
   looks wrong and isn't. While the app sits in *Testing*, Google expires your
   refresh token **every 7 days** and you re-authorise weekly forever. Published
   apps keep their tokens. You'll see an "unverified app" warning the first time
   you authorise — click through it. Verification only matters if you're serving
   strangers, and you're serving one person.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - **Download JSON**, save it as `secrets/google_client_secret.json`

```bash
mkdir -p secrets
mv ~/Downloads/client_secret_*.json secrets/google_client_secret.json
```

`secrets/` is gitignored. Tokens are written 0600.

---

## 2. Authorise

```bash
python3 -m hermes auth google
```

A browser opens. Pick your account, click past the unverified warning, allow
read-only calendar access. That's the last time you'll do it.

---

## 3. Find your calendars

```bash
python3 -m hermes calendars
```

You'll get something like:

```
  primary                Bevin Morgan
                         bevin.morgan.mba@gmail.com

  subscribed?            Tardus
                         abc123...@import.calendar.google.com
```

**The one marked `subscribed?` is your Outlook feed** — the calendar you don't
own. Note both IDs.

---

## 4. Write `secrets/sources.yml`

Copy `docs/sources.example.yml` to `secrets/sources.yml` and fill in your IDs:

```yaml
own_domains: ["@bevinmorgan.com", "bevin.morgan.mba@gmail.com"]

google:
  enabled: true
  calendars:
    - id: bevin.morgan.mba@gmail.com
      label: personal
      lag_minutes: 0

    - id: abc123...@import.calendar.google.com
      label: Tardus (Outlook subscription)
      lag_minutes: 1440        # see step 6 — measure this

calendly:
  enabled: true
```

`own_domains` is how an attendee gets classified as external, which affects how
hard Holt thinks a meeting is to move.

---

## 5. Calendly

Calendly → **Integrations → API & webhooks → Personal access tokens** →
generate one, read scope.

```bash
python3 -m hermes auth calendly      # paste when prompted, input is hidden
```

Check everything landed:

```bash
python3 -m hermes auth status
python3 -m hermes sources
```

You want to see your real calendars listed, not `fixture`.

---

## 6. Measure the lag — don't skip this

Your Tardus events reach Google as a subscribed feed, and **Google polls
external feeds on its own schedule.** Often 8–24 hours. You cannot force it.

This matters more than it sounds. If Holt says Thursday afternoon is clear
while a Tardus meeting landed there four hours ago, you stop trusting him — and
an agent you half-trust is worse than no agent.

**The test:** put a dummy event in Outlook right now, at some odd time next
week. Check Google in an hour, tonight, and tomorrow morning. Whenever it
appears is your real lag. Round up and put it in `lag_minutes`.

Holt then says so on his own:

```
Thursday 14 August — 3 commitments:
  10:00–10:30  Tardus standup (Tardus (Outlook subscription))
  ...
  [!] Tardus (Outlook subscription) syncs on a delay of up to 24h — anything
      booked there very recently may not be here yet
```

Set `lag_minutes: 0` only if you genuinely measured it as near-instant.

---

## 7. Talk to them

```bash
python3 -m hermes chat
```

```
you  what's on today?
you  holt, when could I do a 45 minute call this week?
you  holt, am I free Thursday at 2 for an hour, in person?
you  bailey, I've got 40 minutes
you  danbury, Farm School wants another workshop — worth it?
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `hermes sources` shows `fixture` | `secrets/sources.yml` missing, or `enabled: false` |
| Re-authorising every week | Publishing status still *Testing* — see step 1.4 |
| `insufficient authentication scopes` | Scope changed after the token was issued. Delete `secrets/google_token.json` and re-run `hermes auth google` |
| A calendar is missing | Not listed in `sources.yml`. `hermes calendars` shows everything available |
| Outlook events are stale | Expected — that's step 6. Confirm `lag_minutes` is set |
| Everything 401s | Token expired or revoked. Delete `secrets/google_token.json`, re-authorise |

---

## What you have and haven't given it

**Granted:** read your calendars. Read your Calendly bookings.

**Not granted:** creating, moving or deleting anything; email of any kind;
Drive; anything on the Tardus tenant directly. Calendar writes still queue for
approval and are still refused in Phase 1 — the gate is visible now so it isn't
a surprise in Phase 4.

Revoke everything by deleting `secrets/`. Nothing else holds state.
