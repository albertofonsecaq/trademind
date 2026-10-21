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


# ── the platform connection the source fetches through ──────────────────────
#
# A second question, asked with its own function rather than folded into
# blocked_reason: the pause question is asked at two different moments (the poll
# asks the source half before it has loaded the workspace row, and both halves
# after), so a caller at the first moment would have to pass a placeholder for
# the connection it has not looked at yet — and a placeholder that means "do not
# check" is how a gate goes quietly missing, which is the thing this module
# exists to prevent.

CONNECTION_MISSING = "connection_missing"
CONNECTION_UNUSABLE = "connection_unusable"

# Platforms that fetch as a user, so a source on one is unfetchable without a
# working platform connection. YouTube authenticates with a server-side API key
# instead, so a source on it having no connection is normal, not broken.
_PLATFORMS_FETCHING_AS_A_USER = frozenset({"telegram"})


def connection_blocked_reason(
    *, source_type: str, connection_status: str | None
) -> str | None:
    """
    Why this source's platform connection cannot fetch, or None if it can.

    `connection_status` is None when the source has no connection at all — never
    connected, or the row is gone. That is reported separately from a connection
    that exists but is revoked or still pending, because the user fixes them
    differently: one is "connect an account on that platform", the other
    "reconnect this one".

    The poll has always refused these sources. The on-demand endpoints accepted
    them, returned 202, and then dropped the work in the background (#49).
    """
    if source_type not in _PLATFORMS_FETCHING_AS_A_USER:
        return None
    if connection_status is None:
        return CONNECTION_MISSING
    if connection_status != "active":
        return CONNECTION_UNUSABLE
    return None
