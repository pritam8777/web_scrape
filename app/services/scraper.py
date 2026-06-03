"""
Playwright-based scraper for JavaScript-rendered websites.
Falls back to httpx + BeautifulSoup for static sites.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.utils.user_agents import get_random_user_agent
from app.services import extractors
from app.services.normalizer import normalize_extracted_data
from app.services.ai_extractor import extract_with_ai

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Smart contact-page discovery paths ─────────────────────────────────────

CONTACT_PATH_CANDIDATES = [
    "/contact", "/contact-us", "/contactus", "/contact-us.php",
    "/about", "/about-us", "/aboutus", "/about-us.php",
    "/reach-us", "/get-in-touch", "/connect", "/support",
    "/contact.html", "/contact-us.html", "/about.html", "/about-us.html",
    "/contact.php", "/about.php",
    # Common subfolder patterns (Indian educational sites, CMS patterns)
    "/Footer/contact_us", "/Footer/contact-us", "/footer/contact_us",
    "/Home/contact_us", "/home/contact", "/home/contact-us",
    "/Aboutus/contact", "/aboutus/contact-us", "/About/contact",
    "/ContactUs", "/Contactus", "/CONTACT", "/CONTACT-US",
    "/page/contact", "/pages/contact", "/page/contact-us",
    "/index.php/contact", "/index.php/contact-us",
]


def _discover_contact_paths(base_url: str, collected: dict) -> list[str]:
    """Generate candidate contact/about page URLs when key data is missing."""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [urljoin(base, path) for path in CONTACT_PATH_CANDIDATES]


# ------------------------------------------------------------------

class ScraperEngine:
    """
    Main scraping engine that:
    1. Fetches a page (static via httpx, or dynamic via Playwright)
    2. Extracts structured data
    3. Optionally crawls internal pages up to configured depth
    4. Retries on failure
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Playwright lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self):
        """Lazily launch a Playwright browser instance (singleton)."""
        if self._browser is not None:
            return

        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                logger.warning("Playwright not installed. Dynamic JS scraping disabled.")
                return

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.SCRAPER_HEADLESS_BROWSER,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            logger.info("Playwright browser launched.")

    async def close_browser(self) -> None:
        """Gracefully close browser resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    async def _fetch_static(self, url: str) -> tuple[str, int]:
        """Fetch page content via httpx (fast, no JS rendering)."""
        headers = {
            "User-Agent": get_random_user_agent() if settings.SCRAPER_USER_AGENT_ROTATION
            else "WebScraperBot/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        timeout = httpx.Timeout(settings.SCRAPER_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            return resp.text, resp.status_code

    async def _fetch_dynamic(self, url: str) -> tuple[str, int]:
        """Fetch page content via Playwright (renders JavaScript)."""
        await self._ensure_browser()

        if self._browser is None:
            # Playwright unavailable; fall back to static
            logger.info("Playwright unavailable, falling back to static fetch for %s", url)
            return await self._fetch_static(url)

        page = await self._browser.new_page()
        try:
            ua = get_random_user_agent() if settings.SCRAPER_USER_AGENT_ROTATION else "WebScraperBot/1.0"
            await page.set_extra_http_headers({"User-Agent": ua})
            response = await page.goto(
                url,
                wait_until="networkidle",
                timeout=settings.SCRAPER_TIMEOUT_SECONDS * 1000,
            )
            status = response.status if response else 0
            content = await page.content()
            return content, status
        finally:
            await page.close()

    async def fetch_page(
        self, url: str, use_playwright: bool = False
    ) -> tuple[str, int]:
        """
        Fetch a page. If use_playwright is True, render with Playwright;
        otherwise use the faster static fetcher.
        """
        if use_playwright:
            return await self._fetch_dynamic(url)
        return await self._fetch_static(url)

    # ------------------------------------------------------------------
    # Core scrape logic
    # ------------------------------------------------------------------

    async def scrape_url(
        self,
        url: str,
        crawl_depth: int = 2,
        follow_contact_pages: bool = True,
        use_ai: bool = True,
    ) -> dict[str, Any]:
        """
        Scrape a single URL and return structured data.

        Steps:
        1. Fetch the main page
        2. Try static first; if minimal content detected, retry with Playwright
        3. Extract data from main page
        4. If follow_contact_pages, discover contact pages and scrape them
        5. Crawl internal pages up to crawl_depth
        6. Normalize and return results
        """
        logger.info("Scraping URL: %s (depth=%d)", url, crawl_depth)
        collected: dict[str, Any] = {
            "website_url": url,
            "organization_name": None,
            "address": None,
            "phone_numbers": [],
            "email_addresses": [],
            "social_media_links": {},
            "contact_person": None,
            "description": None,
            "page_title": None,
            "scraped_pages_count": 0,
        }

        visited: set[str] = set()
        all_links: set[str] = set()
        pages_scraped = 0

        async def _scrape_page(page_url: str, depth: int) -> None:
            nonlocal pages_scraped

            normalized = page_url.rstrip("/")
            if normalized in visited:
                return
            visited.add(normalized)

            # Fetch (try static first)
            html, status = await self.fetch_page(page_url, use_playwright=False)
            soup = BeautifulSoup(html, "lxml")

            # If the page looks very empty (likely JS-rendered), retry with Playwright
            body_text = soup.get_text(strip=True)
            if len(body_text) < 200 and status == 200:
                logger.debug("Low content for %s, retrying with Playwright", page_url)
                html, status = await self.fetch_page(page_url, use_playwright=True)
                soup = BeautifulSoup(html, "lxml")

            pages_scraped += 1

            # ── AI-powered extraction ────────────────────────────
            if use_ai and settings.AI_EXTRACTION_ENABLED and settings.DEEPSEEK_API_KEY:
                try:
                    ai_data = await extract_with_ai(html, page_url)
                    if ai_data:
                        # Merge AI results (AI takes precedence when available)
                        if ai_data.get("organization_name"):
                            collected["organization_name"] = ai_data["organization_name"]
                        if ai_data.get("description"):
                            collected["description"] = ai_data["description"]
                        if ai_data.get("address"):
                            collected["address"] = ai_data["address"]
                        if ai_data.get("contact_person"):
                            collected["contact_person"] = ai_data["contact_person"]
                        collected["phone_numbers"].extend(ai_data.get("phone_numbers", []))
                        collected["email_addresses"].extend(ai_data.get("email_addresses", []))
                        for plat, link in ai_data.get("social_media_links", {}).items():
                            if plat not in collected["social_media_links"]:
                                collected["social_media_links"][plat] = link
                        logger.info("AI extraction merged for %s", page_url)
                except Exception as exc:
                    logger.warning("AI extraction failed for %s: %s", page_url, exc)

            # ── Regex/heuristic extraction (always runs as fallback/supplement) ─
            # Regex fills in any gaps that AI might have missed

            if collected["page_title"] is None:
                title_tag = soup.find("title")
                if title_tag:
                    collected["page_title"] = title_tag.get_text(strip=True)

            if collected["organization_name"] is None:
                collected["organization_name"] = extractors.extract_organization_name(soup, page_url)

            # Always extract phones/emails via regex as supplement to AI
            page_text = soup.get_text(separator=" ", strip=True)

            emails = extractors.extract_emails(page_text)
            for email in emails:
                if email not in collected["email_addresses"]:
                    collected["email_addresses"].append(email)

            phones = extractors.extract_phone_numbers(page_text)
            for phone in phones:
                if phone not in collected["phone_numbers"]:
                    collected["phone_numbers"].append(phone)

            if collected["address"] is None:
                collected["address"] = extractors.extract_address_from_soup(soup)

            if collected["description"] is None:
                collected["description"] = extractors.extract_description(soup)

            if collected["contact_person"] is None:
                collected["contact_person"] = extractors.extract_contact_person(soup)

            # Social links (collect all hrefs and classify)
            page_links = extractors.extract_all_links(soup, page_url)
            all_links.update(page_links)
            social = extractors.extract_social_links(list(page_links))
            for plat, link in social.items():
                if plat not in collected["social_media_links"]:
                    collected["social_media_links"][plat] = link

            # ── Crawl deeper if within depth ──────────────────────
            if depth < crawl_depth:
                internal = extractors.extract_internal_links(soup, page_url)
                # Filter out non-HTML URLs (PDFs, images, etc.)
                internal = [
                    link for link in internal
                    if not any(link.lower().endswith(ext) for ext in (
                        '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg',
                        '.doc', '.docx', '.xls', '.xlsx', '.zip', '.mp4',
                        '.mp3', '.ppt', '.pptx',
                    ))
                ]
                # Prioritize contact pages if enabled
                contact_pages: list[str] = []
                other_pages: list[str] = []
                for link in internal:
                    if link in visited:
                        continue
                    if follow_contact_pages and extractors.is_contact_page(link):
                        contact_pages.append(link)
                    else:
                        other_pages.append(link)

                # Scrape contact pages first, then others (limit to avoid explosion)
                pages_to_crawl = (contact_pages + other_pages)[:5]
                tasks = [
                    _scrape_page(link, depth + 1)
                    for link in pages_to_crawl
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

        # ── Execute: scrape main page first ──────────────────────
        await _scrape_page(url, 0)
        collected["scraped_pages_count"] = pages_scraped

        # ── Contact-page fallback: if phone, email, AND address all missing ─
        if follow_contact_pages:
            contact_data_missing = (
                len(collected["phone_numbers"]) == 0
                and len(collected["email_addresses"]) == 0
                and collected["address"] is None
            )
            if contact_data_missing:
                logger.info(
                    "Phone, email & address all missing for %s — searching contact pages",
                    url,
                )

                # 1. Find contact-page links discovered on the main page
                discovered_contacts: list[str] = []
                for link in all_links:
                    if link not in visited and extractors.is_contact_page(link):
                        discovered_contacts.append(link)

                # 2. Also try well-known contact paths as fallback
                candidate_paths = _discover_contact_paths(url, collected)

                # Merge: discovered first, then candidates (deduplicated, limit 5)
                all_contact_urls: list[str] = []
                seen_contacts: set[str] = set()
                for contact_url in discovered_contacts + candidate_paths:
                    normalized = contact_url.rstrip("/")
                    if normalized not in seen_contacts and normalized not in visited:
                        seen_contacts.add(normalized)
                        all_contact_urls.append(contact_url)

                if all_contact_urls:
                    logger.info(
                        "Found %d contact pages to scrape for %s (discovered=%d, candidates=%d)",
                        len(all_contact_urls[:5]), url,
                        len(discovered_contacts), len(candidate_paths),
                    )
                    tasks = [
                        _scrape_page(contact_url, crawl_depth)
                        for contact_url in all_contact_urls[:5]
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    collected["scraped_pages_count"] = pages_scraped
                else:
                    logger.debug("No contact pages found for %s", url)

            # ── Also fill missing org name / description from contact pages ─
            elif collected["organization_name"] is None or collected["description"] is None:
                discovered_contacts: list[str] = []
                for link in all_links:
                    if link not in visited and extractors.is_contact_page(link):
                        discovered_contacts.append(link)

                if discovered_contacts:
                    logger.info(
                        "Org name/description missing for %s, trying %d discovered contact pages",
                        url, len(discovered_contacts[:3]),
                    )
                    tasks = [
                        _scrape_page(contact_url, crawl_depth)
                        for contact_url in discovered_contacts[:3]
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    collected["scraped_pages_count"] = pages_scraped

        # Normalize
        collected = normalize_extracted_data(collected)
        collected["phone_numbers"] = list(set(collected.get("phone_numbers", [])))
        collected["email_addresses"] = list(set(collected.get("email_addresses", [])))

        return collected

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    async def scrape_with_retry(self, url: str, use_ai: bool = True, **kwargs: Any) -> dict[str, Any]:
        """
        Scrape a URL with automatic retries on failure.
        """
        max_retries = settings.SCRAPER_MAX_RETRIES
        last_error: str | None = None

        for attempt in range(1, max_retries + 1):
            try:
                return await self.scrape_url(url, use_ai=use_ai, **kwargs)
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Attempt %d/%d failed for %s: %s",
                    attempt, max_retries, url, last_error,
                )
                if attempt < max_retries:
                    backoff = settings.SCRAPER_RETRY_BACKOFF_FACTOR ** attempt
                    await asyncio.sleep(backoff)

        raise RuntimeError(f"All {max_retries} attempts failed for {url}: {last_error}")
