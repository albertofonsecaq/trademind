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


def instant_from_iso(value: str | None) -> datetime | None:
    """
    The instant an ISO string names, read as UTC when it carries no offset.

    Timestamps that live inside JSONB are stored as whatever `isoformat()`
    produced at the time, so a history written across the #8 batches holds both
    shapes. Reading one is not the same as comparing the strings: `"...+00:00"`
    and `"...T12:00:00"` can name one instant and still differ as text, and a
    non-UTC offset sorts by its wall clock. Returns None for anything
    unparseable, so a caller decides what a malformed snapshot means.
    """
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


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


def with_aware_snapshot_times(snapshots: list | None, field: str = "snapshot_at") -> list:
    """
    Stored snapshots with their timestamp normalized to an aware ISO string.

    A version history accumulates whatever `isoformat()` produced at the time,
    so one written across the #8 batches holds both shapes. That matters on the
    way out, not only internally: JavaScript reads an offset-less ISO string as
    *local time* and one with an offset as UTC, so a page rendering both would
    show two snapshots of the same moment up to a day apart. Normalizing here
    means a client cannot tell when a row was written.

    A snapshot whose timestamp cannot be read keeps whatever it held, rather
    than being silently blanked — it is data to look at, not to hide.
    """
    normalized = []
    for snapshot in snapshots or []:
        if not isinstance(snapshot, dict):
            normalized.append(snapshot)
            continue
        instant = instant_from_iso(snapshot.get(field))
        normalized.append(
            {**snapshot, field: instant.isoformat()} if instant else snapshot
        )
    return normalized
