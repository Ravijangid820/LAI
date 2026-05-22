"""Unit tests for serve_rag's parallel VLM OCR (``_vlm_ocr_pdf``).

No real LLM / GPU: the per-page transcriber and the PDF renderer are
monkeypatched. We assert the invariants the parallelization must keep:
page order in the assembled markdown, one progress tick per completed
page, and exception propagation (so ``convert_document`` can fall back to
docling for the whole document).
"""

from __future__ import annotations

import os

# serve_rag builds an AuthConfig at import; give it a dummy secret.
os.environ.setdefault(
    "LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-vlm-ocr-unit-0123456789abcdef"
)

import time  # noqa: E402

import pytest  # noqa: E402

from lai.api import serve_rag as sr  # noqa: E402


def test_page_order_preserved_when_completion_is_out_of_order(monkeypatch):
    monkeypatch.setattr(
        sr, "_render_pdf_to_images", lambda _b: [f"PNG{i}".encode() for i in range(5)]
    )

    def fake_ocr(png: bytes) -> str:
        idx = int(png.decode().removeprefix("PNG"))
        # Later pages finish first — forces out-of-order completion.
        time.sleep(0.02 * (5 - idx))
        return f"text-of-page-{idx}"

    monkeypatch.setattr(sr, "_vlm_ocr_image", fake_ocr)
    monkeypatch.setattr(sr, "_VLM_OCR_WORKERS", 5)

    seen: list[tuple[int, int]] = []
    md, n, tables = sr._vlm_ocr_pdf(b"x", on_progress=lambda d, t: seen.append((d, t)))

    assert n == 5
    assert tables == []
    # markdown is assembled in page order despite out-of-order completion
    positions = [md.index(f"<!-- Seite {i} -->") for i in range(1, 6)]
    assert positions == sorted(positions)
    assert "text-of-page-0" in md and "text-of-page-4" in md
    # progress: initial (0, total) then one tick per completed page
    assert seen[0] == (0, 5)
    assert [d for d, _ in seen] == [0, 1, 2, 3, 4, 5]
    assert all(t == 5 for _, t in seen)


def test_page_failure_propagates_for_docling_fallback(monkeypatch):
    monkeypatch.setattr(sr, "_render_pdf_to_images", lambda _b: [b"a", b"b"])

    def boom(_png: bytes) -> str:
        raise RuntimeError("vlm down")

    monkeypatch.setattr(sr, "_vlm_ocr_image", boom)
    monkeypatch.setattr(sr, "_VLM_OCR_WORKERS", 2)

    with pytest.raises(RuntimeError, match="vlm down"):
        sr._vlm_ocr_pdf(b"x")
