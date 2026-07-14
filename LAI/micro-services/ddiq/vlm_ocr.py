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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Pages transcribe concurrently — each is one independent vision-LLM call and
# the remote vLLM batches them, so a multi-page scan ingests faster than one
# page at a time. Kept LOW (3): the analyzer LLM is shared with the corpus
# migration / embedding / chat, and firing too many concurrent OCR requests
# made individual pages time out and get silently dropped (truncated docs).
_OCR_WORKERS = max(1, int(os.environ.get("LAI_VLM_OCR_WORKERS", "3")))
# Per-page retry budget. A page that times out under transient contention is
# retried (sequentially, to let the LLM drain) before the whole OCR gives up.
_OCR_PAGE_ATTEMPTS = max(1, int(os.environ.get("LAI_VLM_OCR_PAGE_ATTEMPTS", "3")))

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
    headers = {}
    if os.getenv("GROQ_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('GROQ_API_KEY')}"
    r = requests.post(url, json=body, headers=headers, timeout=_PAGE_TIMEOUT)
    r.raise_for_status()
    obj = r.json()
    return (obj["choices"][0]["message"]["content"] or "").strip()


def vlm_ocr_pdf(file_bytes: bytes) -> tuple[str, int]:
    """OCR a scanned PDF with the vision LLM, pages transcribed concurrently.

    Returns ``(text, num_pages)`` — pages joined with blank lines, in page
    order. RAISES (so the caller falls back to Tesseract for a COMPLETE
    document) if rendering fails, if no page transcribes, or if any page still
    fails after retries — we must never persist a silently TRUNCATED document.

    A page that times out under transient LLM contention is retried up to
    ``_OCR_PAGE_ATTEMPTS`` times; retry passes run sequentially so they don't
    re-induce the contention that caused the timeout. A page that returns empty
    CONTENT (no error — a genuinely blank page) is accepted, not retried.
    """
    images = _render_pages(file_bytes)
    if not images:
        raise RuntimeError("pdftoppm produced no page images")
    total = len(images)
    parts: list[str] = [""] * total
    pending = list(range(total))  # page indices still needing a result
    for attempt in range(1, _OCR_PAGE_ATTEMPTS + 1):
        if not pending:
            break
        # Full concurrency on the first pass; serialize retries to let the
        # shared analyzer LLM drain instead of hammering it again.
        workers = min(_OCR_WORKERS, len(pending)) if attempt == 1 else 1
        failed: list[int] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_to_idx = {ex.submit(_ocr_image, images[i]): i for i in pending}
            for fut in as_completed(fut_to_idx):
                idx = fut_to_idx[fut]
                try:
                    parts[idx] = fut.result()
                except Exception as e:  # noqa: BLE001 — transient; retried below
                    failed.append(idx)
                    _log.warning("VLM OCR: page %d/%d attempt %d failed: %s",
                                 idx + 1, total, attempt, e)
        pending = failed
        if pending and attempt < _OCR_PAGE_ATTEMPTS:
            time.sleep(2.0 * attempt)  # back off before retrying

    if pending:
        # Pages still unrecovered → returning now would TRUNCATE the document.
        # Raise so convert_document degrades to Tesseract, which transcribes
        # every page (lower quality, but COMPLETE — not silently missing pages).
        raise RuntimeError(
            f"VLM OCR: {len(pending)} of {total} page(s) failed after "
            f"{_OCR_PAGE_ATTEMPTS} attempts ({sorted(p + 1 for p in pending)}) "
            "— refusing to return a truncated transcription"
        )
    if not any(p.strip() for p in parts):
        raise RuntimeError("VLM OCR produced no text on any page")
    return "\n\n".join(parts), total
