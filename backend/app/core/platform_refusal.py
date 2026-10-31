"""
A platform refusing us for a while, which is waited out (#125, ADR 0007).

Not the message's fault, and not a reason to stop the run: what the platform
refused becomes a failed message that does not count an attempt and is not
retried until `until`, while everything else carries on.
"""
from datetime import datetime

from app.core.content_unavailable import ContentUnavailable


class PlatformRefused(ContentUnavailable):
    """The platform refused, and will not be asked again before `until`."""

    def __init__(self, message: str, *, until: datetime):
        super().__init__(message)
        self.until = until
