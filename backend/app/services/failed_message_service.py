"""
The record of messages that failed, so they are retried instead of lost (#96, ADR 0005).

Only bookkeeping lives here; fetching a message again and processing it is the
pipeline's job, which calls these as it goes.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.config import settings
from app.core.llm_errors import safe_failure_summary
from app.core.content_unavailable import ContentUnavailable
from app.core.platform_refusal import PlatformRefused, cooldown_length
from app.models.failed_message import MAX_ATTEMPTS, FailedMessage
from app.services import refusal_store

# A database error carries its whole statement and parameters — 6 KB of junk
# for one of #94's links — and a person needs only the start of it.
_MAX_ERROR_CHARS = 500

# The platform handed nothing back for it: deleted there, or a part of a kind the
# source's content filters no longer ask for. Either way there is nothing to retry.
GONE_FROM_SOURCE = (
    "The source no longer offers this message (deleted, or its kind is now "
    "filtered out), so it cannot be retried."
)


async def record_failure(db: AsyncSession, msg: RawMessage, exc: BaseException) -> FailedMessage:
    """
    Record a failed attempt at this message: a first one, or another one.

    A platform's refusal is recorded without counting as an attempt: it was not
    the message's doing, and it is waited out rather than given up (ADR 0007).
    Nor is a failure of our own tooling, which waits for the fix (#139).
    Content no attempt could ever get is given up here and then (#137).
    """
    row = await _find(db, msg.source_config_id, msg.stable_id)
    if row is None:
        refused = isinstance(exc, PlatformRefused)
        row = FailedMessage(
            workspace_id=msg.workspace_id,
            source_config_id=msg.source_config_id,
            stable_id=msg.stable_id,
            message_at=msg.timestamp,
            error=_summary(exc),
            attempts=1 if _counts_an_attempt(exc) else 0,
            retry_after=exc.until if refused else _retry_after(exc, 1),
            given_up_at=now_utc() if _can_never_succeed(exc) else None,
        )
        try:
            # A savepoint: a poll and a backfill of the same source can both get
            # here first, and the loser must not abort the session it shares
            # with the loop that called it.
            async with db.begin_nested():
                db.add(row)
        except IntegrityError:
            return await count_attempt(db, await _find(db, msg.source_config_id, msg.stable_id), exc)
        await db.commit()
        return row
    return await count_attempt(db, row, exc)


async def count_attempt(db: AsyncSession, row: FailedMessage, exc: BaseException) -> FailedMessage:
    """
    Another failed attempt at a recorded message; the third gives it up, and so
    does the first that says no attempt could ever succeed (#137).

    A platform's refusal does not count: the message waits until it ends. A
    failure of our own tooling does not count either, and waits as long as it
    says: its attempts would be spent on a bug of ours, and the message given
    up before anybody could fix it (#139).
    """
    row.error = _summary(exc)
    row.last_failed_at = now_utc()
    if isinstance(exc, PlatformRefused):
        row.retry_after = exc.until
        await db.commit()
        return row
    if not _counts_an_attempt(exc):
        row.retry_after = _retry_after(exc, row.attempts)
        await db.commit()
        return row
    row.attempts += 1
    row.retry_after = _retry_after(exc, row.attempts)
    if row.attempts >= MAX_ATTEMPTS or _can_never_succeed(exc):
        row.given_up_at = now_utc()
    await db.commit()
    return row


async def wait_out(db: AsyncSession, row: FailedMessage, refusal: PlatformRefused) -> FailedMessage:
    """
    A message that was never asked for waits for the end of a refusal met on
    another one (ADR 0007): nothing of it reached the platform.

    So `last_failed_at` is left alone. It is when the message was last
    attempted, and that is what decides whose turn comes next (#138): stamped
    here too, every waiting message would move back together on every tick and
    the front of the queue would never change — which is how one video took
    every cool-down's single request for seven days while the backlog waited.
    """
    row.error = _summary(refusal)
    row.retry_after = refusal.until
    await db.commit()
    return row


async def give_up(db: AsyncSession, row: FailedMessage, reason: str) -> None:
    """
    Stop retrying at once, without spending the attempts left: the reason
    replaces the last failure's, because it is now the one that matters.
    """
    row.error = reason
    row.given_up_at = now_utc()
    await db.commit()


async def send_back(db: AsyncSession, row: FailedMessage) -> FailedMessage:
    """
    A person sends a given-up message back: the next fetch retries it, with a
    fresh set of attempts (#102). The last reason stays until an attempt
    replaces it. One still being retried is left as it is.
    """
    if row.given_up_at is not None:
        row.given_up_at = None
        row.attempts = 0
        await db.commit()
    return row


async def send_back_given_up(db: AsyncSession, source_config_id: uuid.UUID) -> int:
    """Every given-up message of a source, sent back at once — after an outage, say."""
    result = await db.execute(
        update(FailedMessage)
        .where(FailedMessage.source_config_id == source_config_id, FailedMessage.given_up_at.is_not(None))
        .values(given_up_at=None, attempts=0)
    )
    await db.commit()
    return result.rowcount


async def resolve(db: AsyncSession, row: FailedMessage) -> None:
    """The message went through: there is nothing left to remember."""
    await db.delete(row)
    await db.commit()


async def forget(db: AsyncSession, source_config_id: uuid.UUID, stable_id: str) -> None:
    """
    A message just stored needs no record, whatever its state, and neither does
    the whole it is a part of: a video recorded as failed is stored as its
    transcript's chunks, whose ids extend its own (#126). The caller commits.
    """
    await db.execute(
        delete(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.stable_id.in_(_itself_and_its_wholes(stable_id)),
        )
    )


async def to_retry(db: AsyncSession, source_config_id: uuid.UUID) -> list[FailedMessage]:
    """
    A source's failures still being retried and not waiting out a refusal,
    whose turn it is first.

    Whose turn it is is decided here rather than in the query, because it is a
    rule about the queue and is read — and tested — as one. The result is every
    eligible row either way: there is no limit for an ordering to cut at.
    """
    rows = await db.execute(
        select(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.given_up_at.is_(None),
            or_(FailedMessage.retry_after.is_(None), FailedMessage.retry_after <= now_utc()),
        )
    )
    return sorted(rows.scalars(), key=_whose_turn)


def _whose_turn(row: FailedMessage) -> tuple:
    """
    The least recently attempted message goes first, and one never attempted
    before any that has been (#138).

    While a platform is refusing us only one request a cool-down reaches it —
    the one at the front of this queue — and that request earns the next
    cool-down, so everything behind it is refused by the pre-check without
    being asked for. Ordered by
    first failure, that front never moved: nine poll ticks over twenty hours
    spent 15 refusals on two videos and ingested nothing, and the video first
    failing on 2026-11-04 was still taking the turn a week later. Ordered by
    when a message was last attempted, the turn rotates, so a run of refusals
    costs each message a delay instead of costing the backlog everything.

    A refusal does not count an attempt (ADR 0007), so `attempts == 0` is a
    message nothing is yet held against; it goes before one whose own attempts
    failed. `stable_id` is unique per source, so this is a total order: the
    same state always produces the same queue, and two messages cannot swap
    places between ticks without one of them having been attempted.
    """
    return (row.attempts > 0, row.last_failed_at, row.stable_id)


async def cooldown_in_force() -> timedelta:
    """
    How long the cool-down a refusal is being waited out under lasts (#140,
    ADR 0010) — the measure `is_stuck` holds a message's wait against.

    It is the ladder's current rung: the base doubled once for each refusal in
    a row recorded beside it, which is the one thing that remembers them. With
    nothing recorded, or the count back at 0 because the platform answered us,
    it is the base — the shortest any single cool-down can be.

    YouTube's, because it is the only platform that refuses us today (ADR 0007).
    """
    refused = await refusal_store.current(refusal_store.YOUTUBE)
    return cooldown_length(
        refused.consecutive if refused is not None else 0,
        base=timedelta(minutes=settings.YOUTUBE_REFUSAL_COOLDOWN_MINUTES),
        ceiling=timedelta(minutes=settings.YOUTUBE_REFUSAL_COOLDOWN_MAX_MINUTES),
    )


def is_stuck(row: FailedMessage, *, cooldown: timedelta) -> bool:
    """
    Whether a message is *stuck* rather than merely waiting (#141), given how
    long the cool-down now in force lasts.

    A waiting message is one not retried before a time still ahead: a video
    whose captions may come later, or one held back by something that is not
    about the message at all. The second kind reads like the first and is not
    the same thing — such a failure counts no attempt (ADR 0007), so nothing
    ever gives the message up, and `BlBoKnm9O_o` was shown as an ordinary wait
    for seven days.

    This is the one place that rule is written, and the rule is:

        it is waiting, no attempt has ever been counted against it, and the
        whole of its wait — from its first failure to the end of the wait it
        is in now — is longer than the cool-down holding it, so more than one
        cool-down has held it.

    The last clause is what tells the two apart. A message first refused now
    waits exactly one cool-down: its first failure and that wait's start are
    the same moment, so its whole wait is that one wait's length and no more.
    One whose wait reaches further back than the wait it is in was already
    waiting when this one began, which takes a second cool-down. That is the
    spec's words — it has sat across more than one — and not a count of waits,
    which nothing records.

    `attempts == 0` is the rest of the state ADR 0007 creates. A message whose
    own attempts have failed is spending its three chances and will be given
    up by the third; one no attempt is counted against is spending nothing, so
    nothing will ever give it up and nothing it does will move it.

    What it cannot know, and so does not claim:

    - **Which kind of wait holds this message.** Two failures count no
      attempt, and both belong here: a platform refusing us (ADR 0007) and our
      own tooling being stale (#139, ADR 0011). A stale tool waits a flat hour
      at a time, which is the base or less, so while refusals have escalated
      the wait in force, a message waiting on our tooling is measured against
      the longer of the two and reads as stuck later than it is. It says what
      is holding it in its reason either way; that it is held at all is what
      this answers.
    - **Which cool-down was in force when the wait it is in was recorded.**
      Only the one in force now is recorded. A request the platform has
      answered since returns the count to the base (ADR 0010), so a message
      still waiting out a longer wait earned before that answer is measured
      against the base and can read as stuck on its first cool-down. The run
      of refusals that earned the longer wait did happen; this message may not
      have been there for all of it.
    """
    if row.given_up_at is not None or row.attempts > 0:
        return False
    if row.retry_after is None or row.retry_after <= now_utc():
        # Its turn has come: the next fetch retries it, so it is not waiting.
        return False
    return row.retry_after - row.first_failed_at > cooldown


async def stuck_per_source(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """
    How many of each source's waiting messages are stuck rather than merely
    waiting (#141), for the sources list.

    Here beside the rule it applies, and counted in Python, so that what
    "stuck" means is read where it is written rather than restated as SQL. The
    query narrows to the rows the rule could possibly say yes to — waiting,
    with no attempt counted — which is the backlog one platform's refusals are
    holding up: tens of rows, not a workspace's history.
    """
    cooldown = await cooldown_in_force()
    rows = await db.execute(
        select(FailedMessage).where(
            FailedMessage.workspace_id == workspace_id,
            FailedMessage.given_up_at.is_(None),
            FailedMessage.attempts == 0,
            FailedMessage.retry_after > now_utc(),
        )
    )
    counts: dict[uuid.UUID, int] = {}
    for row in rows.scalars():
        if is_stuck(row, cooldown=cooldown):
            counts[row.source_config_id] = counts.get(row.source_config_id, 0) + 1
    return counts


async def _find(db: AsyncSession, source_config_id: uuid.UUID, stable_id: str) -> FailedMessage | None:
    return (await db.execute(
        select(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.stable_id == stable_id,
        )
    )).scalar_one_or_none()


def _itself_and_its_wholes(stable_id: str) -> list[str]:
    """"youtube:v:chunk_0" → it, "youtube:v" and "youtube": every id it extends."""
    parts = stable_id.split(":")
    return [":".join(parts[:n]) for n in range(len(parts), 0, -1)]


def _can_never_succeed(exc: BaseException) -> bool:
    """
    Whether no later attempt could get this content, so it is given up now.

    Only a connector's own verdict says so, and a refusal's is no: it is
    waited out, not given up, whatever the platform said (ADR 0007, ADR 0009).
    """
    return isinstance(exc, ContentUnavailable) and exc.can_never_succeed()


def _counts_an_attempt(exc: BaseException) -> bool:
    """
    Whether this failure is the message's own, so it spends one of the three
    attempts it has (ADR 0005).

    Anything that is not a connector's verdict is: an ordinary bug met while
    processing one message says nothing about the next. A refusal and a failure
    of our own tooling are the two that are not — the first the platform's doing
    (ADR 0007), the second ours (ADR 0011) — and both are waited out instead, so
    nothing is given up for a reason that was never about it.
    """
    return not isinstance(exc, ContentUnavailable) or exc.counts_an_attempt()


def _retry_after(exc: BaseException, attempts: int):
    """When a failure that has now had this many attempts may be retried: None, at once."""
    gap = exc.wait_after(attempts) if isinstance(exc, ContentUnavailable) else None
    return now_utc() + gap if gap else None


def _summary(exc: BaseException) -> str:
    return safe_failure_summary(exc)[:_MAX_ERROR_CHARS]
