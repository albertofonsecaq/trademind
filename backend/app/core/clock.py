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
