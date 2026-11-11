"""
Tests for #140 — a repeated refusal waits longer instead of re-arming an hour.

Every refusal recorded the same 60 minutes, so a block lasting days was
re-earned tick after tick with no end: 17 refusals between 2026-11-04 and
2026-11-11 on the channel source, each one setting the same hour again.

What is tested here is the arithmetic alone, which is pure: the cool-down a
refusal earns from the one before it. That it is read and written where every
fetch sees it is `test_refusal_store.py`; that a fetch escalates and resets
through it is in `test_captions.py`.

This does not shorten a block. At one caption request an hour we are not
refused for volume. It buys a wait as long as the platform has been refusing
us, and an end to re-arming the same hour.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.platform_refusal import Cooldown, cooldown_length, escalate

BASE = timedelta(hours=1)
CEILING = timedelta(hours=24)
NOON = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _refused(previous, *, at=NOON, base=BASE, ceiling=CEILING) -> Cooldown:
    return escalate(previous, now=at, base=base, ceiling=ceiling)


class TestTheFirstRefusalOfABlock:
    async def test_waits_the_base(self):
        assert _refused(None) == Cooldown(until=NOON + BASE, consecutive=1)

    async def test_after_the_platform_answered_us_waits_the_base_again(self):
        # consecutive == 0 is what a successful request leaves behind.
        answered = Cooldown(until=NOON - timedelta(minutes=5), consecutive=0)

        assert _refused(answered) == Cooldown(until=NOON + BASE, consecutive=1)

    async def test_long_after_the_last_wait_ended_waits_the_base(self):
        # Nothing is known about a spell in which nothing was asked — a paused
        # source, a stopped process — so it is not read as a block going on.
        old = Cooldown(until=NOON - timedelta(hours=2), consecutive=1)

        assert _refused(old) == Cooldown(until=NOON + BASE, consecutive=1)


class TestARefusalWhileWeWereOnlyJustLetBackIn:
    async def test_waits_longer_than_the_last_one_did(self):
        first = _refused(None)

        second = _refused(first, at=first.until)
        assert second.until - first.until == 2 * BASE
        assert second.consecutive == 2

    async def test_keeps_doubling_while_the_block_holds(self):
        waited = [BASE]
        cooldown = _refused(None)
        for _ in range(3):
            previous, cooldown = cooldown, _refused(cooldown, at=cooldown.until)
            waited.append(cooldown.until - previous.until)

        assert waited == [BASE, 2 * BASE, 4 * BASE, 8 * BASE]

    async def test_a_refusal_during_the_wait_itself_escalates_too(self):
        # Two fetches can meet the refusal before either recorded it (ADR 0009).
        first = _refused(None)

        assert _refused(first, at=NOON + timedelta(minutes=1)).consecutive == 2

    async def test_the_wait_is_remembered_for_its_own_length_after_it_ends(self):
        # The longer a platform has refused us, the longer a quiet spell has to
        # be before we read it as over.
        fourth = Cooldown(until=NOON, consecutive=4)  # an 8 h wait, ended at noon

        assert _refused(fourth, at=NOON + timedelta(hours=7)).consecutive == 5
        assert _refused(fourth, at=NOON + timedelta(hours=9)).consecutive == 1


class TestTheWaitHasACeiling:
    async def test_it_is_never_passed(self):
        cooldown = _refused(None)
        for _ in range(20):
            met_at = cooldown.until
            cooldown = _refused(cooldown, at=met_at)
            assert timedelta(0) < cooldown.until - met_at <= CEILING

    async def test_a_long_block_settles_at_it(self):
        # 60 min doubled reaches a day on the sixth refusal and stays there.
        assert cooldown_length(5, base=BASE, ceiling=CEILING) == timedelta(hours=16)
        assert cooldown_length(6, base=BASE, ceiling=CEILING) == CEILING
        assert cooldown_length(17, base=BASE, ceiling=CEILING) == CEILING

    async def test_the_refusals_in_a_row_are_still_counted_past_it(self):
        # What the log says, and the only honest answer to "how long has this
        # been going on": the wait stops growing, the count does not.
        at_the_ceiling = Cooldown(until=NOON, consecutive=30)

        assert _refused(at_the_ceiling, at=NOON).consecutive == 31

    async def test_a_ceiling_below_the_base_is_the_wait(self):
        # Nothing stops a person configuring that; it must not invert.
        assert cooldown_length(1, base=timedelta(hours=2), ceiling=BASE) == BASE


@pytest.mark.parametrize("consecutive", [0, 1])
def test_a_wait_is_never_shorter_than_the_base(consecutive):
    assert cooldown_length(consecutive, base=BASE, ceiling=CEILING) == BASE
