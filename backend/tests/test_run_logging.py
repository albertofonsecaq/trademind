"""
Mining and validation say in the log when they finish — #84.

The application never configured logging, so Python's root logger sat at
WARNING and every `log.info` was dropped: "Pattern mining complete" was written
and never printed. A run that had finished looked exactly like one still going;
the only way to tell was to watch the process's connections to Anthropic go
quiet. A background run that crashed logged its message without a traceback.

Offline: logging is configured onto a stream, and the runs are faked.
"""
import io
import logging
import uuid
from contextlib import asynccontextmanager

import pytest

from app.core.log_setup import configure_logging


@pytest.fixture
def stream():
    """A stream to configure logging onto, with the `app` logger put back afterwards."""
    logger = logging.getLogger("app")
    saved = (logger.handlers[:], logger.level)
    out = io.StringIO()
    yield out
    logger.handlers[:], logger.level = saved


class TestConfiguredLogging:
    def test_an_info_line_from_the_application_is_printed(self, stream):
        configure_logging("INFO", stream=stream)

        logging.getLogger("app.services.pattern_mining_service").info("Pattern mining complete")

        assert "Pattern mining complete" in stream.getvalue()

    def test_a_line_says_when_and_where_it_came_from(self, stream):
        configure_logging("INFO", stream=stream)

        logging.getLogger("app.services.validation_service").info("Validation complete")

        [line] = stream.getvalue().splitlines()
        assert line[:4].isdigit()  # starts with the year of a timestamp
        assert "app.services.validation_service" in line
        assert "INFO" in line

    def test_third_party_info_is_not_printed(self, stream):
        configure_logging("INFO", stream=stream)

        for noisy in ("httpx", "sqlalchemy.engine", "yfinance", "apscheduler"):
            logging.getLogger(noisy).info("chatter")

        assert stream.getvalue() == ""

    def test_the_level_is_whatever_it_is_set_to(self, stream):
        configure_logging("DEBUG", stream=stream)
        logging.getLogger("app.connectors.market_data").debug("no bars yet for AA")

        configure_logging("WARNING", stream=stream)
        logging.getLogger("app.services.validation_service").info("Validation complete")

        assert "no bars yet for AA" in stream.getvalue()
        assert "Validation complete" not in stream.getvalue()

    def test_configuring_twice_does_not_print_twice(self, stream):
        configure_logging("INFO", stream=stream)
        configure_logging("INFO", stream=stream)

        logging.getLogger("app.api").info("once")

        assert stream.getvalue().count("once") == 1

    def test_a_warning_is_printed_once(self, stream, capsys):
        configure_logging("INFO", stream=stream)

        logging.getLogger("app.connectors.market_data").warning("yfinance does not know BTCUSD")

        assert stream.getvalue().count("BTCUSD") == 1
        assert "BTCUSD" not in capsys.readouterr().err

    def test_the_lines_still_reach_the_root_logger(self, stream, caplog):
        """What lets every test that uses caplog keep seeing app.* lines."""
        configure_logging("INFO", stream=stream)

        with caplog.at_level(logging.INFO):
            logging.getLogger("app.services.pattern_mining_service").info("Pattern mining complete")

        assert "Pattern mining complete" in caplog.text

    def test_the_app_is_configured_at_the_level_in_settings(self, stream):
        """What `uvicorn app.main:app` imports is what configures logging."""
        import importlib

        import app.main
        from app.core.config import settings

        importlib.reload(app.main)

        assert logging.getLogger("app").level == logging.getLevelName(settings.LOG_LEVEL)
        assert logging.getLogger("app").handlers

    def test_a_level_that_is_not_one_fails_naming_the_setting(self):
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="LOG_LEVEL"):
            Settings(LOG_LEVEL="VERBOSE")


@asynccontextmanager
async def _no_session():
    yield object()


async def _crash(*args, **kwargs):
    raise RuntimeError("the database went away")


class TestABackgroundRunThatCrashes:
    @pytest.mark.parametrize("module, runner, service, function", [
        ("app.api.strategy_cards", "_run_mining_bg",
         "app.services.pattern_mining_service", "run_pattern_mining"),
        ("app.api.validation", "_run_validation_bg",
         "app.services.validation_service", "run_validation"),
    ])
    async def test_logs_the_traceback(self, monkeypatch, caplog, module, runner, service, function):
        import importlib

        api = importlib.import_module(module)

        monkeypatch.setattr(api, "AsyncSessionLocal", _no_session)
        monkeypatch.setattr(importlib.import_module(service), function, _crash)

        with caplog.at_level(logging.ERROR):
            await getattr(api, runner)(uuid.uuid4())

        [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert record.exc_info is not None
        assert "the database went away" in caplog.text
