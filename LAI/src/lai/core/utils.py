"""Shared utilities for the LAI platform."""

import hashlib
import re

from lai.core.constants import (
    ARTICLE_PATTERN,
    COURT_DECISION_PATTERN,
    GERMAN_LAW_CODES,
    PARAGRAPH_PATTERN,
)


def sanitize_text(text: str) -> str:
    """Remove NUL bytes and normalize whitespace. Apply to ALL text before DB writes."""
    text = text.replace("\x00", "")
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def content_hash(text: str) -> str:
    """SHA256 hash of text content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_law_codes(text: str) -> list[str]:
    """Extract German law code abbreviations from text."""
    found = []
    for code in GERMAN_LAW_CODES:
        if re.search(rf"\b{re.escape(code)}\b", text):
            found.append(code)
    return sorted(set(found))


def extract_paragraph_refs(text: str) -> list[str]:
    """Extract SS references from text."""
    return [m.group(0).strip() for m in PARAGRAPH_PATTERN.finditer(text)]


def extract_article_refs(text: str) -> list[str]:
    """Extract Art. references from text."""
    return [m.group(0).strip() for m in ARTICLE_PATTERN.finditer(text)]


def extract_court_refs(text: str) -> list[str]:
    """Extract court decision references from text."""
    return [m.group(0).strip() for m in COURT_DECISION_PATTERN.finditer(text)]


def estimate_tokens(text: str) -> int:
    """Rough token count estimate for German text (~1.3 tokens per word)."""
    word_count = len(text.split())
    return int(word_count * 1.3)
