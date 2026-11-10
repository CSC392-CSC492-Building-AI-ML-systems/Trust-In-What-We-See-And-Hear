"""The current time, in one place so tests can fix it."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Format a time for the database and PostgREST filters.

    Uses `Z` rather than `+00:00`: a `+` in a query string can be read as a space.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
