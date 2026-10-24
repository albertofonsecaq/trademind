"""
Tests for #59's setup plan, offline.

The privilege tests beside this one assert what the roles can and cannot do,
against a real database. These assert the plan that produces them, which is
worth pinning separately because two of its statements were wrong in ways no
unit test would have caught — one refused by Postgres, one that would have left
the application unable to read its own future tables.

The script builds its statements and returns them before executing anything, so
--dry-run can print them. That is also what makes them testable.
"""
import pytest

from scripts.setup_db_roles import (
    APP,
    BACKUP,
    BOOTSTRAP,
    OWNER,
    VERIFY_TEMPLATE,
    reassign_public_objects,
    revert_statements,
    statements,
    template_statements,
)


@pytest.fixture
def plan() -> list[str]:
    return statements("trademind")


def _joined(plan: list[str]) -> str:
    return "\n".join(plan)


class TestWhatTheApplicationRoleNeverGets:
    @pytest.mark.parametrize("privilege", ["SUPERUSER", "CREATEROLE", "CREATEDB"])
    def test_it_is_never_granted(self, plan, privilege):
        granting = [
            statement for statement in plan
            if APP in statement and privilege in statement and f"NO{privilege}" not in statement
        ]

        assert granting == []

    def test_it_is_told_explicitly_what_it_is_not(self, plan):
        """
        A role created with defaults is already none of these, so this is
        belt-and-braces — and it is also the line somebody has to delete on
        purpose to make the application dangerous again.
        """
        assert any(
            f"ALTER ROLE {APP}" in statement and "NOSUPERUSER" in statement
            and "NOCREATEDB" in statement and "NOCREATEROLE" in statement
            for statement in plan
        )

    def test_it_is_never_granted_ownership_of_anything(self, plan):
        assert not any(f"OWNER TO {APP}" in statement for statement in plan)
        assert not any(f"OWNER {APP}" in statement for statement in plan)


class TestOwnershipMoves:
    def test_the_database_changes_hands(self, plan):
        """Quoted, because the name arrives from a URL rather than a constant."""
        assert f'ALTER DATABASE "trademind" OWNER TO {OWNER}' in plan

    def test_it_does_not_use_reassign_owned(self, plan):
        """
        The bug a rehearsal on a scratch copy caught: REASSIGN OWNED BY the
        bootstrap role is refused outright, because that role also owns objects
        the database system requires and REASSIGN cannot separate them from
        ours. Ownership moves object by object instead.
        """
        assert "REASSIGN OWNED" not in _joined(plan)

    def test_it_moves_the_objects_one_at_a_time(self, plan):
        assert "ALTER %s %I OWNER TO %I" in _joined(plan)

    def test_it_leaves_extension_objects_alone(self, plan):
        """
        pgvector's functions and operators live in public and belong to the
        extension. Taking them would be wrong, and trying is an error.
        """
        assert "deptype = 'e'" in _joined(plan)


class TestTheNextMigrationsTables:
    @pytest.mark.parametrize("kind", ["TABLES", "SEQUENCES"])
    def test_default_privileges_are_set_for_them(self, plan, kind):
        """
        Without these, a table added by a later migration is invisible to the
        application, and that failure arrives a deploy after the change that
        caused it — which is the worst time to learn about it.
        """
        assert any(
            "ALTER DEFAULT PRIVILEGES" in statement
            and f"FOR ROLE {OWNER}" in statement
            and f"ON {kind} TO {APP}" in statement
            for statement in plan
        )


class TestTheBackupRole:
    def test_it_can_create_its_own_databases(self, plan):
        assert any(f"ALTER ROLE {BACKUP} WITH CREATEDB" in s for s in plan)

    def test_it_reads_everything_by_role_rather_than_by_list(self, plan):
        """
        pg_read_all_data instead of a grant per table, so a table added later is
        dumped without anybody remembering to grant it.
        """
        assert f"GRANT pg_read_all_data TO {BACKUP}" in plan

    def test_it_is_not_given_ownership_of_the_live_database(self, plan):
        assert not any(f"ALTER DATABASE trademind OWNER TO {BACKUP}" in s for s in plan)


class TestItIsSafeToRunTwice:
    def test_every_role_is_created_conditionally(self, plan):
        creates = [s for s in plan if "CREATE ROLE" in s]

        assert len(creates) == 3
        assert all("IF NOT EXISTS" in s for s in creates)

    def test_the_extension_is_created_conditionally(self, plan):
        assert "CREATE EXTENSION IF NOT EXISTS vector" in plan

    def test_nothing_in_the_plan_drops_anything(self, plan):
        """
        This script runs as the only role that could drop the database. It has
        no business containing the word at all.
        """
        assert "DROP" not in _joined(plan).upper()


class TestTheWayBack:
    """
    revert_statements had no coverage at all, while its own docstring claimed
    to be "a way back that has been tried". The round trip is rehearsed against
    a scratch copy; this pins the shape of it offline.
    """

    @pytest.fixture
    def plan(self) -> list[str]:
        return revert_statements("trademind")

    def test_it_hands_the_database_back(self, plan):
        assert f'ALTER DATABASE "trademind" OWNER TO {BOOTSTRAP}' in plan

    def test_it_hands_the_objects_back_the_same_way_they_came(self, plan):
        """
        The same statement as the forward direction with a different target, so
        a relkind added to one is added to both.
        """
        assert reassign_public_objects(BOOTSTRAP) in plan

    def test_it_drops_nothing(self, plan):
        """
        Reverting is for getting out of trouble. Deleting roles on the way would
        fail anyway while they still own things, and would make the round trip
        one-way.
        """
        assert "DROP" not in "\n".join(plan).upper()

    def test_it_is_the_mirror_of_applying(self):
        """
        Both directions move the same set of objects. If one grows a relkind and
        the other does not, ownership is stranded — which is the state you would
        be reverting to escape.
        """
        forwards = reassign_public_objects(OWNER).replace(OWNER, "ROLE")
        backwards = reassign_public_objects(BOOTSTRAP).replace(BOOTSTRAP, "ROLE")

        assert forwards == backwards


class TestTheVerifyTemplate:
    def test_it_is_owned_by_the_backup_role(self):
        """
        Copying a database requires owning the one you copy, and the backup role
        is what does the copying.
        """
        create, _ = template_statements()

        assert f"CREATE DATABASE {VERIFY_TEMPLATE} OWNER {BACKUP}" == create

    def test_it_gets_the_extension_the_dumps_ask_for(self):
        """
        A dump of this database replays CREATE EXTENSION vector, which only a
        superuser may do — so the template has it already and the statement is
        a notice instead of a refusal.
        """
        _, extension = template_statements()

        assert extension == "CREATE EXTENSION IF NOT EXISTS vector"

    def test_it_shares_the_throwaway_prefix_on_purpose(self):
        """
        It is named like the throwaways it seeds, which is also why the drop
        guard in backup.py has to name it rather than trust the prefix.
        """
        assert VERIFY_TEMPLATE.startswith("trademind_verify_")
