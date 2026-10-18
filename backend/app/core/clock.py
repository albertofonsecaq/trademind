"""
The current instant, in one place.

Every timestamp column in this schema is `timestamptz`, so a value written to
one should carry its time zone. `datetime.utcnow()` does not: it returns a naive
value that only lands correctly because the database session reads naive input
as UTC, and it cannot be compared with anything read back out of such a column
without raising. It is also deprecated in 3.12.

So the codebase asks here instead. See docs/adr/0001-timestamps-are-aware-utc.md
for the decision and what it rules out.
"""
from datetime import datetime, timezone


def now_utc() -> datetime:
    """The current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """
    Read a datetime as aware UTC, assuming UTC when it carries no offset.

    This exists for one boundary: a client may send `2026-01-22T00:00:00` with
    no offset, and Pydantic parses that as naive. Writing a naive value to one
    of our `timestamptz` columns does **not** mean UTC — the driver reads it in
    the machine's local time zone, so the same payload would store a different
    instant depending on where the server runs. It silently meant UTC before
    the mapping declared itself (ADR 1), and this keeps that meaning rather than
    inheriting a deployment detail.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
