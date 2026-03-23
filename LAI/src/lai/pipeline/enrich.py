"""
Step 4: Contextual Enrichment

Generates a short context prefix for each child chunk using Qwen2.5-72B.
Based on Anthropic's "Contextual Retrieval" approach — prepending a brief
document-level context to each chunk before embedding reduces retrieval
failure by 35-67%.

The prefix is stored in child_chunks.context_prefix and prepended to
content before embedding in Step 6.
"""

import json
from typing import Any, Dict, List, Optional

import httpx

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.enrich")

SYSTEM_PROMPT = """Du bist ein Experte für deutsches Recht im Bereich Windenergie.

Gegeben ist ein Dokument und ein Abschnitt daraus. Schreibe einen kurzen Kontext-Präfix (1-2 Sätze, max. 100 Wörter), der erklärt:
- Aus welchem Dokument/Kontext dieser Abschnitt stammt
- Welches Rechtsgebiet betroffen ist
- Warum dieser Abschnitt relevant ist

Der Präfix wird dem Abschnitt vorangestellt, um die Suche zu verbessern.
Antworte NUR mit dem Kontext-Präfix, ohne Anführungszeichen oder Erklärung."""


def generate_context_prefix(
    parent_text: str,
    child_text: str,
    *,
    doc_type: str = "",
    section: str = "",
    domains: Optional[List[str]] = None,
    llm_url: str,
    llm_model: str,
    timeout: float = 60.0,
) -> str:
    """Generate a contextual prefix for a child chunk."""
    # Build user prompt with parent context (truncated)
    max_parent = 2000
    parent_snippet = parent_text[:max_parent]
    if len(parent_text) > max_parent:
        parent_snippet += "\n[...]"

    meta_parts = []
    if doc_type:
        meta_parts.append(f"Dokumenttyp: {doc_type}")
    if section:
        meta_parts.append(f"Abschnitt: {section}")
    if domains:
        meta_parts.append(f"Rechtsgebiete: {', '.join(domains)}")
    meta_line = " | ".join(meta_parts) if meta_parts else ""

    user_msg = f"""Dokument-Kontext:
{meta_line}

{parent_snippet}

---
Abschnitt zum Anreichern:
{child_text}"""

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }

    try:
        resp = httpx.post(llm_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        prefix = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip any quotes the model might add
        if prefix.startswith('"') and prefix.endswith('"'):
            prefix = prefix[1:-1]
        if prefix.startswith("'") and prefix.endswith("'"):
            prefix = prefix[1:-1]

        # Sanity check length
        if len(prefix) > 500:
            prefix = prefix[:500].rsplit(" ", 1)[0] + "..."

        return prefix

    except httpx.HTTPError as e:
        logger.warning(f"Context prefix HTTP error: {e}")
    except (KeyError, IndexError) as e:
        logger.warning(f"Context prefix response error: {e}")

    # Fallback: use metadata as prefix
    if meta_parts:
        logger.debug("Using metadata fallback for context prefix")
        return " | ".join(meta_parts) + "."
    return ""


def enrich_children_for_parent(
    parent: Dict[str, Any],
    children: List[Dict[str, Any]],
    *,
    llm_url: str,
    llm_model: str,
    max_concurrent: int = 16,
) -> Dict[int, str]:
    """
    Generate context prefixes for all children of a parent chunk concurrently.
    Returns {child_id: prefix}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[int, str] = {}
    logger.info(f"Enriching {len(children)} children for parent {parent['id']}")
    parent_text = parent["content"]
    doc_type = parent.get("doc_type", "")
    section = parent.get("section", "")
    domains = parent.get("domain", [])

    def _enrich_one(child):
        child_id = child["id"]
        child_text = child["content"]

        if len(child_text) < 50:
            return child_id, ""

        prefix = generate_context_prefix(
            parent_text,
            child_text,
            doc_type=doc_type,
            section=section,
            domains=domains,
            llm_url=llm_url,
            llm_model=llm_model,
        )
        return child_id, prefix

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {executor.submit(_enrich_one, c): c["id"] for c in children}
        for future in as_completed(futures):
            try:
                child_id, prefix = future.result()
                results[child_id] = prefix
            except Exception as e:
                child_id = futures[future]
                logger.warning(f"Child {child_id} enrichment failed: {e}")
                results[child_id] = ""

    return results
