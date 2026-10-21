"""
What a platform failure says about the account it failed on.

The sibling of core/llm_errors: the same question — does this failure mean the
credentials, or just this attempt? — asked about a platform connection instead
of the AI provider. They are apart because the answers have nothing in common:
one reads Anthropic's error bodies, this one reads Telethon's exception tree,
and a module importing both would make every caller of either depend on both.

#49 refused work a source could not do when the stored state said so. The state
could be wrong: Telegram ends a session from its own side — the user terminates
that device, two-factor is re-armed, the account is deactivated — and nothing
wrote that back, so `status` stayed "active" and the work was accepted and then
dropped. Marking the connection is the whole fix, because #49's gate does the
refusing and #28's row copy does the explaining. That is why nothing in here
knows anything about fetching, and why its callers only have to say "this
failure was about the credentials" and carry on failing (#54).

Getting this wrong in the other direction is the expensive one. A rate limit or
a network blip says nothing about the credentials, and marking a connection for
one would pause a working account until somebody noticed.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import platform_connection as connection_status
from app.models.platform_connection import PlatformConnection

log = logging.getLogger(__name__)

# Rejected credentials are recorded as "revoked" rather than a status of their
# own; see docs/adr/0002-a-connection-has-three-states.md for why, and for what
# that costs.

# One line fit to store on a job row. Telethon's own wording goes nowhere near
# it: `str()` on these errors reads "The key is not registered in the system
# (caused by GetHistoryRequest)", which names its internals and tells the user
# nothing about what to do — the same reason llm_errors summarizes by class.
REJECTED_SUMMARY = (
    "Telegram rejected this platform connection's session, so the run stopped. "
    "Reconnect it and try again."
)


def credentials_rejected(exc: BaseException) -> bool:
    """
    True when this failure means our stored session will not work again.

    Both of Telethon's base classes for it, rather than a list of the leaves:
    `UnauthorizedError` covers the session being gone, expired, terminated by
    the user, needing two-factor again, or belonging to a deactivated account,
    and `AuthKeyError` covers a key Telegram killed for being used from two
    places at once. A list of subclasses would silently stop covering a case the
    library adds, and every subclass of these two is about the credentials.

    Everything else is False, including the failures that are genuinely about
    this attempt (a rate limit, a timeout) or about one channel rather than the
    account (`ChannelPrivateError`).
    """
    # Imported here, not at module scope, because every other telethon import in
    # this codebase is deferred the same way — this module is reached from the
    # API's import graph, and the library costs a second to import.
    from telethon.errors.rpcbaseerrors import AuthKeyError, UnauthorizedError

    return isinstance(exc, (UnauthorizedError, AuthKeyError))


async def mark_rejected(db: AsyncSession, connection_id: uuid.UUID | None) -> None:
    """
    Record that this connection's credentials were rejected.

    Takes an id and re-reads the row, rather than taking the row the caller
    already holds: the callers reach this from inside a fetch, where what they
    hold is a relationship loaded ages earlier, and the one thing that must not
    happen here is the write being skipped because the row in hand is stale.
    Re-reading also means a caller does not have to have loaded `connection` at
    all — only the foreign key, which it reads with its other attributes before
    the loop. The write is scoped to the one id, like every write here.

    A connection already in that state is left alone, so a source failing on
    every tick writes and logs once rather than every minute. `None` is normal:
    a YouTube source has no connection to mark.
    """
    if connection_id is None:
        return
    result = await db.execute(
        select(PlatformConnection).where(PlatformConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None or connection.status == connection_status.REVOKED:
        return
    connection.status = connection_status.REVOKED
    await db.commit()
    log.warning(
        "Platform connection %s marked %s: Telegram rejected its credentials",
        connection_id, connection_status.REVOKED,
    )
