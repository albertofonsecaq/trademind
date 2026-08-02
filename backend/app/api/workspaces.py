import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.schemas.workspace import WorkspaceOut, WorkspaceMemberOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this workspace")
    return member


@router.get("/current", response_model=WorkspaceOut)
async def get_current_workspace(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.default_workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No workspace found")
    result = await db.execute(select(Workspace).where(Workspace.id == current_user.default_workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
async def list_members(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
    return result.scalars().all()
