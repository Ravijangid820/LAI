"""Configuration for :class:`lai.common.retrieval.client.RetrievalClient`.

Mirrors the shape of :class:`~lai.common.embedding.config.EmbeddingConfig`,
with its own ``LAI_RETRIEVAL_`` env prefix so the pgvector knobs are
independent of the embedding endpoint and the analyzer LLM.

The Postgres connection fields intentionally fall back to the *same*
``DB_HOST`` / ``DB_PORT`` / ``DB_NAME`` / ``DB_USER`` / ``DB_PASSWORD``
environment variables that ``scripts/ops/migrate_corpus.py`` and
``micro-services`` already read — so the retrieval client connects to the
exact database the migration wrote to, without a second set of env vars
to keep in sync. The ``LAI_RETRIEVAL_`` prefix is reserved for the
retrieval-specific tuning (pool sizing, ef_search, top-k defaults).
"""

from __future__ import annotations

import os

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["RetrievalConfig"]


# pgvector caps halfvec HNSW indexes at 4000 dimensions. The migration
# truncates Qwen3-Embedding's 4096-d output to the first 4000 dims
# (Matryoshka-safe) before indexing; the query vector must be truncated
# the same way. Kept as a module constant so the client and config agree
# on one source of truth.
INDEX_DIM: int = 4000


def _db_default(env_name: str, fallback: str) -> str:
    """Read a shared ``DB_*`` env var, falling back to a sensible default.

    The retrieval client deliberately shares the microservices' DB env
    rather than inventing ``LAI_RETRIEVAL_DB_*`` duplicates. pydantic-
    settings only reads the ``LAI_RETRIEVAL_`` prefix, so we resolve the
    shared vars here at field-default-construction time.
    """
    return os.environ.get(env_name, fallback)


class RetrievalConfig(BaseSettings):
    """Settings for the pgvector retrieval client.

    All knobs frozen after construction; mutations raise
    :class:`pydantic.ValidationError`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_RETRIEVAL_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── Postgres connection (shared DB_* env, see module docstring) ──────
    host: str = Field(
        default_factory=lambda: _db_default("DB_HOST", "127.0.0.1"),
        description="Postgres host. Defaults to the shared ``DB_HOST`` env.",
    )
    port: int = Field(
        default_factory=lambda: int(_db_default("DB_PORT", "5434")),
        ge=1,
        le=65535,
        description="Postgres port. Defaults to the shared ``DB_PORT`` env.",
    )
    dbname: str = Field(
        default_factory=lambda: _db_default("DB_NAME", "lai_db"),
        min_length=1,
        description="Database name. Defaults to the shared ``DB_NAME`` env.",
    )
    user: str = Field(
        default_factory=lambda: _db_default("DB_USER", "lai_user"),
        min_length=1,
        description="Postgres user. Defaults to the shared ``DB_USER`` env.",
    )
    password: SecretStr = Field(
        default_factory=lambda: SecretStr(_db_default("DB_PASSWORD", "")),
        description="Postgres password. Defaults to the shared ``DB_PASSWORD`` env.",
    )

    # ── Connection pool ──────────────────────────────────────────────────
    pool_min_size: int = Field(
        default=1,
        ge=1,
        description="Minimum connections held open by the pool.",
    )
    pool_max_size: int = Field(
        default=8,
        ge=1,
        description=(
            "Maximum connections the pool will open. serve_rag runs sync "
            "route handlers in a threadpool; this caps concurrent pgvector "
            "queries. 8 matches the FastAPI default threadpool size."
        ),
    )
    connect_timeout_s: int = Field(
        default=30,
        ge=1,
        description="Per-connection TCP/auth timeout in seconds.",
    )

    # ── Query tuning ─────────────────────────────────────────────────────
    index_dim: int = Field(
        default=INDEX_DIM,
        ge=1,
        description=(
            "Dimension the HNSW index expects. Query vectors are truncated "
            "to this width. Must match the migration's INDEX_DIM (4000)."
        ),
    )
    hnsw_ef_search: int = Field(
        default=100,
        ge=1,
        description=(
            "pgvector ``hnsw.ef_search`` — candidate list size at query "
            "time. Higher = better recall, slower. 100 is a balanced "
            "default for halfvec_cosine_ops at m=16, ef_construction=64."
        ),
    )
    default_top_k: int = Field(
        default=30,
        ge=1,
        description=(
            "Default number of child chunks returned by a dense search "
            "when the caller does not specify. 30 matches the candidate_k "
            "the reranker consumes in serve_rag._do_rag."
        ),
    )
    statement_timeout_ms: int = Field(
        default=10_000,
        ge=0,
        description=(
            "Postgres ``statement_timeout`` applied per query (0 = no "
            "limit). Guards against a pathological HNSW scan hanging a "
            "request. 10s is generous for a single ANN query."
        ),
    )

    @field_validator("pool_max_size")
    @classmethod
    def _max_ge_min(cls, v: int, info) -> int:
        """``pool_max_size`` must be >= ``pool_min_size``."""
        pool_min = info.data.get("pool_min_size", 1)
        if v < pool_min:
            raise ValueError(
                f"pool_max_size ({v}) must be >= pool_min_size ({pool_min})",
            )
        return v
