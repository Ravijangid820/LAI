"""Configuration for :class:`lai.common.pdf.extractor.PdfExtractor`.

Pure-local operation (no HTTP), so the config surface is simpler than the
network clients. The defaults match the behaviour of the existing
``micro-services/ddiq_report.py`` extractor: a 50-char per-page quality
threshold, 2x page upscaling for OCR, and the German + English language
pack.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["PdfExtractorConfig"]


class PdfExtractorConfig(BaseSettings):
    """Settings for the PDF extractor.

    All knobs frozen after construction; mutations raise
    :class:`pydantic.ValidationError`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_PDF_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── OCR fallback ────────────────────────────────────────────────────
    enable_ocr: bool = Field(
        default=True,
        description=(
            "If ``True``, pages whose embedded text is shorter than "
            ":attr:`min_chars_per_page` are re-rendered and passed through "
            "Tesseract. If ``False``, the extractor records the page as "
            "low-text and skips OCR entirely — appropriate for environments "
            "without a Tesseract install."
        ),
    )
    min_chars_per_page: int = Field(
        default=50,
        ge=0,
        description=(
            "Per-page character threshold below which OCR is attempted. "
            "Matches the historical ``ddiq_report.py`` heuristic. "
            "``0`` disables the heuristic and forces OCR on every page "
            "when ``enable_ocr`` is true (only useful for scanned-PDF "
            "ingest)."
        ),
    )
    ocr_languages: str = Field(
        default="deu+eng",
        min_length=3,
        description=(
            "Tesseract language pack(s) to use. ``deu+eng`` is the "
            "default for German legal text with occasional English."
        ),
    )
    ocr_zoom: float = Field(
        default=2.0,
        gt=0.0,
        le=8.0,
        description=(
            "Linear page-render zoom factor applied before OCR. Tesseract "
            "needs roughly 300 DPI for reliable text; at typical PDF native "
            "resolution (~72-96 DPI) a 2x zoom comfortably crosses that "
            "threshold without exploding memory."
        ),
    )
    min_alpha_ratio: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Quality gate (E11): a page with enough characters can still be "
            "garbage — a botched embedded font maps glyphs to control bytes / "
            "private-use codepoints, so the page passes the length check but "
            "is unreadable. If the fraction of 'real' characters (letters, "
            "digits, whitespace, common punctuation) in the embedded text is "
            "below this ratio, the page is treated as low-quality and OCR is "
            "attempted (when ``enable_ocr``). ``0.0`` disables the ratio "
            "check, restoring the pure length-only heuristic."
        ),
    )
    min_chars_for_ratio_check: int = Field(
        default=200,
        ge=0,
        description=(
            "The alphabetic-ratio gate only applies to pages with at least "
            "this many embedded characters. Short pages (headers, cover "
            "sheets, a single stamp) legitimately have a low ratio and "
            "shouldn't be forced through OCR on that basis alone; the "
            "length heuristic already covers them."
        ),
    )

    # ── Document limits ────────────────────────────────────────────────
    max_pages: int = Field(
        default=2000,
        gt=0,
        description=(
            "Reject documents longer than this. Wind-energy DD VDRs "
            "occasionally contain massive scanned bundles; the 2000-page "
            "cap protects the OCR worker from a memory blow-up while "
            "comfortably covering normal traffic."
        ),
    )

    # ── Page join ──────────────────────────────────────────────────────
    page_separator: str = Field(
        default="\n\n",
        description=(
            "Separator joined between page texts when callers request the "
            "concatenated document body. Two newlines preserves paragraph "
            "boundaries when downstream chunkers split on blank lines."
        ),
    )
