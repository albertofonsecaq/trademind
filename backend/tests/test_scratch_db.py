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

from scripts.backup import VERIFY_DB_PREFIX
from scripts.scratch_db import (
    SCRATCH_PREFIX,
    assert_scratch,
    newest_verified_dump,
    scratch_name,
    superuser_url_for,
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
        the name is what tells you which one you are connected to. And it must
        not look like the backup's throwaways either: each script refuses to
        drop the other's, so an overlap would strand one of them.
        """
        name = scratch_name(NOW)

        assert name.startswith(SCRATCH_PREFIX)
        assert name != "trademind"
        assert not name.startswith(VERIFY_DB_PREFIX)
        assert not VERIFY_DB_PREFIX.startswith(SCRATCH_PREFIX)


class TestThePathItConnectsTo:
    """
    The seam that was wrong: a URL rebuilt by cutting at the last slash loses
    everything after the database name. setup_db_roles.py had the same bug, and
    the fix only sticks if it is tested in both places.
    """

    def test_it_repoints_a_plain_url(self, monkeypatch):
        monkeypatch.setenv(
            "SUPERUSER_DATABASE_URL", "postgresql://u:p@localhost:5432/trademind"
        )

        assert superuser_url_for("trademind_scratch_x").endswith("/trademind_scratch_x")

    def test_it_keeps_a_query_string(self, monkeypatch):
        """
        `?sslmode=require` cut off the end is a connection that quietly goes
        somewhere else, or nowhere.
        """
        monkeypatch.setenv(
            "SUPERUSER_DATABASE_URL",
            "postgresql://u:p@db.example:5432/trademind?sslmode=require",
        )

        url = superuser_url_for("trademind_scratch_x")

        assert "sslmode=require" in url
        assert "/trademind_scratch_x?" in url

    def test_it_keeps_the_host_and_credentials(self, monkeypatch):
        monkeypatch.setenv(
            "SUPERUSER_DATABASE_URL", "postgresql://user:secret@db.example:6543/trademind"
        )

        url = superuser_url_for("trademind_scratch_x")

        assert "user:secret@db.example:6543" in url

    def test_two_copies_do_not_collide(self):
        assert scratch_name(NOW) != scratch_name(NOW + timedelta(seconds=1))
