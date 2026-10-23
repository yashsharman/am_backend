from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel


class CheckinCreate(BaseModel):
    qr_token: str
    event_id: str
    method: Literal["qr_scan", "manual_search"] = "qr_scan"
    client_timestamp: Optional[datetime] = None


class OfflineCheckin(BaseModel):
    qr_token: str
    event_id: str
    method: Literal["qr_scan", "manual_search"] = "qr_scan"
    client_timestamp: Optional[datetime] = None
    checked_in_by: Optional[str] = None


class CheckinOut(BaseModel):
    id: str
    pass_id: str
    registration_id: str
    event_id: str
    checked_in_at: datetime
    checked_in_by: Optional[str] = None
    method: str
    offline_synced: bool

    model_config = {"from_attributes": True}


class CheckinResult(BaseModel):
    success: bool
    attendee_name: str
    pass_type: str
    remaining_credits: int
    registration_id: str
    check_in_time: datetime
