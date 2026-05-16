"""Citation-handle extraction and validation.

The Day-1 chat refactor (see :mod:`lai.api.serve_rag`) introduced stable
``[C-n]`` / ``[M-n]`` citation handles that the LLM is instructed to
emit verbatim — corpus chunks and matter (uploaded-document) chunks
respectively. Day-4 of the demo plan adds a server-side validator that:

1. Extracts every handle the model emitted.
2. Compares them against the set of handles the prompt actually carried.
3. Strips unresolved handles and marks the surrounding sentence
   ``(unbelegt)`` so the reader knows the claim has no source.

This module is the validator. It is consumed by ``serve_rag`` post-LLM
and (later) by the v1.1 render-from-conversation report path.

Pure-function design — no I/O, no global state, no side effects. The
two public entry points are :func:`extract_citations` and
:func:`validate_citations`.
"""

from __future__ import annotations

from lai.common.citation.validator import (
    CITATION_PATTERN,
    CitationValidationResult,
    extract_citations,
    validate_citations,
)

__all__ = [
    "CITATION_PATTERN",
    "CitationValidationResult",
    "extract_citations",
    "validate_citations",
]
