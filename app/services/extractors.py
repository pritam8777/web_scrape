"""
Data extraction utilities using regex, BeautifulSoup, and heuristics.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.utils.validators import extract_emails, extract_phone_numbers, extract_social_links


# ── Address pattern (postal / physical address heuristics) ────────────────
# Matches common address patterns like "123 Main St, City, ST 12345"
ADDRESS_PATTERNS = [
    re.compile(
        r"\d{1,6}\s+\w+(\s+\w+)*[\s,]*(?:street|st|avenue|ave|road|rd|boulevard|blvd|"
        r"drive|dr|lane|ln|court|ct|plaza|way|circle|cir)[\s,.]*"
        r"(?:#?\s*\d+[\w-]*)?[\s,.]*\n?"
        r".*?(?:[A-Z]{2}\s+\d{5}(?:-\d{4})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:P\.?O\.?\s*Box\s+\d+)[\s,.]*\n?.*?(?:[A-Z]{2}\s+\d{5}(?:-\d{4})?)",
        re.IGNORECASE,
    ),
]


def extract_address_from_text(text: str) -> str | None:
    """Try to find a postal address in free text."""
    for pattern in ADDRESS_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def extract_address_from_soup(soup: BeautifulSoup) -> str | None:
    """
    Heuristically find an address block from common HTML patterns:
    - <address> tag
    - Elements with class/id containing 'address'
    - Footer text blocks
    """
    # 1. <address> tag
    addr_tag = soup.find("address")
    if addr_tag:
        text = addr_tag.get_text(separator=" ", strip=True)
        if len(text) > 10:
            return text

    # 2. Elements with address-related class/id
    for selector in [
        "[class*='address']",
        "[id*='address']",
        "[class*='location']",
        "[itemprop='address']",
    ]:
        for el in soup.select(selector):
            text = el.get_text(separator=" ", strip=True)
            if len(text) > 10:
                return text

    # 3. Fallback: search full page text
    text = soup.get_text(separator="\n", strip=True)
    return extract_address_from_text(text)


def extract_organization_name(soup: BeautifulSoup, url: str) -> str | None:
    """
    Extract organization name from:
    1. <title> tag (often contains company name)
    2. <meta property="og:site_name">
    3. Common header elements
    """
    # og:site_name
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        return str(og["content"]).strip()

    # Schema.org markup
    for schema_tag in soup.select("[itemtype*='Organization'] [itemprop='name']"):
        name = schema_tag.get("content") or schema_tag.get_text(strip=True)
        if name:
            return name

    # <title> tag (strip common suffixes)
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()
        # Remove common separators/suffixes
        for sep in [" | ", " - ", " – ", " :: ", " — "]:
            if sep in title:
                parts = title.split(sep)
                # Return the first meaningful part
                for part in parts:
                    if len(part.split()) >= 2:
                        return part.strip()
        return title

    # h1 as last resort
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    return None


def extract_contact_person(soup: BeautifulSoup) -> str | None:
    """Look for contact person names in the page — only from labeled fields."""
    # Common patterns: "Principal: Dr. Name", "Contact Person: Name", etc.
    patterns = [
        r"(?:Principal\s*:?\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?){1,3})",
        r"(?:Contact\s+Person\s*:?\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?){1,3})",
        r"(?:Head\s*(?:of\s*)?(?:Department|Dept)\s*:?\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?){1,3})",
    ]
    text = soup.get_text(separator=" ", strip=True)
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            # Filter out false positives like "Us" from "Contact Us"
            if name.lower() not in ("us", "me", "our", "the", "for", "and"):
                return name
    return None


def extract_description(soup: BeautifulSoup) -> str | None:
    """Extract description from meta tags or first meaningful paragraph."""
    # Meta description
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return str(meta["content"]).strip()

    # og:description
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return str(og["content"]).strip()

    # First substantial paragraph (skip nav/footer)
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 80:
            return text

    return None


def extract_all_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract all absolute hrefs from a page."""
    from urllib.parse import urljoin

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        absolute = urljoin(base_url, href)
        links.append(absolute)
    return links


def extract_internal_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract only same-domain links."""
    from urllib.parse import urlparse

    all_links = extract_all_links(soup, base_url)
    base_domain = urlparse(base_url).netloc.lower()

    internal: list[str] = []
    for link in all_links:
        try:
            domain = urlparse(link).netloc.lower()
            if domain == base_domain or domain.endswith(f".{base_domain}"):
                internal.append(link)
        except Exception:
            continue
    return internal


def is_contact_page(url: str, soup: BeautifulSoup | None = None) -> bool:
    """Determine if a URL or page is a Contact Us / About page."""
    url_lower = url.lower()
    contact_keywords = [
        "contact", "contact-us", "contactus", "about", "about-us",
        "aboutus", "reach-us", "get-in-touch", "connect", "support",
        "help", "customer-service", "location", "find-us", "our-offices",
        "branches", "enquiry", "enquiries",
    ]
    if any(kw in url_lower for kw in contact_keywords):
        return True

    if soup:
        title = (soup.find("title") or Tag(name="title")).get_text(strip=True).lower()
        h1_text = (soup.find("h1") or Tag(name="h1")).get_text(strip=True).lower()
        if any(kw in title or kw in h1_text for kw in contact_keywords):
            return True

        # Check link text of the page itself (useful when called on a page)
        body_text = soup.get_text(separator=" ", strip=True).lower()
        contact_indicators = [
            "contact us", "get in touch", "reach us", "our address",
            "phone:", "tel:", "email:", "fax:",
        ]
        if any(indicator in body_text for indicator in contact_indicators):
            return True

    return False
