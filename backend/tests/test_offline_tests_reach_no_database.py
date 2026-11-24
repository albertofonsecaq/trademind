"""
An offline test cannot reach a database, whatever DATABASE_URL says (#156).

The application's DATABASE_URL defaults to the live database on localhost, so
an offline test that forgot to fake something that queries would quietly read
and write it on any machine where Postgres is running. The guard in conftest.py
stops every connection a test not marked `db` opens, at the driver, and fails
the test even when the code under test swallows the error.

Every URL here names a closed port on this machine, never the live database:
without the guard these tests fail with a refused connection, not by reaching
anything. They go through the same drivers the application's engine and
alembic use, which is where the guard stands.
"""
import asyncpg
import pytest
from psycopg2 import connect as psycopg2_connect_bound_at_import
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# `conftest`, not `tests.conftest`: tests/ is no package, so the latter is a
# second module with its own copy of the class, which pytest.raises never matches.
from conftest import OfflineTestReachedADatabase

asyncpg_connect_bound_at_import = asyncpg.connect

NOWHERE_ASYNC = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nothing"
NOWHERE_SYNC = "postgresql+psycopg2://nobody:nothing@127.0.0.1:1/nothing"


async def test_a_query_through_an_async_session_is_stopped(offline_database_guard):
    engine = create_async_engine(NOWHERE_ASYNC, poolclass=NullPool)
    session_factory = async_sessionmaker(engine)
    try:
        with pytest.raises(OfflineTestReachedADatabase, match="not marked `db`"):
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
    assert len(offline_database_guard.attempts) == 1
    offline_database_guard.clear_attempts()


def test_a_sync_connection_like_alembics_is_stopped(offline_database_guard):
    engine = create_engine(NOWHERE_SYNC, poolclass=NullPool)
    try:
        with pytest.raises(OfflineTestReachedADatabase, match="not marked `db`"):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    assert len(offline_database_guard.attempts) == 1
    offline_database_guard.clear_attempts()


async def test_an_attempt_the_code_swallows_still_fails_the_test(offline_database_guard):
    engine = create_async_engine(NOWHERE_ASYNC, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except Exception:
        pass  # as code under test that catches everything would
    finally:
        await engine.dispose()

    with pytest.raises(pytest.fail.Exception, match="tried to reach a database"):
        offline_database_guard.check()
    offline_database_guard.clear_attempts()


async def test_a_connect_imported_before_the_guard_is_stopped(offline_database_guard):
    with pytest.raises(OfflineTestReachedADatabase):
        await asyncpg_connect_bound_at_import(NOWHERE_ASYNC.replace("+asyncpg", ""))
    with pytest.raises(OfflineTestReachedADatabase):
        psycopg2_connect_bound_at_import(NOWHERE_SYNC.replace("+psycopg2", ""))
    assert len(offline_database_guard.attempts) == 2
    offline_database_guard.clear_attempts()


async def test_a_pool_is_stopped(offline_database_guard):
    with pytest.raises(OfflineTestReachedADatabase):
        await asyncpg.create_pool(NOWHERE_ASYNC.replace("+asyncpg", ""), min_size=1)
    assert offline_database_guard.attempts
    offline_database_guard.clear_attempts()


def test_the_error_names_the_guard_and_where_it_lives(offline_database_guard):
    with pytest.raises(OfflineTestReachedADatabase, match=r"offline_database_guard.*tests/conftest\.py"):
        psycopg2_connect_bound_at_import(NOWHERE_SYNC.replace("+psycopg2", ""))
    offline_database_guard.clear_attempts()


def test_a_test_the_stopped_connection_already_failed_is_not_failed_twice(offline_database_guard):
    with pytest.raises(OfflineTestReachedADatabase):
        psycopg2_connect_bound_at_import(NOWHERE_SYNC.replace("+psycopg2", ""))
    offline_database_guard.check(test_failed=True)  # no second report
    with pytest.raises(pytest.fail.Exception):
        offline_database_guard.check(test_failed=False)
    offline_database_guard.clear_attempts()
