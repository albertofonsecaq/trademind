"""
The backend says so at startup when the database is behind its migrations (#148).

Started directly with uvicorn rather than through dev.sh, the backend once ran
against a database one migration short, and the first sign was a 500 about a
missing column. These check the one line that would have named the cause.

The database's revision is faked; the code's head is read from the real
migration scripts, which needs no database.
"""
import logging

import pytest

from app.core import schema_revision


def _scripts():
    return schema_revision.migration_scripts()


def _head() -> str:
    return _scripts().get_current_head()


def _one_before_head() -> str:
    return _scripts().get_revision(_head()).down_revision


def _revision_is(value):
    async def read():
        return value

    return read


def _revision_fails_with(error: Exception):
    async def read():
        raise error

    return read


@pytest.fixture
def startup_log(caplog):
    caplog.set_level(logging.DEBUG, logger=schema_revision.log.name)
    return caplog


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


class TestADatabaseBehindTheCode:
    async def test_is_warned_about_naming_both_revisions_and_the_fix(self, startup_log):
        behind = _one_before_head()

        await schema_revision.warn_if_schema_is_behind(_revision_is(behind))

        [warning] = _warnings(startup_log)
        assert f"database is at migration {behind}" in warning
        assert f"code expects {_head()}" in warning
        assert "DATABASE_URL_SYNC=" in warning
        assert "alembic upgrade head" in warning

    async def test_with_no_migration_at_all_is_warned_about_as_behind(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is(None))

        [warning] = _warnings(startup_log)
        assert "database is at no migration" in warning
        assert "alembic upgrade head" in warning


class TestADatabaseOnAMigrationThisCodeDoesNotHave:
    """Ahead of the code, say from a newer branch: upgrading would not help, so it is not suggested."""

    async def test_is_warned_about_without_suggesting_an_upgrade(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is("999"))

        [warning] = _warnings(startup_log)
        assert "999" in warning
        assert _head() in warning
        assert "does not have" in warning
        assert "alembic upgrade head" not in warning


class TestADatabaseAtHead:
    async def test_is_not_warned_about(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is(_head()))

        assert not _warnings(startup_log)

    async def test_is_confirmed_once_quietly(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is(_head()))

        confirmations = [
            r for r in startup_log.records if r.levelno == logging.INFO and _head() in r.getMessage()
        ]
        assert len(confirmations) == 1


class TestARevisionThatCannotBeRead:
    """No version table, permission refused: startup carries on and says it could not check."""

    @pytest.mark.parametrize(
        "error",
        [
            Exception('relation "alembic_version" does not exist'),
            PermissionError("permission denied for table alembic_version"),
            OSError("connection refused"),
        ],
    )
    async def test_does_not_stop_startup_and_says_it_could_not_check(self, startup_log, error):
        await schema_revision.warn_if_schema_is_behind(_revision_fails_with(error))

        [warning] = _warnings(startup_log)
        assert "could not check" in warning
        assert str(error) in warning
