"""Vision-LLM OCR for scanned PDFs on the DDiQ ingestion path.

Tesseract (the default OCR in :mod:`lai.common.pdf`) misreads turbine type
designations on noisy scans — it read an Enercon **E-70** as **E-79** on the
Lamstedt Änderungsgenehmigung, which then propagated into the report. The
vision-capable analyzer LLM (the same ``qwen3.6-27b`` the worker already calls
for extraction) reads the glyph correctly.

This module renders each page to a PNG (poppler ``pdftoppm``) and asks the
analyzer LLM to transcribe it. It is the same approach already proven in
``serve_rag`` for the chat-side document upload; kept self-contained here so
the DDiQ worker has no dependency on the API process.

Routing (see :func:`ddiq_report.extract_pdf_text`): a PDF that carries a real
text layer skips OCR entirely (fast, lossless); only a *scan* — near-empty
``pdftotext`` output — is sent here. Any failure falls back to the Tesseract
path so ingestion never crashes on a single bad page.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import requests

from ddiq.llm import LLM_MODEL, LLM_URL

__all__ = ["vlm_ocr_enabled", "pdf_has_text_layer", "vlm_ocr_pdf"]

_log = logging.getLogger("ddiq")

# On by default; set LAI_VLM_OCR=0 to fall back to Tesseract-only ingestion.
_ENABLED = os.environ.get("LAI_VLM_OCR", "1") not in ("0", "false", "False")
# 200 dpi is plenty for the model and keeps the image token count modest.
_DPI = int(os.environ.get("LAI_VLM_OCR_DPI", "200"))
# Per-page LLM timeout (seconds). A dense scanned page can take a while.
_PAGE_TIMEOUT = float(os.environ.get("LAI_VLM_OCR_PAGE_TIMEOUT", "300"))

_PROMPT = (
    "Du bist ein präzises OCR-System für gescannte deutsche Rechts- und "
    "Behördendokumente. Transkribiere den GESAMTEN sichtbaren Text dieses "
    "Seitenbildes exakt und vollständig.\n"
    "- Gib die Struktur als Markdown wieder (Überschriften, Absätze, Listen; "
    "Tabellen als Markdown-Tabellen).\n"
    "- Wenn ein Zeichen durch die Scan-Qualität mehrdeutig ist, wähle die "
    "anhand des Kontexts plausibelste Lesart (z.B. Typenbezeichnungen, "
    "Eigennamen, Gesetzeszitate, Zahlen). Erfinde aber KEINEN Inhalt.\n"
    "- Übersetze nicht, fasse nicht zusammen, kommentiere nicht.\n"
    "Gib ausschließlich die reine Transkription aus."
)


def vlm_ocr_enabled() -> bool:
    """True when VLM OCR is enabled (env ``LAI_VLM_OCR`` not set to 0)."""
    return _ENABLED


def pdf_has_text_layer(file_bytes: bytes) -> bool:
    """True if the PDF carries an extractable text layer (not a pure scan).

    Such PDFs are read losslessly by the normal extractor — no OCR needed.
    A scan yields a handful of stray glyphs at most and is routed to the VLM.
    Missing/erroring ``pdftotext`` → assume scan and let the VLM handle it.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            out = subprocess.run(
                ["pdftotext", "-q", tmp_path, "-"],
                capture_output=True, timeout=60,
            )
            text = out.stdout.decode("utf-8", errors="replace")
            return len(text.strip()) >= 200
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except Exception:
        return False


def _render_pages(file_bytes: bytes, dpi: int = _DPI) -> list[bytes]:
    """Render every PDF page to a PNG via poppler ``pdftoppm``, in order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "in.pdf"
        pdf_path.write_bytes(file_bytes)
        prefix = Path(tmpdir) / "pg"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
            capture_output=True, timeout=600, check=True,
        )
        return [p.read_bytes() for p in sorted(Path(tmpdir).glob("pg*.png"))]


def _ocr_image(png_bytes: bytes) -> str:
    """Transcribe one page image with the analyzer vision LLM. Raises on
    transport/HTTP error so the caller can fall back to Tesseract."""
    url = LLM_URL.rstrip("/") + "/chat/completions"
    b64 = base64.b64encode(png_bytes).decode()
    body = {
        "model": LLM_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "max_tokens": 4096,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = requests.post(url, json=body, timeout=_PAGE_TIMEOUT)
    r.raise_for_status()
    obj = r.json()
    return (obj["choices"][0]["message"]["content"] or "").strip()


def vlm_ocr_pdf(file_bytes: bytes) -> tuple[str, int]:
    """OCR a scanned PDF page-by-page with the vision LLM.

    Returns ``(text, num_pages)`` — pages joined with blank lines. Raises if
    rendering fails or no page transcribes (so the caller falls back to the
    Tesseract path); a single failed page is skipped, not fatal.
    """
    images = _render_pages(file_bytes)
    if not images:
        raise RuntimeError("pdftoppm produced no page images")
    parts: list[str] = []
    for i, png in enumerate(images, start=1):
        try:
            parts.append(_ocr_image(png))
        except Exception as e:  # noqa: BLE001 — one bad page must not abort
            _log.warning("VLM OCR: page %d/%d failed: %s", i, len(images), e)
    if not any(p.strip() for p in parts):
        raise RuntimeError("VLM OCR produced no text on any page")
    return "\n\n".join(parts), len(images)
