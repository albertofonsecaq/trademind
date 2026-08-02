"""
Platform connection management (Telegram OTP auth flow).
Per-workspace — no shared sessions across workspaces.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.encryption import encrypt_credential, decrypt_credential
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.models.platform_connection import PlatformConnection

router = APIRouter(prefix="/workspaces", tags=["connections"])


async def _assert_owner(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.role == "owner",
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Workspace owner only")


class TelegramStartRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str
    label: str = "Main Telegram"


class TelegramStartResponse(BaseModel):
    connection_id: str
    message: str


class TelegramVerifyRequest(BaseModel):
    code: str


class DialogItem(BaseModel):
    id: str
    name: str
    type: str
    identifier: str


class ConnectionOut(BaseModel):
    id: uuid.UUID
    platform: str
    label: str
    status: str
    connected_at: datetime | None

    model_config = {"from_attributes": True}


@router.post("/{workspace_id}/connections/telegram/start", response_model=TelegramStartResponse)
async def telegram_start(
    workspace_id: uuid.UUID,
    payload: TelegramStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)

    from app.connectors.telegram import start_telegram_auth
    session_str, phone_code_hash = await start_telegram_auth(
        api_id=payload.api_id,
        api_hash=payload.api_hash,
        phone=payload.phone,
    )

    conn = PlatformConnection(
        workspace_id=workspace_id,
        platform="telegram",
        label=payload.label,
        session_credential=encrypt_credential(session_str),
        connected_by_user_id=current_user.id,
        status="pending",
        auth_metadata={
            "phone": payload.phone,
            "phone_code_hash": phone_code_hash,
            "api_id": payload.api_id,
            "api_hash": payload.api_hash,
        },
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    return TelegramStartResponse(
        connection_id=str(conn.id),
        message="OTP sent. Call /verify with the code from your Telegram app.",
    )


@router.post("/{workspace_id}/connections/telegram/{connection_id}/verify", response_model=ConnectionOut)
async def telegram_verify(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: TelegramVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)

    result = await db.execute(
        select(PlatformConnection).where(
            PlatformConnection.id == connection_id,
            PlatformConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.status != "pending":
        raise HTTPException(status_code=409, detail="Connection is not in pending state")

    meta = conn.auth_metadata or {}
    pending_session = decrypt_credential(conn.session_credential)

    from app.connectors.telegram import complete_telegram_auth
    final_session = await complete_telegram_auth(
        api_id=int(meta["api_id"]),
        api_hash=meta["api_hash"],
        session_string=pending_session,
        phone=meta["phone"],
        code=payload.code,
        phone_code_hash=meta["phone_code_hash"],
    )

    conn.session_credential = encrypt_credential(final_session)
    # Keep api_id/api_hash in auth_metadata (needed by the connector at fetch time)
    conn.auth_metadata = {"api_id": meta["api_id"], "api_hash": meta["api_hash"]}
    conn.status = "active"
    conn.connected_at = datetime.utcnow()
    conn.last_verified_at = datetime.utcnow()
    await db.commit()
    await db.refresh(conn)

    return conn


@router.post("/{workspace_id}/connections/telegram/{connection_id}/resend")
async def telegram_resend(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)

    result = await db.execute(
        select(PlatformConnection).where(
            PlatformConnection.id == connection_id,
            PlatformConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.status != "pending":
        raise HTTPException(status_code=409, detail="Connection is not in pending state")

    meta = conn.auth_metadata or {}
    session_str = decrypt_credential(conn.session_credential)

    from app.connectors.telegram import resend_telegram_code
    new_session, new_hash = await resend_telegram_code(
        api_id=int(meta["api_id"]),
        api_hash=meta["api_hash"],
        session_string=session_str,
        phone=meta["phone"],
    )
    conn.session_credential = encrypt_credential(new_session)
    conn.auth_metadata = {**meta, "phone_code_hash": new_hash}
    await db.commit()
    return {"message": "Code resent"}


@router.get("/{workspace_id}/connections/telegram/{connection_id}/dialogs", response_model=list[DialogItem])
async def list_telegram_dialogs(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)
    result = await db.execute(
        select(PlatformConnection).where(
            PlatformConnection.id == connection_id,
            PlatformConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if conn.status != "active":
        raise HTTPException(status_code=409, detail="Connection is not active")

    meta = conn.auth_metadata or {}
    session_string = decrypt_credential(conn.session_credential)

    from app.connectors.telegram import list_dialogs
    return await list_dialogs(
        api_id=int(meta["api_id"]),
        api_hash=meta["api_hash"],
        session_string=session_string,
    )


@router.get("/{workspace_id}/connections", response_model=list[ConnectionOut])
async def list_connections(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)
    result = await db.execute(
        select(PlatformConnection).where(PlatformConnection.workspace_id == workspace_id)
    )
    return result.scalars().all()


@router.delete("/{workspace_id}/connections/{connection_id}", status_code=204)
async def revoke_connection(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_owner(db, workspace_id, current_user.id)
    result = await db.execute(
        select(PlatformConnection).where(
            PlatformConnection.id == connection_id,
            PlatformConnection.workspace_id == workspace_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    conn.status = "revoked"
    await db.commit()
