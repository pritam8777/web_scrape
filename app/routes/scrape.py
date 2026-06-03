"""
Scraping API routes:
- POST /scrape/url        → single URL scrape
- POST /scrape/bulk       → bulk URL scrape
- POST /scrape/upload     → file upload with URLs
- GET  /scrape/status/{job_id}
- GET  /scrape/result/{job_id}
- GET  /export/{job_id}
- GET  /jobs              → list all jobs
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models.job import Job, JobStatus, JobType
from app.models.url import ScrapedURL, URLStatus
from app.models.result import ScrapedResult
from app.models.upload import FileUpload
from app.models.log import ScrapeLog
from app.schemas.scrape import (
    ScrapeURLRequest,
    ScrapeBulkRequest,
    ScrapeResponse,
    ScrapedDataResponse,
)
from app.schemas.job import (
    JobStatusResponse,
    JobResultResponse,
    JobListItem,
    JobListResponse,
)
from app.schemas.export import ExportFormat, ExportResponse
from app.services.scraper import ScraperEngine
from app.utils.validators import is_valid_url, normalize_url, sanitize_filename

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/scrape", tags=["Scraping"])

# Shared scraper engine instance (lazy-init per request)
_scraper: ScraperEngine | None = None


def _get_scraper() -> ScraperEngine:
    global _scraper
    if _scraper is None:
        _scraper = ScraperEngine()
    return _scraper


# ---------------------------------------------------------------------------
# Helper: create job + URLs in DB
# ---------------------------------------------------------------------------

async def _create_job_with_urls(
    db: AsyncSession,
    urls: list[str],
    job_type: JobType,
    crawl_depth: int = 2,
) -> Job:
    """Create a new Job and its associated ScrapedURL rows."""
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        status=JobStatus.PENDING,
        total_urls=len(urls),
    )
    db.add(job)
    await db.flush()

    for url in urls:
        scraped_url = ScrapedURL(
            id=uuid.uuid4(),
            job_id=job.id,
            url=normalize_url(url),
            status=URLStatus.PENDING,
            crawl_depth=crawl_depth,
        )
        db.add(scraped_url)

    await db.flush()
    await db.refresh(job)  # Populate server-generated fields (e.g., created_at)
    return job


# ---------------------------------------------------------------------------
# POST /scrape/url
# ---------------------------------------------------------------------------

@router.post(
    "/url",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape a single website URL",
)
async def scrape_single_url(
    request: ScrapeURLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """
    Submits a single URL for scraping.
    Returns a job ID immediately; processing happens in the background.
    """
    if not is_valid_url(request.url):
        raise HTTPException(status_code=400, detail=f"Invalid URL: {request.url}")

    job = await _create_job_with_urls(
        db, [request.url], JobType.SINGLE, request.crawl_depth
    )
    await db.commit()

    # Fire background scraping
    scraper = _get_scraper()

    async def _do_scrape() -> None:
        try:
            # Mark job as running
            async with async_session_from_db() as sess:
                r = await sess.execute(select(Job).where(Job.id == job.id))
                j = r.scalar_one()
                j.status = JobStatus.RUNNING
                await sess.commit()

            # Get the URL record
            async with async_session_from_db() as sess:
                r = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r.scalars().first()
                if su:
                    su.status = URLStatus.IN_PROGRESS
                    await sess.commit()

            # Scrape
            data = await scraper.scrape_with_retry(
                request.url,
                crawl_depth=request.crawl_depth,
                follow_contact_pages=request.follow_contact_pages,
                use_ai=False,
            )

            # Save result
            async with async_session_from_db() as sess:
                r = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r.scalars().first()
                if su:
                    result = ScrapedResult(
                        job_id=job.id,
                        url_id=su.id,
                        organization_name=data.get("organization_name"),
                        website_url=data.get("website_url"),
                        address=data.get("address"),
                        phone_numbers=data.get("phone_numbers"),
                        email_addresses=data.get("email_addresses"),
                        social_media_links=data.get("social_media_links"),
                        contact_person=data.get("contact_person"),
                        description=data.get("description"),
                        page_title=data.get("page_title"),
                        scraped_pages_count=data.get("scraped_pages_count", 0),
                        raw_data=data,
                    )
                    sess.add(result)
                    su.status = URLStatus.SUCCESS
                    job_ref = await sess.execute(select(Job).where(Job.id == job.id))
                    j = job_ref.scalar_one()
                    j.status = JobStatus.COMPLETED
                    j.completed_urls = 1
                    await sess.commit()

        except Exception as exc:
            logger.exception("Scrape failed for job %s: %s", job.id, exc)
            async with async_session_from_db() as sess:
                r = await sess.execute(select(Job).where(Job.id == job.id))
                j = r.scalar_one()
                j.status = JobStatus.FAILED
                j.error_message = str(exc)
                # Also mark URL as failed
                r2 = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r2.scalars().first()
                if su:
                    su.status = URLStatus.FAILED
                    su.error_message = str(exc)
                await sess.commit()

    background_tasks.add_task(_do_scrape)

    return ScrapeResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        job_type=JobType.SINGLE,
        message=f"Scraping initiated for {request.url}",
        total_urls=1,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# POST /scrape/url/ai  (AI-powered variant)
# ---------------------------------------------------------------------------

@router.post(
    "/url/ai",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape a single website URL with AI extraction",
)
async def scrape_single_url_ai(
    request: ScrapeURLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """Same as /scrape/url but uses DeepSeek AI for intelligent extraction."""
    if not is_valid_url(request.url):
        raise HTTPException(status_code=400, detail=f"Invalid URL: {request.url}")

    job = await _create_job_with_urls(
        db, [request.url], JobType.SINGLE, request.crawl_depth
    )
    await db.commit()

    scraper = _get_scraper()

    async def _do_scrape() -> None:
        try:
            async with async_session_from_db() as sess:
                r = await sess.execute(select(Job).where(Job.id == job.id))
                j = r.scalar_one()
                j.status = JobStatus.RUNNING
                await sess.commit()

            async with async_session_from_db() as sess:
                r = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r.scalars().first()
                if su:
                    su.status = URLStatus.IN_PROGRESS
                    await sess.commit()

            data = await scraper.scrape_with_retry(
                request.url,
                crawl_depth=request.crawl_depth,
                follow_contact_pages=request.follow_contact_pages,
                use_ai=True,
            )

            async with async_session_from_db() as sess:
                r = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r.scalars().first()
                if su:
                    result = ScrapedResult(
                        job_id=job.id,
                        url_id=su.id,
                        organization_name=data.get("organization_name"),
                        website_url=data.get("website_url"),
                        address=data.get("address"),
                        phone_numbers=data.get("phone_numbers"),
                        email_addresses=data.get("email_addresses"),
                        social_media_links=data.get("social_media_links"),
                        contact_person=data.get("contact_person"),
                        description=data.get("description"),
                        page_title=data.get("page_title"),
                        scraped_pages_count=data.get("scraped_pages_count", 0),
                        raw_data=data,
                    )
                    sess.add(result)
                    su.status = URLStatus.SUCCESS
                    job_ref = await sess.execute(select(Job).where(Job.id == job.id))
                    j = job_ref.scalar_one()
                    j.status = JobStatus.COMPLETED
                    j.completed_urls = 1
                    await sess.commit()

        except Exception as exc:
            logger.exception("AI scrape failed for job %s: %s", job.id, exc)
            async with async_session_from_db() as sess:
                r = await sess.execute(select(Job).where(Job.id == job.id))
                j = r.scalar_one()
                j.status = JobStatus.FAILED
                j.error_message = str(exc)
                r2 = await sess.execute(
                    select(ScrapedURL).where(ScrapedURL.job_id == job.id)
                )
                su = r2.scalars().first()
                if su:
                    su.status = URLStatus.FAILED
                    su.error_message = str(exc)
                await sess.commit()

    background_tasks.add_task(_do_scrape)

    return ScrapeResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        job_type=JobType.SINGLE,
        message=f"AI scraping initiated for {request.url}",
        total_urls=1,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# POST /scrape/bulk
# ---------------------------------------------------------------------------

@router.post(
    "/bulk",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape multiple URLs in bulk",
)
async def scrape_bulk_urls(
    request: ScrapeBulkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """
    Submit multiple URLs for asynchronous scraping.
    Returns a job ID for tracking progress and results.
    """
    # Validate all URLs
    invalid = [u for u in request.urls if not is_valid_url(u)]
    if invalid:
        raise HTTPException(
            status_code=400, detail=f"Invalid URLs: {invalid}"
        )

    job = await _create_job_with_urls(
        db, request.urls, JobType.BULK, request.crawl_depth
    )
    await db.commit()

    # Background processing
    async def _process_bulk() -> None:
        scraper = _get_scraper()
        semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENT_REQUESTS)

        async def _scrape_one(url: str, url_id: uuid.UUID) -> None:
            async with semaphore:
                from app.database import async_session_factory
                try:
                    # Mark in-progress
                    async with async_session_factory() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.IN_PROGRESS
                            await sess.commit()

                    data = await scraper.scrape_with_retry(
                        url, crawl_depth=request.crawl_depth, use_ai=False
                    )

                    async with async_session_factory() as sess:
                        # Save result
                        result = ScrapedResult(
                            job_id=job.id,
                            url_id=url_id,
                            organization_name=data.get("organization_name"),
                            website_url=data.get("website_url"),
                            address=data.get("address"),
                            phone_numbers=data.get("phone_numbers"),
                            email_addresses=data.get("email_addresses"),
                            social_media_links=data.get("social_media_links"),
                            contact_person=data.get("contact_person"),
                            description=data.get("description"),
                            page_title=data.get("page_title"),
                            scraped_pages_count=data.get("scraped_pages_count", 0),
                            raw_data=data,
                        )
                        sess.add(result)

                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.SUCCESS

                        # Update job counters
                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.completed_urls += 1
                        await sess.commit()

                except Exception as exc:
                    logger.error("Bulk scrape failed for %s: %s", url, exc)
                    async with async_session_factory() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.FAILED
                            su.error_message = str(exc)
                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.failed_urls += 1
                        await sess.commit()

        # Mark job running
        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            j.status = JobStatus.RUNNING
            await sess.commit()

        # Get all URLs
        async with async_session_from_db() as sess:
            r = await sess.execute(
                select(ScrapedURL).where(ScrapedURL.job_id == job.id)
            )
            urls = r.scalars().all()

        tasks = [_scrape_one(u.url, u.id) for u in urls]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Final status
        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            if j.failed_urls == j.total_urls:
                j.status = JobStatus.FAILED
            elif j.failed_urls > 0:
                j.status = JobStatus.PARTIAL
            else:
                j.status = JobStatus.COMPLETED
            await sess.commit()

        await scraper.close_browser()

    background_tasks.add_task(_process_bulk)

    return ScrapeResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        job_type=JobType.BULK,
        message=f"Bulk scraping initiated for {len(request.urls)} URLs",
        total_urls=len(request.urls),
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# POST /scrape/bulk/ai  (AI-powered variant)
# ---------------------------------------------------------------------------

@router.post(
    "/bulk/ai",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrape multiple URLs in bulk with AI extraction",
)
async def scrape_bulk_urls_ai(
    request: ScrapeBulkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """Same as /scrape/bulk but uses DeepSeek AI for intelligent extraction."""
    invalid = [u for u in request.urls if not is_valid_url(u)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid URLs: {invalid}")

    job = await _create_job_with_urls(
        db, request.urls, JobType.BULK, request.crawl_depth
    )
    await db.commit()

    async def _process_bulk() -> None:
        scraper = _get_scraper()
        semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENT_REQUESTS)

        async def _scrape_one(url: str, url_id: uuid.UUID) -> None:
            async with semaphore:
                from app.database import async_session_factory
                try:
                    async with async_session_factory() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.IN_PROGRESS
                            await sess.commit()

                    data = await scraper.scrape_with_retry(
                        url, crawl_depth=request.crawl_depth, use_ai=True
                    )

                    async with async_session_factory() as sess:
                        result = ScrapedResult(
                            job_id=job.id,
                            url_id=url_id,
                            organization_name=data.get("organization_name"),
                            website_url=data.get("website_url"),
                            address=data.get("address"),
                            phone_numbers=data.get("phone_numbers"),
                            email_addresses=data.get("email_addresses"),
                            social_media_links=data.get("social_media_links"),
                            contact_person=data.get("contact_person"),
                            description=data.get("description"),
                            page_title=data.get("page_title"),
                            scraped_pages_count=data.get("scraped_pages_count", 0),
                            raw_data=data,
                        )
                        sess.add(result)
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.SUCCESS
                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.completed_urls += 1
                        await sess.commit()

                except Exception as exc:
                    logger.error("AI bulk scrape failed for %s: %s", url, exc)
                    async with async_session_factory() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.FAILED
                            su.error_message = str(exc)
                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.failed_urls += 1
                        await sess.commit()

        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            j.status = JobStatus.RUNNING
            await sess.commit()

        async with async_session_from_db() as sess:
            r = await sess.execute(
                select(ScrapedURL).where(ScrapedURL.job_id == job.id)
            )
            urls = r.scalars().all()

        tasks = [_scrape_one(u.url, u.id) for u in urls]
        await asyncio.gather(*tasks, return_exceptions=True)

        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            if j.failed_urls == j.total_urls:
                j.status = JobStatus.FAILED
            elif j.failed_urls > 0:
                j.status = JobStatus.PARTIAL
            else:
                j.status = JobStatus.COMPLETED
            await sess.commit()

        await scraper.close_browser()

    background_tasks.add_task(_process_bulk)

    return ScrapeResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        job_type=JobType.BULK,
        message=f"AI bulk scraping initiated for {len(request.urls)} URLs",
        total_urls=len(request.urls),
        created_at=job.created_at,
    )


# Helper: create a session for background tasks
from app.database import async_session_factory as async_session_from_db


# ---------------------------------------------------------------------------
# POST /scrape/upload
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload Excel/CSV file with URLs to scrape",
)
async def scrape_upload_file(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(
        ...,
        description="Excel (.xlsx) or CSV (.csv) file with a 'url' or 'website' column.",
    ),
    crawl_depth: int = Query(default=2, ge=0, le=5),
) -> ScrapeResponse:
    """
    Upload an Excel or CSV file containing website URLs.
    The file must have a column named 'url' or 'website'.
    """
    # Validate file type
    allowed_extensions = {".xlsx", ".csv"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {allowed_extensions}",
        )

    # Read file content
    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    # Parse URLs from file
    try:
        if ext == ".csv":
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents), engine="openpyxl")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {exc}")

    # Find URL column
    url_column = None
    for col in df.columns:
        if col.lower().strip() in ("url", "website", "link", "website_url"):
            url_column = col
            break
    if url_column is None:
        raise HTTPException(
            status_code=400,
            detail="File must contain a 'url' or 'website' column.",
        )

    urls = df[url_column].dropna().astype(str).str.strip().tolist()
    urls = [u for u in urls if is_valid_url(u)]
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs found in file.")
    if len(urls) > settings.SCRAPER_MAX_URLS_PER_JOB:
        raise HTTPException(
            status_code=400,
            detail=f"Too many URLs ({len(urls)}). Maximum is {settings.SCRAPER_MAX_URLS_PER_JOB}.",
        )

    # Save uploaded file
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{sanitize_filename(file.filename or 'upload')}"
    file_path = os.path.join(settings.UPLOAD_DIR, stored_name)
    with open(file_path, "wb") as f:
        f.write(contents)

    # Create job
    job = await _create_job_with_urls(db, urls, JobType.UPLOAD, crawl_depth)

    # Record file upload
    file_upload = FileUpload(
        id=uuid.uuid4(),
        job_id=job.id,
        original_filename=file.filename or "unknown",
        stored_filename=stored_name,
        file_path=file_path,
        file_size_bytes=len(contents),
        url_count=len(urls),
        mime_type=file.content_type,
    )
    db.add(file_upload)
    await db.commit()

    # Background processing (same as bulk)
    async def _process_upload() -> None:
        scraper = _get_scraper()
        semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENT_REQUESTS)

        async def _scrape_one(url: str, url_id: uuid.UUID) -> None:
            async with semaphore:
                try:
                    async with async_session_from_db() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.IN_PROGRESS
                            await sess.commit()

                    data = await scraper.scrape_with_retry(url, crawl_depth=crawl_depth, use_ai=False)

                    async with async_session_from_db() as sess:
                        result = ScrapedResult(
                            job_id=job.id,
                            url_id=url_id,
                            organization_name=data.get("organization_name"),
                            website_url=data.get("website_url"),
                            address=data.get("address"),
                            phone_numbers=data.get("phone_numbers"),
                            email_addresses=data.get("email_addresses"),
                            social_media_links=data.get("social_media_links"),
                            contact_person=data.get("contact_person"),
                            description=data.get("description"),
                            page_title=data.get("page_title"),
                            scraped_pages_count=data.get("scraped_pages_count", 0),
                            raw_data=data,
                        )
                        sess.add(result)

                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.SUCCESS

                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.completed_urls += 1
                        await sess.commit()

                except Exception as exc:
                    logger.error("Upload scrape failed for %s: %s", url, exc)
                    async with async_session_from_db() as sess:
                        r = await sess.execute(select(ScrapedURL).where(ScrapedURL.id == url_id))
                        su = r.scalar_one_or_none()
                        if su:
                            su.status = URLStatus.FAILED
                            su.error_message = str(exc)
                        rj = await sess.execute(select(Job).where(Job.id == job.id))
                        j = rj.scalar_one()
                        j.failed_urls += 1
                        await sess.commit()

        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            j.status = JobStatus.RUNNING
            await sess.commit()

        async with async_session_from_db() as sess:
            r = await sess.execute(select(ScrapedURL).where(ScrapedURL.job_id == job.id))
            urls_db = r.scalars().all()

        tasks = [_scrape_one(u.url, u.id) for u in urls_db]
        await asyncio.gather(*tasks, return_exceptions=True)

        async with async_session_from_db() as sess:
            r = await sess.execute(select(Job).where(Job.id == job.id))
            j = r.scalar_one()
            if j.failed_urls == j.total_urls:
                j.status = JobStatus.FAILED
            elif j.failed_urls > 0:
                j.status = JobStatus.PARTIAL
            else:
                j.status = JobStatus.COMPLETED
            await sess.commit()

        await scraper.close_browser()

    background_tasks.add_task(_process_upload)

    return ScrapeResponse(
        job_id=job.id,
        status=JobStatus.PENDING,
        job_type=JobType.UPLOAD,
        message=f"File uploaded with {len(urls)} URLs. Processing started.",
        total_urls=len(urls),
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# GET /scrape/status/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
    summary="Get scraping job status",
)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """Returns the current status and progress of a scraping job."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    progress = (
        (job.completed_urls + job.failed_urls) / job.total_urls * 100
        if job.total_urls > 0
        else 0.0
    )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        job_type=job.job_type,
        total_urls=job.total_urls,
        completed_urls=job.completed_urls,
        failed_urls=job.failed_urls,
        error_message=job.error_message,
        progress_percent=round(progress, 2),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /scrape/result/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/result/{job_id}",
    response_model=JobResultResponse,
    summary="Get scraping results for a job",
)
async def get_job_result(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobResultResponse:
    """Returns all scraped results for a completed job."""
    result = await db.execute(
        select(Job).where(Job.id == job_id).options(
            selectinload(Job.results).selectinload(ScrapedResult.url)
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    results_payload: list[ScrapedDataResponse] = []
    for res in job.results:
        results_payload.append(
            ScrapedDataResponse(
                result_id=res.id,
                url=res.url.url if res.url else "",
                organization_name=res.organization_name,
                website_url=res.website_url,
                address=res.address,
                phone_numbers=res.phone_numbers,
                email_addresses=res.email_addresses,
                social_media_links=res.social_media_links,
                contact_person=res.contact_person,
                description=res.description,
                page_title=res.page_title,
                scraped_pages_count=res.scraped_pages_count,
                scraped_at=res.created_at,
            )
        )

    return JobResultResponse(
        job_id=job.id,
        status=job.status,
        total_urls=job.total_urls,
        completed_urls=job.completed_urls,
        failed_urls=job.failed_urls,
        results=results_payload,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------

@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List all scraping jobs",
)
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """Paginated list of all scraping jobs."""
    # Count
    total_result = await db.execute(select(sa_func.count(Job.id)))
    total = total_result.scalar() or 0

    # Fetch page
    result = await db.execute(
        select(Job)
        .order_by(Job.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[
            JobListItem(
                job_id=j.id,
                status=j.status,
                job_type=j.job_type,
                total_urls=j.total_urls,
                completed_urls=j.completed_urls,
                failed_urls=j.failed_urls,
                created_at=j.created_at,
            )
            for j in jobs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /export/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/export/{job_id}",
    summary="Export scraped results",
)
async def export_results(
    job_id: uuid.UUID,
    format: ExportFormat = Query(default=ExportFormat.JSON),
    db: AsyncSession = Depends(get_db),
):
    """
    Export scraped results in JSON, CSV, or Excel format.
    Returns a downloadable file.
    """
    # Load job with results
    result = await db.execute(
        select(Job).where(Job.id == job_id).options(
            selectinload(Job.results)
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build records
    records: list[dict[str, Any]] = []
    for res in job.results:
        records.append({
            "organization_name": res.organization_name,
            "website_url": res.website_url,
            "address": res.address,
            "phone_numbers": ", ".join(res.phone_numbers) if res.phone_numbers else None,
            "email_addresses": ", ".join(res.email_addresses) if res.email_addresses else None,
            "social_media_links": (
                ", ".join(f"{k}: {v}" for k, v in res.social_media_links.items())
                if res.social_media_links else None
            ),
            "contact_person": res.contact_person,
            "description": res.description,
            "page_title": res.page_title,
            "scraped_pages_count": res.scraped_pages_count,
            "scraped_at": res.created_at.isoformat() if res.created_at else None,
        })

    os.makedirs(settings.EXPORT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"export_{job_id.hex[:8]}_{timestamp}"

    if format == ExportFormat.JSON:
        import json
        file_path = os.path.join(settings.EXPORT_DIR, f"{base_name}.json")
        with open(file_path, "w") as f:
            json.dump(records, f, indent=2, default=str)
        media_type = "application/json"
        filename = f"{base_name}.json"

    elif format == ExportFormat.CSV:
        df = pd.DataFrame(records)
        file_path = os.path.join(settings.EXPORT_DIR, f"{base_name}.csv")
        df.to_csv(file_path, index=False)
        media_type = "text/csv"
        filename = f"{base_name}.csv"

    elif format == ExportFormat.EXCEL:
        df = pd.DataFrame(records)
        file_path = os.path.join(settings.EXPORT_DIR, f"{base_name}.xlsx")
        df.to_excel(file_path, index=False, engine="openpyxl")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{base_name}.xlsx"

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")

    file_size = os.path.getsize(file_path)

    def _file_iterator():
        with open(file_path, "rb") as f:
            yield from f

    return StreamingResponse(
        _file_iterator(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
        },
    )
