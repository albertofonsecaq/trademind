"""
A message's content its connector could not get (#125, #126).

The connector hands the message on carrying one of these instead of its
content, and the pipeline records it as a failed message (ADR 0005), retried
as the failure says: a platform's refusal is waited out without counting
(ADR 0007), and a video with no transcript is retried with growing gaps.
"""
from datetime import timedelta


class ContentUnavailable(Exception):
    """Why a message's content could not be had."""

    def wait_after(self, attempts: int) -> timedelta | None:
        """How long to wait before retrying, after this many attempts; None: at once."""
        return None

    def can_never_succeed(self) -> bool:
        """
        Whether no attempt could ever get this content, so the message is given
        up at once rather than retried (#137, ADR 0005).

        False unless a subclass knows better: a failure is retried by default,
        because the cost of retrying something hopeless is three attempts, and
        the cost of giving up on something temporary is the content itself.
        """
        return False
