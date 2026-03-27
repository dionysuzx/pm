from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import urlencode


def _parse_utc_datetime(start_time: str) -> datetime:
    if not isinstance(start_time, str):
        raise TypeError("start_time must be a string")

    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _escape_ics_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("\n", "\\n")
    escaped = escaped.replace(",", "\\,")
    escaped = escaped.replace(";", "\\;")
    return escaped


def build_google_calendar_link(summary: str, start_time: str, duration_minutes: int, description: str = "") -> str:
    start_dt = _parse_utc_datetime(start_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    params = {
        "action": "TEMPLATE",
        "text": summary,
        "dates": f"{start_dt.strftime('%Y%m%dT%H%M%SZ')}/{end_dt.strftime('%Y%m%dT%H%M%SZ')}",
    }
    if description:
        params["details"] = description

    return f"https://www.google.com/calendar/render?{urlencode(params)}"


def build_occurrence_ics_artifact_path(call_series: str, start_time: str, occurrence_number: int) -> str:
    start_dt = _parse_utc_datetime(start_time)
    directory = f"{start_dt.strftime('%Y-%m-%d')}_{occurrence_number:03d}"
    return str(PurePosixPath(".github/ACDbot/artifacts") / call_series / directory / "invite.ics")


def build_raw_github_url(repo_name: str, default_branch: str, relative_path: str) -> str:
    normalized_path = relative_path.lstrip("/")
    return f"https://raw.githubusercontent.com/{repo_name}/{default_branch}/{normalized_path}"


def build_ics_content(
    summary: str,
    start_time: str,
    duration_minutes: int,
    description: str,
    issue_url: str,
    uid: str,
) -> str:
    start_dt = _parse_utc_datetime(start_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ethereum PM//ACDbot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{timestamp}",
        f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{_escape_ics_text(summary)}",
        f"DESCRIPTION:{_escape_ics_text(description)}",
        f"URL:{issue_url}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    return "\r\n".join(lines) + "\r\n"
