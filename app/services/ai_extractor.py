"""
AI-powered data extraction using DeepSeek API with thinking model (deepseek-v4-pro).
Uses the OpenAI-compatible client for cleaner API interaction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

EXTRACTION_PROMPT = """You are an expert data extraction assistant. Extract structured information from the website content below.

Return ONLY a valid JSON object. No markdown, no explanations — just the JSON.

═══════════════════════════════════════════
CRITICAL: ADDRESS, PHONE & EMAIL EXTRACTION
═══════════════════════════════════════════
These are the MOST important fields. Search exhaustively:

📌 ADDRESS — Look EVERYWHERE for any location/address clue:
  - Lines labeled "Address", "Location", "Reach Us", "Find Us", "Office", "Headquarters"
  - Text near a pincode (6-digit number in India, 5-digit in US)
  - Text containing "Road", "Street", "Marg", "Colony", "Nagar", "Peth", "Camp", "District"
  - Footer text blocks, contact sections, sidebars — often multi-line
  - RECONSTRUCT multi-line addresses by joining adjacent address fragments
  - Indian addresses often appear as: "Org Name, Building, Area, City - PINCODE, State (Country)"
  - If you see fragments like "Jalgaon - 425001" or "Maharashtra (India)" nearby, join them into one address
  - Return the COMPLETE address as a single string, even if spread across multiple lines

📞 PHONE NUMBERS — Find ALL numbers matching these patterns:
  - Indian: +91-XXXX-XXXXXXX, +91 XXXXXXXXXX, 0XXX-XXXXXXX, (0XXX) XXXXXXX
  - Also: Fax numbers, mobile numbers (10-digit starting with 6/7/8/9)
  - Check headers, footers, "Contact" sections, sidebar widgets
  - Include numbers even if they appear inside parentheses or after "Phone:", "Tel:", "Fax:", "Mobile:", "Contact:"
  - Format them cleanly: strip extra spaces but keep the +91 prefix if present

📧 EMAIL ADDRESSES — Find ALL emails containing @:
  - Check every section: headers, footers, contact pages, about pages
  - Common patterns: info@..., contact@..., admin@..., principal@..., office@...
  - Include emails from mailto: links

═══════════════════════════════════════════
OTHER FIELDS (secondary):
═══════════════════════════════════════════
- organization_name: Full official name from title, header, or branding
- description: 2-3 sentence summary of what the organization does
- social_media_links: Only facebook, twitter/x, linkedin, instagram, youtube URLs
- contact_person: Name after "Principal", "Contact Person", "Head", "I/c Principal", "Director" labels

Output format:
{
  "organization_name": "Name or null",
  "description": "Summary or null",
  "address": "Full address or null",
  "phone_numbers": ["phone1", "phone2"],
  "email_addresses": ["email1", "email2"],
  "social_media_links": {},
  "contact_person": "Name or null"
}

WEBSITE CONTENT:
"""

# ── Shared AsyncOpenAI client (lazy) ───────────────────────────────────────

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Get or create the shared AsyncOpenAI client for DeepSeek."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
    return _client


def _clean_html(html: str) -> str:
    """Extract clean text from HTML, preserving structure for address extraction."""
    soup = BeautifulSoup(html, "lxml")
    # Only remove truly non-content elements
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas"]):
        tag.decompose()

    # Convert <br> tags to newlines to preserve address line breaks
    for br in soup.find_all("br"):
        br.replace_with("\n")

    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive blank lines but keep single/double newlines (helps with address parsing)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)

    # Increased limit to 18000 chars to capture more content from contact pages
    if len(text) > 18000:
        text = text[:18000] + "\n... [truncated]"
    return text


async def extract_with_ai(html: str, url: str = "") -> dict[str, Any]:
    """
    Send page content to DeepSeek for intelligent data extraction.
    Uses deepseek-v4-pro thinking model for thorough, accurate extraction.
    """
    if not settings.DEEPSEEK_API_KEY or not settings.AI_EXTRACTION_ENABLED:
        logger.debug("AI extraction disabled or no API key configured")
        return {}

    text = _clean_html(html)
    if len(text.strip()) < 50:
        logger.debug("Page content too short: %s", url)
        return {}

    client = _get_client()

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "You are a precise data extraction assistant. Only return valid JSON, no markdown, no explanations.",
        },
        {
            "role": "user",
            "content": EXTRACTION_PROMPT + "\n\nURL: " + url + "\n\n" + text,
        },
    ]

    try:
        response = await client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=8000,
            reasoning_effort=settings.DEEPSEEK_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
            response_format={"type": "json_object"},
            timeout=180.0,
        )

        message = response.choices[0].message

        # Log reasoning tokens if present (thinking model)
        reasoning_content = getattr(message, "reasoning_content", "")
        if reasoning_content:
            logger.debug("AI reasoning used %d chars for %s", len(reasoning_content), url)

        content = message.content or ""
        if not content.strip():
            logger.warning("DeepSeek returned empty content for %s", url)
            return {}

        data = json.loads(content)

        cleaned: dict[str, Any] = {
            "organization_name": _clean_str(data.get("organization_name")),
            "description": _clean_str(data.get("description")),
            "address": _clean_str(data.get("address")),
            "phone_numbers": _clean_list(data.get("phone_numbers")),
            "email_addresses": _clean_list(data.get("email_addresses")),
            "social_media_links": _clean_dict(data.get("social_media_links")),
            "contact_person": _clean_str(data.get("contact_person")),
        }

        logger.info(
            "AI extraction: org=%s phones=%d emails=%d addr=%s",
            cleaned.get("organization_name", "?"),
            len(cleaned.get("phone_numbers", [])),
            len(cleaned.get("email_addresses", [])),
            "yes" if cleaned.get("address") else "no",
        )
        return cleaned

    except json.JSONDecodeError as e:
        logger.warning("DeepSeek JSON parse failed for %s: %s", url, e)
        return {}
    except Exception as e:
        logger.error("AI extraction failed for %s: %s", url, e)
        return {}


def _clean_str(value: Any) -> str | None:
    """Clean a string value."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s and s.lower() not in ("null", "none", "n/a", "not found", "") else None


def _clean_list(value: Any) -> list[str]:
    """Clean a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [
        s for item in value
        if (s := _clean_str(item)) is not None
    ]


def _clean_dict(value: Any) -> dict[str, str]:
    """Clean a dict of string values."""
    if value is None or not isinstance(value, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in value.items()
        if v and str(v).strip()
    }
