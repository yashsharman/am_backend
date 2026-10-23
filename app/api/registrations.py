import csv
import io
import uuid
from datetime import datetime, timezone
from math import ceil
from typing import Annotated, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.core.deps import get_db, get_current_user, require_admin
from app.models import serialize_doc
from app.models.registration import RegistrationCreate, RegistrationUpdate

router = APIRouter(prefix="/api/registrations", tags=["registrations"])


def _object_id(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
    return ObjectId(id_str)


async def _next_registration_id(db, year: int) -> str:
    """Atomically increment the year counter and return formatted AM-YYYY-NNNN."""
    result = await db["counters"].find_one_and_update(
        {"name": f"reg_{year}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = result["seq"]
    return f"AM-{year}-{seq:04d}"


async def _create_registration_and_pass(db, body: RegistrationCreate, registered_by: ObjectId):
    """Core logic: create registration + pass document. Returns (registration_doc, pass_doc)."""
    year = datetime.now(timezone.utc).year

    # Validate event for one_day
    event_oid = None
    if body.pass_type == "one_day":
        if not body.event_id:
            raise HTTPException(status_code=400, detail="event_id is required for one_day pass")
        if not ObjectId.is_valid(body.event_id):
            raise HTTPException(status_code=400, detail="Invalid event_id")
        event_oid = ObjectId(body.event_id)
        event = await db["events"].find_one({"_id": event_oid, "is_deleted": {"$ne": True}})
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

    reg_id_str = await _next_registration_id(db, year)

    registration_doc = {
        "registration_id": reg_id_str,
        "attendee_name": body.attendee_name.strip(),
        "phone": body.phone.strip(),
        "email": body.email,
        "event_id": event_oid,
        "payment_status": body.payment_status,
        "payment_reference": body.payment_reference or "",
        "registered_by": registered_by,
        "notes": body.notes or "",
        "created_at": datetime.now(timezone.utc),
    }

    reg_result = await db["registrations"].insert_one(registration_doc)
    reg_oid = reg_result.inserted_id

    total_credits = 1 if body.pass_type == "one_day" else 3
    pass_doc = {
        "pass_type": body.pass_type,
        "registration_id": reg_oid,
        "qr_token": str(uuid.uuid4()),
        "valid_event_id": event_oid if body.pass_type == "one_day" else None,
        "total_credits": total_credits,
        "used_credits": 0,
        "remaining_credits": total_credits,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    pass_result = await db["passes"].insert_one(pass_doc)
    pass_oid = pass_result.inserted_id

    # Link pass_id back to registration
    await db["registrations"].update_one(
        {"_id": reg_oid}, {"$set": {"pass_id": pass_oid}}
    )

    final_reg = await db["registrations"].find_one({"_id": reg_oid})
    final_pass = await db["passes"].find_one({"_id": pass_oid})
    return final_reg, final_pass


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_registration(
    body: RegistrationCreate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Register an attendee and create their pass."""
    reg, pass_ = await _create_registration_and_pass(db, body, current_user["_id"])
    return {
        "registration": serialize_doc(reg),
        "pass": serialize_doc(pass_),
    }


@router.get("")
async def list_registrations(
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    search: Optional[str] = Query(default=None, description="Search by name, phone, or reg_id"),
    event_id: Optional[str] = Query(default=None),
    payment_status: Optional[Literal["pending", "verified", "failed"]] = Query(default=None),
    pass_type: Optional[Literal["one_day", "exclusive"]] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List registrations with optional search and filters."""
    query: dict = {}

    if search:
        query["$or"] = [
            {"attendee_name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"registration_id": {"$regex": search, "$options": "i"}},
        ]
    if event_id:
        if not ObjectId.is_valid(event_id):
            raise HTTPException(status_code=400, detail="Invalid event_id")
        query["event_id"] = ObjectId(event_id)
    if payment_status:
        query["payment_status"] = payment_status

    # pass_type filter requires a join — get matching pass registration_ids first
    if pass_type:
        pass_reg_ids = []
        async for p in db["passes"].find({"pass_type": pass_type}, {"registration_id": 1}):
            pass_reg_ids.append(p["registration_id"])
        query["_id"] = {"$in": pass_reg_ids}

    total = await db["registrations"].count_documents(query)
    skip = (page - 1) * page_size
    cursor = db["registrations"].find(query).sort("created_at", -1).skip(skip).limit(page_size)

    items = []
    async for doc in cursor:
        items.append(serialize_doc(doc))

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(total / page_size) if total else 0,
        "items": items,
    }


@router.get("/export")
async def export_registrations(
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    event_id: Optional[str] = Query(default=None),
    payment_status: Optional[str] = Query(default=None),
    pass_type: Optional[str] = Query(default=None),
):
    """Export registrations as CSV (streaming response)."""
    query: dict = {}
    if event_id:
        if not ObjectId.is_valid(event_id):
            raise HTTPException(status_code=400, detail="Invalid event_id")
        query["event_id"] = ObjectId(event_id)
    if payment_status:
        query["payment_status"] = payment_status
    if pass_type:
        pass_reg_ids = []
        async for p in db["passes"].find({"pass_type": pass_type}, {"registration_id": 1}):
            pass_reg_ids.append(p["registration_id"])
        query["_id"] = {"$in": pass_reg_ids}

    async def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "registration_id", "attendee_name", "phone", "email",
            "payment_status", "payment_reference", "notes", "created_at",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        async for doc in db["registrations"].find(query).sort("created_at", 1):
            writer.writerow([
                doc.get("registration_id", ""),
                doc.get("attendee_name", ""),
                doc.get("phone", ""),
                doc.get("email", "") or "",
                doc.get("payment_status", ""),
                doc.get("payment_reference", "") or "",
                doc.get("notes", "") or "",
                doc.get("created_at", "").isoformat() if isinstance(doc.get("created_at"), datetime) else str(doc.get("created_at", "")),
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=registrations.csv"},
    )


@router.get("/{reg_id}")
async def get_registration(
    reg_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get registration detail with pass info and check-in history."""
    oid = _object_id(reg_id)
    reg = await db["registrations"].find_one({"_id": oid})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    result = serialize_doc(reg)

    # Attach pass info
    pass_doc = await db["passes"].find_one({"registration_id": oid})
    if pass_doc:
        result["pass"] = serialize_doc(pass_doc)

    # Attach check-in history
    checkins = []
    if pass_doc:
        async for ci in db["checkins"].find({"pass_id": pass_doc["_id"]}).sort("checked_in_at", -1):
            checkins.append(serialize_doc(ci))
    result["checkins"] = checkins

    return result


@router.patch("/{reg_id}")
async def update_registration(
    reg_id: str,
    body: RegistrationUpdate,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Update registration details."""
    oid = _object_id(reg_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db["registrations"].find_one_and_update(
        {"_id": oid}, {"$set": updates}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Registration not found")
    return serialize_doc(result)


@router.delete("/{reg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registration(
    reg_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_admin)],
):
    """Delete a registration and its associated pass (admin only)."""
    oid = _object_id(reg_id)
    reg = await db["registrations"].find_one({"_id": oid})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    # Delete linked pass + checkins
    if reg.get("pass_id"):
        await db["checkins"].delete_many({"pass_id": reg["pass_id"]})
        await db["passes"].delete_one({"_id": reg["pass_id"]})
    await db["registrations"].delete_one({"_id": oid})


@router.post("/import")
async def import_registrations(
    file: UploadFile = File(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bulk import registrations from CSV or Excel file."""
    filename = file.filename or ""
    content = await file.read()
    rows = []

    if filename.endswith(".csv"):
        text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        headers = None
        for row in ws.iter_rows(values_only=True):
            if headers is None:
                headers = [str(c).strip() if c else "" for c in row]
            else:
                rows.append(dict(zip(headers, row)))
    else:
        raise HTTPException(status_code=400, detail="Only CSV or XLSX files are supported")

    success_count = 0
    errors = []

    for idx, row in enumerate(rows, start=2):
        try:
            attendee_name = str(row.get("attendee_name") or row.get("name") or "").strip()
            phone = str(row.get("phone") or "").strip()
            pass_type = str(row.get("pass_type") or "one_day").strip().lower()
            event_id_val = str(row.get("event_id") or "").strip()
            payment_status = str(row.get("payment_status") or "pending").strip().lower()
            email_val = str(row.get("email") or "").strip() or None
            notes_val = str(row.get("notes") or "").strip()

            if not attendee_name:
                raise ValueError("attendee_name is required")
            if not phone:
                raise ValueError("phone is required")
            if pass_type not in ("one_day", "exclusive"):
                raise ValueError(f"Invalid pass_type: {pass_type}")
            if pass_type == "one_day" and not event_id_val:
                raise ValueError("event_id is required for one_day pass")
            if payment_status not in ("pending", "verified", "failed"):
                payment_status = "pending"

            body = RegistrationCreate(
                attendee_name=attendee_name,
                phone=phone,
                email=email_val,  # type: ignore
                pass_type=pass_type,  # type: ignore
                event_id=event_id_val or None,
                payment_status=payment_status,  # type: ignore
                notes=notes_val,
            )
            await _create_registration_and_pass(db, body, current_user["_id"])
            success_count += 1
        except Exception as e:
            errors.append({"row": idx, "error": str(e)})

    return {"success_count": success_count, "error_count": len(errors), "errors": errors}
