import io
import base64
from typing import Annotated

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.core.deps import get_db, get_current_user
from app.models import serialize_doc

router = APIRouter(prefix="/api/passes", tags=["passes"])


def _object_id(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail=f"Invalid ID: {id_str}")
    return ObjectId(id_str)


@router.get("/token/{qr_token}")
async def resolve_qr_token(
    qr_token: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Resolve a QR token to full pass + registration info (used by scanner)."""
    pass_doc = await db["passes"].find_one({"qr_token": qr_token})
    if not pass_doc:
        raise HTTPException(status_code=404, detail="QR token not found")

    registration = await db["registrations"].find_one({"_id": pass_doc["registration_id"]})

    result = serialize_doc(pass_doc)
    if registration:
        result["registration"] = serialize_doc(registration)

    return result


@router.get("/{pass_id}/qr")
async def get_qr_code(
    pass_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Generate and return the QR code as a PNG image (streaming)."""
    oid = _object_id(pass_id)
    pass_doc = await db["passes"].find_one({"_id": oid})
    if not pass_doc:
        raise HTTPException(status_code=404, detail="Pass not found")

    qr_token = pass_doc["qr_token"]
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")
