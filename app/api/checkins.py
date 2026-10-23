from datetime import datetime, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from pymongo import errors as pymongo_errors

from app.core.deps import get_db, get_current_user
from app.models import serialize_doc
from app.models.checkin import CheckinCreate, OfflineCheckin

router = APIRouter(prefix="/api/checkins", tags=["checkins"])


def _object_id(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
    return ObjectId(id_str)


async def _perform_checkin(
    db: AsyncIOMotorDatabase,
    qr_token: str,
    event_id: str,
    method: str,
    checked_in_by: ObjectId,
    client_timestamp: Optional[datetime] = None,
    offline_synced: bool = False,
) -> dict:
    """
    Core check-in logic (shared by scan and offline sync).
    Returns result dict or raises HTTPException with an error code.
    """
    # 1. Find pass by qr_token
    pass_doc = await db["passes"].find_one({"qr_token": qr_token})
    if not pass_doc:
        raise HTTPException(status_code=404, detail="Pass not found for this QR token")

    if not pass_doc.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "pass_inactive", "message": "This pass is inactive"},
        )

    # 2. Find registration + check payment
    reg = await db["registrations"].find_one({"_id": pass_doc["registration_id"]})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    if reg.get("payment_status") != "verified":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "payment_pending",
                "message": f"Payment status is '{reg.get('payment_status')}'; must be 'verified'",
            },
        )

    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event_id")
    event_oid = ObjectId(event_id)

    # 3. Fetch event
    event = await db["events"].find_one({"_id": event_oid, "is_deleted": {"$ne": True}})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    pass_type = pass_doc.get("pass_type")

    # 4. Pass-type specific validation
    if pass_type == "one_day":
        valid_event_id = pass_doc.get("valid_event_id")
        if valid_event_id != event_oid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "wrong_event",
                    "message": "This one-day pass is not valid for this event",
                },
            )
    elif pass_type == "exclusive":
        if not event.get("is_exclusive_eligible", False):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "not_exclusive_eligible",
                    "message": "This event does not accept exclusive passes",
                },
            )
        if pass_doc.get("remaining_credits", 0) <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_credits",
                    "message": "No remaining credits on this exclusive pass",
                },
            )

    # 5. Check idempotency: no duplicate check-in for same pass + event
    existing = await db["checkins"].find_one(
        {"pass_id": pass_doc["_id"], "event_id": event_oid}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_checked_in",
                "message": "This pass has already been checked in to this event",
                "checked_in_at": existing["checked_in_at"].isoformat(),
            },
        )

    now = datetime.now(timezone.utc)

    # 6. ATOMIC credit deduction for exclusive passes
    if pass_type == "exclusive":
        updated_pass = await db["passes"].find_one_and_update(
            {"_id": pass_doc["_id"], "remaining_credits": {"$gt": 0}},
            {"$inc": {"used_credits": 1, "remaining_credits": -1}},
            return_document=True,
        )
        if updated_pass is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_credits",
                    "message": "No remaining credits (concurrent deduction prevented this check-in)",
                },
            )
        remaining_credits = updated_pass["remaining_credits"]
    else:
        # one_day pass: deduct its single credit atomically as well
        updated_pass = await db["passes"].find_one_and_update(
            {"_id": pass_doc["_id"], "remaining_credits": {"$gt": 0}},
            {"$inc": {"used_credits": 1, "remaining_credits": -1}},
            return_document=True,
        )
        if updated_pass is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_credits",
                    "message": "This one-day pass has already been used",
                },
            )
        remaining_credits = updated_pass["remaining_credits"]

    # 7. Insert checkin document
    checkin_doc = {
        "pass_id": pass_doc["_id"],
        "registration_id": reg["_id"],
        "event_id": event_oid,
        "checked_in_at": now,
        "checked_in_by": checked_in_by,
        "method": method,
        "offline_synced": offline_synced,
        "client_timestamp": client_timestamp or now,
    }
    try:
        await db["checkins"].insert_one(checkin_doc)
    except pymongo_errors.DuplicateKeyError:
        # Race condition: another request inserted first; treat as already_checked_in
        # Roll back the credit deduction
        await db["passes"].update_one(
            {"_id": pass_doc["_id"]},
            {"$inc": {"used_credits": -1, "remaining_credits": 1}},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_checked_in",
                "message": "Concurrent check-in detected; this pass is already checked in",
            },
        )

    return {
        "success": True,
        "attendee_name": reg.get("attendee_name", ""),
        "pass_type": pass_type,
        "remaining_credits": remaining_credits,
        "registration_id": reg.get("registration_id", ""),
        "check_in_time": now.isoformat(),
    }


@router.post("/scan")
async def scan_checkin(
    body: CheckinCreate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Main check-in endpoint: validates pass and records entry atomically."""
    return await _perform_checkin(
        db=db,
        qr_token=body.qr_token,
        event_id=body.event_id,
        method=body.method,
        checked_in_by=current_user["_id"],
        client_timestamp=body.client_timestamp,
        offline_synced=False,
    )


class SearchCheckinRequest:
    def __init__(self, search: str, event_id: str):
        self.search = search
        self.event_id = event_id


from pydantic import BaseModel


class CheckinSearchBody(BaseModel):
    search: str
    event_id: str


@router.post("/search")
async def search_for_checkin(
    body: CheckinSearchBody,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Fallback: search by phone/name/registration_id for manual check-in."""
    if not ObjectId.is_valid(body.event_id):
        raise HTTPException(status_code=400, detail="Invalid event_id")
    event_oid = ObjectId(body.event_id)

    query = {
        "$or": [
            {"attendee_name": {"$regex": body.search, "$options": "i"}},
            {"phone": {"$regex": body.search, "$options": "i"}},
            {"registration_id": {"$regex": body.search, "$options": "i"}},
        ]
    }

    results = []
    async for reg in db["registrations"].find(query).limit(20):
        pass_doc = await db["passes"].find_one({"registration_id": reg["_id"]})
        already_checked_in = False
        if pass_doc:
            existing = await db["checkins"].find_one(
                {"pass_id": pass_doc["_id"], "event_id": event_oid}
            )
            already_checked_in = existing is not None

        item = serialize_doc(reg)
        item["already_checked_in"] = already_checked_in
        if pass_doc:
            item["pass"] = serialize_doc(pass_doc)
        results.append(item)

    return {"results": results}


class OfflineSyncBody(BaseModel):
    records: List[OfflineCheckin]


@router.post("/sync")
async def sync_offline_checkins(
    body: OfflineSyncBody,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Process a batch of offline check-in records idempotently."""
    success_count = 0
    skipped = []
    errors = []

    for idx, record in enumerate(body.records):
        try:
            result = await _perform_checkin(
                db=db,
                qr_token=record.qr_token,
                event_id=record.event_id,
                method=record.method,
                checked_in_by=current_user["_id"],
                client_timestamp=record.client_timestamp,
                offline_synced=True,
            )
            success_count += 1
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict) and detail.get("code") == "already_checked_in":
                skipped.append({"index": idx, "qr_token": record.qr_token, "reason": "already_checked_in"})
            else:
                errors.append({"index": idx, "qr_token": record.qr_token, "error": str(detail)})

    return {
        "success_count": success_count,
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "skipped": skipped,
        "errors": errors,
    }


@router.get("/event/{event_id}")
async def get_checkins_for_event(
    event_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Return all check-ins for an event with attendee details."""
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event_id")
    event_oid = ObjectId(event_id)

    results = []
    async for ci in db["checkins"].find({"event_id": event_oid}).sort("checked_in_at", -1):
        item = serialize_doc(ci)
        # Attach registration info
        reg = await db["registrations"].find_one({"_id": ci["registration_id"]})
        if reg:
            item["attendee_name"] = reg.get("attendee_name", "")
            item["phone"] = reg.get("phone", "")
            item["registration_id_str"] = reg.get("registration_id", "")
        results.append(item)

    return {"total": len(results), "items": results}
