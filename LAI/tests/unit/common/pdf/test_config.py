"""Tests for :class:`lai.common.pdf.config.PdfExtractorConfig`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lai.common.pdf.config import PdfExtractorConfig


class TestDefaults:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        cfg = PdfExtractorConfig()
        assert cfg.enable_ocr is True
        assert cfg.min_chars_per_page == 50
        assert cfg.ocr_languages == "deu+eng"
        assert cfg.ocr_zoom == 2.0
        assert cfg.max_pages == 2000
        assert cfg.page_separator == "\n\n"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        cfg = PdfExtractorConfig()
        with pytest.raises(ValidationError):
            cfg.enable_ocr = False  # type: ignore[misc]

    @pytest.mark.unit
    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            PdfExtractorConfig(weird=True)  # type: ignore[call-arg]


class TestBounds:
    @pytest.mark.unit
    def test_zoom_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PdfExtractorConfig(ocr_zoom=0.0)

    @pytest.mark.unit
    def test_zoom_capped(self) -> None:
        with pytest.raises(ValidationError):
            PdfExtractorConfig(ocr_zoom=9.0)

    @pytest.mark.unit
    def test_max_pages_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PdfExtractorConfig(max_pages=0)

    @pytest.mark.unit
    def test_min_chars_zero_allowed(self) -> None:
        cfg = PdfExtractorConfig(min_chars_per_page=0)
        assert cfg.min_chars_per_page == 0

    @pytest.mark.unit
    def test_min_chars_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PdfExtractorConfig(min_chars_per_page=-1)


class TestEnvLoading:
    @pytest.mark.unit
    def test_env_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LAI_PDF_ENABLE_OCR", "false")
        monkeypatch.setenv("LAI_PDF_OCR_LANGUAGES", "fra+ita")
        cfg = PdfExtractorConfig()
        assert cfg.enable_ocr is False
        assert cfg.ocr_languages == "fra+ita"
