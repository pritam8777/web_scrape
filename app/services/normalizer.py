"""
Data normalization utilities – clean, deduplicate, and standardise extracted fields.
"""

from __future__ import annotations

import re


def normalize_phone(phone: str) -> str:
    """Normalize phone number: strip whitespace, standardise format."""
    # Remove all non-digit characters except leading +
    has_plus = phone.strip().startswith("+")
    digits = re.sub(r"\D", "", phone)
    if has_plus:
        return f"+{digits}"
    return digits


def normalize_email(email: str) -> str:
    """Normalize email address to lowercase."""
    return email.strip().lower()


def normalize_url_field(url: str | None) -> str | None:
    """Ensure URL has scheme, lowercase host."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.rstrip("/")


def deduplicate_list(items: list[str], case_sensitive: bool = False) -> list[str]:
    """Remove duplicates while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item if case_sensitive else item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def clean_text(text: str | None) -> str | None:
    """Collapse whitespace, strip, and return None for empty strings."""
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def normalize_extracted_data(data: dict) -> dict:
    """
    Apply all normalisation rules to a raw extraction dict.
    """
    if data.get("phone_numbers"):
        data["phone_numbers"] = deduplicate_list(
            [normalize_phone(p) for p in data["phone_numbers"]]
        )

    if data.get("email_addresses"):
        data["email_addresses"] = deduplicate_list(
            [normalize_email(e) for e in data["email_addresses"]]
        )

    if data.get("website_url"):
        data["website_url"] = normalize_url_field(data["website_url"])

    for field in ("organization_name", "address", "contact_person", "description"):
        if data.get(field):
            data[field] = clean_text(data[field])

    return data
