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
from alembic.script import ScriptDirectory

from app.core import schema_revision


# Read once: parsing every migration file is the slow part of these tests.
SCRIPTS = schema_revision.migration_scripts()
HEAD = SCRIPTS.get_current_head()
ONE_BEFORE_HEAD = SCRIPTS.get_revision(HEAD).down_revision


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
        behind = ONE_BEFORE_HEAD

        await schema_revision.warn_if_schema_is_behind(_revision_is(behind))

        [warning] = _warnings(startup_log)
        assert f"database is at migration {behind}" in warning
        assert f"code expects {HEAD}" in warning
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
        assert HEAD in warning
        assert "does not have" in warning
        assert "alembic upgrade head" not in warning


class TestADatabaseAtHead:
    async def test_is_not_warned_about(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is(HEAD))

        assert not _warnings(startup_log)

    async def test_is_confirmed_once_quietly(self, startup_log):
        await schema_revision.warn_if_schema_is_behind(_revision_is(HEAD))

        confirmations = [
            r for r in startup_log.records if r.levelno == logging.INFO and HEAD in r.getMessage()
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
        assert "could not check the database" in warning
        assert str(error) in warning


def _scripts_with_two_heads(tmp_path) -> ScriptDirectory:
    """Two migrations branching from nothing, as an unmerged pair of branches leaves them."""
    (tmp_path / "versions").mkdir()
    for revision in ("a1", "b2"):
        (tmp_path / "versions" / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = None\n"
        )
    return ScriptDirectory(str(tmp_path))


class TestMigrationScriptsThatCannotBeRead:
    """The code's own migrations are the problem, not the database: said as such, and startup carries on."""

    async def test_with_more_than_one_head_is_named_as_a_problem_with_the_code(
        self, startup_log, monkeypatch, tmp_path
    ):
        scripts = _scripts_with_two_heads(tmp_path)
        monkeypatch.setattr(schema_revision, "migration_scripts", lambda: scripts)

        await schema_revision.warn_if_schema_is_behind(_revision_is(HEAD))

        [warning] = _warnings(startup_log)
        assert "this code's own migrations" in warning
        assert "multiple heads" in warning
        assert "could not check the database" not in warning

    async def test_failing_to_look_up_a_revision_is_not_mistaken_for_an_unknown_one(
        self, startup_log, monkeypatch
    ):
        scripts = schema_revision.migration_scripts()

        def broken(revision):
            raise RuntimeError("could not parse a migration file")

        monkeypatch.setattr(scripts.revision_map, "get_revision", broken)
        monkeypatch.setattr(schema_revision, "migration_scripts", lambda: scripts)

        await schema_revision.warn_if_schema_is_behind(_revision_is("999"))

        [warning] = _warnings(startup_log)
        assert "does not have" not in warning
        assert "this code's own migrations" in warning
        assert "could not parse a migration file" in warning


class _UnreachableDatabase:
    """Stands in for AsyncSessionLocal: every read fails as a refused connection would."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        raise OSError("connection refused")


class _Scheduler:
    started = False

    def start(self):
        self.started = True

    def shutdown(self):
        pass


class TestStartingTheBackend:
    """Offline: the session factory is faked, so nothing here reaches a database."""

    async def test_checks_the_databases_migration_and_carries_on_when_it_cannot(
        self, startup_log, monkeypatch
    ):
        from app import main
        from app.core import database
        from app.services import analysis_run_service, keyframe_service
        from app.workers import poller

        def refuse(*args, **kwargs):
            raise AssertionError("the test reached the real database")

        # Belt and braces: should the fake below ever stop being used, fail
        # rather than connect to whatever DATABASE_URL points at.
        monkeypatch.setattr(database.engine.sync_engine, "raw_connection", refuse)
        monkeypatch.setattr(database, "AsyncSessionLocal", _UnreachableDatabase())

        async def no_interrupted_runs(db):
            pass

        scheduler = _Scheduler()
        monkeypatch.setattr(analysis_run_service, "close_interrupted_runs", no_interrupted_runs)
        monkeypatch.setattr(keyframe_service, "warn_if_ffmpeg_missing", lambda: None)
        monkeypatch.setattr(poller, "create_scheduler", lambda: scheduler)

        async with main.lifespan(main.app):
            assert scheduler.started

        [warning] = [w for w in _warnings(startup_log) if "migration" in w]
        assert "could not check the database" in warning
        assert "connection refused" in warning


@pytest.mark.db
async def test_the_application_role_can_read_the_databases_revision(db_session):
    """The real read, as trademind_app: a read it can already do (ADR 0004)."""
    assert await schema_revision.read_database_revision(db_session) == HEAD
