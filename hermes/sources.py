"""Calendar sources.

Phase 1 swaps fixtures for real read-only calendars without changing a single
tool signature. Everything above this module — Holt, the broker, the policy —
is unchanged; only where the events come from moves.

Each source declares its own staleness. Bevin's Tardus calendar reaches Google
as a subscribed feed, and Google polls external feeds on its own slow
schedule, so events booked in Outlook today may not be visible for hours. An
availability agent that doesn't know that will state a free slot with more
confidence than the data supports, which is the fastest way to lose trust in
it. The lag travels with the events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

import yaml

from . import config


@dataclass
class Source:
    """Where events came from, and how much to trust their freshness."""
    label: str
    kind: str               # "fixture" | "google" | "calendly"
    lag_minutes: int = 0    # 0 means live

    @property
    def stale(self) -> bool:
        return self.lag_minutes > 0

    def caveat(self) -> str:
        hours = self.lag_minutes / 60
        window = f"{hours:.0f}h" if hours >= 1 else f"{self.lag_minutes}min"
        return (f"{self.label} syncs on a delay of up to {window} — anything "
                f"booked there very recently may not be here yet")


class Provider(Protocol):
    sources: list[Source]

    def fetch(self, day_from: int, day_to: int) -> list[dict]: ...


# ----------------------------------------------------------------- helpers


def _today() -> date:
    return datetime.now(config.TZ).date()


def _at(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=config.TZ)


def _event(source: Source, day: date, start: datetime, end: datetime, title: str,
           location: str = "", in_person: bool = False, external: bool = False) -> dict:
    return {
        "source": source.label,
        "lag_minutes": source.lag_minutes,
        "day_offset": (day - _today()).days,
        "date": day.isoformat(),
        "start": start,
        "end": end,
        "title": title,
        "location": location,
        "in_person": in_person,
        "external": external,
    }


# ---------------------------------------------------------------- fixtures


@dataclass
class FixtureProvider:
    """Phase 0 behaviour, kept as the fallback so the fleet runs with no
    credentials at all — `drill`, the tests, and a fresh clone all work."""

    sources: list[Source] = field(default_factory=list)

    def __post_init__(self):
        self.sources = [Source("fixtures", "fixture", 0)]

    def fetch(self, day_from: int, day_to: int) -> list[dict]:
        out: list[dict] = []
        for fname in ("calendar_google.json", "calendar_outlook.json", "calendly.json"):
            path = config.FIXTURES / fname
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            src = Source(doc["source"], "fixture", 0)
            for ev in doc["events"]:
                off = ev["day_offset"]
                if not (day_from <= off <= day_to):
                    continue
                day = _today() + timedelta(days=off)
                out.append(_event(
                    src, day, _at(day, ev["start"]), _at(day, ev["end"]), ev["title"],
                    ev.get("location", ""), bool(ev.get("in_person")), bool(ev.get("external")),
                ))
        return out


# ------------------------------------------------------------------ google


def _parse_google_dt(node: dict) -> tuple[datetime, bool]:
    """Returns (datetime, is_all_day). All-day events carry `date`, timed ones
    carry `dateTime`."""
    if "dateTime" in node:
        return datetime.fromisoformat(node["dateTime"]).astimezone(config.TZ), False
    day = date.fromisoformat(node["date"])
    return datetime.combine(day, time(0, 0), tzinfo=config.TZ), True


@dataclass
class GoogleProvider:
    """Read-only Google Calendar. Reads every calendar named in sources.yml,
    including the subscribed feed that carries the Tardus/Outlook events."""

    calendars: list[dict]
    own_domains: tuple[str, ...]
    sources: list[Source] = field(default_factory=list)

    def __post_init__(self):
        self.sources = [
            Source(c.get("label", c["id"]), "google", int(c.get("lag_minutes", 0)))
            for c in self.calendars
        ]

    def _service(self):
        from googleapiclient.discovery import build  # imported lazily
        from .auth import google_credentials
        return build("calendar", "v3", credentials=google_credentials(), cache_discovery=False)

    def fetch(self, day_from: int, day_to: int) -> list[dict]:
        service = self._service()
        start = _at(_today() + timedelta(days=day_from), "00:00")
        end = _at(_today() + timedelta(days=day_to), "00:00") + timedelta(days=1)
        out: list[dict] = []

        for cal, src in zip(self.calendars, self.sources):
            resp = service.events().list(
                calendarId=cal["id"],
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,          # expand recurrences into instances
                orderBy="startTime",
                maxResults=250,
            ).execute()

            for item in resp.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                # Skip events Bevin has declined — they are not commitments.
                mine = next((a for a in item.get("attendees", []) if a.get("self")), None)
                if mine and mine.get("responseStatus") == "declined":
                    continue

                begin, all_day = _parse_google_dt(item["start"])
                finish, _ = _parse_google_dt(item["end"])
                if all_day:
                    # An all-day event is not a busy block; treat it as context
                    # rather than something that blocks a 2pm call.
                    continue

                location = item.get("location", "") or ""
                # Heuristic, and worth stating: a physical location with no
                # video link reads as in-person, which is what triggers the
                # travel buffer. Wrong either way is visible and correctable.
                in_person = bool(location) and not item.get("conferenceData")
                external = any(
                    not a.get("self")
                    and not str(a.get("email", "")).lower().endswith(self.own_domains)
                    for a in item.get("attendees", [])
                )
                out.append(_event(
                    src, begin.date(), begin, finish,
                    item.get("summary", "(no title)"), location, in_person, external,
                ))
        return out


# ---------------------------------------------------------------- calendly


@dataclass
class CalendlyProvider:
    """Scheduled events from Calendly. Read-only personal access token."""

    sources: list[Source] = field(default_factory=list)

    def __post_init__(self):
        self.sources = [Source("calendly", "calendly", 0)]

    def fetch(self, day_from: int, day_to: int) -> list[dict]:
        import requests  # imported lazily so the fixture path needs no deps
        from .auth import calendly_token

        token = calendly_token()
        headers = {"Authorization": f"Bearer {token}"}
        me = requests.get("https://api.calendly.com/users/me",
                          headers=headers, timeout=20)
        me.raise_for_status()
        uri = me.json()["resource"]["uri"]

        start = _at(_today() + timedelta(days=day_from), "00:00")
        end = _at(_today() + timedelta(days=day_to), "00:00") + timedelta(days=1)
        resp = requests.get(
            "https://api.calendly.com/scheduled_events",
            headers=headers,
            params={"user": uri, "min_start_time": start.isoformat(),
                    "max_start_time": end.isoformat(), "count": 100},
            timeout=20,
        )
        resp.raise_for_status()

        src = self.sources[0]
        out = []
        for ev in resp.json().get("collection", []):
            if ev.get("status") != "active":
                continue
            begin = datetime.fromisoformat(ev["start_time"]).astimezone(config.TZ)
            finish = datetime.fromisoformat(ev["end_time"]).astimezone(config.TZ)
            loc = (ev.get("location") or {}).get("location") or ""
            out.append(_event(
                src, begin.date(), begin, finish, ev.get("name", "Calendly booking"),
                str(loc), False, True,   # a Calendly booking is external by definition
            ))
        return out


# ---------------------------------------------------------------- assembly


class Fleet:
    """All configured providers, with graceful degradation: a source that
    fails is reported rather than silently dropped, because a missing calendar
    turns a wrong answer into a confident one."""

    def __init__(self, providers: list[Provider], errors: list[str] | None = None):
        self.providers = providers
        self.errors = errors or []

    @property
    def sources(self) -> list[Source]:
        return [s for p in self.providers for s in p.sources]

    def fetch(self, day_from: int, day_to: int) -> tuple[list[dict], list[str]]:
        events: list[dict] = []
        problems = list(self.errors)
        for provider in self.providers:
            try:
                events.extend(provider.fetch(day_from, day_to))
            except Exception as exc:
                labels = ", ".join(s.label for s in provider.sources)
                problems.append(f"{labels} unavailable ({type(exc).__name__}: {exc})")
        return sorted(events, key=lambda e: e["start"]), problems


def _sources_config() -> dict[str, Any]:
    if not config.SOURCES_FILE.exists():
        return {}
    return yaml.safe_load(config.SOURCES_FILE.read_text(encoding="utf-8")) or {}


def load() -> Fleet:
    """Build the provider list from secrets/sources.yml. With no config, or
    no credentials, this returns the fixture provider — so a fresh clone runs
    and the guardrail tests never depend on network access."""
    doc = _sources_config()
    providers: list[Provider] = []
    errors: list[str] = []

    google = doc.get("google") or {}
    if google.get("enabled") and google.get("calendars"):
        if config.GOOGLE_TOKEN.exists():
            providers.append(GoogleProvider(
                calendars=google["calendars"],
                own_domains=tuple(doc.get("own_domains", [])),
            ))
        else:
            errors.append("Google Calendar configured but not authorised — run `hermes auth google`")

    calendly = doc.get("calendly") or {}
    if calendly.get("enabled"):
        if config.CALENDLY_TOKEN.exists():
            providers.append(CalendlyProvider())
        else:
            errors.append("Calendly configured but no token — run `hermes auth calendly`")

    if not providers:
        return Fleet([FixtureProvider()], errors)
    return Fleet(providers, errors)
