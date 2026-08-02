import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


class WorkspaceOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID
    monthly_budget_cap: Decimal | None
    payment_enabled: bool
    topic_scope: str
    created_at: datetime


class WorkspaceMemberOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    joined_at: datetime
