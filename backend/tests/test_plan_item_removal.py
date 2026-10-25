"""
Removing a plan item says nothing about whether it was there.

Two sibling deletes in this API disagreed about an item that is already gone:
deleting an ask record succeeded (#74) and deleting a plan item was a 404. A
delete states the end state it wants, and that state is already true either
way, so both now succeed — and the asymmetry is gone rather than written down
somewhere as a thing to remember.

The 404 was not only inconsistent. `MyPlan` awaits the call with no catch, so a
second click threw and the row it had just removed stayed on screen: the one
error the client was most likely to see was the one it handled worst.

Offline like the rest of the suite.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import plan_items as plan_items_api
from app.models.plan_item import PlanItem
from tests.conftest import FakeSession


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), preferred_language="en")


async def _remove(*, user, workspace_id, item_id, membership: bool = True, matched: int = 1):
    """The remove endpoint, with the statement it ran for the delete."""
    seen: dict = {}

    def capture(statement):
        seen["statement"] = statement
        return matched                          # what the DELETE claimed

    db = FakeSession(rows={PlanItem: capture}, membership=membership)
    returned = await plan_items_api.remove_plan_item(
        workspace_id=workspace_id, item_id=item_id, current_user=user, db=db
    )
    return seen, db, returned


class TestRemovingAPlanItem:
    async def test_the_delete_is_scoped_to_the_caller(self):
        """
        Verified to bite: drop the `user_id` filter and this fails. A delete
        matching on the id alone would take another member's plan item out of
        their plan on request.
        """
        user, item_id = _user(), uuid.uuid4()

        seen, db, _ = await _remove(user=user, workspace_id=uuid.uuid4(), item_id=item_id)

        sql = str(seen["statement"]).lower()
        assert sql.startswith("delete from plan_items")
        assert {user.id, item_id} <= set(seen["statement"].compile().params.values())
        assert db.commits == 1

    async def test_an_item_that_is_already_gone_is_not_an_error(self):
        """
        What a double click is. It used to be a 404, and the page that makes
        the call does not catch one.
        """
        user = _user()

        _, _, returned = await _remove(
            user=user, workspace_id=uuid.uuid4(), item_id=uuid.uuid4(), matched=0
        )

        assert returned is None                 # 204, same as a real one

    async def test_another_users_item_is_not_a_target_and_is_not_reported(self):
        """
        Both halves: the delete is filtered to the caller, so another member's
        item is not among the rows it could match, and the caller is told
        nothing that distinguishes that from an item which never existed.
        """
        user, not_mine = _user(), uuid.uuid4()

        seen, _, returned = await _remove(
            user=user, workspace_id=uuid.uuid4(), item_id=not_mine, matched=0
        )

        assert user.id in set(seen["statement"].compile().params.values())
        assert returned is None

    async def test_the_strategy_card_it_pointed_at_is_untouched(self):
        """
        A plan item is a user's stance on a strategy card, not the card. The
        card is the workspace's and outlives anybody's opinion of it.
        """
        user = _user()

        _, db, _ = await _remove(user=user, workspace_id=uuid.uuid4(), item_id=uuid.uuid4())

        assert db.touched("plan_items")
        assert not db.touched("strategy_cards")

    async def test_a_non_member_is_refused_before_anything_is_deleted(self):
        user = _user()

        with pytest.raises(HTTPException) as refused:
            await _remove(
                user=user, workspace_id=uuid.uuid4(), item_id=uuid.uuid4(), membership=False
            )

        assert refused.value.status_code == 403
