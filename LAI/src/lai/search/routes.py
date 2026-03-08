"""Search API routes.

POST /query — main RAG query endpoint.
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from lai.core.config import get_settings
from lai.core.exceptions import EmptyRetrievalError, InputValidationError
from lai.core.logging import get_logger

logger = get_logger("lai.search.routes")
router = APIRouter(prefix="/query", tags=["search"])


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: dict | None = None


class QueryResponse(BaseModel):
    answer: str
    citations: list[dict]
    chunks_used: int
    query_intent: str
    latency_ms: float


@router.post("", response_model=QueryResponse)
async def query(request: QueryRequest, x_user_id: str | None = Header(default=None)):
    """Execute a RAG query. Optionally pass X-User-ID to include user documents."""
    # Import here to avoid circular imports at module load
    from lai.api.pipeline import run_rag_pipeline

    settings = get_settings()
    if len(request.query) > settings.api.max_query_length:
        raise HTTPException(status_code=400, detail="Query too long")

    logger.info("Query received: %s (user=%s)", request.query[:80], x_user_id)
    try:
        result = await run_rag_pipeline(
            query=request.query,
            user_id=x_user_id,
            top_k=request.top_k,
            filters=request.filters,
        )
        return result
    except EmptyRetrievalError:
        raise HTTPException(status_code=404, detail="No relevant documents found")
    except InputValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
