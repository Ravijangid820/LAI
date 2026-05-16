"""Tests for :class:`lai.common.pdf.extractor.PdfExtractor`.

All PyMuPDF / Tesseract calls are injected via ``monkeypatch.setattr`` on
module-level helpers, so the test suite never touches the real engines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lai.common.exceptions import PdfExtractError, PdfOcrUnavailableError
from lai.common.pdf import extractor as ext
from lai.common.pdf.config import PdfExtractorConfig
from lai.common.pdf.extractor import (
    PdfExtractor,
    PdfPageResult,
    PdfPageSource,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────


class _FakePage:
    """In-memory page that returns a canned text body."""

    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _FakeDocument:
    """In-memory document with controllable page count and per-page text."""

    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self._closed = False

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self._closed = True


def _install_fake_document(monkeypatch: pytest.MonkeyPatch, document: _FakeDocument) -> None:
    """Route ``_open_pdf_document`` to return our fake."""

    def _open(**kwargs: Any) -> _FakeDocument:
        return document

    monkeypatch.setattr(ext, "_open_pdf_document", _open)
    monkeypatch.setattr(ext, "_render_page_png", lambda page, *, zoom: b"PNG")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractBytes:
    @pytest.mark.unit
    def test_empty_bytes_rejected(self) -> None:
        e = PdfExtractor()
        with pytest.raises(PdfExtractError, match="empty"):
            e.extract_bytes(b"")

    @pytest.mark.unit
    def test_open_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(**kwargs: Any) -> _FakeDocument:
            raise RuntimeError("garbage bytes")

        monkeypatch.setattr(ext, "_open_pdf_document", fail)
        e = PdfExtractor()
        with pytest.raises(PdfExtractError, match="failed to open PDF"):
            e.extract_bytes(b"%PDF-corrupt")

    @pytest.mark.unit
    def test_zero_page_document_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(monkeypatch, _FakeDocument(pages=[]))
        e = PdfExtractor()
        with pytest.raises(PdfExtractError, match="zero pages"):
            e.extract_bytes(b"%PDF-")

    @pytest.mark.unit
    def test_too_many_pages_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = PdfExtractorConfig(max_pages=2)
        _install_fake_document(
            monkeypatch,
            _FakeDocument(pages=[_FakePage("a"), _FakePage("b"), _FakePage("c")]),
        )
        e = PdfExtractor(config=cfg)
        with pytest.raises(PdfExtractError, match="max_pages is 2"):
            e.extract_bytes(b"%PDF-")

    @pytest.mark.unit
    def test_embedded_text_above_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = "x" * 200
        _install_fake_document(monkeypatch, _FakeDocument(pages=[_FakePage(body)]))
        e = PdfExtractor(config=PdfExtractorConfig(min_chars_per_page=50))
        result = e.extract_bytes(b"%PDF-")
        assert result.page_count == 1
        assert result.ocr_page_count == 0
        assert result.pages[0].source is PdfPageSource.EMBEDDED
        assert result.pages[0].text == body

    @pytest.mark.unit
    def test_ocr_kicks_in_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(
            monkeypatch,
            _FakeDocument(pages=[_FakePage("only ten")]),  # below 50-char threshold
        )

        def fake_ocr(png: bytes, *, languages: str) -> str:
            assert png == b"PNG"
            assert languages == "deu+eng"
            return "OCR'd text body"

        monkeypatch.setattr(ext, "_ocr_page_image", fake_ocr)
        e = PdfExtractor()
        result = e.extract_bytes(b"%PDF-")
        assert result.pages[0].source is PdfPageSource.OCR
        assert result.pages[0].text == "OCR'd text body"
        assert result.ocr_page_count == 1

    @pytest.mark.unit
    def test_ocr_disabled_low_text_marked_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(monkeypatch, _FakeDocument(pages=[_FakePage("")]))
        cfg = PdfExtractorConfig(enable_ocr=False)
        e = PdfExtractor(config=cfg)
        result = e.extract_bytes(b"%PDF-")
        assert result.pages[0].source is PdfPageSource.EMPTY
        assert result.text == ""

    @pytest.mark.unit
    def test_ocr_disabled_short_embedded_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(monkeypatch, _FakeDocument(pages=[_FakePage("tiny")]))
        cfg = PdfExtractorConfig(enable_ocr=False)
        e = PdfExtractor(config=cfg)
        result = e.extract_bytes(b"%PDF-")
        assert result.pages[0].source is PdfPageSource.EMBEDDED
        assert result.pages[0].text == "tiny"

    @pytest.mark.unit
    def test_ocr_empty_falls_back_to_empty_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(monkeypatch, _FakeDocument(pages=[_FakePage("")]))
        monkeypatch.setattr(ext, "_ocr_page_image", lambda png, *, languages: "")
        e = PdfExtractor()
        result = e.extract_bytes(b"%PDF-")
        assert result.pages[0].source is PdfPageSource.EMPTY
        assert result.text == ""

    @pytest.mark.unit
    def test_ocr_unavailable_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_document(monkeypatch, _FakeDocument(pages=[_FakePage("")]))

        def raise_unavailable(png: bytes, *, languages: str) -> str:
            raise PdfOcrUnavailableError("no tesseract")

        monkeypatch.setattr(ext, "_ocr_page_image", raise_unavailable)
        e = PdfExtractor()
        with pytest.raises(PdfOcrUnavailableError):
            e.extract_bytes(b"%PDF-")

    @pytest.mark.unit
    def test_page_extraction_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _ExplodingPage:
            def get_text(self) -> str:
                raise RuntimeError("page boom")

        doc = _FakeDocument(pages=[_ExplodingPage()])  # type: ignore[list-item]
        _install_fake_document(monkeypatch, doc)
        e = PdfExtractor()
        with pytest.raises(PdfExtractError, match="page 0") as info:
            e.extract_bytes(b"%PDF-")
        assert info.value.page_index == 0

    @pytest.mark.unit
    def test_pages_joined_with_separator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        doc = _FakeDocument(pages=[_FakePage("x" * 60), _FakePage("y" * 60)])
        _install_fake_document(monkeypatch, doc)
        cfg = PdfExtractorConfig(page_separator="\n---\n")
        e = PdfExtractor(config=cfg)
        result = e.extract_bytes(b"%PDF-")
        assert result.text == "x" * 60 + "\n---\n" + "y" * 60

    @pytest.mark.unit
    def test_document_close_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        doc = _FakeDocument(pages=[_FakePage("a" * 100)])
        _install_fake_document(monkeypatch, doc)
        PdfExtractor().extract_bytes(b"%PDF-")
        assert doc._closed is True

    @pytest.mark.unit
    def test_close_swallows_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BadCloseDocument(_FakeDocument):
            def close(self) -> None:
                raise RuntimeError("close fails")

        doc = _BadCloseDocument(pages=[_FakePage("a" * 100)])
        _install_fake_document(monkeypatch, doc)
        # Must not raise.
        PdfExtractor().extract_bytes(b"%PDF-")


class TestExtractPath:
    @pytest.mark.unit
    def test_missing_path_rejected(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.pdf"
        with pytest.raises(PdfExtractError, match="not found") as info:
            PdfExtractor().extract_path(missing)
        assert info.value.path == str(missing)

    @pytest.mark.unit
    def test_non_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PdfExtractError, match="not a regular file"):
            PdfExtractor().extract_path(tmp_path)  # tmp_path is a directory

    @pytest.mark.unit
    def test_valid_path_opened(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-")

        opened_with: dict[str, Any] = {}

        def _open(**kwargs: Any) -> _FakeDocument:
            opened_with.update(kwargs)
            return _FakeDocument(pages=[_FakePage("a" * 100)])

        monkeypatch.setattr(ext, "_open_pdf_document", _open)
        result = PdfExtractor().extract_path(path)
        assert opened_with == {"filename": str(path)}
        assert result.page_count == 1


class TestPageResult:
    @pytest.mark.unit
    def test_page_result_is_frozen(self) -> None:
        r = PdfPageResult(index=0, text="x", source=PdfPageSource.EMBEDDED)
        with pytest.raises((AttributeError, TypeError)):
            r.index = 5  # type: ignore[misc]


class TestPageSource:
    @pytest.mark.unit
    def test_str_enum_values(self) -> None:
        assert PdfPageSource.EMBEDDED.value == "embedded"
        assert PdfPageSource.OCR.value == "ocr"
        assert PdfPageSource.EMPTY.value == "empty"
