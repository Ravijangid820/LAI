"""Tests for :class:`lai.common.reranker.config.RerankerConfig`."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from lai.common.reranker.config import RerankerConfig


@pytest.fixture(autouse=True)
def _clear_lai_reranker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any LAI_RERANKER_* env vars so tests are reproducible."""
    for key in [k for k in os.environ if k.startswith("LAI_RERANKER_")]:
        monkeypatch.delenv(key, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_construction_uses_live_system_values() -> None:
    cfg = RerankerConfig()
    assert cfg.base_url == "http://lai-test-reranker:80"
    assert cfg.timeout_seconds == 30.0
    assert cfg.max_retries == 3
    assert cfg.retry_initial_wait_seconds == 0.5
    assert cfg.retry_max_wait_seconds == 10.0
    assert cfg.max_batch_size == 32


# ─────────────────────────────────────────────────────────────────────────────
# Env-driven overrides
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_RERANKER_BASE_URL", "http://other:9000/")
    monkeypatch.setenv("LAI_RERANKER_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("LAI_RERANKER_MAX_BATCH_SIZE", "16")

    cfg = RerankerConfig()

    # Trailing slash normalised away by the validator.
    assert cfg.base_url == "http://other:9000"
    assert cfg.timeout_seconds == 5.0
    assert cfg.max_batch_size == 16


@pytest.mark.unit
def test_env_var_names_are_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("lai_reranker_max_retries", "0")
    assert RerankerConfig().max_retries == 0


@pytest.mark.unit
def test_keyword_overrides_take_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAI_RERANKER_BASE_URL", "http://from-env")
    cfg = RerankerConfig(base_url="http://from-kwarg")
    assert cfg.base_url == "http://from-kwarg"


# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://x:8000",
        "https://x:8000",
        "http://lai-test-reranker:80",
        "http://10.0.0.5:8004",
    ],
)
def test_base_url_accepts_valid_schemes(url: str) -> None:
    cfg = RerankerConfig(base_url=url)
    assert cfg.base_url == url.rstrip("/")


@pytest.mark.unit
@pytest.mark.parametrize("url", ["ftp://host/path", "ws://host:8000", "", "host:8000"])
def test_base_url_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(ValidationError, match="base_url must start with http"):
        RerankerConfig(base_url=url)


@pytest.mark.unit
def test_base_url_trailing_slash_is_normalised() -> None:
    cfg = RerankerConfig(base_url="http://x:8000//")
    assert cfg.base_url == "http://x:8000"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", -1.0),
        ("retry_initial_wait_seconds", 0),
        ("retry_max_wait_seconds", 0),
        ("max_retries", -1),
        ("max_retries", 11),
        ("max_batch_size", 0),
        ("max_batch_size", -1),
    ],
)
def test_invalid_values_raise(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RerankerConfig(**{field: value})


@pytest.mark.unit
def test_retry_max_must_be_at_least_initial() -> None:
    with pytest.raises(ValidationError, match="retry_max_wait_seconds must be >="):
        RerankerConfig(retry_initial_wait_seconds=5.0, retry_max_wait_seconds=2.0)


@pytest.mark.unit
def test_retry_equal_initial_and_max_is_allowed() -> None:
    cfg = RerankerConfig(retry_initial_wait_seconds=1.0, retry_max_wait_seconds=1.0)
    assert cfg.retry_max_wait_seconds == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Immutability & extra-fields
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_is_frozen() -> None:
    cfg = RerankerConfig()
    with pytest.raises(ValidationError, match=r"[Ff]rozen"):
        cfg.base_url = "http://other"  # type: ignore[misc]


@pytest.mark.unit
def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        RerankerConfig(timeout=10)  # type: ignore[call-arg]
