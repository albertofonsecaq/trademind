"""
Most tests here are offline by design: no database, no network, no API keys.
Everything they touch is either pure or stubbed, so they stay fast enough to
run on every change.

    pip install -r requirements-dev.txt && pytest

A few things cannot be checked that way. How Postgres stores a timestamp, for
instance, is a property of the driver and the column — fakes cannot see it, and
that is exactly where #8 hides. Those tests are marked `db` and are **skipped
unless a database is configured for tests**, so the default run stays offline:

    TEST_DATABASE_URL=postgresql+asyncpg://trademind:trademind@localhost:5432/trademind pytest

    # or, with the project's compose file already up:
    docker compose up -d db
    TEST_DATABASE_URL=postgresql+asyncpg://trademind:trademind@localhost:5432/trademind pytest -m db

The variable is deliberately its own, not `DATABASE_URL`: that one has a
default pointing at localhost, so reading it would mean a contributor with
Postgres running locally had tests writing to their development data without
ever asking for it.

Whatever a `db` test writes is rolled back when it finishes, so a run leaves no
rows behind — including on failure.
"""
import os
import sys
from pathlib import Path

import pytest

# Let `import app...` work whether pytest runs from backend/ or from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def db_session():
    """
    A session against a real database, in a transaction that is always rolled
    back. Skips when no test database is configured.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run database-backed tests")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=None)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            transaction = await session.begin()
            try:
                yield session
            finally:
                # Rolled back whatever happened above, so these tests can use
                # the development database without leaving anything in it.
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()
