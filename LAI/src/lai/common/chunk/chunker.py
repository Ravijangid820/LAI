"""Sentence-aware text chunker for German legal documents.

The chunker turns a long string into a list of overlapping :class:`Chunk`
objects that downstream code can pass to the embedding service. Sentences
are grouped greedily into chunks targeting :attr:`ChunkerConfig.target_chars`,
respecting the hard cap :attr:`ChunkerConfig.max_chars`. Overlap is
implemented by re-prefixing the trailing characters of the previous chunk
onto the next.

Why not import the pipeline chunker?
------------------------------------

The pipeline chunker (``src/lai/pipeline/chunk.py``) lives in a package
that depends on the rest of :mod:`lai`; reusing it from :mod:`lai.common`
would introduce a circular dependency in spirit (common → pipeline → …).
Inlining the pure-function logic keeps :mod:`lai.common` a leaf package
and lets future consumers (notably the DDiQ microservice container,
which installs only ``lai.common``) import this module without dragging
in the rest of the codebase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lai.common.chunk.config import ChunkerConfig
from lai.common.chunk.sentences import split_sentences
from lai.common.exceptions import ChunkInvalidInputError

__all__ = ["Chunk", "Chunker"]


@dataclass(frozen=True, slots=True)
class Chunk:
    """One chunk produced by :class:`Chunker`.

    Attributes:
        index: Zero-based position of this chunk in the document.
        text: The chunk body. Already ``.strip()``-ed and may include
            the overlap prefix from the preceding chunk.
        char_start: Inclusive character offset of the chunk body in the
            *original* input text (before whitespace normalisation).
            Useful for citation anchoring.
        char_end: Exclusive character offset.
    """

    index: int
    text: str
    char_start: int
    char_end: int


# Match runs of whitespace that include at least one newline; collapse
# them to a single space when joining sentences inside a chunk.
_INTRA_WS = re.compile(r"\s+")


class Chunker:
    """Greedy, sentence-aware chunker.

    Args:
        config: :class:`ChunkerConfig`; defaults to ``ChunkerConfig()``.

    The chunker is stateless — instances are cheap and re-entrant. The
    same instance can chunk many documents from many threads safely.
    """

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self._config = config or ChunkerConfig()

    def chunk(self, text: str) -> list[Chunk]:
        """Split ``text`` into a list of :class:`Chunk` objects.

        Args:
            text: The source text. May be empty (returns ``[]``).

        Returns:
            A list of chunks in document order. Empty list if the input
            contains no sentence-bearing content.

        Raises:
            ChunkInvalidInputError: ``text`` is not a string.
        """
        if not isinstance(text, str):
            raise ChunkInvalidInputError(
                f"text must be str, got {type(text).__name__}",
            )
        if not text or not text.strip():
            return []

        sentences = split_sentences(text)
        if not sentences:
            return []

        # Locate every sentence's char offset in the original text so we
        # can record accurate ``char_start`` / ``char_end`` for citation.
        sentence_spans = _locate_sentences(text, sentences)

        grouped = _group_sentences_into_chunks(
            sentence_spans,
            target_chars=self._config.target_chars,
            max_chars=self._config.max_chars,
        )

        # Merge a too-small trailing chunk into its predecessor.
        if self._config.min_chars > 0:
            grouped = _merge_short_tail(grouped, min_chars=self._config.min_chars)

        if not grouped:
            return []

        return _apply_overlap(
            grouped,
            text=text,
            overlap_chars=self._config.overlap_chars,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Internals (pure functions; trivial to unit-test)
# ─────────────────────────────────────────────────────────────────────────────


def _locate_sentences(text: str, sentences: list[str]) -> list[tuple[str, int, int]]:
    """Locate each sentence in ``text``.

    The splitter normalises whitespace (``.strip()`` removes leading /
    trailing space), so we cannot simply use ``text.find``. We search
    forward from the last found offset, sliding past whitespace. Sentences
    that cannot be located fall back to a zero-length span at the current
    cursor position — defensive only; in practice every split sentence
    is locatable.
    """
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for sentence in sentences:
        match_pos = text.find(sentence, cursor)
        if match_pos == -1:
            # Whitespace inside the sentence was collapsed by the splitter;
            # fall back to a zero-width span at the cursor.
            spans.append((sentence, cursor, cursor))
            continue
        end = match_pos + len(sentence)
        spans.append((sentence, match_pos, end))
        cursor = end
    return spans


def _group_sentences_into_chunks(
    spans: list[tuple[str, int, int]],
    *,
    target_chars: int,
    max_chars: int,
) -> list[tuple[str, int, int]]:
    """Greedy grouping: add sentences until target reached, then close.

    A single sentence exceeding ``max_chars`` is split on word boundaries
    as a last resort — the splitter never produces a chunk wider than
    ``max_chars`` even when input has very long lines.
    """
    chunks: list[tuple[str, int, int]] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0

    def _flush() -> None:
        if not current:
            return
        body = _join_chunk_body(s for s, _, _ in current)
        chunks.append((body, current[0][1], current[-1][2]))
        current.clear()

    for sentence, start, end in spans:
        sentence_len = len(sentence)
        if sentence_len > max_chars:
            # Flush whatever we have first.
            _flush()
            current_len = 0
            # Then split the over-long sentence on word boundaries.
            for piece_body, piece_start, piece_end in _split_long_sentence(
                sentence,
                start,
                end,
                max_chars=max_chars,
            ):
                chunks.append((piece_body, piece_start, piece_end))
            continue

        if current_len + sentence_len + 1 > target_chars and current:
            _flush()
            current_len = 0

        current.append((sentence, start, end))
        # +1 accounts for the joining space we insert between sentences.
        current_len += sentence_len + (1 if current_len else 0)

    _flush()
    return chunks


def _split_long_sentence(
    sentence: str,
    start: int,
    end: int,
    *,
    max_chars: int,
) -> list[tuple[str, int, int]]:
    """Last-resort splitter for a single sentence wider than ``max_chars``.

    Splits on word boundaries; preserves approximate ``char_start`` /
    ``char_end`` by linear interpolation. Used so rarely in legal text
    (statute citations and footnotes are short) that we accept the
    interpolated offsets rather than building a per-word offset map.
    """
    words = sentence.split()
    if not words:
        return []
    pieces: list[tuple[str, int, int]] = []
    buf: list[str] = []
    buf_len = 0
    chars_consumed = 0
    for word in words:
        extra = len(word) + (1 if buf else 0)
        if buf and buf_len + extra > max_chars:
            piece = " ".join(buf)
            piece_start = start + chars_consumed - buf_len
            piece_end = piece_start + len(piece)
            pieces.append((piece, piece_start, piece_end))
            buf = []
            buf_len = 0
        buf.append(word)
        buf_len += extra
        chars_consumed += extra
    if buf:
        piece = " ".join(buf)
        piece_start = start + chars_consumed - buf_len
        piece_end = min(piece_start + len(piece), end)
        pieces.append((piece, piece_start, piece_end))
    return pieces


def _join_chunk_body(parts: object) -> str:
    """Join sentences with single spaces, collapsing intra-sentence whitespace."""
    joined = " ".join(parts)  # type: ignore[arg-type]
    return _INTRA_WS.sub(" ", joined).strip()


def _merge_short_tail(
    chunks: list[tuple[str, int, int]],
    *,
    min_chars: int,
) -> list[tuple[str, int, int]]:
    """Merge a trailing chunk shorter than ``min_chars`` into its predecessor.

    Only the last chunk is considered. Mid-list short chunks (which can
    only arise from the long-sentence splitter) are kept as-is.
    """
    if len(chunks) < 2:
        return chunks
    body, _start, end = chunks[-1]
    if len(body) >= min_chars:
        return chunks
    prev_body, prev_start, _ = chunks[-2]
    merged = _join_chunk_body((prev_body, body))
    return [*chunks[:-2], (merged, prev_start, end)]


def _apply_overlap(
    chunks: list[tuple[str, int, int]],
    *,
    text: str,
    overlap_chars: int,
) -> list[Chunk]:
    """Re-prefix the trailing characters of each chunk onto the next.

    The overlap is taken from the *original* text (so we re-prefix
    accurate punctuation and casing) rather than from the previously
    emitted chunk body. Where the overlap would split a word, we round
    forward to the next whitespace boundary so the prefix begins at a
    clean token.
    """
    out: list[Chunk] = []
    for index, (body, start, end) in enumerate(chunks):
        if index == 0 or overlap_chars <= 0:
            out.append(Chunk(index=index, text=body, char_start=start, char_end=end))
            continue
        prev_end = chunks[index - 1][2]
        overlap_start = max(0, prev_end - overlap_chars)
        # Round forward to next whitespace if we landed mid-token.
        if overlap_start > 0 and not text[overlap_start - 1].isspace():
            forward = text.find(" ", overlap_start, prev_end)
            if forward != -1:
                overlap_start = forward + 1
        prefix = _INTRA_WS.sub(" ", text[overlap_start:prev_end]).strip()
        new_body = f"{prefix} {body}".strip() if prefix else body
        out.append(Chunk(index=index, text=new_body, char_start=start, char_end=end))
    return out
