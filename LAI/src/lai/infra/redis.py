"""Async Redis client for embedding cache.

Caches embedding vectors by content hash to avoid redundant calls.
Degrades gracefully if Redis is unavailable.
"""

import hashlib
import json

import redis.asyncio as aioredis

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.infra.redis")

_redis: aioredis.Redis | None = None
_ttl: int = 3600
_stats = {"hits": 0, "misses": 0}

CACHE_PREFIX = "lai:emb:"


async def init_cache() -> None:
    """Initialize the async Redis client."""
    global _redis, _ttl
    settings = get_settings().redis
    _ttl = settings.cache_ttl

    _redis = aioredis.Redis(host=settings.host, port=settings.port, decode_responses=True)
    try:
        await _redis.ping()
        logger.info("Redis cache initialized", extra={"host": settings.host, "port": settings.port, "ttl": _ttl})
    except Exception as e:
        logger.warning("Redis unavailable, caching disabled: %s", e)
        await _redis.aclose()
        _redis = None


async def close_cache() -> None:
    """Close the Redis client."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis cache closed")


def _cache_key(text: str) -> str:
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"{CACHE_PREFIX}{text_hash}"


async def get_embedding(text: str) -> list[float] | None:
    """Retrieve a cached embedding. Returns None on miss or if Redis is down."""
    if _redis is None:
        return None
    try:
        cached = await _redis.get(_cache_key(text))
        if cached is not None:
            _stats["hits"] += 1
            return json.loads(cached)
        _stats["misses"] += 1
        return None
    except Exception as e:
        logger.debug("Cache get failed: %s", e)
        return None


async def set_embedding(text: str, embedding: list[float]) -> None:
    """Store an embedding vector in the cache."""
    if _redis is None:
        return
    try:
        await _redis.setex(_cache_key(text), _ttl, json.dumps(embedding))
    except Exception as e:
        logger.debug("Cache set failed: %s", e)


def get_stats() -> dict:
    return dict(_stats)


async def check_health() -> dict:
    if _redis is None:
        return {"status": "unavailable"}
    try:
        await _redis.ping()
        return {"status": "healthy", **_stats}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
