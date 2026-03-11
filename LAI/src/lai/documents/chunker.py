"""Legal-aware document chunker.

Splits documents into chunks respecting legal section boundaries
(§, Art., Abschnitt). Extracts metadata (law refs, dates) per chunk.
"""

import re
from dataclasses import dataclass, field

from lai.core.config import get_settings
from lai.core.constants import GERMAN_LAW_CODES
from lai.core.logging import get_logger
from lai.core.utils import sanitize_text

logger = get_logger("lai.documents.chunker")

SECTION_PATTERN = re.compile(
    r'^(?:§+\s*\d|Art(?:ikel)?\.?\s*\d|Abschnitt\s+[IVXLC\d]|Kapitel\s+[IVXLC\d]|Teil\s+[IVXLC\d])',
    re.MULTILINE,
)

PARAGRAPH_REF_PATTERN = re.compile(r'§§?\s*(\d+[a-z]?)(?:\s+([A-Z][A-Za-z]{1,15}))?')
ARTICLE_REF_PATTERN = re.compile(r'Art(?:ikel)?\.?\s*(\d+)(?:\s+([A-Z][A-Za-z]{1,15}))?')
LAW_CODE_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(c) for c in sorted(GERMAN_LAW_CODES, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)


@dataclass
class Chunk:
    text: str
    section: str = ""
    chunk_index: int = 0
    paragraph_refs: list[str] = field(default_factory=list)
    article_refs: list[str] = field(default_factory=list)
    law_refs: list[str] = field(default_factory=list)
    char_count: int = 0

    def to_dict(self) -> dict:
        return {
            "text_clean": self.text,
            "section": self.section,
            "chunk_index": self.chunk_index,
            "paragraph_refs": self.paragraph_refs,
            "article_refs": self.article_refs,
            "law_refs": self.law_refs,
            "char_count": self.char_count,
        }


class Chunker:
    """Legal-aware document chunker."""

    def __init__(self) -> None:
        settings = get_settings().chunking
        # Child chunk params (for RAG retrieval)
        self._max_chars = settings.child_max_chars
        self._min_chars = settings.child_min_chars
        self._overlap_chars = settings.child_overlap_chars
        # Parent chunk params (for fine-tuning context)
        self._parent_max = settings.parent_max_chars
        self._parent_target = settings.parent_target_chars
        self._parent_min = settings.parent_min_chars
        logger.info("Chunker initialized: child_max=%d, child_min=%d, overlap=%d", self._max_chars, self._min_chars, self._overlap_chars)

    def chunk_text(self, text: str, doc_section: str = "") -> list[Chunk]:
        """Split text into chunks respecting legal section boundaries."""
        text = sanitize_text(text)
        if not text.strip():
            return []

        # Split by legal section headers
        sections = self._split_by_sections(text)

        chunks: list[Chunk] = []
        chunk_idx = 0

        for section_title, section_text in sections:
            section_label = section_title or doc_section

            if len(section_text) <= self._max_chars:
                if len(section_text) >= self._min_chars:
                    chunks.append(self._make_chunk(section_text, section_label, chunk_idx))
                    chunk_idx += 1
                elif chunks:
                    # Merge small section into previous chunk
                    prev = chunks[-1]
                    merged = prev.text + "\n\n" + section_text
                    if len(merged) <= self._max_chars:
                        chunks[-1] = self._make_chunk(merged, prev.section, prev.chunk_index)
                    else:
                        chunks.append(self._make_chunk(section_text, section_label, chunk_idx))
                        chunk_idx += 1
                else:
                    chunks.append(self._make_chunk(section_text, section_label, chunk_idx))
                    chunk_idx += 1
            else:
                # Split long sections at sentence boundaries
                sub_chunks = self._split_long_section(section_text, section_label, chunk_idx)
                chunks.extend(sub_chunks)
                chunk_idx += len(sub_chunks)

        logger.info("Chunked text into %d chunks (input: %d chars)", len(chunks), len(text))
        return chunks

    def _split_by_sections(self, text: str) -> list[tuple[str, str]]:
        """Split text by legal section headers."""
        matches = list(SECTION_PATTERN.finditer(text))
        if not matches:
            return [("", text)]

        sections = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            header_end = text.index('\n', start) if '\n' in text[start:start + 200] else start + 100
            section_title = text[start:min(header_end, start + 100)].strip()
            section_text = text[start:end].strip()
            sections.append((section_title, section_text))

        # Include text before first section header
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                sections.insert(0, ("", preamble))

        return sections

    def _split_long_section(self, text: str, section: str, start_idx: int) -> list[Chunk]:
        """Split a long section at sentence boundaries with overlap."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current = ""

        for sentence in sentences:
            if len(current) + len(sentence) + 1 > self._max_chars and current:
                chunks.append(self._make_chunk(current.strip(), section, start_idx + len(chunks)))
                # Keep overlap
                overlap_text = current[-self._overlap_chars:] if self._overlap_chars else ""
                current = overlap_text + " " + sentence
            else:
                current = current + " " + sentence if current else sentence

        if current.strip():
            chunks.append(self._make_chunk(current.strip(), section, start_idx + len(chunks)))

        return chunks

    def _make_chunk(self, text: str, section: str, idx: int) -> Chunk:
        para_refs = list({f"§ {m.group(1)}" + (f" {m.group(2)}" if m.group(2) else "") for m in PARAGRAPH_REF_PATTERN.finditer(text)})
        art_refs = list({f"Art. {m.group(1)}" + (f" {m.group(2)}" if m.group(2) else "") for m in ARTICLE_REF_PATTERN.finditer(text)})
        law_refs = list({m.group(1).upper() for m in LAW_CODE_PATTERN.finditer(text)})

        return Chunk(
            text=text,
            section=section,
            chunk_index=idx,
            paragraph_refs=para_refs,
            article_refs=art_refs,
            law_refs=law_refs,
            char_count=len(text),
        )
