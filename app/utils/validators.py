"""
URL and input validation utilities.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------
# Stricter email regex — requires valid TLD of 2+ chars, domain must have at least one dot
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?",
    re.IGNORECASE,
)

# Stricter phone regex — requires country code OR common Indian/mobile patterns
# Matches: +91-257-2237363, (0257) 2237363, 0257-2237363, 9876543210, +1-555-123-4567
PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s.-]?)?"             # optional country code
    r"(?:\(\d{2,5}\)[\s.-]?)?"           # optional area code in parens
    r"\d{2,5}[\s.-]?\d{6,10}"             # main number: 2-5 digits + 6-10 digits
    r"(?:[\s.-]?\d{1,6})?",               # optional extension
    re.IGNORECASE,
)

# Minimum phone digits (excluding country code) for a valid phone
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15

# Invalid phone patterns (page numbers, IDs, counters, years, etc.)
INVALID_PHONE_PATTERNS = [
    re.compile(r"^0{5,}"),              # all zeros
    re.compile(r"^(\d)\1{7,}$"),        # same digit repeated 8+ times (like 11111111)
    re.compile(r"^\d{10,}$"),           # too many consecutive digits (likely an ID, not phone)
]

SOCIAL_MEDIA_DOMAINS = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "linkedin.com": "linkedin",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "github.com": "github",
    "medium.com": "medium",
}


def is_valid_url(url: str) -> bool:
    """Check if a string is a valid HTTP/HTTPS URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def normalize_url(url: str) -> str:
    """Normalize a URL: lowercase scheme+host, remove trailing slash, strip fragments."""
    parsed = urlparse(url)
    normalized = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/') or '/'}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


def extract_emails(text: str) -> list[str]:
    """Extract unique email addresses from text."""
    return list({match.group(0).lower() for match in EMAIL_RE.finditer(text)})


def extract_phone_numbers(text: str) -> list[str]:
    """Extract realistic phone numbers from text. Filters out IDs, counters, page numbers."""
    raw = [match.group(0).strip() for match in PHONE_RE.finditer(text)]
    valid: list[str] = []
    seen: set[str] = set()
    for p in raw:
        digits = re.sub(r"\D", "", p)
        # Must have reasonable number of digits
        if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
            continue
        # Skip obviously invalid patterns (all zeros, repeated single digits, long IDs)
        if any(pat.search(digits) for pat in INVALID_PHONE_PATTERNS):
            continue
        if p not in seen:
            seen.add(p)
            valid.append(p)
    return valid


def classify_social_link(url: str) -> tuple[str, str] | None:
    """
    Attempt to classify a URL as a social media link.
    Returns (platform_name, url) or None.
    """
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().lstrip("www.")
        for domain, platform in SOCIAL_MEDIA_DOMAINS.items():
            if host == domain or host.endswith(f".{domain}"):
                return (platform, url)
    except Exception:
        pass
    return None


def extract_social_links(links: list[str]) -> dict[str, str]:
    """Classify a list of hrefs into a {platform: url} dict."""
    result: dict[str, str] = {}
    for link in links:
        classified = classify_social_link(link)
        if classified:
            platform, url = classified
            if platform not in result:
                result[platform] = url
    return result


def sanitize_filename(filename: str) -> str:
    """Remove or replace characters unsafe for file names."""
    return re.sub(r"[^\w\s.-]", "_", filename).strip()
