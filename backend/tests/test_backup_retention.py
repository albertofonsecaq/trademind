"""
Tests for #51 — the scheduled backup's two judgements.

The script is mostly subprocess calls to pg_dump and pg_restore, which these
tests do not touch. Three decisions inside it are worth pinning, because they
fail quietly and two of them are unrecoverable when they do:

- Which dumps get deleted. A retention rule that deletes the wrong file is worse
  than no retention rule, and the files it could delete are the only copies of
  months of paid ingestion.
- Whether a restored dump counts as verified. The failure this guards is a dump
  that restores "successfully" into an empty schema: pg_restore exits 0, the
  file looks fine, and it is worth nothing.

The third is the refusal to drop anything that is not a throwaway, because that
one is a `dropdb` away from the live database. The fourth is where a second copy
goes, which fails quietly in the other direction: believing a copy is somewhere
else when it is on the same disk.
"""
from datetime import datetime, timedelta, timezone

import pytest

from pathlib import Path

from scripts.backup import (
    MUST_NOT_BE_EMPTY,
    VERIFY_DB_PREFIX,
    _drop_throwaway,
    dump_name,
    prune_plan,
    mirror_destination,
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

    def test_weeklies_survive_past_the_daily_window(self):
        """
        The point of the weekly half: damage noticed after a week is exactly
        what seven dailies cannot help with.
        """
        names = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(0, 29, 7)]

        assert prune_plan(names) == []

    def test_only_four_weeks_are_kept(self):
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


class TestMoreThanOneDumpInADay:
    """
    The script is runnable by hand, so this is not hypothetical — two dumps were
    taken on the day it was written. These are the tests the first version of
    the rule passed by never being given a day with two dumps in it.
    """

    def test_an_afternoon_of_manual_runs_does_not_eat_the_week(self):
        """
        The bug: seven runs in one afternoon counted as seven "dailies" and
        deleted every earlier day. A week of history collapsed into one day,
        which is the exact opposite of what a retention rule is for.
        """
        week = _days(7)
        afternoon = [_auto(SUNDAY + timedelta(hours=hour)) for hour in range(1, 8)]

        doomed = prune_plan(week + afternoon)
        surviving_days = {
            taken_at(name).date() for name in week + afternoon if name not in doomed
        }

        assert len(surviving_days) == 7

    def test_a_day_keeps_its_newest_dump(self):
        """Two dumps on one day is one day of history, and the later one is it."""
        first = _auto(SUNDAY)
        second = _auto(SUNDAY + timedelta(hours=6))

        doomed = prune_plan(_days(7) + [second])

        assert first in doomed and second not in doomed

    def test_the_redundant_ones_are_what_goes(self):
        """Ten runs in a day leave one file, not ten, and not none."""
        same_day = [_auto(SUNDAY + timedelta(minutes=10 * n)) for n in range(10)]

        kept = [name for name in same_day if name not in prune_plan(same_day)]

        assert len(kept) == 1


class TestWeeksNotSundays:
    """
    "7 daily" means the seven most recent days that have a dump, as the backup
    tools everyone knows mean it — not the last seven calendar days. A dump a
    week apart is still its own day, so these tests use daily dumps, which is
    the only shape where the weekly rule is doing any work.
    """

    def test_history_reaches_past_the_daily_window(self):
        """
        Six weeks of daily dumps leave ten files: the seven newest days, which
        all fall inside one ISO week, plus the newest dump of each of the three
        weeks before it. The fourth weekly bucket is that current week, already
        paid for by the dailies — so "7 daily + 4 weekly" is ten files here, not
        eleven, and the overlap is the rule working rather than a miscount.
        """
        six_weeks = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(42)]

        kept = [name for name in six_weeks if name not in prune_plan(six_weeks)]
        weeks_kept = {taken_at(name).isocalendar()[:2] for name in kept}
        newest_seven = [
            name for name in kept if taken_at(name) > SUNDAY - timedelta(days=7)
        ]

        assert len(newest_seven) == 7
        assert len(weeks_kept) == 4
        assert len(kept) == 10

    def test_a_month_of_missed_sundays_still_keeps_weeklies(self):
        """
        This runs on a laptop that is asleep at 03:00 often enough, so some
        weekday is always being skipped. A rule that recognised only dumps
        landing on a Sunday would keep nothing at all from a month where every
        Sunday was missed — which is the month you would most want history from.
        """
        no_sundays = [
            _auto(SUNDAY - timedelta(days=offset))
            for offset in range(1, 43)
            if (SUNDAY - timedelta(days=offset)).weekday() != 6
        ]

        kept = [name for name in no_sundays if name not in prune_plan(no_sundays)]
        weeks_kept = {taken_at(name).isocalendar()[:2] for name in kept}

        assert not any(taken_at(name).weekday() == 6 for name in no_sundays)
        assert len(weeks_kept) >= 4

    def test_the_daily_rule_keeps_exactly_seven_days(self):
        """
        Each rule is pinned with the other switched off, because together they
        hide an off-by-one in either: keeping an eighth daily only borrows a file
        the weekly rule was keeping anyway, so the total does not move and every
        assertion about the total still passes. This is the test that fails when
        "7" becomes "8".
        """
        ten_days = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(10)]

        kept = [name for name in ten_days if name not in prune_plan(ten_days, keep_weeks=0)]

        assert len(kept) == 7

    def test_the_weekly_rule_keeps_exactly_four_weeks(self):
        six_weeks = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(42)]

        kept = [name for name in six_weeks if name not in prune_plan(six_weeks, keep_days=0)]

        assert len(kept) == 4

    def test_the_oldest_weeks_still_go(self):
        """Retention has to actually delete, or the disk fills anyway."""
        six_weeks = [_auto(SUNDAY - timedelta(days=offset)) for offset in range(42)]

        doomed = prune_plan(six_weeks)

        assert _auto(SUNDAY - timedelta(days=41)) in doomed
        assert len(doomed) > 30


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


class TestWhereASecondCopyGoes:
    def test_an_explicit_destination_wins(self, tmp_path):
        default = tmp_path / "default"
        default.mkdir()
        explicit = tmp_path / "elsewhere"

        assert mirror_destination(str(explicit), default) == explicit

    def test_the_default_is_used_when_it_exists(self, tmp_path):
        """
        Somebody made that folder on purpose. Treating its existence as the
        instruction is one less thing to remember, and remembering is the
        failure this whole script exists to remove.
        """
        default = tmp_path / "backup-mirror"
        default.mkdir()

        assert mirror_destination("", default) == default

    def test_nowhere_when_the_default_is_not_there(self, tmp_path):
        """
        Then the caller says the dump is on one disk, rather than copying it
        into a directory it just invented.
        """
        assert mirror_destination("", tmp_path / "absent") is None

    def test_an_empty_setting_is_not_a_destination(self, tmp_path):
        """
        `BACKUP_MIRROR_DIR=""` in a plist or a shell is unset, not the current
        directory — which is what a bare Path("") would mean.
        """
        default = tmp_path / "default"
        default.mkdir()

        assert mirror_destination("   ", default) == default

    def test_a_home_relative_path_is_expanded(self):
        """
        The obvious place to put one is under ~, and a literal "~" directory is
        not what anybody means.
        """
        destination = mirror_destination("~/dumps", Path("/nonexistent"))

        assert destination is not None
        assert "~" not in str(destination)
