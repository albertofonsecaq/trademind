from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# Sized deliberately rather than left at the default 5 + 10: recording a
# provider call commits in its own transaction (#27), so a request or a poll
# tick briefly holds a second connection while still holding its own. The
# timeout is explicit so saturation surfaces as an error rather than a hang.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    pool_pre_ping=True,
    # Stated rather than inherited. Every timestamp column is timestamptz, and
    # until the models declare that (ADR 1, #8) a naive write is only correct
    # because the session reads it as UTC. Taking that from the server's default
    # means a differently configured server silently changes what stored values
    # mean, so it is pinned here.
    #
    # `server_settings` is asyncpg's spelling: swapping DATABASE_URL to another
    # driver means translating this, not deleting it (psycopg2 wants
    # `options="-c timezone=UTC"`, as alembic/env.py does).
    connect_args={"server_settings": {"timezone": "UTC"}},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
