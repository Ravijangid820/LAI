"""LAI configuration management.

Nested Pydantic settings loaded from environment variables / .env file.
Merges the best of V3 (validation, SecretStr, field descriptions) and
V4 (cleaner grouping, improved defaults from LAIV5 improvements doc).
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Base config shared by all settings groups
# ---------------------------------------------------------------------------

_COMMON_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseSettings):
    """PostgreSQL + pgvector configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "PG"})

    host: str = Field(default="localhost", alias="PGHOST")
    port: int = Field(default=5434, ge=1, le=65535, alias="PGPORT")
    database: str = Field(default="lai_db", alias="PGDATABASE")
    user: str = Field(default="lai_user", alias="PGUSER")
    password: SecretStr = Field(default=SecretStr("lai_test_password_2024"), alias="PGPASSWORD")
    pool_min_size: int = Field(default=2, ge=1, le=100)
    pool_max_size: int = Field(default=10, ge=1, le=200)

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.database}"

    @property
    def async_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class RedisSettings(BaseSettings):
    """Redis configuration for caching and task queue."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "REDIS_"})

    host: str = "localhost"
    port: int = Field(default=6380, ge=1, le=65535)
    password: SecretStr | None = None
    db: int = Field(default=0, ge=0, le=15)
    ssl: bool = False
    cache_ttl: int = Field(default=3600, description="Embedding cache TTL in seconds.")

    @property
    def url(self) -> str:
        scheme = "rediss" if self.ssl else "redis"
        if self.password:
            return f"{scheme}://:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.db}"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"


class MinIOSettings(BaseSettings):
    """MinIO object storage configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "MINIO_"})

    endpoint: str = Field(default="localhost:9000")
    access_key: str = "laiadmin"
    secret_key: SecretStr = Field(default=SecretStr("superStrongPassword123!"))
    use_ssl: bool = False
    documents_bucket: str = "documents"
    datasets_bucket: str = "datasets"
    user_documents_bucket: str = "user-documents"


# ---------------------------------------------------------------------------
# ML Services
# ---------------------------------------------------------------------------


class EmbeddingSettings(BaseSettings):
    """Embedding service configuration (Qwen3-Embedding-8B via vLLM).

    Qwen3-Embedding-8B is a dense 4096-dim embedding model (native hidden_size).
    The model does NOT support Matryoshka truncation, so we use full dims.

    Storage: pgvector `halfvec(4096)` (fp16, 2x smaller than fp32 vector).
    Index: exact cosine search — 4096 dims exceeds pgvector's HNSW limit
    (vector: 2000, halfvec: 4000). At 217K rows, exact search is fast
    enough (<100ms/query) with pre-filters on domain/doc_type.
    """

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "EMBEDDING_"})

    url: str = "http://localhost:8003"
    model: str = "Qwen/Qwen3-Embedding-8B"
    dimension: int = Field(default=4096, ge=1)
    batch_size: int = Field(default=32, ge=1, le=256)
    timeout: float = Field(default=60.0, ge=1, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)


class RerankerSettings(BaseSettings):
    """Reranker configuration (cross-encoder via vLLM/TEI)."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "RERANKER_"})

    url: str = "http://localhost:8004/v1"
    model: str = "cross-encoder/ms-marco-MiniLM-L-12-v2"
    timeout: float = Field(default=30.0, ge=1, le=120)
    max_text_length: int = Field(default=400, description="Max chars per doc sent to reranker.")
    batch_size: int = Field(default=16, ge=1, le=128)


class LLMSettings(BaseSettings):
    """LLM configuration (Qwen2.5-7B via vLLM)."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "LLM_"})

    url: str = "http://localhost:8001/v1"
    model: str = "Qwen/Qwen2.5-7B-Instruct"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=8192)  # Increased from 2048 -> 4096
    timeout: float = Field(default=120.0, ge=1, le=600)
    top_p: float = Field(default=0.95, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class RetrievalSettings(BaseSettings):
    """Retrieval pipeline configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "RETRIEVAL_"})

    # Hybrid search weights
    dense_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    sparse_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1, le=1000)

    # Retrieval counts
    initial_k: int = Field(default=100, ge=1, le=1000)
    final_k: int = Field(default=7, ge=1, le=50)  # Increased from 5 -> 7
    max_final_k: int = Field(default=10, ge=1, le=50)  # Increased from 7 -> 10
    min_final_k: int = Field(default=2, ge=1, le=50)

    # Quality thresholds
    min_similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)  # Raised from 0.3 -> 0.5
    min_chunks_required: int = Field(default=2, ge=1, le=10)

    # HNSW parameters
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)

    @field_validator("sparse_weight")
    @classmethod
    def validate_weights(cls, v: float) -> float:
        return v


class CRAGSettings(BaseSettings):
    """Corrective RAG settings."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "CRAG_"})

    enabled: bool = Field(default=True, description="Enable CRAG loop for LEGAL_COMPLEX queries.")
    max_loops: int = Field(default=2, ge=1, le=5)
    min_relevant_chunks: int = Field(default=2, ge=1, le=10)
    grading_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    grading_max_tokens: int = Field(default=16, ge=1, le=100)


class ChunkingSettings(BaseSettings):
    """Parent-child chunking configuration for German legal text.

    German tokenization: ~3 chars per token (compound words).
    Parent chunks: used as context for fine-tuning data generation.
    Child chunks: embedded for RAG retrieval.
    """

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "CHUNK_"})

    # Parent chunks: 1024-2048 tokens × 3 chars/token
    parent_target_chars: int = Field(default=3072, ge=500, le=10000)
    parent_max_chars: int = Field(default=6144, ge=1000, le=20000)
    parent_min_chars: int = Field(default=400, ge=50, le=2000)

    # Child chunks: ~512 tokens × 3 chars/token
    child_target_chars: int = Field(default=1536, ge=200, le=5000)
    child_max_chars: int = Field(default=1800, ge=300, le=6000)
    child_min_chars: int = Field(default=200, ge=50, le=1000)
    child_overlap_chars: int = Field(default=384, ge=0, le=2000)

    max_file_size_mb: int = Field(default=50, ge=1, le=500)
    allowed_extensions: list[str] = [".pdf", ".docx", ".json", ".jsonl", ".txt", ".md", ".html"]


class BatchProcessingSettings(BaseSettings):
    """Batch processing for large-scale corpus ingestion."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "BATCH_"})

    size: int = Field(default=10000, ge=100, le=100000)
    checkpoint_interval: int = Field(default=5, ge=1, le=100)
    max_concurrent: int = Field(default=3, ge=1, le=10)
    dedup_minhash_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    dedup_minhash_permutations: int = Field(default=128, ge=16, le=512)


class PipelineSettings(BaseSettings):
    """Data processing pipeline configuration (Steps 1-6)."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "PIPELINE_"})

    # MinIO buckets for pipeline stages
    bucket_raw: str = Field(default="lai-raw", description="Source bucket with raw documents.")
    bucket_segments: str = Field(default="lai-segments", description="Step 1 output: normalized segments.")

    # Processing
    max_workers: int = Field(default=0, ge=0, le=64, description="0 = auto-detect from GPU/CPU.")
    vram_per_worker_gb: float = Field(default=4.0, ge=1.0, le=48.0)

    # LLM for classification, enrichment, fine-tuning generation (Qwen2.5-72B)
    synth_llm_url: str = "http://localhost:8005/v1/chat/completions"
    synth_llm_model: str = "Qwen/Qwen2.5-72B-Instruct-AWQ"
    synth_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    synth_max_tokens: int = Field(default=2048, ge=256, le=8192)

    # Fine-tuning target
    target_training_samples: int = Field(default=200000, ge=1000)
    refusal_ratio: float = Field(default=0.10, ge=0.0, le=0.5)


# ---------------------------------------------------------------------------
# External Services
# ---------------------------------------------------------------------------


class BraveSearchSettings(BaseSettings):
    """Brave Search API for web search fallback."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "BRAVE_"})

    api_key: str = ""
    base_url: str = "https://api.search.brave.com/res/v1/web/search"
    max_results: int = Field(default=5, ge=1, le=20)
    timeout: float = Field(default=15.0, ge=1, le=60)
    enabled: bool = Field(default=False, description="Enable web search fallback.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class JWTSettings(BaseSettings):
    """JWT authentication configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "JWT_"})

    secret_key: SecretStr = Field(default=SecretStr("CHANGE-ME-IN-PRODUCTION"))
    algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)


class MonitoringSettings(BaseSettings):
    """Monitoring SLOs and alert thresholds."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "MONITOR_"})

    target_refusal_rate_min: float = Field(default=0.15, ge=0.0, le=1.0)
    target_refusal_rate_max: float = Field(default=0.25, ge=0.0, le=1.0)
    alert_refusal_rate_max: float = Field(default=0.40, ge=0.0, le=1.0)
    alert_refusal_rate_min: float = Field(default=0.05, ge=0.0, le=1.0)
    target_citation_verification_rate: float = Field(default=0.98, ge=0.0, le=1.0)
    alert_citation_verification_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    target_p95_latency_ms: int = Field(default=3000, ge=100, le=30000)
    alert_p95_latency_ms: int = Field(default=5000, ge=100, le=60000)


class APISettings(BaseSettings):
    """API server configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "API_"})

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = False
    cors_origins: list[str] = ["*"]
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    request_timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_query_length: int = Field(default=2000, description="Max chars for user query input.")


class SessionSettings(BaseSettings):
    """Conversation session configuration."""

    model_config = SettingsConfigDict(**{**_COMMON_CONFIG, "env_prefix": "SESSION_"})

    max_turns: int = Field(default=10, ge=1, le=100)
    expiry_days: int = Field(default=7, ge=1, le=90)  # Increased from 1 -> 7


# ---------------------------------------------------------------------------
# Aggregate settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Main settings container - aggregates all settings groups.

    Usage:
        from lai.core.config import get_settings
        settings = get_settings()
        print(settings.db.host)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Infrastructure
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)

    # ML Services
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    # Pipeline
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    crag: CRAGSettings = Field(default_factory=CRAGSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    batch: BatchProcessingSettings = Field(default_factory=BatchProcessingSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)

    # External services
    brave_search: BraveSearchSettings = Field(default_factory=BraveSearchSettings)

    # Application
    jwt: JWTSettings = Field(default_factory=JWTSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    api: APISettings = Field(default_factory=APISettings)
    session: SessionSettings = Field(default_factory=SessionSettings)

    # Metadata
    app_name: str = "LAI"
    app_version: str = "5.0.0"
    environment: str = Field(default="development", description="development | staging | production")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance. Call get_settings.cache_clear() to reload."""
    return Settings()
