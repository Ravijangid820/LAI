"""
Step 5: Synthetic Fine-Tuning Data Generation

Generates ~200K RAG-style Q&A training samples from parent chunks using
Qwen2.5-72B. Each sample is a ChatML conversation stored in
training_samples.messages as JSONB.

Task types (diverse training signal):
    - rag_qa:       Question → answer grounded in the text
    - summarize:    Summarize the legal content
    - explain:      Explain a legal concept to a non-expert
    - compare:      Compare two aspects mentioned in the text
    - extract:      Extract specific legal facts (dates, parties, clauses)
    - classify_qa:  Which legal domain does this text belong to and why?
    - refusal:      Questions the model should refuse (out-of-scope, speculation)

Target distribution per parent chunk:
    ~3-5 samples depending on chunk length, with refusal_ratio from config.
"""

import json
import random
from typing import Any, Dict, List, Optional

import httpx

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.generate")

TASK_TYPES = ["rag_qa", "summarize", "explain", "compare", "extract", "classify_qa"]

TASK_PROMPTS = {
    "rag_qa": """Erstelle eine präzise Frage-Antwort-Kombination auf Deutsch basierend auf dem folgenden Rechtstext.
Die Frage soll so gestellt sein, wie ein Jurist oder Due-Diligence-Analyst sie stellen würde.
Die Antwort muss ausschließlich auf dem Text basieren und relevante §§ oder Klauseln zitieren.

Format (JSON):
{{"question": "...", "answer": "..."}}""",

    "summarize": """Erstelle eine Aufgabe, bei der der folgende Rechtstext zusammengefasst werden soll.
Die Zusammenfassung soll die wichtigsten rechtlichen Punkte hervorheben.

Format (JSON):
{{"question": "Fasse den folgenden Rechtstext zusammen und hebe die wichtigsten rechtlichen Aspekte hervor.", "answer": "..."}}""",

    "explain": """Erstelle eine Frage, die einen juristischen Laien betrifft, und erkläre den Inhalt des folgenden Rechtstexts verständlich.
Die Erklärung soll fachlich korrekt, aber allgemeinverständlich sein.

Format (JSON):
{{"question": "...", "answer": "..."}}""",

    "compare": """Erstelle eine Vergleichsfrage basierend auf dem folgenden Rechtstext.
Vergleiche zwei Aspekte, Regelungen oder Parteien, die im Text erwähnt werden.

Format (JSON):
{{"question": "...", "answer": "..."}}""",

    "extract": """Erstelle eine Extraktionsaufgabe basierend auf dem folgenden Rechtstext.
Die Frage soll nach konkreten Fakten fragen: Daten, Fristen, Parteien, Beträge, Klauselnummern.

Format (JSON):
{{"question": "...", "answer": "..."}}""",

    "classify_qa": """Erstelle eine Klassifikationsfrage zum folgenden Rechtstext.
Die Frage soll nach dem Rechtsgebiet und der rechtlichen Einordnung fragen.

Format (JSON):
{{"question": "...", "answer": "..."}}""",
}

REFUSAL_SYSTEM = """Du bist ein juristischer KI-Assistent für deutsches Windenergie-Recht.
Erstelle eine Frage, die ein Nutzer stellen könnte, die aber NICHT aus dem gegebenen Text beantwortet werden kann.
Dann gib eine höfliche Ablehnung mit Begründung.

Beispiele für ablehnungswürdige Fragen:
- Spekulative Fragen über zukünftige Gesetzesänderungen
- Fragen zu anderen Rechtsgebieten (Strafrecht, Familienrecht)
- Bitte um konkrete Rechtsberatung für einen Einzelfall
- Fragen die über den Textinhalt hinausgehen

Format (JSON):
{"question": "...", "answer": "Ich kann diese Frage nicht beantworten, weil... Ich empfehle, einen Fachanwalt zu konsultieren."}"""


def _generate_sample(
    text: str,
    task_type: str,
    *,
    domains: Optional[List[str]] = None,
    llm_url: str,
    llm_model: str,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """Generate a single training sample from a parent chunk."""
    is_refusal = task_type == "refusal"

    system = REFUSAL_SYSTEM if is_refusal else TASK_PROMPTS.get(task_type, TASK_PROMPTS["rag_qa"])

    # Truncate text to fit context
    max_input = 5000
    if len(text) > max_input:
        half = max_input // 2
        text = text[:half] + "\n[...]\n" + text[-half:]

    domain_hint = ""
    if domains:
        domain_hint = f"\n[Rechtsgebiete: {', '.join(domains)}]\n"

    user_msg = f"{domain_hint}Rechtstext:\n{text}"

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = httpx.post(llm_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON from response
        if "```" in content:
            parts = content.split("```")
            for part in parts[1:]:
                if part.startswith("json"):
                    part = part[4:]
                part = part.strip()
                if part.startswith("{"):
                    content = part
                    break

        # Find the JSON object in the response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            return None

        qa = json.loads(content[start:end])
        question = qa.get("question", "").strip()
        answer = qa.get("answer", "").strip()

        if not question or not answer or len(question) < 10 or len(answer) < 20:
            return None

        # Build ChatML format
        messages = [
            {"role": "system", "content": "Du bist ein juristischer KI-Assistent spezialisiert auf deutsches Windenergie-Recht und Due-Diligence-Prüfungen."},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]

        return {
            "task_type": task_type,
            "messages": messages,
            "quality_score": None,  # Can be scored later
        }

    except httpx.HTTPError as e:
        logger.warning(f"Sample generation HTTP error ({task_type}): {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Sample generation JSON parse error ({task_type}): {e}")
        return None
    except (KeyError, IndexError) as e:
        logger.warning(f"Sample generation response error ({task_type}): {e}")
        return None


def generate_samples_for_parent(
    parent: Dict[str, Any],
    *,
    llm_url: str,
    llm_model: str,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    refusal_ratio: float = 0.10,
) -> List[Dict[str, Any]]:
    """
    Generate multiple training samples from a single parent chunk.

    Returns list of sample dicts with keys: task_type, messages, quality_score.
    Generates 3-5 samples per parent depending on text length.
    """
    text = parent["content"]
    domains = parent.get("domain", [])
    char_count = len(text)

    logger.info(f"Generating samples for parent {parent['id']} ({char_count} chars, domains={domains})")

    # Determine number of samples based on chunk size
    if char_count < 500:
        num_samples = 2
    elif char_count < 2000:
        num_samples = 3
    elif char_count < 4000:
        num_samples = 4
    else:
        num_samples = 5

    # Select task types — always include rag_qa, then diversify
    tasks = ["rag_qa"]
    remaining = [t for t in TASK_TYPES if t != "rag_qa"]
    random.shuffle(remaining)
    tasks.extend(remaining[: num_samples - 1])

    # Add refusal sample based on ratio
    if random.random() < refusal_ratio:
        tasks.append("refusal")

    logger.debug(f"Parent {parent['id']}: generating {len(tasks)} samples with tasks {tasks}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    samples = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {
            executor.submit(
                _generate_sample, text, task_type,
                domains=domains, llm_url=llm_url, llm_model=llm_model,
                temperature=temperature, max_tokens=max_tokens,
            ): task_type
            for task_type in tasks
        }
        for future in as_completed(futures):
            try:
                sample = future.result(timeout=120)
                if sample:
                    sample["parent_id"] = parent["id"]
                    sample["domain"] = domains[0] if domains else "allgemein"
                    samples.append(sample)
            except Exception as e:
                logger.warning(f"Parent {parent['id']} task {futures[future]} failed: {e}")

    logger.info(f"Parent {parent['id']}: generated {len(samples)}/{len(tasks)} samples successfully")
    return samples
