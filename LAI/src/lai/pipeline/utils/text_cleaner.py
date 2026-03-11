"""Text cleaning utilities for German legal documents."""

import re

from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.utils.text_cleaner")


def clean_text(text: str) -> str:
    """Remove noise patterns common in legal document conversions."""
    if not text:
        return ""
    original_len = len(text)
    text = text.replace("\x00", "")
    text = text.replace("[Picture]", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    cleaned = text.strip()
    removed = original_len - len(cleaned)
    if removed > 100:
        logger.debug(f"Cleaned text: {original_len} → {len(cleaned)} chars ({removed} removed)")
    return cleaned
