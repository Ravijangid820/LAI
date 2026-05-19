"""Timeline / deadline extraction (P0 #2).

Pulls every date-bound milestone out of the supplied documents and
tags it with kind, urgency, days-from-now, plus :class:`Evidence`
pointers. A real DD report ranks issues by their proximity to a
deadline; without this pass the pipeline is date-blind and misses
things like ``BImSchG-Genehmigung gilt bis 2027-06-30,
Verlängerungsantrag 6 Mt vorher``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from ddiq.llm import EXTRACTION_SYSTEM, llm_json
from ddiq.models import TimelineEntry
from ddiq.rag import evidence_from_chunks, rag_context_with_meta

__all__ = ["extract_timeline"]


_log = logging.getLogger("ddiq")


def extract_timeline(doc_ids: list[str], full_text: str) -> list[TimelineEntry]:
    """Pull every date-bound milestone out of the documents and tag it.

    ``full_text`` is currently unused (the RAG path retrieves on its
    own); the parameter is kept in the signature for backwards
    compatibility with the orchestrator's call site and for the
    future case where we want a doc-wide regex pre-pass to seed the
    LLM prompt.
    """
    del full_text  # see docstring — kept for signature compatibility
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Frist Ablauf Bestandskraft Inbetriebnahme Genehmigung gültig bis Verlängerung Pachtdauer Bürgschaft Laufzeit",
        top_k=8,
    )
    prompt = f"""Extract every date or deadline relevant to the wind-park DD.

Context:
{ctx}

Return JSON array. Each entry:
{{"kind":"permit_expiry|lease_term_end|renewal_deadline|warranty_end|bond_validity|construction_milestone|objection_window|other",
  "date":"YYYY-MM-DD or free text if month/year only",
  "description":"what this date governs (e.g. 'BImSchG permit Aktenzeichen 12-345 expires')",
  "legal_basis":"statute if applicable, e.g. 'VwGO §70 Widerspruchsfrist'",
  "evidence_chunks":[1,2]}}

Look specifically for:
- BImSchG permit Ausfertigungsdatum + Bestandskraft (3 Mt nach Zustellung per §70 VwGO)
- Pachtvertrag-Laufzeit-Ende and Verlängerungsoptionen-Frist
- Bürgschaft (Rückbaubürgschaft) Ablaufdatum
- EEG-Zuschlag-Inbetriebnahmefrist (regelmäßig 30 Mt nach Gebotstermin)
- Hersteller-Gewährleistung Ende
- Netzanschluss vereinbartes Inbetriebnahmedatum
- DIBt/§52 BImSchG wiederkehrende Prüfungstermine
Return [] if nothing date-bound is in the documents. Never invent a date."""
    try:
        result: Any = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get("timeline", result.get("data", []))
        if not isinstance(result, list):
            return []
        today = date.today()
        out: list[TimelineEntry] = []
        for r in result:
            ds = str(r.get("date", "")).strip()
            if not ds:
                continue
            days: int | None = None
            urgency: str | None = None
            try:
                d = datetime.strptime(ds[:10], "%Y-%m-%d").date()
                days = (d - today).days
                urgency = (
                    "expired" if days < 0 else
                    "urgent" if days <= 30 else
                    "soon" if days <= 180 else
                    "future"
                )
            except Exception:
                pass
            ev = evidence_from_chunks(reranked, r.get("evidence_chunks", []))
            out.append(TimelineEntry(
                kind=str(r.get("kind", "other")),
                date=ds,
                description=str(r.get("description", "")),
                legal_basis=r.get("legal_basis"),
                evidence=ev,
                days_from_now=days,
                urgency=urgency,
            ))
        return sorted(
            out,
            key=lambda t: (t.days_from_now if t.days_from_now is not None else 99999),
        )
    except Exception as e:
        _log.error(f"Timeline extraction: {e}")
        return []
