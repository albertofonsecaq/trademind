"""
Validation works in dates, and those dates are UTC — #35, for #8.

Two things here decide numbers a user sees, and both used the *machine's* date
until #35:

- `recency_weight` weights an idea by how long ago it traded, which feeds the
  win rate and therefore the confidence tier a card is shown with;
- `_check_bars` decides whether an outcome is still `open`, which is a value
  written to the database.

So a server in Sydney and one in Chicago would have weighted the same idea
differently, and called a window elapsed up to a day apart. Nothing in this
repo sets TZ, so nothing moved in practice — which is exactly why it needed a
test rather than a reading of the diff.

These are offline: pure functions over dates, no database needed.
"""
from datetime import date, timedelta

import pytest

from app.services.validation_service import _check_bars, recency_weight


class TestRecencyWeight:
    def test_today_weighs_the_most(self):
        from app.core.clock import now_utc

        assert recency_weight(now_utc().date()) == pytest.approx(1.0)

    def test_older_ideas_weigh_less(self):
        from app.core.clock import now_utc

        today = now_utc().date()

        assert recency_weight(today - timedelta(days=30)) < recency_weight(today)

    def test_it_decays_rather_than_cutting_off(self):
        from app.core.clock import now_utc

        long_ago = now_utc().date() - timedelta(days=365)

        assert 0 < recency_weight(long_ago) < 0.5

    def test_a_missing_date_weighs_something_rather_than_nothing(self):
        """An idea with no trade date still counts, at half weight."""
        assert recency_weight(None) == 0.5

    def test_it_uses_the_utc_date_whatever_the_machine_says(self, monkeypatch):
        """
        The #35 change, pinned: the weight for a given idea must not depend on
        where the process runs. Moving the machine's clock a day either side of
        UTC must not move the answer.
        """
        import time

        from app.core.clock import now_utc

        trade_date = now_utc().date() - timedelta(days=10)
        reference = recency_weight(trade_date)

        for zone in ("Pacific/Kiritimati", "Pacific/Midway"):   # UTC+14 and UTC-11
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            assert recency_weight(trade_date) == reference, zone


class TestTheOpenWindow:
    """
    `_check_bars` returns "open" while a trade's window has not elapsed. That
    verdict is stored on an outcome check, so the boundary is a stored value.
    """

    def _bars(self, start: date, days: int) -> list[dict]:
        # prices that never reach target or stop, so only the window decides
        return [
            {"date": start + timedelta(days=i), "high": 1.01, "low": 0.99}
            for i in range(days)
        ]

    def _check(self, trade_date: date, window_days: int, bar_days: int) -> dict:
        return _check_bars(
            bars=self._bars(trade_date, bar_days),
            trade_date=trade_date,
            action="long",
            # floats, as the production caller passes them
            entry=1.00,
            target=1.50,
            stop=0.50,
            window_days=window_days,
        )

    def test_a_window_still_running_is_open(self):
        from app.core.clock import now_utc

        trade_date = now_utc().date() - timedelta(days=2)

        assert self._check(trade_date, window_days=30, bar_days=3)["outcome"] == "open"

    def test_a_window_long_past_is_not_open(self):
        from app.core.clock import now_utc

        trade_date = now_utc().date() - timedelta(days=90)

        assert self._check(trade_date, window_days=30, bar_days=31)["outcome"] != "open"

    def test_the_boundary_does_not_move_with_the_machines_zone(self, monkeypatch):
        """
        The #35 change, pinned where it writes data: a trade whose window ends
        today must get the same verdict in Kiritimati as in Midway.
        """
        import time

        from app.core.clock import now_utc

        trade_date = now_utc().date() - timedelta(days=30)
        verdicts = []

        for zone in ("Pacific/Kiritimati", "Pacific/Midway"):
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            verdicts.append(self._check(trade_date, window_days=30, bar_days=31)["outcome"])

        assert verdicts[0] == verdicts[1], verdicts
