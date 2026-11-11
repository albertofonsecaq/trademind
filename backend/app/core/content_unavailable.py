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

    def counts_an_attempt(self) -> bool:
        """
        Whether this failure is the message's own, so it spends one of the three
        attempts it has (#139, ADR 0005).

        True unless a subclass knows better. A platform refusing us does not
        count (ADR 0007), and neither does our own tooling failing (ADR 0010):
        a message whose attempts were spent on our bug is given up and has to be
        sent back by hand, while one that waited is picked up by an ordinary
        retry as soon as the bug is fixed.
        """
        return True
