"""
One place that decides whether a source may be fetched right now.

Four paths ask the same question — the recurring poll, the two on-demand
endpoints, and the background runners that re-read after the work was queued —
and they were each spelling out their own version of it. Review of #13 found one
of them had silently gone missing in an edit, which is exactly what duplicated
conditions do, so the question lives here and the callers map the answer to
whatever they need: an HTTP status, a log line, a cancelled job's reason.

Deliberately not included: the payment and budget gates. Those are the platform
protecting its margin and live in billing_service; these two are the user's own
choices about their own ingestion (#10, #13).
"""

SOURCE_PAUSED = "source_paused"
WORKSPACE_PAUSED = "workspace_paused"


def blocked_reason(*, source_paused: bool, workspace_paused: bool) -> str | None:
    """
    Why this source must not be fetched, or None if it may be.

    Takes plain booleans rather than the rows themselves: the poll reads its
    values into a snapshot before any fetching starts, because re-reading an
    expired ORM attribute mid-tick raises MissingGreenlet and kills the whole
    tick (#2).

    The source's own pause is reported first: it is the more specific answer,
    and the one the user acted on most recently.
    """
    if source_paused:
        return SOURCE_PAUSED
    if workspace_paused:
        return WORKSPACE_PAUSED
    return None
