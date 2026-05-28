"""PDF text extractor with optional Tesseract OCR fallback.

Replaces the hand-rolled ``extract_pdf_text`` block in
``micro-services/ddiq_report.py``. The extractor accepts a path *or* raw
bytes, returns per-page results tagged with the source of each page's
text (embedded vs. OCR), and yields a clean :class:`PdfExtractError` on
unrecoverable input — never a bare PyMuPDF exception.

Optional dependencies
---------------------

This module imports :mod:`fitz` (PyMuPDF) lazily and uses :mod:`pytesseract`
+ :mod:`PIL` lazily as well. The imports are deferred to method-call time
so consumers that never touch a PDF do not pay the import cost, and so
unit tests can dependency-inject fakes without monkey-patching the
top-level imports.

Sync-only
---------

Unlike the LLM / reranker / embedding clients, this extractor is sync.
The bottleneck is local CPU work (PyMuPDF page rendering, Tesseract OCR)
rather than network I/O. Wrapping it in ``asyncio.to_thread`` from a
caller's async context is the right pattern when needed; an explicit
async surface would not buy concurrency and would obscure the fact that
each call is bound to one CPU core.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from lai.common.exceptions import PdfExtractError, PdfOcrUnavailableError
from lai.common.pdf.config import PdfExtractorConfig

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["PdfExtractResult", "PdfExtractor", "PdfPageResult", "PdfPageSource"]

_log = structlog.get_logger(__name__)


class PdfPageSource(StrEnum):
    """Where a page's text came from."""

    EMBEDDED = "embedded"
    """Text was read directly from the PDF's text layer."""

    OCR = "ocr"
    """Text was synthesised by running Tesseract over a rendered image."""

    EMPTY = "empty"
    """Page produced no usable text (embedded missed the quality bar and
    OCR was disabled, or OCR also returned empty / whitespace)."""


@dataclass(frozen=True, slots=True)
class PdfPageResult:
    """Result for a single PDF page."""

    index: int
    """Zero-based page index in the source document."""

    text: str
    """Extracted text (already ``str.strip()``-ed)."""

    source: PdfPageSource
    """Provenance of the text — see :class:`PdfPageSource`."""


@dataclass(frozen=True, slots=True)
class PdfExtractResult:
    """Result of extracting a full PDF.

    Attributes:
        pages: Per-page results in document order.
        text: Concatenation of the per-page texts, separated by
            :attr:`PdfExtractorConfig.page_separator`. Empty when every
            page failed to produce text.
        page_count: ``len(pages)`` — handy shorthand for callers that
            only need the count.
        ocr_page_count: Number of pages whose text came from OCR. Useful
            for downstream quality / metrics dashboards.
        elapsed_seconds: Wall-clock time spent extracting, useful for the
            DDiQ pipeline's wall-time accounting.
    """

    pages: tuple[PdfPageResult, ...]
    text: str
    page_count: int
    ocr_page_count: int
    elapsed_seconds: float


# ─────────────────────────────────────────────────────────────────────────────
# PdfExtractor
# ─────────────────────────────────────────────────────────────────────────────


class PdfExtractor:
    """Extract text (and optionally OCR'd text) from a PDF.

    Args:
        config: :class:`PdfExtractorConfig`; defaults to
            ``PdfExtractorConfig()`` which reads from environment variables.

    Test injection:
        :meth:`extract` calls module-level helpers
        :func:`_open_pdf_document` and :func:`_ocr_page_image`. Tests can
        replace either at the module level via ``monkeypatch.setattr`` to
        avoid the real PyMuPDF / Tesseract dependencies.
    """

    def __init__(self, config: PdfExtractorConfig | None = None) -> None:
        self._config = config or PdfExtractorConfig()

    # ── Public API ──────────────────────────────────────────────────────

    def extract_bytes(self, data: bytes) -> PdfExtractResult:
        """Extract from a PDF held in memory.

        Args:
            data: The raw PDF bytes. Must start with the ``%PDF-`` magic.

        Raises:
            PdfExtractError: The bytes were not a valid PDF, or were
                empty, or exceeded :attr:`PdfExtractorConfig.max_pages`.
            PdfOcrUnavailableError: OCR was needed (low-text page,
                ``enable_ocr=True``) but Tesseract is not installed.
        """
        if not data:
            raise PdfExtractError("PDF bytes were empty")
        return self._extract(source_label=None, open_args={"stream": data, "filetype": "pdf"})

    def extract_path(self, path: str | Path) -> PdfExtractResult:
        """Extract from a PDF on disk.

        Args:
            path: Filesystem path to a readable PDF.

        Raises:
            PdfExtractError: The path does not exist, is not readable, is
                not a PDF, or exceeds :attr:`PdfExtractorConfig.max_pages`.
            PdfOcrUnavailableError: As :meth:`extract_bytes`.
        """
        p = Path(path)
        if not p.exists():
            raise PdfExtractError(f"PDF not found: {p}", path=str(p))
        if not p.is_file():
            raise PdfExtractError(f"PDF path is not a regular file: {p}", path=str(p))
        return self._extract(source_label=str(p), open_args={"filename": str(p)})

    # ── Internals ───────────────────────────────────────────────────────

    def _extract(
        self,
        *,
        source_label: str | None,
        open_args: dict[str, Any],
    ) -> PdfExtractResult:
        """Shared body for :meth:`extract_bytes` and :meth:`extract_path`."""
        start = time.perf_counter()
        try:
            document = _open_pdf_document(**open_args)
        except PdfExtractError:
            raise
        except Exception as exc:
            raise PdfExtractError(
                f"failed to open PDF: {exc}",
                path=source_label,
            ) from exc

        try:
            page_count = _document_page_count(document)
            if page_count == 0:
                raise PdfExtractError("PDF has zero pages", path=source_label)
            if page_count > self._config.max_pages:
                raise PdfExtractError(
                    f"PDF has {page_count} pages; max_pages is {self._config.max_pages}",
                    path=source_label,
                )

            page_results: list[PdfPageResult] = []
            for index in range(page_count):
                try:
                    page_result = self._extract_page(document, index)
                except PdfOcrUnavailableError:
                    raise
                except Exception as exc:
                    raise PdfExtractError(
                        f"failed to extract page {index}: {exc}",
                        path=source_label,
                        page_index=index,
                    ) from exc
                page_results.append(page_result)
        finally:
            _close_document(document)

        text = self._config.page_separator.join(p.text for p in page_results if p.text)
        ocr_pages = sum(1 for p in page_results if p.source is PdfPageSource.OCR)
        elapsed = time.perf_counter() - start
        _log.info(
            "pdf.extract.complete",
            path=source_label,
            page_count=len(page_results),
            ocr_page_count=ocr_pages,
            elapsed_seconds=round(elapsed, 4),
        )
        return PdfExtractResult(
            pages=tuple(page_results),
            text=text,
            page_count=len(page_results),
            ocr_page_count=ocr_pages,
            elapsed_seconds=elapsed,
        )

    def _extract_page(self, document: Any, index: int) -> PdfPageResult:
        """Pull text from one page, with OCR fallback if configured."""
        page = _document_page(document, index)
        embedded_text = _page_embedded_text(page).strip()

        # The embedded text is good enough only if it clears BOTH the
        # length threshold AND the quality gate (E11). A page can be long
        # but garbled — a broken embedded font maps glyphs to control
        # bytes / private-use codepoints, so it passes the length check
        # yet is unreadable. In that case we fall through to OCR.
        if len(embedded_text) >= self._config.min_chars_per_page and not self._is_low_quality_text(embedded_text):
            return PdfPageResult(
                index=index,
                text=embedded_text,
                source=PdfPageSource.EMBEDDED,
            )

        if not self._config.enable_ocr:
            return PdfPageResult(
                index=index,
                text=embedded_text,
                source=PdfPageSource.EMPTY if not embedded_text else PdfPageSource.EMBEDDED,
            )

        # OCR path: render to PNG bytes and pass to Tesseract.
        png_bytes = _render_page_png(page, zoom=self._config.ocr_zoom)
        ocr_text = _ocr_page_image(png_bytes, languages=self._config.ocr_languages).strip()
        if ocr_text:
            return PdfPageResult(index=index, text=ocr_text, source=PdfPageSource.OCR)
        return PdfPageResult(
            index=index,
            text=embedded_text,
            source=PdfPageSource.EMPTY if not embedded_text else PdfPageSource.EMBEDDED,
        )

    def _is_low_quality_text(self, text: str) -> bool:
        """E11 quality gate: detect garbled embedded text that passes the
        length check but is unreadable (broken-font mojibake → control
        bytes / private-use codepoints / replacement chars).

        Only applies to pages with at least
        ``min_chars_for_ratio_check`` characters — short pages (cover
        sheets, a lone stamp) legitimately have a low readable-character
        ratio and shouldn't be forced through OCR on that basis. Returns
        ``False`` (gate disabled) when ``min_alpha_ratio == 0``.
        """
        if self._config.min_alpha_ratio <= 0.0:
            return False
        if len(text) < self._config.min_chars_for_ratio_check:
            return False
        ratio = _readable_char_ratio(text)
        if ratio < self._config.min_alpha_ratio:
            _log.info(
                "pdf.low_quality_text",
                readable_ratio=round(ratio, 3),
                threshold=self._config.min_alpha_ratio,
                chars=len(text),
            )
            return True
        return False


# Punctuation + symbols that legitimately appear in German legal text and
# must count as "readable" alongside alphanumerics and whitespace. Anything
# outside this set AND not alnum/space (control bytes, private-use-area
# glyphs, U+FFFD replacement chars) signals broken-font mojibake.
# The en/em dashes and German typographic quotes in the set below are
# intentional: they are exactly the characters real German legal text
# uses, so they must count as readable. RUF001 (ambiguous-unicode in a
# string) is therefore expected here and suppressed on the data line.
_READABLE_PUNCT: frozenset[str] = frozenset(" .,;:!?-–—()[]{}'\"/%&§€$@#*+=<>|~`^_\\…„“”‚‘’«»°№")


def _readable_char_ratio(text: str) -> float:
    """Fraction of characters that are alphanumeric, whitespace, or common
    punctuation. A genuinely-text page scores ~0.97-1.0; a mojibake page
    (control / private-use codepoints) scores far lower. Empty string → 0.0.
    """
    if not text:
        return 0.0
    readable = sum(1 for ch in text if ch.isalnum() or ch.isspace() or ch in _READABLE_PUNCT)
    return readable / len(text)


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions — separated so tests can ``monkeypatch.setattr`` them
# without touching the real PyMuPDF / Tesseract imports.
# ─────────────────────────────────────────────────────────────────────────────


def _open_pdf_document(**kwargs: Any) -> Any:
    """Open a PDF via PyMuPDF (``fitz.open``).

    Either ``stream=...`` (with ``filetype="pdf"``) or ``filename=...``
    must be passed. Exposed as a module-level function so unit tests can
    patch it to a fake document.
    """
    import fitz

    return fitz.open(**kwargs)


def _document_page_count(document: Any) -> int:
    """Return the page count of a PyMuPDF document."""
    return int(document.page_count)


def _document_page(document: Any, index: int) -> Any:
    """Return one page (zero-indexed). PyMuPDF supports ``doc[i]``."""
    return document[index]


def _close_document(document: Any) -> None:
    """Close a PyMuPDF document, swallowing any close-time errors."""
    try:
        document.close()
    except Exception:
        _log.warning("pdf.close.failed", exc_info=True)


def _page_embedded_text(page: Any) -> str:
    """Read the embedded text layer of a page."""
    result = page.get_text()
    return result if isinstance(result, str) else str(result)


def _render_page_png(page: Any, *, zoom: float) -> bytes:
    """Render a page to PNG bytes at the given linear zoom factor."""
    import fitz

    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix)
    raw = pixmap.tobytes("png")
    return raw if isinstance(raw, bytes) else bytes(raw)


def _ocr_page_image(png_bytes: bytes, *, languages: str) -> str:
    """OCR a single page image and return the recognised text.

    Raises :class:`PdfOcrUnavailableError` if Tesseract is not installed
    on the host — distinguishes a missing-engine deployment problem from
    a real OCR failure.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise PdfOcrUnavailableError(
            "Tesseract / pytesseract not installed; set enable_ocr=False or install the OCR dependencies.",
        ) from exc

    try:
        image = Image.open(io.BytesIO(png_bytes))
        text = pytesseract.image_to_string(image, lang=languages)
    except pytesseract.TesseractNotFoundError as exc:
        raise PdfOcrUnavailableError(
            "Tesseract binary not on PATH; install it or set enable_ocr=False.",
        ) from exc
    return text if isinstance(text, str) else str(text)


def _iter_pages_for_test(document: Any) -> Iterable[Any]:  # pragma: no cover
    """Iteration helper retained for ad-hoc debug use."""
    for i in range(_document_page_count(document)):
        yield _document_page(document, i)
