"""
Job model – represents a single scraping job (single URL, bulk, or file upload).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.result import ScrapedResult
    from app.models.url import ScrapedURL
    from app.models.log import ScrapeLog


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # some URLs succeeded, some failed


class JobType(str, Enum):
    SINGLE = "single"
    BULK = "bulk"
    UPLOAD = "upload"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_type: Mapped[JobType] = mapped_column(String(20), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        String(20), nullable=False, default=JobStatus.PENDING
    )

    total_urls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_urls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_urls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────
    urls: Mapped[list["ScrapedURL"]] = relationship(
        "ScrapedURL", back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    results: Mapped[list["ScrapedResult"]] = relationship(
        "ScrapedResult", back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    logs: Mapped[list["ScrapeLog"]] = relationship(
        "ScrapeLog", back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Job {self.id} [{self.job_type}] {self.status}>"
