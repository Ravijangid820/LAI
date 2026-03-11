"""
Step 3: Domain Classification

Classifies parent chunks into legal domains using Qwen2.5-72B via vLLM.
Domains are stored as TEXT[] on parent_chunks.domain column.

Wind-energy due-diligence domains:
    - immissionsschutzrecht (BImSchG, TA Lärm, TA Luft)
    - energierecht (EEG, WindSeeG, EnWG)
    - baurecht (BauGB, BauNVO, regional plans)
    - umweltrecht (BNatSchG, UVPG, WHG)
    - vertragsrecht (purchase agreements, leases, EPC)
    - gesellschaftsrecht (GmbH/KG structures, shareholding)
    - grundstuecksrecht (Grundbuch, easements, Dienstbarkeiten)
    - arbeitsrecht (employment, works council)
    - steuerrecht (tax structuring, EEG-Umlage)
    - verwaltungsrecht (permits, Genehmigungen, administrative procedure)
    - prozessrecht (litigation, arbitration, court procedures)
    - allgemein (general legal, not domain-specific)
"""

import json
import time
from typing import Any, Dict, List, Optional

import httpx

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.classify")

DOMAINS = [
    "immissionsschutzrecht",
    "energierecht",
    "baurecht",
    "umweltrecht",
    "vertragsrecht",
    "gesellschaftsrecht",
    "grundstuecksrecht",
    "arbeitsrecht",
    "steuerrecht",
    "verwaltungsrecht",
    "prozessrecht",
    "allgemein",
]

SYSTEM_PROMPT = f"""Du bist ein juristischer Klassifikator für deutsche Rechtstexte im Bereich Windenergie-Due-Diligence.

Klassifiziere den folgenden Text in eine oder mehrere der folgenden Rechtsgebiete:
{chr(10).join(f"- {d}" for d in DOMAINS)}

Antworte NUR mit einem JSON-Array der zutreffenden Domains, z.B.: ["energierecht", "umweltrecht"]
Wähle 1-3 Domains, die am besten passen. Bei unklarem Text: ["allgemein"]
Keine Erklärung, nur das JSON-Array."""


def classify_chunk(
    text: str,
    *,
    llm_url: str,
    llm_model: str,
    timeout: float = 60.0,
    max_input_chars: int = 4000,
) -> List[str]:
    """Classify a single parent chunk text into domains."""
    # Truncate to save tokens — first + last portions give best signal
    if len(text) > max_input_chars:
        half = max_input_chars // 2
        text = text[:half] + "\n[...]\n" + text[-half:]

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "max_tokens": 64,
    }

    try:
        resp = httpx.post(llm_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON array from response
        # Handle cases where LLM wraps in markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        domains = json.loads(content)
        if isinstance(domains, list):
            return [d for d in domains if d in DOMAINS] or ["allgemein"]

    except httpx.HTTPError as e:
        logger.warning(f"Classification HTTP error: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"Classification JSON parse error: {e} — raw: {content[:200] if 'content' in dir() else 'N/A'}")
    except (KeyError, IndexError) as e:
        logger.warning(f"Classification response structure error: {e}")

    return ["allgemein"]


def classify_batch(
    chunks: List[Dict[str, Any]],
    *,
    llm_url: str,
    llm_model: str,
    batch_size: int = 8,
) -> Dict[int, List[str]]:
    """
    Classify a batch of parent chunks. Returns {parent_id: [domains]}.

    Uses sequential requests (vLLM handles batching internally via
    continuous batching, so concurrent HTTP requests are fine but
    sequential is simpler and sufficient for classification).
    """
    results: Dict[int, List[str]] = {}
    logger.info(f"Classifying batch of {len(chunks)} parent chunks")

    for chunk in chunks:
        parent_id = chunk["id"]
        text = chunk["content"]
        doc_type = chunk.get("doc_type", "")

        # Skip very short chunks
        if len(text) < 50:
            results[parent_id] = ["allgemein"]
            continue

        # Use doc_type as hint if available
        hint = ""
        if doc_type:
            hint = f"\n[Dokumenttyp: {doc_type}]"

        domains = classify_chunk(
            text + hint,
            llm_url=llm_url,
            llm_model=llm_model,
        )
        results[parent_id] = domains
        logger.debug(f"Parent {parent_id} classified as {domains}")

    logger.info(f"Batch classification complete: {len(results)} chunks classified")
    return results
