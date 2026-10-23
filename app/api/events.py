from datetime import datetime, timezone
from typing import Annotated, Optional, Literal
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.core.deps import get_db, get_current_user, require_admin
from app.models import serialize_doc
from app.models.event import EventCreate, EventUpdate

router = APIRouter(prefix="/api/events", tags=["events"])


def _object_id(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
    return ObjectId(id_str)


@router.get("")
async def list_events(
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    status: Optional[Literal["upcoming", "active", "completed"]] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List events with optional status filter and pagination."""
    query: dict = {"is_deleted": {"$ne": True}}
    if status:
        query["status"] = status

    total = await db["events"].count_documents(query)
    skip = (page - 1) * page_size
    cursor = db["events"].find(query).sort("date", -1).skip(skip).limit(page_size)
    events = []
    async for doc in cursor:
        events.append(serialize_doc(doc))

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(total / page_size) if total else 0,
        "items": events,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventCreate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Create a new event (admin only)."""
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    doc["created_by"] = current_user["_id"]
    doc["is_deleted"] = False

    result = await db["events"].insert_one(doc)
    created = await db["events"].find_one({"_id": result.inserted_id})
    return serialize_doc(created)


@router.get("/{event_id}")
async def get_event(
    event_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Return event detail with live stats."""
    oid = _object_id(event_id)
    event = await db["events"].find_one({"_id": oid, "is_deleted": {"$ne": True}})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Gather stats
    registered = await db["registrations"].count_documents({"event_id": oid})
    # For exclusive passes, registrations without direct event_id still check in to events
    exclusive_count = await db["passes"].count_documents(
        {"pass_type": "exclusive", "is_active": True}
    )
    checked_in = await db["checkins"].count_documents({"event_id": oid})
    payment_pending = await db["registrations"].count_documents(
        {"event_id": oid, "payment_status": "pending"}
    )
    not_arrived = registered - checked_in if registered >= checked_in else 0
    capacity = event.get("capacity", 0)
    available_slots = max(0, capacity - registered)

    result = serialize_doc(event)
    result.update(
        {
            "registered": registered,
            "checked_in": checked_in,
            "available_slots": available_slots,
            "payment_pending": payment_pending,
            "not_arrived": not_arrived,
            "exclusive_members": exclusive_count,
        }
    )
    return result


@router.patch("/{event_id}")
async def update_event(
    event_id: str,
    body: EventUpdate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Update event fields (admin only)."""
    oid = _object_id(event_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db["events"].find_one_and_update(
        {"_id": oid, "is_deleted": {"$ne": True}},
        {"$set": updates},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Event not found")
    return serialize_doc(result)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Soft-delete an event (admin only)."""
    oid = _object_id(event_id)
    result = await db["events"].update_one(
        {"_id": oid, "is_deleted": {"$ne": True}},
        {"$set": {"is_deleted": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
