"""
Step 2: Segments → Parent-Child Chunks

Reads normalized segments (Step 1 output), creates parent-child chunks
with legal-aware German splitting, and stores in PostgreSQL.

Parent chunks: 1024-2048 tokens (~3072-6144 chars) — fine-tuning context
Child chunks:  ~512 tokens (~1536 chars) — RAG retrieval
"""

from typing import Any

from lai.core.config import get_settings
from lai.core.logging import get_logger
from lai.pipeline.utils.german_splitter import find_section_boundaries, split_sentences

logger = get_logger("lai.pipeline.chunk")


def build_parent_chunks(
    segments: list[dict[str, Any]],
    target_chars: int,
    max_chars: int,
    min_chars: int,
) -> list[dict[str, Any]]:
    """
    Build parent chunks from document segments.
    Respects section boundaries from the document structure.
    """
    if not segments:
        logger.debug("build_parent_chunks called with empty segments")
        return []

    parents: list[dict[str, Any]] = []
    current_text = ""
    current_section = segments[0].get("section", "General")
    current_pages: set = set()

    def emit():
        nonlocal current_text, current_section, current_pages
        text = current_text.strip()
        if len(text) >= min_chars:
            parents.append(
                {
                    "text": text,
                    "section": current_section,
                    "page_start": min(current_pages) if current_pages else None,
                    "page_end": max(current_pages) if current_pages else None,
                    "char_count": len(text),
                }
            )
        current_text = ""
        current_pages = set()

    for seg in segments:
        seg_text = seg.get("text", "").strip()
        if not seg_text:
            continue

        seg_section = seg.get("section", "General")

        # Section change → emit
        if seg_section != current_section and current_text:
            emit()
            current_section = seg_section

        new_len = len(current_text) + len(seg_text) + (2 if current_text else 0)

        if new_len > max_chars and len(current_text) >= min_chars:
            emit()
            current_section = seg_section

        current_text += ("\n\n" if current_text else "") + seg_text

        if seg.get("page_start"):
            current_pages.add(seg["page_start"])
        if seg.get("page_end"):
            current_pages.add(seg["page_end"])

        # Natural break at target if next segment is a new section
        if len(current_text) >= target_chars:
            pass  # Will be emitted on next section change or max_chars

    if current_text.strip():
        emit()

    # Split any oversized parents at sentence boundaries
    oversized = sum(1 for p in parents if len(p["text"]) > max_chars)
    if oversized:
        logger.debug(f"Splitting {oversized} oversized parent chunks at sentence boundaries")
    final = []
    for parent in parents:
        if len(parent["text"]) <= max_chars:
            final.append(parent)
        else:
            sentences = split_sentences(parent["text"])
            sub_text = ""
            for sent in sentences:
                if len(sub_text) + len(sent) + 1 > target_chars and len(sub_text) >= min_chars:
                    final.append(
                        {
                            "text": sub_text.strip(),
                            "section": parent["section"],
                            "page_start": parent["page_start"],
                            "page_end": parent["page_end"],
                            "char_count": len(sub_text.strip()),
                        }
                    )
                    sub_text = sent
                else:
                    sub_text += (" " if sub_text else "") + sent

            if sub_text.strip():
                if len(sub_text.strip()) >= min_chars:
                    final.append(
                        {
                            "text": sub_text.strip(),
                            "section": parent["section"],
                            "page_start": parent["page_start"],
                            "page_end": parent["page_end"],
                            "char_count": len(sub_text.strip()),
                        }
                    )
                elif final:
                    final[-1]["text"] += "\n\n" + sub_text.strip()
                    final[-1]["char_count"] = len(final[-1]["text"])

    return final


def build_child_chunks(
    parent_text: str,
    target_chars: int,
    max_chars: int,
    min_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    """Split a parent chunk into overlapping child chunks using sentence boundaries."""
    if len(parent_text) <= max_chars:
        return [{"text": parent_text, "char_count": len(parent_text)}]

    sentences = split_sentences(parent_text)
    if not sentences:
        # Fallback: word-boundary split
        children = []
        step = target_chars - overlap_chars
        for i in range(0, len(parent_text), step):
            chunk = parent_text[i : i + target_chars]
            if i + target_chars < len(parent_text):
                last_space = chunk.rfind(" ")
                if last_space > min_chars:
                    chunk = chunk[:last_space]
            chunk = chunk.strip()
            if chunk and len(chunk) >= min_chars:
                children.append({"text": chunk, "char_count": len(chunk)})
        return children or [{"text": parent_text, "char_count": len(parent_text)}]

    children = []
    current: list[str] = []
    current_len = 0
    i = 0

    while i < len(sentences):
        sent = sentences[i]
        addition = len(sent) + (1 if current_len > 0 else 0)

        if current_len + addition > target_chars and current_len >= min_chars:
            child_text = " ".join(current).strip()
            children.append({"text": child_text, "char_count": len(child_text)})

            # Calculate overlap
            overlap_len = 0
            overlap_start = len(current)
            for j in range(len(current) - 1, -1, -1):
                overlap_len += len(current[j]) + 1
                if overlap_len >= overlap_chars:
                    overlap_start = j
                    break

            prev_len = current_len
            current = current[overlap_start:]
            current_len = sum(len(s) + 1 for s in current) - 1 if current else 0

            # Guard: if overlap didn't reduce length, clear it to prevent infinite loop
            if current_len >= prev_len or (current_len + addition > target_chars and current_len >= min_chars):
                current = []
                current_len = 0
            continue

        current.append(sent)
        current_len += addition
        i += 1

    if current:
        child_text = " ".join(current).strip()
        if len(child_text) >= min_chars:
            children.append({"text": child_text, "char_count": len(child_text)})
        elif children:
            children[-1]["text"] += " " + child_text
            children[-1]["char_count"] = len(children[-1]["text"])
        else:
            children.append({"text": child_text, "char_count": len(child_text)})

    return children


def process_document(doc: dict[str, Any]) -> tuple[list[dict], list[list[dict]]]:
    """
    Process one document: segments → parent chunks → child chunks.

    Returns (parents, children_per_parent).
    """
    settings = get_settings()
    chunk_cfg = settings.chunking
    segments = doc.get("segments", [])
    doc_id = doc.get("doc_id", "unknown")

    if not segments:
        logger.debug(f"Document {doc_id} has no segments, skipping")
        return [], []

    # For single-segment docs, try to find internal section boundaries
    if len(segments) == 1 and len(segments[0].get("text", "")) > chunk_cfg.parent_max_chars:
        text = segments[0]["text"]
        boundaries = find_section_boundaries(text)

        if boundaries:
            new_segments = []
            if boundaries[0][0] > 50:
                intro = text[: boundaries[0][0]].strip()
                if intro:
                    new_segments.append({"text": intro, "section": "Einleitung", "type": "text"})

            for idx, (pos, title) in enumerate(boundaries):
                end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(text)
                seg_text = text[pos:end].strip()
                if seg_text:
                    new_segments.append({"text": seg_text, "section": title, "type": "text"})

            if new_segments:
                segments = new_segments

    parents = build_parent_chunks(
        segments,
        target_chars=chunk_cfg.parent_target_chars,
        max_chars=chunk_cfg.parent_max_chars,
        min_chars=chunk_cfg.parent_min_chars,
    )

    all_children = []
    total_children = 0
    for parent in parents:
        children = build_child_chunks(
            parent["text"],
            target_chars=chunk_cfg.child_target_chars,
            max_chars=chunk_cfg.child_max_chars,
            min_chars=chunk_cfg.child_min_chars,
            overlap_chars=chunk_cfg.child_overlap_chars,
        )
        all_children.append(children)
        total_children += len(children)

    logger.debug(f"Document {doc_id}: {len(segments)} segments -> {len(parents)} parents -> {total_children} children")
    return parents, all_children
