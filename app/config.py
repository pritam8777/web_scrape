"""
Centralized application configuration loaded from environment variables.
All settings have sensible defaults for development.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve the project root (two levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application-wide settings sourced from .env / environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    APP_NAME: str = "Web Scraper Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    API_PREFIX: str = "/api/v1"

    # ── Database Connection ──────────────────────────────────────
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASS: str = "root"
    DB_NAME: str = "web_scraper_db"
    DB_DIALECT: str = "mysql"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_ECHO: bool = False

    @property
    def DATABASE_URL(self) -> str:
        """Async SQLAlchemy connection URL for the application."""
        return (
            f"{self.DB_DIALECT}+aiomysql://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def CELERY_BROKER_URL(self) -> str:
        """Celery broker URL backed by the database."""
        return (
            f"sqla+{self.DB_DIALECT}+pymysql://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        """Celery result backend URL backed by the database."""
        return (
            f"db+{self.DB_DIALECT}+pymysql://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # ── AI Extraction (DeepSeek) ─────────────────────────────────
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-v4-pro"
    DEEPSEEK_REASONING_EFFORT: str = "high"
    AI_EXTRACTION_ENABLED: bool = True

    # ── Scraper ──────────────────────────────────────────────────
    SCRAPER_TIMEOUT_SECONDS: int = 30
    SCRAPER_MAX_RETRIES: int = 3
    SCRAPER_RETRY_BACKOFF_FACTOR: float = 2.0
    SCRAPER_CRAWL_DEPTH: int = 2
    SCRAPER_MAX_URLS_PER_JOB: int = 500
    SCRAPER_RESPECT_ROBOTS_TXT: bool = True
    SCRAPER_USER_AGENT_ROTATION: bool = True
    SCRAPER_HEADLESS_BROWSER: bool = True
    SCRAPER_CONCURRENT_REQUESTS: int = 5

    # ── Proxy (optional) ─────────────────────────────────────────
    PROXY_ENABLED: bool = False
    PROXY_LIST: str = ""  # comma-separated proxy URLs

    # ── Rate Limiting ────────────────────────────────────────────
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 10

    # ── File Upload ──────────────────────────────────────────────
    UPLOAD_DIR: str = str(PROJECT_ROOT / "uploads")
    MAX_UPLOAD_SIZE_MB: int = 10

    # ── Logging ──────────────────────────────────────────────────
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"

    # ── Export ───────────────────────────────────────────────────
    EXPORT_DIR: str = str(PROJECT_ROOT / "exports")

    @property
    def proxy_list_parsed(self) -> list[str]:
        """Return proxies as a list, filtering empty strings."""
        if not self.PROXY_ENABLED or not self.PROXY_LIST:
            return []
        return [p.strip() for p in self.PROXY_LIST.split(",") if p.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
