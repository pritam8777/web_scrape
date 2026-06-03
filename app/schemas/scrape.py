"""
Pydantic schemas for scrape requests and responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ScrapeURLRequest(BaseModel):
    """Request body for scraping a single URL."""

    url: str = Field(
        ...,
        description="Full URL of the website to scrape.",
        examples=["https://example.com"],
    )
    crawl_depth: int = Field(
        default=2,
        ge=0,
        le=5,
        description="How many levels of internal links to follow.",
    )
    follow_contact_pages: bool = Field(
        default=True,
        description="Automatically discover and scrape Contact Us pages.",
    )

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v.strip().rstrip("/")


class ScrapeBulkRequest(BaseModel):
    """Request body for scraping multiple URLs in bulk."""

    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of website URLs to scrape.",
        examples=[["https://example.com", "https://example.org"]],
    )
    crawl_depth: int = Field(default=2, ge=0, le=5)
    follow_contact_pages: bool = Field(default=True)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for url in v:
            url = url.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"URL must start with http:// or https://: {url}")
            cleaned.append(url)
        return cleaned


class ScrapedDataResponse(BaseModel):
    """Structured scraped data returned for a single URL."""

    result_id: UUID
    url: str
    organization_name: str | None = None
    website_url: str | None = None
    address: str | None = None
    phone_numbers: list[str] | None = None
    email_addresses: list[str] | None = None
    social_media_links: dict[str, str] | None = None
    contact_person: str | None = None
    description: str | None = None
    page_title: str | None = None
    scraped_pages_count: int | None = 0
    scraped_at: datetime | None = None


class ScrapeResponse(BaseModel):
    """Response returned after initiating a scrape job."""

    job_id: UUID
    status: str
    job_type: str
    message: str
    total_urls: int
    created_at: datetime

    model_config = {"from_attributes": True}
