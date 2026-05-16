"""Shared PDF text extractor.

Consolidates the PyMuPDF + Tesseract OCR-fallback logic that is
currently hand-rolled inside ``micro-services/ddiq_report.py``
(:func:`extract_pdf_text`). The new client is consumed by:

- ``serve_rag``'s upload path (the chat-document drop zone).
- The DDiQ microservice (which still has its own copy until it migrates).
- Any future pipeline stage that needs single-document extraction at
  runtime (e.g. the v1.1 "render-from-conversation" flow).

Submodules:

- :mod:`~lai.common.pdf.config` — :class:`PdfExtractorConfig` (settings).
- :mod:`~lai.common.pdf.extractor` — :class:`PdfExtractor`,
  :class:`PdfExtractResult`, :class:`PdfPageResult`.
"""

from __future__ import annotations

from lai.common.pdf.config import PdfExtractorConfig
from lai.common.pdf.extractor import (
    PdfExtractor,
    PdfExtractResult,
    PdfPageResult,
    PdfPageSource,
)

__all__ = [
    "PdfExtractResult",
    "PdfExtractor",
    "PdfExtractorConfig",
    "PdfPageResult",
    "PdfPageSource",
]
