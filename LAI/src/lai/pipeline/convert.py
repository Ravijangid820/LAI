"""
Step 1: Raw → Normalized Segments

Converts raw files from MinIO into normalized text segments.
Segments preserve document structure (sections, pages) without chunking.
Chunking is handled by Step 2 (lai.pipeline.chunk).

Output format (one JSONL line per document):
{
    "doc_id": "hash16",
    "source_file": "path/in/minio",
    "language": "de",
    "doc_type": "gesetz",
    "segments": [{"text": "...", "section": "§1", "page_start": 1, "page_end": 1, "type": "text"}],
    "metadata": {}
}
"""

import gc
import hashlib
import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from lai.core.logging import get_logger
from lai.pipeline.utils.text_cleaner import clean_text

logger = get_logger("lai.pipeline.convert")

# ============================================================
# Language & doc_type inference from MinIO path
# ============================================================

_PATH_RULES: List[Tuple[str, str, str]] = [
    ("de/gesetzes/",             "de", "gesetz"),
    ("de/TA ",                   "de", "technische_anleitung"),
    ("en/english/",              "en", "regulation"),
    ("DD Reports/",              "de", "dd_report"),
    ("VDRs/",                    "de", "vdr"),
    ("Libary/",                  "de", "fachbuch"),
    ("rss/",                     "de", "bgbl_update"),
    ("legal_data/hf_cases/",     "de", "urteil"),
    ("legal_data/openlegaldata", "de", "urteil"),
    ("legal_data/gerdalir",      "de", "legal_corpus"),
    ("legal_data/german_ler",    "de", "ner_corpus"),
    ("legal_data/multilegalpile", "multi", "legal_corpus"),
]


def infer_language_doctype(file_path: str) -> Tuple[str, str]:
    """Infer language and doc_type from the MinIO object path."""
    for prefix, lang, dtype in _PATH_RULES:
        if file_path.startswith(prefix):
            return lang, dtype
    return "de", "document"


# ============================================================
# Helpers for reading MinIO part-files
# ============================================================

def read_json(source: io.BytesIO) -> Any:
    """Read JSON, handling MinIO part-file binary headers."""
    raw = source.getvalue()
    start = raw.find(b"{")
    if start < 0:
        start = raw.find(b"[")
    if start < 0:
        return None
    return json.loads(raw[start:].decode("utf-8", errors="replace"))


def read_jsonl_lines(source: io.BytesIO) -> List[str]:
    """Read JSONL lines, handling binary header."""
    raw = source.getvalue()
    start = raw.find(b"{")
    if start < 0:
        return []
    text = raw[start:].decode("utf-8", errors="replace")
    return [line for line in text.split("\n") if line.strip().startswith("{")]


# ============================================================
# File type detection
# ============================================================

DOCLING_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".html", ".htm"}
LEGACY_EXTENSIONS = {".doc", ".ppt", ".xls"}


def get_source_type(file_path: str) -> str:
    """Determine which converter to use based on the file path."""
    fp = file_path.lower()
    ext = os.path.splitext(fp)[1]

    if "multilegalpile" in fp:
        return "multilegalpile"
    if "hf_cases" in fp:
        return "hf_cases"
    if "openlegaldata" in fp:
        return "openlegaldata"
    if "gerdalir" in fp:
        return "gerdalir"
    if "german_ler" in fp:
        return "german_ler"

    if ext in DOCLING_EXTENSIONS:
        return "docling"
    if ext in LEGACY_EXTENSIONS:
        return "legacy"
    if ext == ".json":
        return "json_generic"
    if ext == ".jsonl":
        return "jsonl_generic"

    return "unsupported"


# ============================================================
# Docling converter (PDF, DOCX, PPTX, etc.)
# ============================================================

_CONVERTER = None


def _get_docling_converter():
    """Lazy-init Docling DocumentConverter (cached per process)."""
    global _CONVERTER
    if _CONVERTER is None:
        logger.info("Initializing Docling DocumentConverter (first call in this process)")
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
            TesseractCliOcrOptions,
        )

        pdf_opts = PdfPipelineOptions()
        pdf_opts.do_ocr = True
        pdf_opts.do_table_structure = True
        pdf_opts.table_structure_options.mode = TableFormerMode.ACCURATE

        # Use Tesseract with German language pack for accurate OCR on legal text.
        # Default RapidOCR uses Chinese PP-OCR models — unsuitable for German.
        pdf_opts.ocr_options = TesseractCliOcrOptions(lang=["deu", "eng"])
        logger.info("OCR engine: Tesseract CLI (languages: deu, eng)")

        _CONVERTER = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
        )
        logger.info("Docling DocumentConverter ready (Tesseract OCR)")
    return _CONVERTER


def _normalize_docling_document(doc_dict: Dict, source_filename: str) -> List[Dict[str, Any]]:
    """Traverse Docling document dict → flat list of text segments with metadata."""
    texts_map = {f"#/texts/{i}": item for i, item in enumerate(doc_dict.get("texts", []))}
    tables_map = {f"#/tables/{i}": item for i, item in enumerate(doc_dict.get("tables", []))}
    groups_map = {f"#/groups/{i}": item for i, item in enumerate(doc_dict.get("groups", []))}
    equations_map = {f"#/equations/{i}": item for i, item in enumerate(doc_dict.get("equations", []))}
    pictures_map = {f"#/pictures/{i}": item for i, item in enumerate(doc_dict.get("pictures", []))}
    kv_map = {f"#/key_value_items/{i}": item for i, item in enumerate(doc_dict.get("key_value_items", []))}

    segments: List[Dict[str, Any]] = []
    current_section = "General"

    def process_ref(ref_obj: Dict):
        nonlocal current_section
        ref = ref_obj.get("$ref")
        if not ref:
            return

        item = None
        if "/texts/" in ref:
            item = texts_map.get(ref)
        elif "/tables/" in ref:
            item = tables_map.get(ref)
        elif "/equations/" in ref:
            item = equations_map.get(ref)
        elif "/pictures/" in ref:
            item = pictures_map.get(ref)
        elif "/key_value_items/" in ref:
            item = kv_map.get(ref)
        elif "/groups/" in ref:
            group = groups_map.get(ref)
            if group:
                for child in group.get("children", []):
                    process_ref(child)
            return

        if not item:
            return

        label = item.get("label", "")
        if label in ("page_header", "page_footer"):
            return
        if label == "section_header":
            head = item.get("text")
            if isinstance(head, str) and head.strip():
                current_section = head.strip()

        pages = [p.get("page_no") for p in item.get("prov", []) if p.get("page_no") is not None]

        # Determine content and type
        seg_type = "text"
        content = ""
        if "/tables/" in ref:
            content = _table_to_markdown(item)
            seg_type = "table"
        elif "/equations/" in ref:
            content = item.get("text", "")
            seg_type = "equation"
        elif "/pictures/" in ref:
            captions = [c.get("text", "") for c in item.get("captions", [])]
            content = "\n".join(captions)
            seg_type = "picture"
        elif "/key_value_items/" in ref:
            k = item.get("key", {}).get("text", "")
            v = item.get("value", {}).get("text", "")
            content = f"{k}: {v}"
        else:
            content = item.get("text", "")
            if not isinstance(content, str):
                content = str(content)

        content = clean_text(content)
        if content and len(content) >= 5:
            segments.append({
                "text": content,
                "section": current_section,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "type": seg_type,
            })

    for child in doc_dict.get("body", {}).get("children", []):
        process_ref(child)

    return segments


def _table_to_markdown(table_obj: Dict) -> str:
    """Convert Docling table object to Markdown."""
    try:
        grid = table_obj.get("data", {}).get("grid", [])
        if not grid or not isinstance(grid[0], list):
            return "[Table]"
        lines = []
        for row_idx, row in enumerate(grid):
            cells = [cell.get("text", "").strip().replace("\n", " ") for cell in row]
            lines.append("| " + " | ".join(cells) + " |")
            if row_idx == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(lines)
    except Exception:
        return "[Table]"


def convert_docling(file_bytes: io.BytesIO, filename: str) -> List[Dict[str, Any]]:
    """Convert a document via Docling → list of text segments."""
    from docling.datamodel.base_models import DocumentStream

    logger.debug(f"Docling converting: {filename}")
    converter = _get_docling_converter()
    stream = DocumentStream(name=filename, stream=file_bytes)

    result = converter.convert(stream)
    doc_obj = getattr(result, "document", result)

    if hasattr(doc_obj, "export_to_dict"):
        doc_dict = doc_obj.export_to_dict()
    else:
        text = getattr(doc_obj, "text", "") or getattr(doc_obj, "plain_text", "") or ""
        logger.warning(f"Docling returned plain text (no dict) for {filename}")
        return [{"text": clean_text(text), "section": "Content", "type": "text"}] if text else []

    segments = _normalize_docling_document(doc_dict, os.path.basename(filename))
    logger.debug(f"Docling extracted {len(segments)} segments from {filename}")
    return segments


# ============================================================
# JSON/JSONL converters
# ============================================================

def convert_hf_case(source: io.BytesIO, filename: str) -> List[Dict[str, Any]]:
    """Single JSON court case → one document."""
    data = read_json(source)
    if not data or not isinstance(data, dict):
        return []

    text = ""
    if data.get("markdown_content"):
        text = data["markdown_content"]
    elif data.get("content"):
        from bs4 import BeautifulSoup
        text = BeautifulSoup(data["content"], "html.parser").get_text(" ", strip=True)
    elif data.get("text"):
        text = data["text"]

    text = clean_text(text)
    if not text or len(text) < 20:
        return []

    return [{
        "text": text,
        "section": "Content",
        "type": "text",
        "extra_metadata": {
            k: v for k, v in data.items()
            if k not in ("content", "markdown_content", "text", "results")
            and isinstance(v, (str, int, float, bool, type(None)))
        },
    }]


def convert_openlegaldata(source: io.BytesIO, filename: str) -> List[Dict[str, Any]]:
    """OpenLegalData JSON dump → multiple documents."""
    data = read_json(source)
    if not data:
        return []

    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "results" in data:
        items = data["results"]
    else:
        items = [data]

    documents = []
    for case in items:
        text = ""
        if case.get("markdown_content"):
            text = case["markdown_content"]
        elif case.get("content"):
            from bs4 import BeautifulSoup
            text = BeautifulSoup(case["content"], "html.parser").get_text(" ", strip=True)
        elif case.get("text"):
            text = case["text"]

        text = clean_text(text)
        if not text or len(text) < 20:
            continue

        court_info = case.get("court", {})
        documents.append({
            "text": text,
            "section": "Content",
            "type": "text",
            "case_id": str(case.get("id", "")),
            "extra_metadata": {
                "court_name": court_info.get("name") if isinstance(court_info, dict) else None,
                "date": case.get("date"),
                "file_number": case.get("file_number"),
                "ecli": case.get("ecli"),
            },
        })

    return documents


def convert_multilegalpile_line(record: Dict) -> Optional[Dict[str, Any]]:
    """One MultiLegalPile record → segment (None if non-German)."""
    lang = record.get("language", "")
    if lang and lang.lower() not in ("de", "german", "deu"):
        return None

    text = clean_text(record.get("text", ""))
    if not text or len(text) < 20:
        return None

    return {
        "text": text,
        "section": "Content",
        "type": "text",
        "extra_metadata": {
            "jurisdiction": record.get("jurisdiction", ""),
            "mlp_type": record.get("type", ""),
        },
    }


def convert_gerdalir_line(record: Dict) -> Optional[Dict[str, Any]]:
    """One Gerdalir record → segment."""
    text = clean_text(record.get("text", ""))
    if not text or len(text) < 20:
        return None

    return {
        "text": text,
        "section": record.get("title", "Content"),
        "type": "text",
        "extra_metadata": {"original_id": record.get("id", "")},
    }


def convert_german_ler_line(record: Dict) -> Optional[Dict[str, Any]]:
    """One GermanLER record (tokens list) → segment."""
    tokens = record.get("tokens", [])
    if not tokens or not isinstance(tokens, list):
        return None

    text = " ".join(str(t) for t in tokens)
    for p in [".", ",", "!", "?", ":", ";", ")", "]"]:
        text = text.replace(f" {p}", p)
    for p in ["(", "["]:
        text = text.replace(f"{p} ", p)

    text = clean_text(text)
    if not text or len(text) < 20:
        return None

    return {"text": text, "section": "NER_Sentence", "type": "text"}


def convert_jsonl_file(source: io.BytesIO, line_converter) -> List[Dict[str, Any]]:
    """Convert JSONL using a per-line converter function."""
    lines = read_jsonl_lines(source)
    segments = []
    skipped = 0
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        seg = line_converter(record)
        if seg is not None:
            segments.append(seg)
        else:
            skipped += 1
    if skipped > 0:
        logger.debug(f"JSONL: {len(segments)} segments extracted, {skipped} lines skipped/filtered")
    return segments


# ============================================================
# Main dispatch
# ============================================================

def convert_file(file_bytes: io.BytesIO, file_path: str) -> List[Dict[str, Any]]:
    """Convert a single raw file to a list of document segments."""
    source_type = get_source_type(file_path)
    logger.info(f"Converting {file_path} (type={source_type})")

    if source_type == "docling":
        return convert_docling(file_bytes, file_path)
    elif source_type == "legacy":
        logger.warning(f"Legacy format (.doc/.ppt/.xls) not supported, skipping: {file_path}")
        return []
    elif source_type == "hf_cases":
        return convert_hf_case(file_bytes, file_path)
    elif source_type == "openlegaldata":
        return convert_openlegaldata(file_bytes, file_path)
    elif source_type == "multilegalpile":
        return convert_jsonl_file(file_bytes, convert_multilegalpile_line)
    elif source_type == "gerdalir":
        return convert_jsonl_file(file_bytes, convert_gerdalir_line)
    elif source_type == "german_ler":
        return convert_jsonl_file(file_bytes, convert_german_ler_line)
    elif source_type == "json_generic":
        return convert_hf_case(file_bytes, file_path)
    elif source_type == "jsonl_generic":
        def generic_line(record):
            for key in ("text", "content", "body", "document_text"):
                if key in record and isinstance(record[key], str) and len(record[key]) > 20:
                    return {"text": clean_text(record[key]), "section": "Content", "type": "text"}
            return None
        return convert_jsonl_file(file_bytes, generic_line)
    else:
        logger.warning(f"Unsupported file type: {file_path}")
        raise ValueError(f"Unsupported file type: {file_path}")


def build_output_documents(
    file_path: str,
    source_type: str,
    raw_segments: List[Dict[str, Any]],
    language: str,
    doc_type: str,
) -> List[Dict[str, Any]]:
    """Package raw segments into output document records."""
    logger.debug(f"Packaging {len(raw_segments)} segments from {file_path} (type={source_type}, lang={language})")
    documents = []

    if source_type in ("multilegalpile", "gerdalir", "german_ler", "jsonl_generic"):
        for idx, seg in enumerate(raw_segments):
            doc_id = hashlib.md5(f"{file_path}:{idx}".encode()).hexdigest()[:16]
            extra = seg.pop("extra_metadata", {})
            documents.append({
                "doc_id": doc_id,
                "source_file": file_path,
                "language": language,
                "doc_type": doc_type,
                "segments": [seg],
                "metadata": extra,
            })

    elif source_type == "openlegaldata":
        for idx, seg in enumerate(raw_segments):
            case_id = seg.pop("case_id", str(idx))
            extra = seg.pop("extra_metadata", {})
            doc_id = hashlib.md5(f"old:{case_id}:{file_path}".encode()).hexdigest()[:16]
            documents.append({
                "doc_id": doc_id,
                "source_file": file_path,
                "language": language,
                "doc_type": doc_type,
                "segments": [seg],
                "metadata": extra,
            })

    else:
        doc_id = hashlib.md5(file_path.encode()).hexdigest()[:16]
        extra = {}
        for seg in raw_segments:
            if "extra_metadata" in seg:
                extra.update(seg.pop("extra_metadata"))
        documents.append({
            "doc_id": doc_id,
            "source_file": file_path,
            "language": language,
            "doc_type": doc_type,
            "segments": raw_segments,
            "metadata": extra,
        })

    return documents


def release_gpu_memory():
    """Release GPU memory after conversion."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
