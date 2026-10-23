from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.core.deps import get_db, get_current_user, require_admin
from app.core.security import hash_password
from app.models import serialize_doc
from app.models.user import UserCreate, UserUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _object_id(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
    return ObjectId(id_str)


@router.get("/dashboard/{event_id}")
async def get_dashboard(
    event_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Return all 7 dashboard metrics for an event."""
    oid = _object_id(event_id)
    event = await db["events"].find_one({"_id": oid, "is_deleted": {"$ne": True}})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    total_capacity = event.get("capacity", 0)

    # Count registrations directly tied to this event (one_day passes)
    registered = await db["registrations"].count_documents({"event_id": oid})

    # Exclusive pass holders who have ever checked in to this event
    exclusive_members_checked_in = await db["checkins"].count_documents(
        {"event_id": oid}
    )
    # All active exclusive passes (potential exclusive members)
    exclusive_members = await db["passes"].count_documents(
        {"pass_type": "exclusive", "is_active": True}
    )

    checked_in = await db["checkins"].count_documents({"event_id": oid})
    payment_pending = await db["registrations"].count_documents(
        {"event_id": oid, "payment_status": "pending"}
    )
    not_arrived = max(0, registered - checked_in)
    available_slots = max(0, total_capacity - registered)

    return {
        "event_id": event_id,
        "event_name": event.get("name", ""),
        "total_capacity": total_capacity,
        "registered": registered,
        "available_slots": available_slots,
        "payment_pending": payment_pending,
        "checked_in": checked_in,
        "not_arrived": not_arrived,
        "exclusive_members": exclusive_members,
    }


@router.get("/users")
async def list_users(
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """List all staff users (admin only)."""
    users = []
    async for u in db["users"].find({}).sort("created_at", -1):
        doc = serialize_doc(u)
        doc.pop("hashed_password", None)
        users.append(doc)
    return {"total": len(users), "items": users}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Create a staff account (admin only)."""
    existing = await db["users"].find_one({"email": body.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    doc = {
        "name": body.name.strip(),
        "email": body.email.lower().strip(),
        "hashed_password": hash_password(body.password),
        "role": body.role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["users"].insert_one(doc)
    created = await db["users"].find_one({"_id": result.inserted_id})
    out = serialize_doc(created)
    out.pop("hashed_password", None)
    return out


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Update a staff user's details (admin only)."""
    oid = _object_id(user_id)
    updates = {}
    raw = body.model_dump(exclude_none=True)

    for field, val in raw.items():
        if field == "password":
            updates["hashed_password"] = hash_password(val)
        elif field == "email":
            updates["email"] = val.lower().strip()
        else:
            updates[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db["users"].find_one_and_update(
        {"_id": oid}, {"$set": updates}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    out = serialize_doc(result)
    out.pop("hashed_password", None)
    return out


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Deactivate a staff user (soft delete via is_active=False). Admin only."""
    oid = _object_id(user_id)
    if str(current_user["_id"]) == user_id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    result = await db["users"].update_one({"_id": oid}, {"$set": {"is_active": False}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
