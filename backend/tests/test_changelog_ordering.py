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

from app.core.clock import instant_from_iso, with_aware_snapshot_times


class TestReadingASnapshotTimestamp:
    def test_an_aware_string_keeps_its_instant(self):
        assert instant_from_iso("2026-10-17T12:00:00.123456+00:00") == datetime(
            2026, 10, 17, 12, 0, 0, 123456, tzinfo=timezone.utc
        )

    def test_a_naive_string_is_read_as_utc(self):
        """What it meant when it was written, before #35 (ADR 1)."""
        assert instant_from_iso("2026-10-17T12:00:00.123456") == datetime(
            2026, 10, 17, 12, 0, 0, 123456, tzinfo=timezone.utc
        )

    def test_both_shapes_of_one_moment_are_equal(self):
        assert instant_from_iso("2026-10-17T12:00:00.123456") == instant_from_iso(
            "2026-10-17T12:00:00.123456+00:00"
        )

    def test_a_non_utc_offset_is_converted_not_truncated(self):
        assert instant_from_iso("2026-10-17T08:00:00-05:00") == datetime(
            2026, 10, 17, 13, 0, tzinfo=timezone.utc
        )

    def test_an_unreadable_snapshot_reads_as_nothing(self):
        """
        None, not a guess. The endpoint decides what that means — it sorts such
        a row last rather than dropping it or failing the page, which is a
        reason to inspect the data rather than to 500.
        """
        assert instant_from_iso("not a timestamp") is None
        assert instant_from_iso("") is None
        assert instant_from_iso(None) is None


class TestWhereTheStringSortWasWrong:
    """The two cases that justify the change, each pinned."""

    def _newest_by_string(self, *values):
        return sorted(values, reverse=True)[0]

    def _newest_by_instant(self, *values):
        return max(values, key=instant_from_iso)

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
        assert instant_from_iso(naive) == instant_from_iso(aware)  # though they are one instant


class TestWhatAClientSees:
    """
    The half the changelog fix did not cover. A card's version history also
    ships raw from the card endpoint, and JavaScript reads an offset-less ISO
    string as *local time* while one with an offset is UTC — so a page rendering
    both would show two snapshots of the same moment up to a day apart.
    """

    def test_every_snapshot_goes_out_with_an_offset(self):
        history = [
            {"version": 1, "snapshot_at": "2026-10-17T12:00:00.123456"},        # pre-#35
            {"version": 2, "snapshot_at": "2026-10-17T13:00:00.123456+00:00"},  # post-#35
        ]

        served = with_aware_snapshot_times(history)

        assert [s["snapshot_at"] for s in served] == [
            "2026-10-17T12:00:00.123456+00:00",
            "2026-10-17T13:00:00.123456+00:00",
        ]

    def test_the_naive_one_is_read_as_utc_not_shifted(self):
        served = with_aware_snapshot_times([{"snapshot_at": "2026-10-17T12:00:00"}])

        assert instant_from_iso(served[0]["snapshot_at"]) == instant_from_iso(
            "2026-10-17T12:00:00+00:00"
        )

    def test_everything_else_in_the_snapshot_is_untouched(self):
        served = with_aware_snapshot_times(
            [{"version": 3, "changes_en": "tier up", "snapshot_at": "2026-10-17T12:00:00"}]
        )

        assert served[0]["version"] == 3 and served[0]["changes_en"] == "tier up"

    def test_an_unreadable_timestamp_is_left_alone_rather_than_blanked(self):
        """Data to look at, not to hide."""
        served = with_aware_snapshot_times([{"snapshot_at": "sometime last Tuesday"}])

        assert served[0]["snapshot_at"] == "sometime last Tuesday"

    def test_a_history_that_is_not_a_list_of_dicts_does_not_break_the_page(self):
        assert with_aware_snapshot_times(None) == []
        assert with_aware_snapshot_times(["junk"]) == ["junk"]
