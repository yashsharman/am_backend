from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_db, get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models import serialize_doc

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory set for revoked refresh tokens (use Redis in production)
_revoked_refresh_tokens: set = set()


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
):
    """Authenticate with email + password; returns access and refresh JWTs."""
    user = await db["users"].find_one({"email": body.email.lower().strip()})
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    user_id = str(user["_id"])
    access_token = create_access_token(
        {"sub": user_id, "role": user["role"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token({"sub": user_id})
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
):
    """Exchange a valid refresh token for a new access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    token = body.refresh_token
    if token in _revoked_refresh_tokens:
        raise credentials_exception
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await db["users"].find_one({"_id": ObjectId(user_id)})
    if not user or not user.get("is_active", False):
        raise credentials_exception

    new_access = create_access_token(
        {"sub": user_id, "role": user["role"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    new_refresh = create_refresh_token({"sub": user_id})
    # Revoke old token
    _revoked_refresh_tokens.add(token)
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout")
async def logout(body: RefreshRequest):
    """Revoke a refresh token (invalidate session)."""
    _revoked_refresh_tokens.add(body.refresh_token)
    return {"message": "Logged out successfully"}


@router.get("/me")
async def get_me(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Return the currently authenticated user's info."""
    return serialize_doc(current_user)
