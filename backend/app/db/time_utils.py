"""UTC helpers shared by API serializers and persistence code."""

from __future__ import annotations

import datetime


def utc_now() -> datetime.datetime:
    """Return timezone-aware UTC now; SQLite stores the naive UTC value."""
    return datetime.datetime.now(datetime.timezone.utc)


def to_utc_iso_z(value: datetime.datetime | None) -> str:
    """Serialize a datetime as UTC ISO-8601 with an explicit trailing Z."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    else:
        value = value.astimezone(datetime.timezone.utc)
    text = value.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text + "Z"
