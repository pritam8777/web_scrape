"""
Pydantic schemas for job status, results, and listing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.scrape import ScrapedDataResponse


class JobStatusResponse(BaseModel):
    """Response for GET /scrape/status/{job_id}."""

    job_id: UUID
    status: str
    job_type: str
    total_urls: int
    completed_urls: int
    failed_urls: int
    error_message: str | None = None
    progress_percent: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobResultResponse(BaseModel):
    """Response for GET /scrape/result/{job_id}."""

    job_id: UUID
    status: str
    total_urls: int
    completed_urls: int
    failed_urls: int
    results: list[ScrapedDataResponse]
    created_at: datetime


class JobListItem(BaseModel):
    """Summary item for the jobs listing endpoint."""

    job_id: UUID
    status: str
    job_type: str
    total_urls: int
    completed_urls: int
    failed_urls: int
    created_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Response for listing all jobs."""

    jobs: list[JobListItem]
    total: int
    page: int
    page_size: int
