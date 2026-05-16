"""Configuration for :class:`lai.common.reranker.client.RerankerClient`.

Mirrors the shape of :class:`~lai.common.llm.config.LlmConfig` but with
its own ``LAI_RERANKER_`` env prefix so reranker and LLM endpoints can
be tuned independently. Defaults match the live ``lai-test-reranker``
HuggingFace TEI container (port 8004 from the host, port 80 in the
Docker network).
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["RerankerConfig"]


class RerankerConfig(BaseSettings):
    """Settings for the reranker client.

    All knobs frozen after construction; mutations raise
    :class:`pydantic.ValidationError`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_RERANKER_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── Endpoint ────────────────────────────────────────────────────────
    base_url: str = Field(
        default="http://lai-test-reranker:80",
        description=(
            "Base URL for the TEI reranker service. The live container's "
            "Docker-network DNS name is ``lai-test-reranker``; from the "
            "host it is reachable at ``http://localhost:8004``."
        ),
    )

    # ── Transport ───────────────────────────────────────────────────────
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description=(
            "Per-request timeout. Reranking is fast (~10-100ms for "
            "ms-marco-MiniLM on the ~32 documents the upstream services "
            "send); 30s is generous headroom for cold cache / contention."
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
            "Maximum ``texts`` array length per request. TEI's "
            "``max_client_batch_size`` for ms-marco-MiniLM-L-12-v2 is 32; "
            "longer inputs are split into multiple requests by the client."
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
                "retry_max_wait_seconds must be >= retry_initial_wait_seconds; " f"got max={value} < initial={initial}",
            )
        return value
