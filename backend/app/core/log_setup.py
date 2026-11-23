"""
Where the application's own log lines go — #84.

Nothing configured logging, so Python's root logger sat at WARNING and every
`log.info` was dropped: "Pattern mining complete" was written and never
printed, and a finished run looked exactly like one still going.

Only the application's loggers (`app.*`) get a handler and a level here. Third
parties — HTTP clients, SQLAlchemy, yfinance, the scheduler — are left as they
were, which prints their warnings and nothing below; uvicorn keeps its own.
"""
import logging
import sys
from typing import TextIO

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Print the application's log lines at `level` and above. Safe to call again."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))

    logger = logging.getLogger("app")
    logger.handlers[:] = [handler]
    logger.setLevel(level.upper())
    # Still propagates, so pytest's caplog and anything else on the root logger
    # sees these lines. Python's last-resort printer only runs when no handler
    # exists anywhere up the chain, so a warning is not printed twice.
