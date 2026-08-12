"""Credential storage for Phase 1.

Tokens live in `secrets/`, which is gitignored, owner-readable only, and never
touched by an agent — the broker holds credentials, and this is where the
broker gets them. Nothing here is importable by a tool.

Read-only scopes only. Phase 1 grants no ability to change anything.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from . import config

GOOGLE_SCOPES = [
    # Read-only, and deliberately the narrower of the two read scopes:
    # calendar.readonly rather than calendar.
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _write_private(path: Path, text: str) -> None:
    """Write owner-read-only. A token file the whole machine can read is not
    a secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def _check_private(path: Path) -> str | None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        return f"{path} is readable by other users (mode {mode:o}) — chmod 600 it"
    return None


# ------------------------------------------------------------------ google


def google_authorise() -> str:
    """One-time browser flow. Stores a refresh token; never asked again."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "Google auth needs extra packages:\n"
            "    pip install -r requirements.txt"
        ) from exc

    if not config.GOOGLE_CLIENT_SECRET.exists():
        raise SystemExit(
            f"Missing {config.GOOGLE_CLIENT_SECRET}.\n"
            "Download the OAuth client JSON from Google Cloud Console and save it "
            "there. Setup steps: docs/PHASE1.md"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.GOOGLE_CLIENT_SECRET), GOOGLE_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _write_private(config.GOOGLE_TOKEN, creds.to_json())
    return "Google Calendar authorised (read-only). Token stored in secrets/."


def google_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not config.GOOGLE_TOKEN.exists():
        raise SystemExit("Not authorised yet — run `hermes auth google`")

    creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN), GOOGLE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_private(config.GOOGLE_TOKEN, creds.to_json())
    return creds


def google_list_calendars() -> list[dict]:
    """Every calendar on the account, so the right ones can be named in
    sources.yml — including the subscribed feed carrying Outlook events."""
    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=google_credentials(),
                    cache_discovery=False)
    out = []
    page = None
    while True:
        resp = service.calendarList().list(pageToken=page).execute()
        for item in resp.get("items", []):
            out.append({
                "id": item["id"],
                "summary": item.get("summary", ""),
                "primary": bool(item.get("primary")),
                # Google does not label a calendar as "subscribed", but a
                # non-owner access role is the reliable tell for a feed you
                # subscribed to rather than one you own.
                "access": item.get("accessRole", ""),
            })
        page = resp.get("nextPageToken")
        if not page:
            break
    return out


# ---------------------------------------------------------------- calendly


def calendly_store(token: str) -> str:
    token = token.strip()
    if not token:
        raise SystemExit("Empty token.")
    _write_private(config.CALENDLY_TOKEN, json.dumps({"token": token}))
    return "Calendly token stored in secrets/."


def calendly_token() -> str:
    if not config.CALENDLY_TOKEN.exists():
        raise SystemExit("No Calendly token — run `hermes auth calendly`")
    return json.loads(config.CALENDLY_TOKEN.read_text(encoding="utf-8"))["token"]


# ------------------------------------------------------------------ status


def status() -> list[tuple[str, bool, str]]:
    rows = []
    for label, path in (("Google OAuth client", config.GOOGLE_CLIENT_SECRET),
                        ("Google token", config.GOOGLE_TOKEN),
                        ("Calendly token", config.CALENDLY_TOKEN),
                        ("sources.yml", config.SOURCES_FILE)):
        exists = path.exists()
        note = ""
        if exists and path != config.SOURCES_FILE:
            note = _check_private(path) or "0600"
        rows.append((label, exists, note))
    return rows
