from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr


class RegistrationCreate(BaseModel):
    attendee_name: str
    phone: str
    email: Optional[EmailStr] = None
    pass_type: Literal["one_day", "exclusive"]
    event_id: Optional[str] = None  # required for one_day
    payment_status: Literal["pending", "verified", "failed"] = "pending"
    payment_reference: Optional[str] = ""
    notes: Optional[str] = ""


class RegistrationUpdate(BaseModel):
    attendee_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    payment_status: Optional[Literal["pending", "verified", "failed"]] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None


class RegistrationOut(BaseModel):
    id: str
    registration_id: str
    attendee_name: str
    phone: str
    email: Optional[str] = None
    pass_id: Optional[str] = None
    event_id: Optional[str] = None
    payment_status: str
    payment_reference: Optional[str] = None
    registered_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
