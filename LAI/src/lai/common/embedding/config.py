"""Configuration for :class:`lai.common.embedding.client.EmbeddingClient`.

Mirrors the shape of :class:`~lai.common.llm.config.LlmConfig` and
:class:`~lai.common.reranker.config.RerankerConfig`, with its own
``LAI_EMBEDDING_`` env prefix so embedding endpoint tuning is independent
of the analyzer LLM and the reranker. Defaults match the live
``lai_embedding`` container (Qwen3-Embedding-8B, port 8003 from the host,
port 8000 in the Docker network).
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["EmbeddingConfig"]


class EmbeddingConfig(BaseSettings):
    """Settings for the embedding client.

    All knobs frozen after construction; mutations raise
    :class:`pydantic.ValidationError`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_EMBEDDING_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── Endpoint ────────────────────────────────────────────────────────
    base_url: str = Field(
        default="http://lai_embedding:8000/v1",
        description=(
            "OpenAI-compatible base URL for the embedding service. The "
            "live container's Docker-network DNS name is ``lai_embedding``; "
            "from the host it is reachable at ``http://localhost:8003/v1``."
        ),
    )
    model: str = Field(
        default="Qwen/Qwen3-Embedding-8B",
        min_length=1,
        description=(
            "Served model name as registered with vLLM. Verified against "
            "the live ``GET /v1/models`` response which echoes "
            "``Qwen/Qwen3-Embedding-8B``."
        ),
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Optional bearer token for OpenAI-compatible Authorization. "
            "vLLM does not require this by default but the field exists "
            "for deployments that put an auth-aware proxy in front."
        ),
    )

    # ── Embedding shape ─────────────────────────────────────────────────
    dimension: int = Field(
        default=4096,
        gt=0,
        description=(
            "Expected vector dimension. Qwen3-Embedding-8B emits 4096-dim "
            "vectors natively. The client validates every returned vector "
            "against this value and raises "
            ":class:`EmbeddingDimensionMismatchError` on mismatch — a "
            "configuration-drift signal worth alerting on rather than "
            "silently corrupting downstream pgvector indices."
        ),
    )

    # ── Transport ───────────────────────────────────────────────────────
    timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Per-request timeout. A single-query embedding finishes in "
            "10-100ms; a 32-input batch in 100-500ms. 60s is generous "
            "headroom for cold-cache and contention."
        ),
    )

    # ── Retry policy ────────────────────────────────────────────────────
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Total retry attempts on transport / transient failure.",
    )
    retry_initial_wait_seconds: float = Field(
        default=0.5,
        gt=0.0,
        description="Initial backoff before the first retry.",
    )
    retry_max_wait_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Cap on exponential backoff between retries.",
    )

    # ── Request-shape limits ────────────────────────────────────────────
    max_batch_size: int = Field(
        default=32,
        gt=0,
        description=(
            "Maximum ``input`` array length per request. vLLM accepts "
            "larger batches but 32 matches the working batch size used by "
            "the live ``resume_step6.sh`` pipeline and keeps each request "
            "within the embedding container's per-request memory budget. "
            "Longer inputs are split into multiple requests by the client "
            "and merged on return."
        ),
    )
    max_input_chars: int = Field(
        default=24_000,
        gt=0,
        description=(
            "Per-input character cap before the client refuses to send. "
            "Qwen3-Embedding-8B's ``max_model_len`` is 32768 tokens; "
            "24000 chars is a conservative ceiling (~6000-8000 tokens for "
            "German/English mixed) that leaves headroom for the model's "
            "own prefix tokens. Inputs exceeding this raise "
            ":class:`ValueError` rather than silently truncating, so "
            "callers can decide whether to chunk or to summarise."
        ),
    )

    # ── Validators ──────────────────────────────────────────────────────
    @field_validator("base_url")
    @classmethod
    def _check_base_url_scheme(cls, value: str) -> str:
        """Reject non-HTTP URLs; normalise trailing slash."""
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                "base_url must start with http:// or https://",
            )
        return value.rstrip("/")

    @field_validator("retry_max_wait_seconds")
    @classmethod
    def _check_retry_bounds(cls, value: float, info: object) -> float:
        """Ensure ``retry_max_wait_seconds >= retry_initial_wait_seconds``."""
        initial = getattr(info, "data", {}).get("retry_initial_wait_seconds")
        if initial is not None and value < initial:
            raise ValueError(
                f"retry_max_wait_seconds must be >= retry_initial_wait_seconds; got max={value} < initial={initial}",
            )
        return value
