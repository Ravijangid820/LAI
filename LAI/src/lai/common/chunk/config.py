"""Configuration for :class:`lai.common.chunk.chunker.Chunker`.

Local-only (no HTTP) so the settings surface is small. Defaults match
the values the live pipeline (``src/lai/pipeline/chunk.py``) settled on
after the 4096-dim retrieval rollout:

- target ~1200 characters per chunk
- hard cap 2000 characters (vLLM tokeniser leaves comfortable headroom
  under Qwen3-Embedding-8B's ``max_model_len=32768``)
- floor 200 characters (anything smaller produces noisy embeddings)
- 150-character overlap to preserve sentence-spanning context
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ChunkerConfig"]


class ChunkerConfig(BaseSettings):
    """Settings for the chunker.

    All knobs frozen after construction; mutations raise
    :class:`pydantic.ValidationError`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_CHUNK_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    target_chars: int = Field(
        default=1200,
        gt=0,
        description=(
            "Soft target chunk size in characters. Sentences are added to "
            "the current chunk until appending the next sentence would "
            "exceed this; that closes the chunk."
        ),
    )
    max_chars: int = Field(
        default=2000,
        gt=0,
        description=(
            "Hard maximum chunk size. A single sentence longer than this "
            "is split on word boundaries (last-resort fallback)."
        ),
    )
    min_chars: int = Field(
        default=200,
        ge=0,
        description=(
            "Minimum chunk size. Trailing chunks below this floor are "
            "merged back into the preceding chunk where possible. "
            "``0`` disables the floor entirely (useful for very short "
            "single-paragraph documents)."
        ),
    )
    overlap_chars: int = Field(
        default=150,
        ge=0,
        description=(
            "Trailing characters of one chunk re-prefixed onto the next, "
            "to preserve sentence-spanning context for retrieval. ``0`` "
            "disables overlap. Must be smaller than ``target_chars``."
        ),
    )

    @field_validator("max_chars")
    @classmethod
    def _max_ge_target(cls, value: int, info: object) -> int:
        target = getattr(info, "data", {}).get("target_chars")
        if target is not None and value < target:
            raise ValueError(
                f"max_chars must be >= target_chars; got max={value} < target={target}",
            )
        return value

    @field_validator("min_chars")
    @classmethod
    def _min_le_target(cls, value: int, info: object) -> int:
        target = getattr(info, "data", {}).get("target_chars")
        if target is not None and value > target:
            raise ValueError(
                f"min_chars must be <= target_chars; got min={value} > target={target}",
            )
        return value

    @field_validator("overlap_chars")
    @classmethod
    def _overlap_lt_target(cls, value: int, info: object) -> int:
        target = getattr(info, "data", {}).get("target_chars")
        if target is not None and value >= target:
            raise ValueError(
                f"overlap_chars must be < target_chars; got overlap={value} >= target={target}",
            )
        return value
