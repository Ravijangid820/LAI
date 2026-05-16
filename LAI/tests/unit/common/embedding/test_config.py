"""Tests for :class:`lai.common.embedding.config.EmbeddingConfig`."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from lai.common.embedding.config import EmbeddingConfig


class TestDefaults:
    @pytest.mark.unit
    def test_defaults_match_live_container(self) -> None:
        cfg = EmbeddingConfig()
        assert cfg.base_url == "http://lai_embedding:8000/v1"
        assert cfg.model == "Qwen/Qwen3-Embedding-8B"
        assert cfg.dimension == 4096
        assert cfg.max_batch_size == 32

    @pytest.mark.unit
    def test_frozen(self) -> None:
        cfg = EmbeddingConfig()
        with pytest.raises(ValidationError):
            cfg.model = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            EmbeddingConfig(undeclared=True)  # type: ignore[call-arg]


class TestBaseUrl:
    @pytest.mark.unit
    def test_http_scheme_accepted(self) -> None:
        cfg = EmbeddingConfig(base_url="http://embed:8000/v1")
        assert cfg.base_url == "http://embed:8000/v1"

    @pytest.mark.unit
    def test_https_scheme_accepted(self) -> None:
        cfg = EmbeddingConfig(base_url="https://embed.example/v1")
        assert cfg.base_url == "https://embed.example/v1"

    @pytest.mark.unit
    def test_trailing_slash_normalised(self) -> None:
        cfg = EmbeddingConfig(base_url="http://embed:8000/v1/")
        assert cfg.base_url == "http://embed:8000/v1"

    @pytest.mark.unit
    def test_non_http_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must start with http"):
            EmbeddingConfig(base_url="ftp://embed:8000/v1")


class TestNumericFields:
    @pytest.mark.unit
    def test_dimension_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingConfig(dimension=0)

    @pytest.mark.unit
    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingConfig(timeout_seconds=0.0)

    @pytest.mark.unit
    def test_max_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingConfig(max_batch_size=0)

    @pytest.mark.unit
    def test_max_input_chars_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingConfig(max_input_chars=0)

    @pytest.mark.unit
    def test_max_retries_caps_at_ten(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingConfig(max_retries=11)

    @pytest.mark.unit
    def test_max_retries_zero_allowed(self) -> None:
        cfg = EmbeddingConfig(max_retries=0)
        assert cfg.max_retries == 0


class TestRetryBounds:
    @pytest.mark.unit
    def test_retry_max_less_than_initial_rejected(self) -> None:
        with pytest.raises(ValidationError, match="retry_max_wait_seconds must be >="):
            EmbeddingConfig(retry_initial_wait_seconds=2.0, retry_max_wait_seconds=1.0)

    @pytest.mark.unit
    def test_retry_max_equal_to_initial_accepted(self) -> None:
        cfg = EmbeddingConfig(retry_initial_wait_seconds=1.0, retry_max_wait_seconds=1.0)
        assert cfg.retry_max_wait_seconds == 1.0


class TestApiKey:
    @pytest.mark.unit
    def test_api_key_default_none(self) -> None:
        cfg = EmbeddingConfig()
        assert cfg.api_key is None

    @pytest.mark.unit
    def test_api_key_wrapped_as_secret(self) -> None:
        cfg = EmbeddingConfig(api_key="super-secret")  # type: ignore[arg-type]
        assert isinstance(cfg.api_key, SecretStr)
        assert cfg.api_key.get_secret_value() == "super-secret"


class TestEnvLoading:
    @pytest.mark.unit
    def test_env_prefix_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LAI_EMBEDDING_MODEL", "qwen2-test")
        monkeypatch.setenv("LAI_EMBEDDING_DIMENSION", "1024")
        cfg = EmbeddingConfig()
        assert cfg.model == "qwen2-test"
        assert cfg.dimension == 1024
