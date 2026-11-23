"""
Whether the database is on the migration this code expects (#148).

Started directly with uvicorn rather than through dev.sh, the backend once ran
against a database one migration behind the code, and the first sign was a 500
about a missing column. Checked once at startup, so the cause is named before
any request fails.

It only warns. Migrations run as the owner role and nothing else does (ADR 0004):
the application's credentials cannot alter the schema, and must not try.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from alembic.config import Config
from alembic.script import ScriptDirectory

log = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[2]

UPGRADE_COMMAND = (
    "cd backend && DATABASE_URL_SYNC=<the trademind_owner URL> alembic upgrade head"
)

ReadRevision = Callable[[], Awaitable[str | None]]


def migration_scripts() -> ScriptDirectory:
    """The migrations in this working tree, read from disk; no database involved."""
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config)


async def warn_if_schema_is_behind(read_database_revision: ReadRevision) -> None:
    """Called once when the backend starts. Logs; never raises, never migrates."""
    try:
        scripts = migration_scripts()
        head = scripts.get_current_head()
        current = await read_database_revision()
        known = current is None or _is_known(scripts, current)
    except Exception as error:
        # A missing version table or a refused read is not a reason to stay down;
        # it is a reason to say the check did not happen.
        log.warning(
            "Started without knowing whether the database is on this code's migrations: "
            "could not check (%s)",
            error,
        )
        return
    if current == head:
        log.info("The database is at migration %s, as this code expects", head)
    elif known:
        log.warning(
            "The database is at %s but this code expects %s, so requests "
            "touching the newer schema will fail. Bring it up to date as the owner: %s",
            "no migration" if current is None else f"migration {current}",
            head,
            UPGRADE_COMMAND,
        )
    else:
        log.warning(
            "The database is at migration %s, which this code does not have (it expects %s): "
            "the database was migrated by newer code, and this code may not match its schema",
            current,
            head,
        )


def _is_known(scripts: ScriptDirectory, revision: str) -> bool:
    try:
        return scripts.get_revision(revision) is not None
    except Exception:
        return False
