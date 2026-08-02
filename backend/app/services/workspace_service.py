import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.core.config import settings


async def create_private_workspace(db: AsyncSession, *, owner_id: uuid.UUID, name: str) -> Workspace:
    """Create a private workspace and set the owner as its sole member."""
    workspace = Workspace(
        owner_user_id=owner_id,
        name=name,
        payment_enabled=False,
        topic_scope=settings.DEFAULT_TOPIC_SCOPE,
    )
    db.add(workspace)
    await db.flush()

    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=owner_id,
        role="owner",
    )
    db.add(member)
    await db.flush()

    return workspace
