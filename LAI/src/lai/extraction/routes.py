"""API routes for location extraction.

POST /extraction/locations/{segment_id}  — extract locations from one segment
POST /extraction/locations/batch         — extract from multiple segments
GET  /extraction/locations/{segment_id}  — get extracted locations
GET  /extraction/locations/summary       — get extraction statistics
"""

from fastapi import APIRouter, HTTPException, Query

from lai.core.logging import get_logger
from lai.extraction.models import ExtractionResult

logger = get_logger("lai.extraction.routes")
router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.post("/locations/{segment_id}", response_model=ExtractionResult)
async def extract_segment_locations(segment_id: int):
    """Extract locations from a single segment."""
    from lai.extraction.location import extract_locations
    from lai.extraction.repository import ensure_table, save_extraction
    from lai.infra.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        await ensure_table(conn)

        # Fetch segment text
        row = await conn.fetchrow(
            "SELECT id, text FROM segments WHERE id = $1", segment_id
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"Segment {segment_id} not found")

        result = await extract_locations(text=row["text"], segment_id=segment_id)

        if result.locations:
            inserted = await save_extraction(conn, result)
            logger.info("Saved %d locations for segment %d", inserted, segment_id)

    return result


@router.post("/locations/batch")
async def extract_batch_locations(
    source: str = Query(..., description="Source name to extract from (e.g. 'vdrs', 'dd_reports')"),
    limit: int = Query(default=100, ge=1, le=10000),
    max_concurrent: int = Query(default=8, ge=1, le=32),
):
    """Extract locations from segments of a given source that haven't been processed yet."""
    from lai.extraction.location import extract_locations_batch
    from lai.extraction.repository import ensure_table, save_extraction
    from lai.infra.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        await ensure_table(conn)

        # Find segments not yet processed for location extraction
        rows = await conn.fetch(
            """
            SELECT s.id, s.text
            FROM segments s
            LEFT JOIN document_locations dl ON dl.segment_id = s.id
            WHERE s.source = $1
              AND dl.id IS NULL
              AND s.text IS NOT NULL
              AND LENGTH(s.text) > 100
            LIMIT $2
            """,
            source, limit,
        )

    if not rows:
        return {"message": "No unprocessed segments found", "source": source, "extracted": 0}

    segments = [{"id": r["id"], "text": r["text"]} for r in rows]
    results = await extract_locations_batch(
        segments, max_concurrent=max_concurrent,
    )

    total_saved = 0
    async with pool.acquire() as conn:
        for result in results:
            if result.locations:
                total_saved += await save_extraction(conn, result)

    total_locations = sum(len(r.locations) for r in results)
    errors = sum(1 for r in results if r.error)

    return {
        "source": source,
        "segments_processed": len(segments),
        "locations_found": total_locations,
        "locations_saved": total_saved,
        "errors": errors,
    }


@router.get("/locations/{segment_id}")
async def get_segment_locations(segment_id: int):
    """Get extracted locations for a segment."""
    from lai.extraction.repository import get_locations_by_segment
    from lai.infra.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        locations = await get_locations_by_segment(conn, segment_id)

    return {"segment_id": segment_id, "locations": locations}


@router.get("/locations/summary")
async def get_extraction_summary():
    """Get summary statistics of all extracted locations."""
    from lai.extraction.repository import get_location_summary
    from lai.infra.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        summary = await get_location_summary(conn)

    return summary
