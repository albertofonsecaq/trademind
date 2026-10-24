"""
Tests for #59 — the credentials the application uses cannot destroy anything.

On 2026-10-22 this database was dropped by a tool holding the application's own
credentials. It was recovered from a verified dump taken five minutes earlier,
so nothing was lost, but nothing about the database stopped it: one role owned
the database and every object in it, and that role was in every connection
string in the repo. The only thing in the way was an `if` in a Python helper,
which is protection against a mistake and none at all against anything that
edits the helper.

So the roles are split, and these tests are how that stays true. They are
`db`-marked: a privilege model cannot be checked against a fake.

**Why these do not run DROP.** A role that is not a superuser and does not own a
table cannot drop it — that is not a guess about Postgres, it is the whole of
what Postgres checks. Asserting those two facts proves the refusal exactly, and
proving it by actually running `DROP TABLE evidence_items` against the live
database would be the same reasoning that caused the incident: "it is fine, it
will be rolled back". A real attempt belongs against a scratch copy (#60), not
against the only copy of months of paid ingestion.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


async def _scalar(session, sql: str, **params):
    return (await session.execute(text(sql), params)).scalar()


class TestTheRoleTheApplicationConnectsAs:
    async def test_it_is_not_a_superuser(self, db_session):
        """A superuser is refused nothing, so every other test here needs this."""
        assert await _scalar(
            db_session, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ) is False

    @pytest.mark.parametrize("table", ["evidence_items", "usage_events", "alembic_version"])
    async def test_it_does_not_own_the_tables_it_reads(self, db_session, table):
        """
        Not an owner and not a superuser is precisely what makes DROP TABLE and
        every other DDL on these impossible.
        """
        owner = await _scalar(
            db_session,
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = :table",
            table=table,
        )
        current = await _scalar(db_session, "SELECT current_user")

        assert owner != current

    async def test_it_does_not_own_the_database(self, db_session):
        """Dropping a database is an owner's privilege, so it must not be one."""
        owner = await _scalar(
            db_session,
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()",
        )
        current = await _scalar(db_session, "SELECT current_user")

        assert owner != current

    async def test_it_cannot_create_databases(self, db_session):
        assert await _scalar(
            db_session, "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ) is False

    async def test_it_cannot_create_objects_in_the_schema(self, db_session):
        """
        No CREATE on public, so it cannot add a table, and cannot leave one
        behind that the next migration collides with.
        """
        assert await _scalar(
            db_session, "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ) is False


class TestWhatTheApplicationStillNeeds:
    """The guard is worthless if it also stops the product working."""

    @pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
    @pytest.mark.parametrize("table", ["evidence_items", "trade_ideas", "usage_events"])
    async def test_it_can_read_and_write_the_tables(self, db_session, table, privilege):
        assert await _scalar(
            db_session,
            "SELECT has_table_privilege(current_user, :table, :privilege)",
            table=table, privilege=privilege,
        ) is True

    async def test_it_can_use_the_sequences_behind_those_writes(self, db_session):
        """
        An INSERT into a table with a serial column fails on the sequence rather
        than the table, which is a confusing way to find out a grant is missing.
        """
        missing = (await db_session.execute(text("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'S' AND n.nspname = 'public'
              AND NOT has_sequence_privilege(current_user, c.oid, 'USAGE')
        """))).scalars().all()

        assert missing == []

    async def test_it_can_actually_write(self, db_session):
        """
        The privileges above say it may; this does it. The update sets a column
        to the value it already holds, so it proves the write is permitted
        without changing anything even for the moment before the rollback.
        """
        result = await db_session.execute(text(
            "UPDATE source_configs SET fetch_cadence = fetch_cadence "
            "WHERE id = (SELECT id FROM source_configs LIMIT 1)"
        ))

        assert result.rowcount == 1

    async def test_the_vector_type_is_usable(self, db_session):
        """
        Hybrid search casts to vector. The extension is created by the setup
        step as a superuser, so the application's role has to be able to *use*
        a type it cannot have created.
        """
        assert await _scalar(db_session, "SELECT '[1,2,3]'::vector IS NOT NULL") is True


class TestTheNextMigrationsTables:
    async def test_future_tables_are_readable_without_another_grant(self, db_session):
        """
        Without default privileges, every table a later migration adds is
        invisible to the application — and that failure shows up one deploy
        after the change that caused it.
        """
        defaults = (await db_session.execute(text("""
            SELECT unnest(defaclacl)::text FROM pg_default_acl d
            JOIN pg_namespace n ON n.oid = d.defaclnamespace
            WHERE n.nspname = 'public' AND d.defaclobjtype = 'r'
        """))).scalars().all()
        current = await _scalar(db_session, "SELECT current_user")

        assert any(acl.startswith(f"{current}=") for acl in defaults), defaults


class TestTheBackupRole:
    """
    Checked by attribute rather than by connecting: the backup role's own
    credentials are not the tests' to hold, and what matters about it is exactly
    these two facts.
    """

    async def test_it_can_create_its_own_databases(self, db_session):
        assert await _scalar(
            db_session,
            "SELECT rolcreatedb FROM pg_roles WHERE rolname = :role",
            role="trademind_backup",
        ) is True

    async def test_it_does_not_own_the_live_database(self, db_session):
        owner = await _scalar(
            db_session,
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'trademind'",
        )

        assert owner != "trademind_backup"

    async def test_it_can_read_every_table(self, db_session):
        """It has to dump them all, including any a later migration adds."""
        assert await _scalar(
            db_session,
            "SELECT pg_has_role(:role, 'pg_read_all_data', 'MEMBER')",
            role="trademind_backup",
        ) is True
