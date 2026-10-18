"""
One source of "now" — #33, for #8.

There were 29 `datetime.utcnow()` calls across 12 files. Each returned a naive
value, which is half of #8: the columns are `timestamptz`, so a naive now is
only correct by accident of the session's time zone. `utcnow()` is also
deprecated in 3.12, so every one of them was a future warning too.

The property that matters is not the wording of the return type — it is that a
value from here can be compared against anything else aware without raising,
because that comparison is what silently breaks today: a naive now raises
`TypeError: can't compare offset-naive and offset-aware datetimes` against
anything read out of a `timestamptz` column.

Kept deliberately short. Asserting that `datetime.now(timezone.utc)` is aware
would be testing CPython, and the invariant worth having — that nothing in the
codebase produces a naive now any more — is #37's, where it can be asserted
across the whole tree rather than one function.
"""
from datetime import datetime, timedelta, timezone

from app.core.clock import now_utc


class TestNowUtc:
    def test_is_aware(self):
        assert now_utc().tzinfo is not None

    def test_is_utc(self):
        assert now_utc().utcoffset() == timedelta(0)

    def test_compares_with_other_aware_values(self):
        """
        The #8 failure in one assertion: a naive now cannot be compared with a
        value read back from a timestamptz column.
        """
        from_the_database = datetime(2026, 1, 22, 14, 30, tzinfo=timezone.utc)

        assert now_utc() > from_the_database        # must not raise
