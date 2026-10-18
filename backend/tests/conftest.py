"""
Most tests here are offline by design: no database, no network, no API keys.
Everything they touch is either pure or stubbed, so they stay fast enough to
run on every change.

    pip install -r requirements-dev.txt && pytest

A few things cannot be checked that way. How Postgres stores a timestamp, for
instance, is a property of the driver and the column — fakes cannot see it, and
that is exactly where #8 hides. Those tests are marked `db` and are **skipped
unless a database is configured for tests**, so the default run stays offline:

    docker compose up -d db
    cd backend && DATABASE_URL_SYNC=postgresql+psycopg2://trademind:trademind@localhost:5432/trademind \
        alembic upgrade head
    TEST_DATABASE_URL=postgresql+asyncpg://trademind:trademind@localhost:5432/trademind pytest

The `alembic upgrade head` matters: these tests read the real schema, so a
database on an older revision fails in ways that look like code bugs. The
fixture checks the revision and says so rather than letting that happen.

The variable is deliberately its own, not `DATABASE_URL`: that one has a
default pointing at localhost, so reading it would mean a contributor with
Postgres running locally had tests writing to their development data without
ever asking for it.

Whatever a `db` test writes is rolled back when it finishes, so a run leaves no
rows behind — including when the test fails, when the failure is a statement
that aborted the transaction, and when the test commits. That last one is why
the session runs inside an outer transaction it cannot see: a `commit()` in a
test releases a savepoint, and the outer transaction is still thrown away.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[1]

# Let `import app...` work whether pytest runs from backend/ or from the repo root.
sys.path.insert(0, str(BACKEND))


def _expected_revision() -> str | None:
    """The head revision of the migrations in this working tree."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


async def _assert_schema_is_current(session) -> None:
    """
    Fail loudly on a stale test database.

    These tests assert against the real schema, so one on an older revision
    produces errors about missing columns that read as application bugs. This
    turns that into one sentence naming the fix.
    """
    expected = _expected_revision()
    if not expected:
        return
    actual = (
        await session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalars().first()
    if actual != expected:
        pytest.fail(
            f"test database is at migration {actual or 'nothing'}, "
            f"but this tree's head is {expected} — run `alembic upgrade head` against it",
            pytrace=False,
        )


@pytest.fixture
async def db_session():
    """
    A session against a real database, in a transaction that is always rolled
    back. Skips when no test database is configured.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run database-backed tests")

    # An engine per test, with no pooling: the asyncio loop is function-scoped
    # (see pytest.ini), so a pooled connection kept between tests would be bound
    # to a loop that has already closed. Do not "optimize" this to session scope
    # without changing that too.
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            session_factory = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                # The session's own commits become savepoint releases inside the
                # transaction above, so even a test that commits deliberately
                # leaves nothing behind once that transaction is discarded.
                join_transaction_mode="create_savepoint",
            )
            async with session_factory() as session:
                await _assert_schema_is_current(session)
                try:
                    yield session
                finally:
                    await session.rollback()
            # Unconditional, and safe on a transaction a failed statement has
            # already aborted — which is a case these tests deliberately provoke.
            await outer.rollback()
    finally:
        await engine.dispose()
