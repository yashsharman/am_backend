from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from bson import ObjectId


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PyObjectId(str):
    """Custom Pydantic type for MongoDB ObjectId serialized as string."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, info=None):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )


def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document _id and ObjectId fields to strings."""
    if doc is None:
        return doc
    result = {}
    for k, v in doc.items():
        if k == "_id":
            result["id"] = str(v)
        elif isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
