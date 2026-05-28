"""DDiQ scanned-doc OCR must never persist a TRUNCATED document.

The live bug: under analyzer-LLM contention, per-page OCR requests timed out
and the parallel loop silently SKIPPED them — the same 19-page contract came
out 24K-33K chars across uploads (≈5 pages dropped). The fix retries a failed
page and, if it still can't be transcribed, raises so the caller falls back to
Tesseract (a COMPLETE, lower-quality transcription) instead of a partial one.
"""

from __future__ import annotations

import pytest
from ddiq import vlm_ocr as v


def test_unrecovered_page_raises_not_truncates(monkeypatch):
    monkeypatch.setattr(v, "_render_pages", lambda _b: [b"a", b"b", b"c"])
    monkeypatch.setattr(v, "_ocr_image", lambda _p: (_ for _ in ()).throw(RuntimeError("Read timed out")))
    monkeypatch.setattr(v, "_OCR_WORKERS", 3)
    monkeypatch.setattr(v, "_OCR_PAGE_ATTEMPTS", 2)
    monkeypatch.setattr(v.time, "sleep", lambda _s: None)

    # Must RAISE (→ Tesseract fallback), never return a partial transcription.
    with pytest.raises(RuntimeError, match=r"failed after 2 attempts"):
        v.vlm_ocr_pdf(b"x")


def test_transient_failure_retried_and_recovered(monkeypatch):
    monkeypatch.setattr(v, "_render_pages", lambda _b: [f"PG{i}".encode() for i in range(4)])
    monkeypatch.setattr(v, "_OCR_WORKERS", 3)
    monkeypatch.setattr(v, "_OCR_PAGE_ATTEMPTS", 3)
    monkeypatch.setattr(v.time, "sleep", lambda _s: None)
    calls: dict[int, int] = {}

    def flaky(png: bytes) -> str:
        idx = int(png.decode().removeprefix("PG"))
        calls[idx] = calls.get(idx, 0) + 1
        if idx == 2 and calls[idx] == 1:  # page 3 times out once
            raise RuntimeError("Read timed out")
        return f"page-{idx}-text"

    monkeypatch.setattr(v, "_ocr_image", flaky)
    text, n = v.vlm_ocr_pdf(b"x")
    assert n == 4
    for i in range(4):  # all pages present, none dropped
        assert f"page-{i}-text" in text
    assert calls[2] == 2  # the flaky page was retried
