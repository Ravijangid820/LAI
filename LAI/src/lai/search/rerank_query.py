"""Query augmentation fed to the reranker (NOT to BM25).

Hypothesis: the 2026-06-02 BM25-expansion sweep showed that OR-ing
LLM-generated expansions into BM25 hurts Recall@K (broader BM25 →
worse precision). But the *content* of the expansions was real
semantic signal — just fed to the wrong layer. This module composes
the augmented query the **reranker** sees, leaving BM25 (v5) and
dense untouched.

Three augmentation modes (plus the `none` control):

* ``q1``: synonyms appended via "Verwandte Begriffe: …" (German "related terms").
* ``q2``: morphology appended via "Verwandte Formen: …" (German "related forms").
* ``q3``: both.

Expansions come from ``lai.search.query_rewriter.get_safe_expansions``
which already has the LLM cache from the 2026-06-02 BM25-rewrite
sweep — re-runs cost zero.

Production posture: inert until ``LAI_RERANK_QUERY_VARIANT`` env is
set in the harness subprocess; serve_rag never sets it.
"""

from __future__ import annotations

import os
from typing import Literal

from lai.search.query_rewriter import get_safe_expansions

__all__ = ["Variant", "augment"]


Variant = Literal["none", "q1", "q2", "q3"]


def _format_synonyms(syns: list[str]) -> str:
    if not syns:
        return ""
    return "\nVerwandte Begriffe: " + ", ".join(syns)


def _format_morphology(forms: list[str]) -> str:
    if not forms:
        return ""
    return "\nVerwandte Formen: " + ", ".join(forms)


def augment(query: str, variant: Variant | str | None = None) -> str:
    """Return the query string to hand to the reranker.

    With ``variant`` ``"none"`` (or omitted, or ``LAI_RERANK_QUERY_VARIANT``
    unset), the original query is returned unchanged — production
    behaviour.
    """
    v = variant if variant is not None else os.environ.get("LAI_RERANK_QUERY_VARIANT", "none")
    if v == "none" or not query.strip():
        return query

    if v == "q1":
        syns = get_safe_expansions(query, "r2")
        return query + _format_synonyms(syns)
    if v == "q2":
        forms = get_safe_expansions(query, "r1")
        return query + _format_morphology(forms)
    if v == "q3":
        syns = get_safe_expansions(query, "r2")
        forms = get_safe_expansions(query, "r1")
        return query + _format_synonyms(syns) + _format_morphology(forms)

    # Unknown variant → passthrough (be permissive, never error in retrieval).
    return query
