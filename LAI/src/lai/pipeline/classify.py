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
        import re
        resp = httpx.post(llm_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

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
    max_concurrent: int = 16,
) -> Dict[int, List[str]]:
    """
    Classify a batch of parent chunks concurrently. Returns {parent_id: [domains]}.

    Sends up to max_concurrent requests in parallel to saturate vLLM's
    continuous batching (--max-num-seqs). This is 10-16x faster than sequential.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[int, List[str]] = {}
    logger.info(f"Classifying batch of {len(chunks)} parent chunks ({max_concurrent} concurrent)")

    def _classify_one(chunk):
        parent_id = chunk["id"]
        text = chunk["content"]
        doc_type = chunk.get("doc_type", "")

        if len(text) < 50:
            return parent_id, ["allgemein"]

        hint = f"\n[Dokumenttyp: {doc_type}]" if doc_type else ""
        domains = classify_chunk(
            text + hint,
            llm_url=llm_url,
            llm_model=llm_model,
        )
        return parent_id, domains

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_classify_one, c): c["id"] for c in chunks}
        for future in as_completed(futures):
            try:
                parent_id, domains = future.result()
                results[parent_id] = domains
                logger.debug(f"Parent {parent_id} classified as {domains}")
            except Exception as e:
                parent_id = futures[future]
                logger.warning(f"Parent {parent_id} classification failed: {e}")
                results[parent_id] = ["allgemein"]

    logger.info(f"Batch classification complete: {len(results)} chunks classified")
    return results
