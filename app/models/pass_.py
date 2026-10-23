from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel


class PassOut(BaseModel):
    id: str
    pass_type: str
    registration_id: str
    qr_token: str
    valid_event_id: Optional[str] = None
    total_credits: int
    used_credits: int
    remaining_credits: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PassWithRegistration(PassOut):
    attendee_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    reg_id: Optional[str] = None
    payment_status: Optional[str] = None
