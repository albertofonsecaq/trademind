"""
The changelog orders snapshots by instant — #35, for #8.

A card's version history lives in JSONB as whatever `isoformat()` produced at
the time, so after #35 the history holds both shapes: naive strings written
before it, aware ones after. The endpoint used to sort those strings.

Measured rather than assumed, because the review that raised this — and my own
first description of it — overstated the damage. For UTC values the string order
happens to agree with the instant order almost everywhere, since the date and
time fields dominate the comparison. Two cases are real:

- the same instant in both shapes compares *unequal* as strings, so a naive and
  an aware snapshot of one moment order by punctuation;
- any snapshot carrying a non-UTC offset sorts by its wall clock instead of its
  instant, which is simply wrong.

Nothing writes a non-UTC offset today. That is the argument for fixing it rather
than against: the sort is correct by a coincidence of formatting, and the next
thing to write one of these — a different process, an import, a client-supplied
value — breaks the history with no error to notice.
"""
from datetime import datetime, timezone

from app.api.changelog import _as_instant


class TestReadingASnapshotTimestamp:
    def test_an_aware_string_keeps_its_instant(self):
        assert _as_instant("2026-10-17T12:00:00.123456+00:00") == datetime(
            2026, 10, 17, 12, 0, 0, 123456, tzinfo=timezone.utc
        )

    def test_a_naive_string_is_read_as_utc(self):
        """What it meant when it was written, before #35 (ADR 1)."""
        assert _as_instant("2026-10-17T12:00:00.123456") == datetime(
            2026, 10, 17, 12, 0, 0, 123456, tzinfo=timezone.utc
        )

    def test_both_shapes_of_one_moment_are_equal(self):
        assert _as_instant("2026-10-17T12:00:00.123456") == _as_instant(
            "2026-10-17T12:00:00.123456+00:00"
        )

    def test_a_non_utc_offset_is_converted_not_truncated(self):
        assert _as_instant("2026-10-17T08:00:00-05:00") == datetime(
            2026, 10, 17, 13, 0, tzinfo=timezone.utc
        )

    def test_an_unreadable_snapshot_sorts_last_instead_of_failing_the_read(self):
        """A malformed snapshot is a reason to inspect data, not to 500 a page."""
        assert _as_instant("not a timestamp") < _as_instant("2000-01-01T00:00:00")
        assert _as_instant("") < _as_instant("2000-01-01T00:00:00")
        assert _as_instant(None) < _as_instant("2000-01-01T00:00:00")


class TestWhereTheStringSortWasWrong:
    """The two cases that justify the change, each pinned."""

    def _newest_by_string(self, *values):
        return sorted(values, reverse=True)[0]

    def _newest_by_instant(self, *values):
        return max(values, key=_as_instant)

    def test_a_non_utc_offset_sorted_by_its_wall_clock(self):
        earlier_wall_clock_but_later_instant = "2026-10-17T08:00:00-05:00"   # 13:00Z
        later_wall_clock_but_earlier_instant = "2026-10-17T12:30:00+00:00"

        assert self._newest_by_string(
            earlier_wall_clock_but_later_instant, later_wall_clock_but_earlier_instant
        ) == later_wall_clock_but_earlier_instant        # wrong

        assert self._newest_by_instant(
            earlier_wall_clock_but_later_instant, later_wall_clock_but_earlier_instant
        ) == earlier_wall_clock_but_later_instant        # right

    def test_one_moment_in_two_shapes_is_not_equal_as_a_string(self):
        naive, aware = "2026-10-17T12:00:00.123456", "2026-10-17T12:00:00.123456+00:00"

        assert naive != aware                            # so the string sort picks one
        assert _as_instant(naive) == _as_instant(aware)  # though they are one instant
