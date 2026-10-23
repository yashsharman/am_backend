from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings

_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_client() -> AsyncIOMotorClient:
    return _client


def get_database() -> AsyncIOMotorDatabase:
    return _db


async def connect_to_mongo():
    """Create and store the Motor client and DB reference."""
    global _client, _db
    _client = AsyncIOMotorClient(settings.mongo_url)
    _db = _client[settings.DB_NAME]


async def close_mongo_connection():
    """Close the Motor client."""
    global _client
    if _client:
        _client.close()


async def setup_indexes():
    """Create all required indexes on the collections."""
    db = get_database()

    # users
    await db["users"].create_index("email", unique=True)

    # events
    await db["events"].create_index("status")
    await db["events"].create_index("date")
    await db["events"].create_index("is_deleted")

    # registrations
    await db["registrations"].create_index("registration_id", unique=True)
    await db["registrations"].create_index("phone")
    await db["registrations"].create_index("pass_id")
    await db["registrations"].create_index("event_id")
    await db["registrations"].create_index("payment_status")

    # passes
    await db["passes"].create_index("qr_token", unique=True)
    await db["passes"].create_index("registration_id")

    # checkins: (pass_id, event_id) compound unique
    await db["checkins"].create_index(
        [("pass_id", 1), ("event_id", 1)], unique=True
    )

    # counters (for registration ID sequencing)
    await db["counters"].create_index("name", unique=True)
