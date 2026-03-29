"""Database operations for extracted location data."""

from lai.core.logging import get_logger
from lai.extraction.models import ExtractionResult

logger = get_logger("lai.extraction.repository")

# -- Schema DDL ---------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS document_locations (
    id BIGSERIAL PRIMARY KEY,
    segment_id BIGINT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    location_name TEXT NOT NULL,
    location_type VARCHAR(50) NOT NULL DEFAULT 'other',
    geocode_address TEXT,
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    flurstuck TEXT,
    flur TEXT,
    gemarkung TEXT,
    gemeinde TEXT,
    landkreis TEXT,
    bundesland TEXT,
    raw_excerpt TEXT,
    confidence FLOAT DEFAULT 0.0,
    model_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_doc_locations_segment
    ON document_locations(segment_id);

CREATE INDEX IF NOT EXISTS idx_doc_locations_type
    ON document_locations(location_type);

CREATE INDEX IF NOT EXISTS idx_doc_locations_gemeinde
    ON document_locations(gemeinde) WHERE gemeinde IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_doc_locations_gemarkung
    ON document_locations(gemarkung) WHERE gemarkung IS NOT NULL;

COMMENT ON TABLE document_locations IS
    'Geographic locations extracted from legal documents via LLM';
"""


async def ensure_table(conn) -> None:
    """Create the document_locations table if it doesn't exist."""
    await conn.execute(CREATE_TABLE_SQL)
    logger.info("document_locations table ensured")


async def save_extraction(conn, result: ExtractionResult) -> int:
    """Save extracted locations to the database.

    Returns number of locations inserted.
    """
    if not result.locations:
        return 0

    inserted = 0
    for loc in result.locations:
        try:
            await conn.execute(
                """
                INSERT INTO document_locations
                    (segment_id, location_name, location_type, geocode_address,
                     address, latitude, longitude, flurstuck, flur, gemarkung,
                     gemeinde, landkreis, bundesland, raw_excerpt,
                     confidence, model_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                """,
                result.segment_id,
                loc.location_name,
                loc.location_type.value,
                loc.geocode_address,
                loc.address,
                loc.coordinates.latitude if loc.coordinates else None,
                loc.coordinates.longitude if loc.coordinates else None,
                loc.flurstuck,
                loc.flur,
                loc.gemarkung,
                loc.gemeinde,
                loc.landkreis,
                loc.bundesland,
                loc.raw_excerpt,
                loc.confidence,
                result.model_name,
            )
            inserted += 1
        except Exception as e:
            logger.error(
                "Failed to insert location '%s' for segment %d: %s",
                loc.location_name, result.segment_id, e,
            )

    return inserted


async def get_locations_by_segment(conn, segment_id: int) -> list[dict]:
    """Get all extracted locations for a segment."""
    rows = await conn.fetch(
        """
        SELECT id, location_name, location_type, geocode_address,
               address, latitude, longitude, flurstuck, flur, gemarkung,
               gemeinde, landkreis, bundesland, raw_excerpt,
               confidence, model_name, created_at
        FROM document_locations
        WHERE segment_id = $1
        ORDER BY confidence DESC
        """,
        segment_id,
    )
    return [dict(r) for r in rows]


async def get_locations_by_source(conn, source: str) -> list[dict]:
    """Get all extracted locations for segments from a given source."""
    rows = await conn.fetch(
        """
        SELECT dl.*, s.source, s.filename
        FROM document_locations dl
        JOIN segments s ON s.id = dl.segment_id
        WHERE s.source = $1
        ORDER BY dl.gemeinde, dl.gemarkung, dl.location_name
        """,
        source,
    )
    return [dict(r) for r in rows]


async def get_location_summary(conn) -> dict:
    """Get a summary of all extracted locations."""
    stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) AS total_locations,
            COUNT(DISTINCT segment_id) AS segments_with_locations,
            COUNT(DISTINCT gemeinde) FILTER (WHERE gemeinde IS NOT NULL) AS unique_gemeinden,
            COUNT(DISTINCT gemarkung) FILTER (WHERE gemarkung IS NOT NULL) AS unique_gemarkungen,
            COUNT(*) FILTER (WHERE latitude IS NOT NULL) AS with_coordinates,
            COUNT(*) FILTER (WHERE flurstuck IS NOT NULL) AS with_flurstuck
        FROM document_locations
        """
    )
    type_counts = await conn.fetch(
        """
        SELECT location_type, COUNT(*) AS cnt
        FROM document_locations
        GROUP BY location_type
        ORDER BY cnt DESC
        """
    )
    return {
        "total_locations": stats["total_locations"],
        "segments_with_locations": stats["segments_with_locations"],
        "unique_gemeinden": stats["unique_gemeinden"],
        "unique_gemarkungen": stats["unique_gemarkungen"],
        "with_coordinates": stats["with_coordinates"],
        "with_flurstuck": stats["with_flurstuck"],
        "by_type": {r["location_type"]: r["cnt"] for r in type_counts},
    }
