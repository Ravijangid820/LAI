"""
Step 3: Domain Classification

Classifies parent chunks into legal domains using Qwen2.5-72B via vLLM.

Classifications are stored in two places:
- parent_chunks.domain (TEXT[]) — latest classification for fast queries
- chunk_classifications table — full history with model/prompt versioning

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


# Current classification version — bump when changing prompt or model
PROMPT_VERSION = "1"


def classify_chunk(
    text: str,
    *,
    llm_url: str,
    llm_model: str,
    timeout: float = 60.0,
    max_input_chars: int = 4000,
) -> tuple[List[str], str | None]:
    """Classify a single parent chunk text into domains.

    Returns (domains, raw_response) — raw_response is kept for audit trail.
    """
    import re

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

    raw_content = None
    try:
        resp = httpx.post(llm_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        raw_content = resp.json()["choices"][0]["message"]["content"].strip()
        content = raw_content

        # Parse JSON array from response
        # Handle cases where LLM wraps in markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        # Extract first JSON array — LLM sometimes adds explanation after it
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            content = match.group(0)

        domains = json.loads(content)
        if isinstance(domains, list):
            valid = [d for d in domains if d in DOMAINS] or ["allgemein"]
            return valid, raw_content

    except httpx.HTTPError as e:
        logger.warning(f"Classification HTTP error: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"Classification JSON parse error: {e} — raw: {raw_content[:200] if raw_content else 'N/A'}")
    except (KeyError, IndexError) as e:
        logger.warning(f"Classification response structure error: {e}")

    return ["allgemein"], raw_content


def classify_batch(
    chunks: List[Dict[str, Any]],
    *,
    llm_url: str,
    llm_model: str,
    max_concurrent: int = 16,
) -> Dict[int, tuple[List[str], str | None]]:
    """
    Classify a batch of parent chunks concurrently.

    Returns {parent_id: (domains, raw_response)}.
    raw_response is stored in chunk_classifications for audit trail.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[int, tuple[List[str], str | None]] = {}
    logger.info(f"Classifying batch of {len(chunks)} parent chunks ({max_concurrent} concurrent)")

    def _classify_one(chunk):
        parent_id = chunk["id"]
        text = chunk["content"]
        doc_type = chunk.get("doc_type", "")

        if len(text) < 50:
            return parent_id, (["allgemein"], None)

        hint = f"\n[Dokumenttyp: {doc_type}]" if doc_type else ""
        domains, raw = classify_chunk(
            text + hint,
            llm_url=llm_url,
            llm_model=llm_model,
        )
        return parent_id, (domains, raw)

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_classify_one, c): c["id"] for c in chunks}
        for future in as_completed(futures):
            try:
                parent_id, result = future.result()
                results[parent_id] = result
                logger.debug(f"Parent {parent_id} classified as {result[0]}")
            except Exception as e:
                parent_id = futures[future]
                logger.warning(f"Parent {parent_id} classification failed: {e}")
                results[parent_id] = (["allgemein"], None)

    logger.info(f"Batch classification complete: {len(results)} chunks classified")
    return results
