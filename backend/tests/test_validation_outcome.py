"""
What a validation run reports about itself — #86.

Each strategy card it checked counts as updated; a card whose check raised is a
failure the run carried on past, named by its symbol scope and setup type with
the reason, so the Library can say which card it was.
"""
import pytest

from app.services import validation_service
from tests.conftest import make_card, make_workspace

pytestmark = pytest.mark.db


async def test_counts_the_cards_checked_and_names_the_one_that_failed(db_session, monkeypatch):
    workspace = await make_workspace(db_session)
    broken = await make_card(db_session, workspace)
    broken.symbol_scope, broken.setup_type = "BTCUSD", "wedge"
    await db_session.flush()
    healthy = await make_card(db_session, workspace)  # EURUSD/breakout

    async def _check(db, card, market, window):
        if card.id == broken.id:
            raise RuntimeError("price provider timed out")

    monkeypatch.setattr(validation_service, "validate_strategy_card", _check)

    outcome = await validation_service.run_validation(db_session, workspace.id)

    assert (outcome.cards_created, outcome.cards_updated) == (0, 1)
    assert outcome.failures == [{"subject": "BTCUSD/wedge", "reason": "price provider timed out"}]
    assert outcome.stopped_because is None
    assert healthy.id != broken.id
