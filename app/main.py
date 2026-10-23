from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.security import hash_password
from app.db.mongodb import connect_to_mongo, close_mongo_connection, get_database, setup_indexes
from app.api import auth, events, registrations, passes, checkins, admin

app = FastAPI(
    title="Aftermiles Admin API",
    description="Backend for the Aftermiles event check-in management system",
    version="1.0.0",
)

# ──────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(registrations.router)
app.include_router(passes.router)
app.include_router(checkins.router)
app.include_router(admin.router)


# ──────────────────────────────────────────────
# Startup / Shutdown
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    await setup_indexes()
    await seed_first_admin()


@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()


async def seed_first_admin():
    """Create the first admin user if the users collection is empty."""
    db = get_database()
    count = await db["users"].count_documents({})
    if count == 0:
        doc = {
            "name": "Admin",
            "email": settings.FIRST_ADMIN_EMAIL.lower().strip(),
            "hashed_password": hash_password(settings.FIRST_ADMIN_PASSWORD),
            "role": "admin",
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        await db["users"].insert_one(doc)
        print(
            f"[SEED] First admin created: {settings.FIRST_ADMIN_EMAIL} / {settings.FIRST_ADMIN_PASSWORD}"
        )


# ──────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────
@app.get("/", tags=["health"])
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "aftermiles-admin-api"}
