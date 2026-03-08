"""Prompt builder for German legal RAG.

Constructs system prompts and user messages with retrieved context.
Key principle: LLM is text formatter only — cite sources, never invent.
"""

from lai.core.constants import QueryIntent
from lai.core.logging import get_logger
from lai.core.models import ParsedQuery

logger = get_logger("lai.generation.prompt_builder")

# German system prompts per intent
SYSTEM_PROMPTS = {
    "legal_rag": """Du bist ein juristischer Assistent für deutsches Recht.

KRITISCHE REGELN - NIEMALS VERLETZEN:
1. Du DARFST NUR Informationen aus dem bereitgestellten Kontext verwenden
2. Du MUSST jede Aussage mit der exakten Quelle belegen (z.B. "§ 823 BGB", "BGH, Az. ...")
3. Wenn der Kontext NICHT ausreicht, MUSST du dies klar sagen und DARFST NICHT spekulieren
4. KEINE erfundenen Paragraphen, Urteile oder Rechtsauskünfte
5. Bei Unsicherheit: Lieber ablehnen als falsche Information geben

FORMATIERUNG:
- Verwende formelle juristische Sprache
- Strukturiere die Antwort logisch
- Zitiere Quellen im Text: "Gemäß § X Abs. Y Satz Z [Gesetz]..."
- Liste verwendete Quellen am Ende auf

WENN DER KONTEXT UNZUREICHEND IST:
Sage klar: "Auf Basis der verfügbaren Informationen kann ich diese Frage nicht vollständig beantworten." und erkläre warum.""",

    "definition": """Du bist ein juristischer Assistent für Begriffsdefinitionen im deutschen Recht.

KRITISCHE REGELN:
1. Definiere Begriffe NUR basierend auf dem bereitgestellten Kontext
2. Zitiere die exakte Rechtsquelle der Definition
3. Unterscheide zwischen Legaldefinition, Rechtsprechungsdefinition und Lehrmeinung
4. Bei mehreren Definitionen: Stelle alle dar mit jeweiliger Quelle

WENN KEINE DEFINITION IM KONTEXT:
Sage: "Eine Definition dieses Begriffs findet sich nicht in den bereitgestellten Quellen.\"""",

    "court_ruling": """Du bist ein juristischer Assistent für Analyse von Gerichtsentscheidungen.

KRITISCHE REGELN:
1. Fasse NUR das zusammen, was im Kontext steht
2. Zitiere das Aktenzeichen und Gericht exakt
3. Unterscheide zwischen Leitsatz, Tenor und Entscheidungsgründen
4. Keine eigene rechtliche Bewertung""",

    "comparison": """Du bist ein juristischer Assistent für Rechtsvergleiche.

KRITISCHE REGELN:
1. Vergleiche NUR Normen aus dem bereitgestellten Kontext
2. Zitiere beide/alle Fassungen mit Datum
3. Stelle Änderungen strukturiert dar""",

    "general": """Du bist ein juristischer Assistent für deutsches Recht.

KRITISCHE REGELN:
1. Verwende NUR Informationen aus dem bereitgestellten Kontext
2. Belege jede Aussage mit der Quelle
3. Bei Unsicherheit: Ablehnung statt Spekulation

Beantworte die Frage basierend auf dem Kontext.""",
}

INTENT_TO_PROMPT = {
    QueryIntent.CURRENT_LAW: "legal_rag",
    QueryIntent.DEFINITION: "definition",
    QueryIntent.COURT_RULING: "court_ruling",
    QueryIntent.LAW_COMPARISON: "comparison",
    QueryIntent.HISTORICAL_LAW: "comparison",
    QueryIntent.PROCEDURE: "legal_rag",
    QueryIntent.GENERAL_INFO: "general",
}

MAX_CONTEXT_CHARS = 12000


def build_prompt(
    query: str,
    chunks: list[dict],
    parsed_query: ParsedQuery | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_message) for LLM generation."""
    intent_key = INTENT_TO_PROMPT.get(parsed_query.intent if parsed_query else QueryIntent.GENERAL_INFO, "legal_rag")
    system_prompt = SYSTEM_PROMPTS[intent_key]
    context = _format_context(chunks)
    user_message = _build_user_message(query, context, parsed_query)

    logger.debug("Built prompt: template=%s, context_chars=%d, chunks=%d", intent_key, len(context), len(chunks))
    return system_prompt, user_message


def _format_context(chunks: list[dict]) -> str:
    if not chunks:
        return "KONTEXT: Keine relevanten Dokumente gefunden."

    parts = ["KONTEXT:"]
    total = 0

    for i, chunk in enumerate(chunks, 1):
        header_parts = [f"[Quelle {i}]"]
        if chunk.get("doc_type") and chunk["doc_type"] != "other":
            header_parts.append(f"Typ: {chunk['doc_type']}")
        if chunk.get("section"):
            header_parts.append(f"Abschnitt: {chunk['section']}")
        if chunk.get("law_refs"):
            header_parts.append(f"Gesetz: {', '.join(chunk['law_refs'][:3])}")
        if chunk.get("paragraph_refs"):
            header_parts.append(f"§§: {', '.join(chunk['paragraph_refs'][:5])}")

        header = " | ".join(header_parts)
        chunk_text = f"\n{header}\n{chunk.get('text_clean', '')}\n"

        if total + len(chunk_text) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total - len(header) - 50
            if remaining > 200:
                parts.append(f"\n{header}\n{chunk.get('text_clean', '')[:remaining]}...\n")
            parts.append(f"\n[{len(chunks) - i} weitere Quellen gekürzt]")
            break

        parts.append(chunk_text)
        total += len(chunk_text)

    return "\n".join(parts)


def _build_user_message(query: str, context: str, parsed_query: ParsedQuery | None) -> str:
    parts = [context, ""]

    if parsed_query and parsed_query.intent == QueryIntent.DEFINITION:
        parts.append("HINWEIS: Der Nutzer fragt nach einer Definition. Gib die Legaldefinition an.")
        parts.append("")
    elif parsed_query and parsed_query.intent == QueryIntent.COURT_RULING:
        parts.append("HINWEIS: Der Nutzer fragt nach Rechtsprechung. Zitiere das Aktenzeichen.")
        parts.append("")

    parts.append("FRAGE:")
    parts.append(query)
    return "\n".join(parts)


def build_refusal(reason: str) -> str:
    """Build a polite refusal response in German."""
    return (
        "Leider kann ich diese Frage auf Basis der verfügbaren Informationen "
        "nicht zuverlässig beantworten.\n\n"
        f"Grund: {reason}\n\n"
        "Bitte beachten Sie: Ich kann nur Informationen aus den mir "
        "zur Verfügung stehenden Rechtsquellen verwenden."
    )
