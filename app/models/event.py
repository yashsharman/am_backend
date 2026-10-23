from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    name: str
    date: datetime
    venue: str
    description: Optional[str] = ""
    capacity: int = Field(gt=0)
    is_exclusive_eligible: bool = False
    status: Literal["upcoming", "active", "completed"] = "upcoming"


class EventUpdate(BaseModel):
    name: Optional[str] = None
    date: Optional[datetime] = None
    venue: Optional[str] = None
    description: Optional[str] = None
    capacity: Optional[int] = Field(default=None, gt=0)
    is_exclusive_eligible: Optional[bool] = None
    status: Optional[Literal["upcoming", "active", "completed"]] = None


class EventOut(BaseModel):
    id: str
    name: str
    date: datetime
    venue: str
    description: str
    capacity: int
    is_exclusive_eligible: bool
    status: str
    created_at: datetime
    created_by: Optional[str] = None

    model_config = {"from_attributes": True}


class EventStats(EventOut):
    registered: int = 0
    checked_in: int = 0
    available_slots: int = 0
    payment_pending: int = 0
    not_arrived: int = 0
    exclusive_members: int = 0
