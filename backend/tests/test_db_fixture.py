"""
The `db` fixture's own guarantee — #32.

These tests exist because the rest of #8 will be written against this fixture,
and the promise it makes ("a run leaves no rows behind") is the kind that is
easy to state and easy to break. The dangerous case is a test that *commits*:
the fixture used to roll back the session's own transaction, which a commit has
already ended, so rows would survive into the contributor's database.

The two tests below are a pair, and depend on running in file order: the first
writes and commits, the second looks for what it wrote.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.user import User

pytestmark = pytest.mark.db

# Fixed, so the second test can look for exactly what the first one wrote.
COMMITTED_EMAIL = "fixture-commit-check@example.com"


class TestEvenACommitLeavesNothing:
    async def test_write_and_commit(self, db_session):
        db_session.add(
            User(id=uuid.uuid4(), email=COMMITTED_EMAIL, password_hash="x")
        )
        await db_session.commit()          # a real commit, as far as the test knows

        found = (
            await db_session.execute(select(User.id).where(User.email == COMMITTED_EMAIL))
        ).scalars().first()
        assert found is not None           # visible within the test, as it should be

    async def test_and_then_it_is_gone(self, db_session):
        """
        A fresh fixture, a fresh transaction: if the commit above had really
        committed, this row would still be here.
        """
        found = (
            await db_session.execute(select(User.id).where(User.email == COMMITTED_EMAIL))
        ).scalars().first()

        assert found is None
