import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    preferred_language: str
    platform_role: str
    default_workspace_id: uuid.UUID | None
    created_at: datetime


class UserUpdateRequest(BaseModel):
    preferred_language: str | None = None
