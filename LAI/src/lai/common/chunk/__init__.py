"""Shared German-legal-aware text chunker.

Consolidates the three duplicated chunkers (one in
``src/lai/documents/chunker.py``, one in ``src/lai/pipeline/chunk.py``, and a
hand-rolled stub in ``micro-services/ddiq_report.py``) into a single,
typed, pure-function module under :mod:`lai.common`.

The sentence-splitter logic is inlined here (see
:mod:`~lai.common.chunk.sentences`) rather than imported from
:mod:`lai.pipeline` so :mod:`lai.common` remains a leaf package — no
upward dependency on the legacy stack. The historical
:mod:`lai.pipeline.utils.german_splitter` continues to work for its
existing callers; new code uses :func:`lai.common.chunk.split_sentences`.

Submodules:

- :mod:`~lai.common.chunk.config` — :class:`ChunkerConfig` (settings).
- :mod:`~lai.common.chunk.sentences` — :func:`split_sentences`,
  :func:`find_section_boundaries`, and the abbreviation set.
- :mod:`~lai.common.chunk.chunker` — :class:`Chunker`, :class:`Chunk`.
"""

from __future__ import annotations

from lai.common.chunk.chunker import Chunk, Chunker
from lai.common.chunk.config import ChunkerConfig
from lai.common.chunk.sentences import find_section_boundaries, split_sentences

__all__ = [
    "Chunk",
    "Chunker",
    "ChunkerConfig",
    "find_section_boundaries",
    "split_sentences",
]
