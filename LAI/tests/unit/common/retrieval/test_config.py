"""Tests for :class:`lai.common.retrieval.config.RetrievalConfig`."""

from __future__ import annotations

import pytest

from lai.common.retrieval.config import INDEX_DIM, RetrievalConfig


@pytest.mark.unit
def test_defaults_read_shared_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection fields fall back to the shared ``DB_*`` env vars."""
    monkeypatch.setenv("DB_HOST", "pg.internal")
    monkeypatch.setenv("DB_PORT", "6000")
    monkeypatch.setenv("DB_NAME", "corpus")
    monkeypatch.setenv("DB_USER", "reader")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    cfg = RetrievalConfig()
    assert cfg.host == "pg.internal"
    assert cfg.port == 6000
    assert cfg.dbname == "corpus"
    assert cfg.user == "reader"
    assert cfg.password.get_secret_value() == "s3cret"


@pytest.mark.unit
def test_index_dim_matches_migration_constant() -> None:
    """The config default must equal the migration's INDEX_DIM (4000)."""
    assert INDEX_DIM == 4000
    assert RetrievalConfig().index_dim == 4000


@pytest.mark.unit
def test_lai_retrieval_prefix_overrides_tuning(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LAI_RETRIEVAL_`` prefix tunes pool / ef_search independently."""
    monkeypatch.setenv("LAI_RETRIEVAL_HNSW_EF_SEARCH", "200")
    monkeypatch.setenv("LAI_RETRIEVAL_DEFAULT_TOP_K", "10")
    monkeypatch.setenv("LAI_RETRIEVAL_POOL_MAX_SIZE", "4")
    cfg = RetrievalConfig()
    assert cfg.hnsw_ef_search == 200
    assert cfg.default_top_k == 10
    assert cfg.pool_max_size == 4


@pytest.mark.unit
def test_pool_max_must_be_ge_min(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_RETRIEVAL_POOL_MIN_SIZE", "8")
    monkeypatch.setenv("LAI_RETRIEVAL_POOL_MAX_SIZE", "2")
    with pytest.raises(ValueError, match="pool_max_size"):
        RetrievalConfig()


@pytest.mark.unit
def test_frozen_after_construction() -> None:
    cfg = RetrievalConfig()
    with pytest.raises(Exception):  # pydantic ValidationError on frozen mutate
        cfg.host = "elsewhere"  # type: ignore[misc]
