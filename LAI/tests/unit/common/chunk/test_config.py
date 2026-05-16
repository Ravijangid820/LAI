"""Tests for :class:`lai.common.chunk.config.ChunkerConfig`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lai.common.chunk.config import ChunkerConfig


class TestDefaults:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        cfg = ChunkerConfig()
        assert cfg.target_chars == 1200
        assert cfg.max_chars == 2000
        assert cfg.min_chars == 200
        assert cfg.overlap_chars == 150

    @pytest.mark.unit
    def test_frozen(self) -> None:
        cfg = ChunkerConfig()
        with pytest.raises(ValidationError):
            cfg.target_chars = 800  # type: ignore[misc]

    @pytest.mark.unit
    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ChunkerConfig(weird=True)  # type: ignore[call-arg]


class TestBounds:
    @pytest.mark.unit
    def test_target_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ChunkerConfig(target_chars=0)

    @pytest.mark.unit
    def test_max_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ChunkerConfig(max_chars=0)

    @pytest.mark.unit
    def test_min_zero_allowed(self) -> None:
        cfg = ChunkerConfig(min_chars=0)
        assert cfg.min_chars == 0

    @pytest.mark.unit
    def test_min_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChunkerConfig(min_chars=-1)


class TestRelationalValidators:
    @pytest.mark.unit
    def test_max_less_than_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_chars must be >= target_chars"):
            ChunkerConfig(target_chars=1200, max_chars=1000)

    @pytest.mark.unit
    def test_max_equal_to_target_accepted(self) -> None:
        cfg = ChunkerConfig(target_chars=1200, max_chars=1200)
        assert cfg.max_chars == cfg.target_chars

    @pytest.mark.unit
    def test_min_greater_than_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_chars must be <= target_chars"):
            ChunkerConfig(target_chars=500, min_chars=600)

    @pytest.mark.unit
    def test_min_equal_to_target_accepted(self) -> None:
        cfg = ChunkerConfig(target_chars=500, max_chars=600, min_chars=500)
        assert cfg.min_chars == cfg.target_chars

    @pytest.mark.unit
    def test_overlap_equal_to_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="overlap_chars must be < target_chars"):
            ChunkerConfig(target_chars=500, overlap_chars=500)

    @pytest.mark.unit
    def test_overlap_zero_allowed(self) -> None:
        cfg = ChunkerConfig(overlap_chars=0)
        assert cfg.overlap_chars == 0


class TestEnvLoading:
    @pytest.mark.unit
    def test_env_prefix_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LAI_CHUNK_TARGET_CHARS", "800")
        monkeypatch.setenv("LAI_CHUNK_MAX_CHARS", "1500")
        monkeypatch.setenv("LAI_CHUNK_OVERLAP_CHARS", "100")
        cfg = ChunkerConfig()
        assert cfg.target_chars == 800
        assert cfg.max_chars == 1500
        assert cfg.overlap_chars == 100
