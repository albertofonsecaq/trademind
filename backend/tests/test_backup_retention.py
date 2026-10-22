"""
Tests for #51 — the scheduled backup's two judgements.

The script is mostly subprocess calls to pg_dump and pg_restore, which these
tests do not touch. Two decisions inside it are worth pinning, because both fail
quietly and both are unrecoverable when they do:

- Which dumps get deleted. A retention rule that deletes the wrong file is worse
  than no retention rule, and the files it could delete are the only copies of
  months of paid ingestion.
- Whether a restored dump counts as verified. The failure this guards is a dump
  that restores "successfully" into an empty schema: pg_restore exits 0, the
  file looks fine, and it is worth nothing.

The third thing pinned here is the refusal to drop anything that is not a
throwaway, because that one is a `dropdb` away from the live database.
"""
from datetime import datetime, timedelta, timezone

import pytest

from scripts.backup import (
    MUST_NOT_BE_EMPTY,
    VERIFY_DB_PREFIX,
    _drop_throwaway,
    dump_name,
    prune_plan,
    taken_at,
    verification_verdict,
)

# 2026-10-25 is a Sunday; the dates below are counted from it on purpose.
SUNDAY = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)


def _auto(day: datetime, revision: str = "016") -> str:
    return dump_name(day, revision)


def _days(n: int) -> list[str]:
    """n daily dumps ending on the Sunday, newest last."""
    return [_auto(SUNDAY - timedelta(days=offset)) for offset in reversed(range(n))]


class TestWhatGetsKept:
    def test_a_week_of_dumps_is_left_alone(self):
        names = _days(7)

        assert prune_plan(names) == []

    def test_the_eighth_day_pushes_out_the_oldest(self):
        names = _days(9)

        doomed = prune_plan(names)

        # ...except the oldest two here include a Sunday, which the weekly half
        # of the rule keeps. 2026-10-18 is one.
        assert _auto(SUNDAY - timedelta(days=8)) in doomed
        assert _auto(SUNDAY - timedelta(days=7)) not in doomed

    def test_sundays_survive_past_the_daily_window(self):
        """
        The point of the weekly half: damage noticed after a week is exactly
        what seven dailies cannot help with.
        """
        names = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(0, 29, 7)]

        assert prune_plan(names) == []

    def test_only_four_sundays_are_kept(self):
        """
        A week of dailies plus older Sundays, so the daily window does not
        already cover every file — with seven Sundays and nothing else, all
        seven are inside the daily window and the weekly rule never bites.
        """
        names = _days(7) + [
            _auto(SUNDAY - timedelta(days=offset)) for offset in (7, 14, 21, 28, 35)
        ]

        doomed = prune_plan(names)

        # Sundays present: today and 7, 14, 21, 28, 35 days back. The newest
        # four of those are kept; the two beyond them are not.
        assert _auto(SUNDAY - timedelta(days=21)) not in doomed
        assert _auto(SUNDAY - timedelta(days=28)) in doomed
        assert _auto(SUNDAY - timedelta(days=35)) in doomed

    def test_nothing_to_do_with_too_few(self):
        assert prune_plan(_days(3)) == []


class TestWhatIsNeverTouched:
    @pytest.mark.parametrize("name", [
        "trademind-20261018-alembic016.dump",            # taken by hand
        "trademind-20261018-alembic016-pre35.dump",      # taken before a migration
        "trademind-20261018-alembic016-pre36.dump",
        "notes.txt",
        "trademind-20261018T030000Z-alembic016.dump",    # ours but for the -auto
    ])
    def test_a_dump_this_script_did_not_write(self, name):
        """
        A hand-taken dump is somebody's deliberate snapshot, usually from just
        before something risky. Age is not a reason to delete one, and the three
        in this repo are the only copies of the database before #8's batches.
        """
        names = _days(40) + [name]

        assert name not in prune_plan(names)

    def test_they_do_not_crowd_out_the_scheduled_ones_either(self):
        """A manual dump must not count towards the seven, either way."""
        names = _days(7) + ["trademind-20261018-alembic016.dump"]

        assert prune_plan(names) == []

    def test_an_unparsable_name_is_not_a_dump_of_ours(self):
        assert taken_at("trademind-nonsense-auto.dump") is None


class TestWhenARestoreCountsAsVerified:
    def test_a_good_restore(self):
        live = {"evidence_items": 8600, "usage_events": 11700, "users": 2}
        restored = {"evidence_items": 8582, "usage_events": 11630, "users": 2}

        # _rest() first: the union's right-hand side wins, and the point of
        # this test is the counts on the left.
        assert verification_verdict(_rest() | restored, live) is None

    def test_a_dump_that_restored_a_schema_and_no_data(self):
        """
        The failure this exists for: pg_restore exits 0, the file is the right
        size, and every table is empty.
        """
        live = {"evidence_items": 8600, "usage_events": 11700}
        restored = dict.fromkeys(_rest(), 1) | {"evidence_items": 0, "usage_events": 0}

        verdict = verification_verdict(restored, live)

        assert verdict is not None and "no rows" in verdict

    @pytest.mark.parametrize("table", MUST_NOT_BE_EMPTY)
    def test_either_table_being_empty_is_enough(self, table):
        live = {name: 10 for name in _rest()}
        restored = {name: 10 for name in _rest()} | {table: 0}

        assert verification_verdict(restored, live) is not None

    def test_a_missing_table(self):
        restored = {name: 10 for name in _rest()}
        del restored["strategy_cards"]

        verdict = verification_verdict(restored, {})

        assert verdict is not None and "strategy_cards" in verdict

    def test_more_rows_than_the_live_database(self):
        """Then it is a dump of something else, whatever else is true of it."""
        live = {name: 10 for name in _rest()}
        restored = {name: 10 for name in _rest()} | {"evidence_items": 99}

        verdict = verification_verdict(restored, live)

        assert verdict is not None and "not a dump of it" in verdict

    def test_ingestion_during_the_dump_is_not_a_failure(self):
        """
        The dump is a snapshot and the poller keeps working, so live is ahead of
        it by the time this runs. Demanding equal counts would fail a run every
        time ingestion did its job, and an alarm that cries wolf gets ignored —
        which costs the next real failure.
        """
        live = {name: 500 for name in _rest()}
        restored = {name: 10 for name in _rest()}

        assert verification_verdict(restored, live) is None


class TestItWillNotDropTheLiveDatabase:
    @pytest.mark.parametrize("name", ["trademind", "postgres", "trademind_verify", ""])
    def test_it_refuses_a_name_that_is_not_a_throwaway(self, name):
        """
        The one irreversible command in the script. "trademind_verify" is in
        here deliberately: it looks like a throwaway and is not one of ours, so
        the check is the prefix, not a resemblance.
        """
        with pytest.raises(RuntimeError, match="not a throwaway"):
            _drop_throwaway(name)

    def test_the_prefix_is_what_makes_one(self):
        assert VERIFY_DB_PREFIX.startswith("trademind_verify_")


def _rest() -> dict[str, int]:
    """Every counted table at a plausible count, for tests about one of them."""
    from scripts.backup import COUNTED_TABLES

    return {table: 10 for table in COUNTED_TABLES}
