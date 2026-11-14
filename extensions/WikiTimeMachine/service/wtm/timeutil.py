"""UTC time parsing. Calendar dates without a time mean END of that day (UTC)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TimeInputError(ValueError):
    pass


def parse_utc(value: str, *, date_only_end_of_day: bool = True) -> tuple[datetime, bool]:
    """Return (naive UTC datetime with microseconds, was_date_only).

    Accepts 'YYYY-MM-DD' (=> 23:59:59.999999 UTC), 'YYYY-MM-DDTHH:MM[:SS[.ffffff]]' with optional
    'Z' or '+00:00'. Non-UTC offsets are converted to UTC.
    """
    if value is None:
        raise TimeInputError("missing time")
    v = value.strip()
    if _DATE.match(v):
        d = datetime.strptime(v, "%Y-%m-%d")
        if date_only_end_of_day:
            d = d + timedelta(days=1) - timedelta(microseconds=1)
        return d, True
    v2 = v.replace(" ", "T")
    if v2.endswith("Z") or v2.endswith("z"):
        v2 = v2[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(v2)
    except ValueError as exc:
        raise TimeInputError(f"invalid UTC time: {value!r}") from exc
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    if d.year < 2001 or d.year > 2100:
        raise TimeInputError("time out of supported range")
    return d, False


def parse_api_ts(value: str) -> datetime:
    """MediaWiki API timestamp '2023-04-16T12:02:41Z' -> naive UTC."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.microsecond:
        return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def api_ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")
