from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import json


class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_URI: str | None = None
    DB_NAME: str = "aftermiles"
    SECRET_KEY: str = "change-me-in-production-super-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    FIRST_ADMIN_EMAIL: str = "admin@aftermiles.com"
    FIRST_ADMIN_PASSWORD: str = "admin123"
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    @property
    def mongo_url(self) -> str:
        return self.MONGODB_URI or self.MONGODB_URL

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return [origin.strip() for origin in v.split(",")]
        return v

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
