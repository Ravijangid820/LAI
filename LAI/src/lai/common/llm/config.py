"""Configuration for :class:`lai.common.llm.client.LlmClient`.

A single :class:`LlmConfig` :class:`~pydantic_settings.BaseSettings`
subclass owns every knob the client exposes. Defaults are pinned to the
live system's actual values so a default-constructed client works
out-of-the-box against the deployed ``lai_analyzer_llm`` container.

Configuration sources, in precedence order (highest first):

1. Keyword arguments passed to ``LlmConfig(...)`` explicitly.
2. Environment variables prefixed ``LAI_LLM_`` (e.g.
   ``LAI_LLM_BASE_URL``, ``LAI_LLM_MODEL``). Case-insensitive.
3. The defaults declared here.

The settings object is **frozen** — construct once at process start,
share read-only thereafter. Mutations raise
:class:`pydantic.ValidationError`. This is intentional: configuration
that mutates at runtime is a debugging hazard.

Example
-------

::

    config = LlmConfig()  # picks up env
    config = LlmConfig(model="qwen2.5-7b")  # override per-instance
    client = LlmClient(config=config)
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LlmConfig"]


class LlmConfig(BaseSettings):
    """Settings for the LLM client.

    Every field has a production-quality default. Override via
    keyword argument or environment variable.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_LLM_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── Endpoint ────────────────────────────────────────────────────────
    base_url: str = Field(
        default="http://lai_analyzer_llm:8000/v1",
        description=(
            "OpenAI-compatible base URL for the vLLM endpoint. Defaults to "
            "the live ``lai_analyzer_llm`` container's address on the "
            "``lai_network`` Docker network."
        ),
    )
    model: str = Field(
        default="qwen3.6-27b",
        min_length=1,
        description="Served model name as registered with vLLM.",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Optional bearer token for OpenAI-compatible Authorization. "
            "vLLM does not require this by default but the field exists "
            "for deployments that put an auth-aware proxy in front."
        ),
    )

    # ── Transport ───────────────────────────────────────────────────────
    timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description=(
            "Per-request timeout in seconds. Default matches the live "
            "DDiQ pipeline's setting; LLM calls in thinking mode regularly "
            "run 30-60s and occasionally up to 5 minutes for long contexts."
        ),
    )

    # ── Retry policy (tenacity-friendly) ────────────────────────────────
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "Total retry attempts on transport / transient failure. "
            "``0`` disables retry. Capped at 10 to bound worst-case "
            "wall-clock under sustained outage."
        ),
    )
    retry_initial_wait_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial backoff before the first retry.",
    )
    retry_max_wait_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Cap on exponential backoff between retries.",
    )

    # ── Generation defaults ─────────────────────────────────────────────
    default_max_tokens: int = Field(
        default=2048,
        gt=0,
        description=(
            "Default ``max_tokens`` for completions when the caller does "
            "not specify. Matches the historical DDiQ ``llm_call`` default."
        ),
    )
    default_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description=(
            "Default sampling temperature. The DDiQ structured-extraction "
            "passes use 0.0; conversational chat uses 0.1-0.3. The default "
            "favours determinism for the more numerous extraction calls."
        ),
    )
    thinking_mode_enabled: bool = Field(
        default=True,
        description=(
            "Whether Qwen3-style ``<think>`` reasoning is permitted by "
            "default. The contract analyzer relies on this; the structured-"
            "extraction passes may flip it off per-call once a future ADR "
            "supersedes 0003."
        ),
    )

    # ── Guided decoding (ADR 0002) ──────────────────────────────────────
    guided_decoding_backend: Literal["xgrammar", "outlines"] = Field(
        default="xgrammar",
        description=(
            "vLLM guided-decoding backend. ``xgrammar`` is the current "
            "vLLM default and the recommended choice; ``outlines`` is "
            "the historical alternative kept available for fallback."
        ),
    )

    # ── Validators ──────────────────────────────────────────────────────
    @field_validator("base_url")
    @classmethod
    def _check_base_url_scheme(cls, value: str) -> str:
        """Reject non-HTTP URLs and normalise the trailing slash.

        Docker-internal hostnames contain underscores (``lai_analyzer_llm``)
        which fail strict :class:`~pydantic.HttpUrl` parsing, so we accept
        any ``http://`` / ``https://`` scheme without further validation.
        """
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                "base_url must start with http:// or https://",
            )
        return value.rstrip("/")

    @field_validator("retry_max_wait_seconds")
    @classmethod
    def _check_retry_bounds(cls, value: float, info: object) -> float:
        """Ensure ``retry_max_wait_seconds >= retry_initial_wait_seconds``.

        Pydantic invokes field validators in declaration order; by the time
        we validate ``retry_max_wait_seconds`` the ``data`` dict in
        ``info`` already contains the validated initial value.
        """
        # ``info`` is ``pydantic.ValidationInfo``; we access ``.data``
        # without depending on the import to keep this module's import set
        # minimal — Pydantic ships only one ValidationInfo type.
        initial = getattr(info, "data", {}).get("retry_initial_wait_seconds")
        if initial is not None and value < initial:
            raise ValueError(
                "retry_max_wait_seconds must be >= retry_initial_wait_seconds; " f"got max={value} < initial={initial}",
            )
        return value
