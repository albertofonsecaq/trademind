"""
Tests for #60 — a disposable copy of the real database, on one command.

The 2026-10-22 incident was a tool running a `dropdb` guard against the live
database to find out whether the guard worked. The proximate cause was the
tool; the reason it reached for production is that there was nowhere else to
go. Making the safe thing one command is the fix for that, and the rest of this
file is the two judgements inside it:

- which dump it copies, because "the newest file" and "the newest dump we know
  restores" are not the same thing, and
- what it will agree to drop, because this script's whole job is handing people
  a database to destroy.
"""
from datetime import datetime, timedelta, timezone

import pytest

from scripts.scratch_db import (
    SCRATCH_PREFIX,
    assert_scratch,
    newest_verified_dump,
    scratch_name,
)

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)


def _auto(when: datetime) -> str:
    return f"trademind-{when.strftime('%Y%m%dT%H%M%SZ')}-alembic016-auto.dump"


class TestWhichDumpItCopies:
    def test_the_newest_one_the_backup_verified(self):
        names = [_auto(NOW - timedelta(days=offset)) for offset in (3, 1, 7)]

        assert newest_verified_dump(names) == _auto(NOW - timedelta(days=1))

    def test_a_dump_that_failed_verification_is_not_a_candidate(self):
        """
        It is kept as evidence and it is exactly the dump you must not hand
        somebody as a copy of the database.
        """
        failed = _auto(NOW) + ".FAILED-VERIFICATION"
        names = [failed, _auto(NOW - timedelta(days=2))]

        assert newest_verified_dump(names) == _auto(NOW - timedelta(days=2))

    def test_a_hand_taken_dump_is_not_a_candidate_either(self):
        """
        Nothing records whether one was verified, and the scheduled dumps are
        verified by construction — a failed one gets renamed. So "verified"
        means "written by the backup and still called a dump", and a hand-taken
        file is chosen explicitly or not at all.
        """
        names = ["trademind-20261018-alembic016.dump", _auto(NOW - timedelta(days=30))]

        assert newest_verified_dump(names) == _auto(NOW - timedelta(days=30))

    def test_no_dump_at_all_is_not_an_empty_database(self):
        """
        The failure this prevents: creating the copy anyway, so somebody tests
        against an empty schema and concludes something false about the data.
        """
        assert newest_verified_dump([]) is None
        assert newest_verified_dump(["notes.txt", "trademind-20261018-alembic016.dump"]) is None


class TestWhatItWillAgreeToDrop:
    @pytest.mark.parametrize("name", [
        "trademind",                    # the live database
        "postgres",
        "trademind_verify_template",    # the backup's template, superuser-only to rebuild
        "trademind_verify_1234",        # the backup's throwaway, not this script's
        "",
    ])
    def test_it_refuses_anything_that_is_not_its_own_copy(self, name):
        """
        A script whose purpose is handing out databases to destroy has to be
        unable to destroy the one that matters. The verify names are in here
        deliberately: they belong to the backup, and dropping its template
        needs a superuser to put back.
        """
        with pytest.raises(RuntimeError, match="not a scratch database"):
            assert_scratch(name)

    def test_it_accepts_its_own(self):
        assert_scratch(scratch_name(NOW)) is None

    def test_its_names_are_unmistakable(self):
        """
        A disposable copy that reads like the live database is its own trap —
        the name is what tells you which one you are connected to.
        """
        name = scratch_name(NOW)

        assert name.startswith(SCRATCH_PREFIX)
        assert "scratch" in name
        assert name != "trademind"

    def test_two_copies_do_not_collide(self):
        assert scratch_name(NOW) != scratch_name(NOW + timedelta(seconds=1))
