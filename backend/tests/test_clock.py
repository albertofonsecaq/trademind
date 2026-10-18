"""
One source of "now" — #33, for #8.

There were 29 `datetime.utcnow()` calls across 12 files. Each returned a naive
value, which is half of #8: the columns are `timestamptz`, so a naive now is
only correct by accident of the session's time zone. `utcnow()` is also
deprecated in 3.12, so every one of them was a future warning too.

The property that matters is not the wording of the return type — it is that a
value from here can be compared against anything else aware without raising,
because that comparison is what silently breaks today.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import now_utc


class TestNowUtc:
    def test_is_aware(self):
        assert now_utc().tzinfo is not None

    def test_is_utc(self):
        assert now_utc().utcoffset() == timedelta(0)

    def test_is_actually_now(self):
        """A clock that is aware but wrong would pass every other test here."""
        reference = datetime.now(timezone.utc)

        assert abs(now_utc() - reference) < timedelta(seconds=5)

    def test_compares_with_other_aware_values(self):
        """
        The #8 failure in one assertion: a naive now cannot be compared with a
        value read back from a timestamptz column.
        """
        from_the_database = datetime(2026, 1, 22, 14, 30, tzinfo=timezone.utc)

        assert now_utc() > from_the_database        # must not raise

    def test_a_naive_now_is_what_this_replaces(self):
        """
        Pins why the helper exists: the thing it replaces raises against an
        aware value. If this ever stops raising, Python changed, not us.
        """
        naive_now = datetime.utcnow()
        from_the_database = datetime(2026, 1, 22, 14, 30, tzinfo=timezone.utc)

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            naive_now > from_the_database

    def test_keeps_sub_second_precision(self):
        """Timestamps are compared for ordering, so whole seconds are not enough."""
        first = now_utc()
        second = now_utc()

        assert first.microsecond or second.microsecond
