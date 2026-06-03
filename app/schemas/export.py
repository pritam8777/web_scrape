"""
Pydantic schemas for data export functionality.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class ExportFormat(str, Enum):
    """Supported export formats."""

    JSON = "json"
    CSV = "csv"
    EXCEL = "xlsx"


class ExportResponse(BaseModel):
    """Response for GET /export/{job_id}."""

    job_id: UUID
    format: ExportFormat
    file_name: str
    file_size_bytes: int
    record_count: int
    download_url: str
    generated_at: datetime
