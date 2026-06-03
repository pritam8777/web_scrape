"""
ScrapedResult model – stores extracted structured data for each scraped URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.url import ScrapedURL


class ScrapedResult(Base):
    __tablename__ = "scraped_results"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scraped_urls.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )

    # ── Extracted fields ─────────────────────────────────────────
    organization_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_numbers: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    email_addresses: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    social_media_links: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Raw extracted data for debugging / full access
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Metadata about the scrape
    page_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    scraped_pages_count: Mapped[int | None] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationships ────────────────────────────────────────────
    job: Mapped["Job"] = relationship("Job", back_populates="results")
    url: Mapped["ScrapedURL"] = relationship("ScrapedURL", back_populates="result")

    def __repr__(self) -> str:
        return f"<ScrapedResult {self.organization_name or self.website_url}>"
