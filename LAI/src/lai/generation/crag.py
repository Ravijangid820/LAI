"""Corrective RAG (CRAG) — grades retrieval quality and rewrites query if insufficient.

CRAG loop:
1. Grade each chunk's relevance to the query (LLM, temp=0)
2. If fewer than min_relevant_chunks pass, rewrite query and re-retrieve
3. Max 2 loops to bound latency
"""

from lai.core.config import get_settings
from lai.core.logging import get_logger
from lai.generation.llm_client import get_llm_client

logger = get_logger("lai.generation.crag")

GRADING_PROMPT = """Du bist ein Relevanz-Bewerter. Bewerte ob der folgende Textabschnitt
für die Beantwortung der Frage relevant ist.

Antworte NUR mit "ja" oder "nein".

Frage: {query}

Textabschnitt:
{chunk_text}

Relevant (ja/nein):"""

REWRITE_PROMPT = """Formuliere die folgende Frage um, damit sie besser für eine Suche
in einer juristischen Datenbank geeignet ist. Verwende andere Begriffe oder Formulierungen.
Antworte NUR mit der umformulierten Frage.

Originalfrage: {query}

Umformulierte Frage:"""


async def grade_chunks(query: str, chunks: list[dict]) -> list[dict]:
    """Grade each chunk's relevance to the query. Adds 'crag_relevant' field."""
    settings = get_settings().crag
    if not settings.enabled or not chunks:
        for c in chunks:
            c["crag_relevant"] = True
        return chunks

    llm = get_llm_client()
    relevant_count = 0

    for chunk in chunks:
        prompt = GRADING_PROMPT.format(query=query, chunk_text=chunk.get("text_clean", "")[:500])
        response = llm.generate(
            system_prompt="Du bist ein Relevanz-Bewerter.",
            user_message=prompt,
            temperature=settings.grading_temperature,
            max_tokens=settings.grading_max_tokens,
        )
        is_relevant = "ja" in response.text.lower()
        chunk["crag_relevant"] = is_relevant
        if is_relevant:
            relevant_count += 1

    logger.info("CRAG grading: %d/%d chunks relevant", relevant_count, len(chunks))
    return chunks


async def rewrite_query(query: str) -> str:
    """Rewrite query for better retrieval using LLM."""
    llm = get_llm_client()
    response = llm.generate(
        system_prompt="Du bist ein Suchoptimierer für juristische Datenbanken.",
        user_message=REWRITE_PROMPT.format(query=query),
        temperature=0.3,
        max_tokens=200,
    )
    rewritten = response.text.strip()
    if rewritten and len(rewritten) > 10:
        logger.info("CRAG query rewrite: '%s' -> '%s'", query[:60], rewritten[:60])
        return rewritten
    return query


def filter_relevant(chunks: list[dict]) -> list[dict]:
    """Keep only chunks graded as relevant by CRAG."""
    return [c for c in chunks if c.get("crag_relevant", True)]
