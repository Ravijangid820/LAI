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

    # Fix § vs $ OCR confusion in legal context:
    # "$" followed by a section number + law code is almost certainly "§"
    text = re.sub(r"\$\s*(\d+\s*(?:Abs\.|Nr\.|Satz|ff\b))", r"§ \1", text)
    # "$" + number + German law abbreviation (3+ chars starting uppercase, not currency codes)
    text = re.sub(
        r"\$\s*(\d+[a-z]?\s+(?:(?:HGB|BGB|BImSchG|GG|VwVfG|BauGB|StGB|ZPO|AO|GewO|WHG|BNatSchG|EEG|EnWG|UVPG|ROG|LPlG|FlurbG|GrStG|UStG|EStG|KStG|GewStG|AktG|GmbHG)\b))",
        r"§ \1",
        text,
    )

    # Rejoin hyphenated line breaks: "Bau- last" → "Baulast"
    # Only when lowercase letter follows (not compound words like "Getriebe- und")
    text = re.sub(r"(\w{2,})- (\w)", _rejoin_hyphen, text)

    cleaned = text.strip()
    removed = original_len - len(cleaned)
    if removed > 100:
        logger.debug(f"Cleaned text: {original_len} → {len(cleaned)} chars ({removed} removed)")
    return cleaned


def _rejoin_hyphen(match: re.Match) -> str:
    """Rejoin hyphenated words, but keep intentional compounds."""
    before, after = match.group(1), match.group(2)
    # Keep compound separations: "Getriebe- und", "Bau- und"
    if after in ("u", "o", "b"):  # und, oder, bzw — keep the hyphen
        return match.group(0)
    # Keep if next char is uppercase (proper noun or compound): "WEA- Betrieb"
    if after.isupper():
        return match.group(0)
    # Rejoin: "Bau- last" → "Baulast"
    return before + after
