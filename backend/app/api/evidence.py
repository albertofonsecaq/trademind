"""
Evidence API — what a member can see of a piece of evidence beyond its text.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analysis_runs import assert_member
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.evidence_image import EvidenceImage
from app.models.user import User

router = APIRouter(prefix="/workspaces", tags=["evidence"])


@router.get("/{workspace_id}/evidence/{evidence_id}/image")
async def get_evidence_image(
    workspace_id: uuid.UUID,
    evidence_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The picture a piece of image evidence was read from (#108)."""
    await assert_member(db, workspace_id, current_user.id)

    image = (await db.execute(
        select(EvidenceImage).where(
            EvidenceImage.evidence_item_id == evidence_id,
            # Another workspace's evidence is simply not in this one.
            EvidenceImage.workspace_id == workspace_id,
        )
    )).scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="No image for this evidence")

    # Private: it is one workspace's, and a shared cache must not keep it.
    return Response(content=image.data, media_type=image.mime_type,
                    headers={"Cache-Control": "private, max-age=86400"})
