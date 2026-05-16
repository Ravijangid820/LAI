"""Tests for :class:`lai.common.llm.config.LlmConfig`.

Default construction, field validation, environment-variable overrides,
and the secret-handling / frozen-immutability contracts.
"""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr, ValidationError

from lai.common.llm.config import LlmConfig

# ─────────────────────────────────────────────────────────────────────────────
# Env-isolation fixture — every test starts from a clean LAI_LLM_* env
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_lai_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``LAI_LLM_*`` env var so tests are reproducible.

    ``monkeypatch`` restores the environment automatically when the test
    finishes, so this fixture only needs the setup half (no ``yield``).
    """
    for key in [k for k in os.environ if k.startswith("LAI_LLM_")]:
        monkeypatch.delenv(key, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_construction_uses_live_system_values() -> None:
    """Defaults point at the live ``lai_analyzer_llm`` container."""
    cfg = LlmConfig()
    assert cfg.base_url == "http://lai_analyzer_llm:8000/v1"
    assert cfg.model == "qwen3.6-27b"
    assert cfg.api_key is None
    assert cfg.timeout_seconds == 300.0
    assert cfg.max_retries == 3
    assert cfg.retry_initial_wait_seconds == 1.0
    assert cfg.retry_max_wait_seconds == 30.0
    assert cfg.default_max_tokens == 2048
    assert cfg.default_temperature == pytest.approx(0.1)
    assert cfg.thinking_mode_enabled is True
    assert cfg.guided_decoding_backend == "xgrammar"


# ─────────────────────────────────────────────────────────────────────────────
# Env-driven overrides
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_LLM_BASE_URL", "http://other:9000/v1/")
    monkeypatch.setenv("LAI_LLM_MODEL", "qwen2.5-7b")
    monkeypatch.setenv("LAI_LLM_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("LAI_LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("LAI_LLM_THINKING_MODE_ENABLED", "false")
    monkeypatch.setenv("LAI_LLM_GUIDED_DECODING_BACKEND", "outlines")

    cfg = LlmConfig()

    # Trailing slash on base_url is normalised away by the validator.
    assert cfg.base_url == "http://other:9000/v1"
    assert cfg.model == "qwen2.5-7b"
    assert cfg.timeout_seconds == 45.0
    assert cfg.max_retries == 5
    assert cfg.thinking_mode_enabled is False
    assert cfg.guided_decoding_backend == "outlines"


@pytest.mark.unit
def test_env_var_names_are_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``case_sensitive=False`` — lowercase variant works too."""
    monkeypatch.setenv("lai_llm_model", "qwen2.5-7b")
    assert LlmConfig().model == "qwen2.5-7b"


@pytest.mark.unit
def test_keyword_overrides_take_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAI_LLM_MODEL", "from-env")
    cfg = LlmConfig(model="from-kwarg")
    assert cfg.model == "from-kwarg"


# ─────────────────────────────────────────────────────────────────────────────
# Validators — base_url
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://x:8000/v1",
        "https://x:8000/v1",
        "http://lai_analyzer_llm:8000/v1",  # Docker hostname with underscore
        "http://10.0.0.5:8000",  # IP address
    ],
)
def test_base_url_accepts_valid_schemes(url: str) -> None:
    cfg = LlmConfig(base_url=url)
    assert cfg.base_url == url.rstrip("/")


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "ftp://host/path",
        "ws://host:8000",
        "lai_analyzer_llm:8000",  # missing scheme entirely
        "",
    ],
)
def test_base_url_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(ValidationError, match="base_url must start with http"):
        LlmConfig(base_url=url)


@pytest.mark.unit
def test_base_url_trailing_slash_is_normalised() -> None:
    cfg = LlmConfig(base_url="http://x:8000/v1//")
    assert cfg.base_url == "http://x:8000/v1"


# ─────────────────────────────────────────────────────────────────────────────
# Validators — bounded numerics
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", -1.0),
        ("retry_initial_wait_seconds", 0),
        ("retry_max_wait_seconds", 0),
        ("default_max_tokens", 0),
        ("default_max_tokens", -1),
        ("default_temperature", -0.1),
        ("default_temperature", 2.1),
        ("max_retries", -1),
        ("max_retries", 11),
        ("model", ""),  # min_length=1
    ],
)
def test_invalid_values_raise(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        LlmConfig(**{field: value})


@pytest.mark.unit
def test_retry_max_must_be_at_least_initial() -> None:
    with pytest.raises(ValidationError, match="retry_max_wait_seconds must be >="):
        LlmConfig(retry_initial_wait_seconds=5.0, retry_max_wait_seconds=2.0)


@pytest.mark.unit
def test_retry_equal_initial_and_max_is_allowed() -> None:
    """``initial == max`` is a no-backoff configuration — valid."""
    cfg = LlmConfig(retry_initial_wait_seconds=1.0, retry_max_wait_seconds=1.0)
    assert cfg.retry_max_wait_seconds == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Validators — enums
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_guided_decoding_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        LlmConfig(guided_decoding_backend="lark")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# Secret handling
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_api_key_wraps_to_secret_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_LLM_API_KEY", "sk-supersecret-12345")
    cfg = LlmConfig()
    assert isinstance(cfg.api_key, SecretStr)
    assert cfg.api_key.get_secret_value() == "sk-supersecret-12345"


@pytest.mark.unit
def test_api_key_does_not_leak_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_LLM_API_KEY", "sk-supersecret-12345")
    cfg = LlmConfig()
    assert "supersecret" not in repr(cfg)
    assert "supersecret" not in str(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Immutability & extra-fields
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_is_frozen() -> None:
    cfg = LlmConfig()
    with pytest.raises(ValidationError, match=r"[Ff]rozen"):
        cfg.model = "different"  # type: ignore[misc]


@pytest.mark.unit
def test_extra_fields_are_rejected() -> None:
    """Typos in field names fail loudly."""
    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        LlmConfig(modle="qwen2.5-7b")  # type: ignore[call-arg]
