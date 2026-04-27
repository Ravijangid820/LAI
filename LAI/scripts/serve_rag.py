"""
Contract-review backend matching the LAI web UI contract.

Endpoints:
    GET  /health
    POST /query              — conversational; routes to RAG only when needed
    POST /upload             — ingest contract PDF/DOCX via Docling
    POST /analyze-contract   — full clause-by-clause analysis of an uploaded doc

Loads once at startup:
    - 8M+ child embeddings (~127 GB RAM)
    - Qwen3-Reranker-8B on GPU
    - Qwen2.5-7B-Instruct (or fine-tuned) on GPU
    - Reuses lai_embedding container (port 8003) for query encoding

Usage:
    cd /data/projects/lai/LAI
    CUDA_VISIBLE_DEVICES=1 .venv/bin/python scripts/serve_rag.py [--port 18000]

Per-session uploaded documents live in process-memory only (lost on
restart). For persistence, add a SQLite session table later.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

LAI_DIR = Path(__file__).resolve().parents[1]
DB      = LAI_DIR / "processed" / "pipeline_local.db"

sys.path.insert(0, str(LAI_DIR / "scripts"))
sys.path.insert(0, str(LAI_DIR / "src"))
from rag_eval import (  # type: ignore[import-not-found]
    Corpus, load_embeddings, ensure_bm25, embed_query,
    retrieve_dense, retrieve_bm25, rrf_fuse, Reranker,
    load_parent_texts, dedupe_by_parent,
)
from lai.analyzer import pipeline as analyzer_pipeline  # noqa: E402
from lai.analyzer import llm_client as analyzer_llm     # noqa: E402
from lai import persistence                              # noqa: E402

STATE: dict = {
    "corpus": None, "conn": None, "parent_text": None,
    "reranker": None,
    # Local LLM (transformers) — used if LLM_API_URL is unset
    "lm": None, "tok": None,
    # Remote LLM (vLLM container, OpenAI-compatible) — preferred when set
    "llm_api_url": None,
    "llm_model_name": None,
    # Analyzer V2 — separate vLLM endpoint, Qwen3.6-27B with thinking mode
    "analyzer_cfg": None,
    "analyzer_version_default": "1",  # "1" | "2"
    # Sessions live in SQLite via lai.persistence — see init in lifespan().
    # Process-memory cache here intentionally removed; refresh-safe across
    # both UI reloads and serve_rag restarts.
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

RAG_SYSTEM = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage ausschließlich auf "
    "Grundlage der unten bereitgestellten Rechtstexte. Zitiere bei jeder "
    "Aussage den entsprechenden Quellabschnitt (z.B. [Quelle 1]). "
    "Wenn die Frage mit den Quellen nicht eindeutig beantwortet werden "
    "kann, gib das ehrlich an."
)

CHAT_SYSTEM = (
    "Du bist ein freundlicher KI-Assistent für deutsche Anwälte, die mit "
    "Wind­energie-Verträgen arbeiten. Antworte natürlich auf Begrüßungen "
    "und Smalltalk. Bei juristischen Fragen verweise ggf. darauf, dass "
    "du Verträge analysieren kannst."
)

ROUTER_SYSTEM = (
    "Klassifiziere die folgende Nachricht als RAG oder CHAT.\n"
    "RAG: juristische Frage zu deutschem Recht, Verträgen, Gesetzen, "
    "BImSchG, BauGB, EEG, Genehmigungen, Pacht usw.\n"
    "CHAT: Begrüßung, Smalltalk, Dankeschön, Frage zur Funktionsweise "
    "des Assistenten, sonstige nicht-juristische Inhalte.\n"
    "Antworte ausschließlich mit RAG oder CHAT."
)

CONTRACT_USES_SYSTEM = (
    "Entscheide, ob die folgende Nutzerfrage sich auf den hochgeladenen "
    "Vertrag bezieht. Antworte ausschließlich mit YES oder NO."
)

CLAUSE_TYPES = [
    "Vertragsdauer", "Pacht/Vergütung", "Kündigung", "Verlängerung",
    "Rückbau", "Genehmigungsrisiko", "Haftung", "Versicherung",
    "Wegerecht/Zufahrt", "Parzellen/Flurstücke", "Vorkaufsrecht",
    "Nutzungsausschluss", "Übertragung/Sukzession", "Steuern",
    "Gerichtsstand", "Sonstiges",
]

CLAUSE_SEGMENT_SYSTEM = (
    "Du bist ein juristischer Vertragsanalyst. Zerlege den folgenden "
    "Vertragstext in einzelne Klauseln. Antworte AUSSCHLIESSLICH mit "
    "einer JSON-Liste, in der jeder Eintrag konkrete Werte enthält "
    "(KEINE Platzhalter wie 'Kurztitel'). Format:\n"
    '[\n'
    '  {"id": "1", "title": "<Echter, aussagekräftiger Titel der Klausel>", "text": "<voller Originaltext>"},\n'
    '  {"id": "2", "title": "<…>", "text": "<…>"}\n'
    ']\n'
    "Keine zusätzlichen Erklärungen, keine Markdown-Codeblöcke. "
    "Wenn der Text keine Klauselstruktur hat, gib trotzdem eine "
    "vernünftige Aufteilung zurück."
)

CLAUSE_ANALYZE_SYSTEM = (
    "Du bist ein erfahrener deutscher Rechtsanwalt. Analysiere die "
    "folgende Vertragsklausel und antworte AUSSCHLIESSLICH mit einem "
    "JSON-Objekt, das KONKRETE Werte enthält (KEINE Platzhalter, KEINE "
    "Wiederholung der erlaubten Werte, sondern genau einer davon).\n\n"
    "Erlaubte type-Werte: " + ", ".join(CLAUSE_TYPES) + ".\n\n"
    "Format (Beispielwerte zur Illustration):\n"
    '{\n'
    '  "type": "Haftung",\n'
    '  "summary": "Beschränkt die Haftung des Pächters auf Vorsatz und grobe Fahrlässigkeit.",\n'
    '  "issues": [\n'
    '    {"severity": "high", "description": "Pauschale Haftungsbeschränkung wäre nach § 309 Nr. 7 BGB unwirksam.", "recommendation": "Personenschäden ausnehmen."}\n'
    '  ],\n'
    '  "citations": ["§ 309 Nr. 7 BGB"]\n'
    "}\n\n"
    "Keine Markdown-Codeblöcke. Wenn keine Probleme: issues=[]."
)

# Minimal playbook for wind-farm Pachtverträge (German lease agreements).
# Each entry: required clause type + reason it must be present.
WIND_LEASE_PLAYBOOK = [
    ("Vertragsdauer",
     "Wind­farms haben typische Laufzeit von 25-30 Jahren; Fehlen kann "
     "zu vorzeitiger Beendigung führen."),
    ("Pacht/Vergütung",
     "Höhe und Anpassungsmechanismus müssen klar geregelt sein."),
    ("Rückbau",
     "Wer trägt nach Betriebsende die Rückbaukosten? Pflicht nach § 35 BauGB."),
    ("Genehmigungsrisiko",
     "Allokation des Risikos, falls Genehmigung versagt wird."),
    ("Wegerecht/Zufahrt",
     "Zugang zur WEA muss dauerhaft gesichert sein."),
    ("Übertragung/Sukzession",
     "Übergang der Rechte/Pflichten bei Eigentümerwechsel."),
    ("Haftung",
     "Haftungsverteilung zwischen Verpächter und Betreiber."),
    ("Vorkaufsrecht",
     "Schutz des Betreibers bei Veräußerung des Grundstücks."),
]


def build_rag_messages(question: str, sources: list[str]) -> list[dict]:
    src_block = "\n\n".join(f"[Quelle {i+1}]\n{s}" for i, s in enumerate(sources))
    user = f"Rechtstexte:\n{src_block}\n\nFrage: {question}"
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user",   "content": user},
    ]


def build_chat_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": CHAT_SYSTEM},
        {"role": "user",   "content": question},
    ]


def build_router_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user",   "content": question},
    ]


def build_contract_uses_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": CONTRACT_USES_SYSTEM},
        {"role": "user",   "content": question},
    ]


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _messages_for_remote_model(messages: list[dict], model_path: str) -> list[dict]:
    """Some models (Gemma family) reject the system role in the chat
    template. Merge system into the first user message for those."""
    if "gemma" in model_path.lower():
        sys_msgs = [m["content"] for m in messages if m["role"] == "system"]
        rest = [m for m in messages if m["role"] != "system"]
        if sys_msgs and rest and rest[0]["role"] == "user":
            rest[0] = {
                "role": "user",
                "content": "\n\n".join(sys_msgs) + "\n\n" + rest[0]["content"],
            }
            return rest
    return messages


def _strip_reasoning_trace(text: str) -> str:
    """Reasoning models (Qwen3.x) emit `<think>...</think>` before the
    final answer. Strip that prefix for the user-facing reply."""
    m = re.search(r"</think>\s*", text)
    if m:
        return text[m.end():].strip()
    return text


def llm_generate(messages: list[dict], max_new_tokens: int = 400) -> tuple[str, int, int]:
    """Two backends:

    1. Remote (LLM_API_URL set) — POST to an OpenAI-compatible /v1/chat/completions
       endpoint. Used to swap the LLM (e.g. Gemma 4 in vLLM) without
       breaking when transformers can't load the architecture.
    2. Local — load via transformers (legacy path, still default if no
       LLM_API_URL).
    """
    if STATE["llm_api_url"]:
        # Remote vLLM endpoint
        url = STATE["llm_api_url"].rstrip("/") + "/v1/chat/completions"
        msgs = _messages_for_remote_model(messages, STATE["llm_model_name"])
        body = {
            "model": STATE["llm_model_name"],
            "messages": msgs,
            "max_tokens": max_new_tokens,
            "temperature": 0.0,
            # Conversational path — thinking mode off so /query stays fast.
            # The analyzer V2 path uses its own client (lai.analyzer.llm_client)
            # which enables thinking explicitly per call.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        r = httpx.post(url, json=body, timeout=600.0)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        text = _strip_reasoning_trace(text)
        usage = data.get("usage", {}) or {}
        return text.strip(), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    # Local transformers path
    tok = STATE["tok"]; model = STATE["lm"]
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt", truncation=True, max_length=8192).to(model.device)
    prompt_tokens = int(inp.input_ids.shape[1])
    with torch.no_grad():
        out = model.generate(
            **inp, max_new_tokens=max_new_tokens, do_sample=False,
            temperature=1.0, repetition_penalty=1.05,
            pad_token_id=tok.pad_token_id,
        )
    gen_ids = out[0][inp.input_ids.shape[1]:]
    completion_tokens = int(gen_ids.shape[0])
    return tok.decode(gen_ids, skip_special_tokens=True).strip(), prompt_tokens, completion_tokens


def parse_json_lenient(s: str) -> object:
    """Strip markdown fences and parse JSON. Falls back to `{}`/`[]` on error."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # Find first { or [
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for ch in "[{":
            i = s.find(ch)
            if i >= 0:
                try:
                    return json.loads(s[i:])
                except json.JSONDecodeError:
                    continue
    return None


# ---------------------------------------------------------------------------
# Conditional-RAG router
# ---------------------------------------------------------------------------

CONVERSATIONAL = re.compile(
    r"^\s*(hi|hallo|hey|servus|moin|guten\s+(morgen|tag|abend)|"
    r"danke|thanks|ok|okay|yes|no|ja|nein|tschüss|bye|"
    r"wer\s+bist\s+du|was\s+kannst\s+du|how\s+are\s+you)\b",
    re.IGNORECASE,
)
LEGAL_KEYWORDS = re.compile(
    r"\b(BImSchG|BauGB|EEG|BGB|StGB|UStG|HGB|§|Art\.|Abs\.|"
    r"Genehmigung|Pacht|Vertrag|Kündigung|Klausel|Paragraf|"
    r"Urteil|Beschluss|Gericht|Bundesgerichtshof|BGH)",
    re.IGNORECASE,
)

# When a contract is already in session, only fire RAG retrieval if the
# question asks about EXTERNAL law/precedent — not when it just asks
# about the uploaded doc. Without this, e.g. "tell me about this
# contract" pulled in chunks from other VDR contracts and the model
# confused them with the user's upload.
EXTERNAL_LAW_REFS = re.compile(
    r"§|Art\.|\bBImSchG\b|\bBauGB\b|\bEEG\b|\bBGB\b|\bStGB\b|\bUStG\b|\bHGB\b|"
    r"\bUrteil\b|\bBeschluss\b|\bRechtsprechung\b|\bBGH\b|\bOLG\b|\bLG\b|"
    r"\bgesetzlich\b|\bvorschrift\b",
    re.IGNORECASE,
)


def needs_rag(question: str) -> bool:
    """Decide whether to retrieve. Heuristic-first; LLM classifier as fallback
    for ambiguous middle-length queries.

    Rules:
      - very short greeting/smalltalk → no RAG
      - explicit legal keywords → RAG
      - otherwise → ask the LLM
    """
    q = question.strip()
    if len(q) < 4:
        return False
    if CONVERSATIONAL.match(q):
        return False
    if LEGAL_KEYWORDS.search(q):
        return True
    if "?" in q and len(q) > 20:
        return True
    # Fallback: ask the LLM
    try:
        ans, _, _ = llm_generate(build_router_messages(q), max_new_tokens=4)
        return "RAG" in ans.upper()
    except Exception:
        # On any error, default to RAG to err on the side of helpfulness
        return True


def session_uses_contract(session_id: str | None, question: str) -> bool:
    """If a contract was uploaded in this session AND the question mentions
    'Vertrag', 'Klausel', 'Pacht', 'Rückbau' etc., or directly references it,
    we should pull the contract text into the prompt context.
    """
    if not session_id:
        return False
    sess = persistence.load_session(session_id)
    if not sess or not sess.get("contract_text"):
        return False
    q = question.lower()
    contract_keywords = ("vertrag", "klausel", "pacht", "rückbau", "kündigung",
                         "abschnitt", "paragraf", "ziffer", "absatz", "vereinbarung",
                         "im dokument", "im upload", "hochgeladen", "this contract",
                         "the contract", "the document", "the agreement")
    if any(k in q for k in contract_keywords):
        return True
    # Ambiguous — ask LLM
    try:
        ans, _, _ = llm_generate(build_contract_uses_messages(question), max_new_tokens=4)
        return "YES" in ans.upper()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Document ingestion (Docling)
# ---------------------------------------------------------------------------

_DOCLING_CONVERTER = None  # lazy


def docling_convert(file_bytes: bytes, filename: str) -> tuple[str, int, list[dict]]:
    """Convert uploaded document to markdown + structured tables.

    Plain text and markdown are decoded directly (Docling refuses .txt).
    Everything else goes through Docling (PDF, DOCX, HTML, etc.).
    Returns (markdown_text, num_pages, tables).
        tables: list of {"title", "rows": [{col_label: cell, ...}, ...]}
    """
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        try:
            return file_bytes.decode("utf-8", errors="replace"), 0, []
        except Exception as e:
            raise RuntimeError(f"Could not decode text file: {e}")

    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions, TesseractCliOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # Default RapidOCR struggles on signed/scanned German contracts —
        # umlauts and word boundaries get lost (e.g. "Reußenköge" became
        # "ReuBenkoge", entire sections dropped). Tesseract with the
        # German training data is significantly better at this. Falls
        # back to default (RapidOCR) if Tesseract isn't installed.
        try:
            pipeline_options = PdfPipelineOptions(
                do_ocr=True,
                ocr_options=TesseractCliOcrOptions(lang=["deu", "eng"]),
            )
            _DOCLING_CONVERTER = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
            print("[docling] Using Tesseract (deu+eng) for OCR.", flush=True)
        except Exception as e:
            print(f"[docling] Tesseract setup failed ({e}) — falling back to default OCR", flush=True)
            _DOCLING_CONVERTER = DocumentConverter()

    suffix_for_tmp = suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix_for_tmp, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        result = _DOCLING_CONVERTER.convert(tmp_path)
        md = result.document.export_to_markdown()
        try:
            num_pages = len(result.document.pages) if hasattr(result.document, "pages") else 0
        except Exception:
            num_pages = 0
        tables = _extract_docling_tables(result.document)
        return md, num_pages, tables
    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_docling_tables(doc) -> list[dict]:
    """Pull tables out of a Docling document into row-dicts.

    Each table → {"title": <caption-or-heading>, "rows": [{col: cell}, ...]}.
    Falls back to empty list on any failure — analyzer treats missing
    tables as "nothing to reconcile."
    """
    out: list[dict] = []
    tables = getattr(doc, "tables", None) or []
    for tbl in tables:
        try:
            df = tbl.export_to_dataframe() if hasattr(tbl, "export_to_dataframe") else None
            if df is None or df.empty:
                continue
            df = df.fillna("")
            rows = df.to_dict(orient="records")
            caption = ""
            cap_attr = getattr(tbl, "captions", None) or getattr(tbl, "caption", None)
            if cap_attr:
                if isinstance(cap_attr, list) and cap_attr:
                    cap_attr = cap_attr[0]
                caption = getattr(cap_attr, "text", str(cap_attr)) or ""
            out.append({"title": caption or "Tabelle", "rows": rows})
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Clause segmentation + analysis
# ---------------------------------------------------------------------------

def segment_clauses(contract_text: str, max_chars: int = 8000) -> list[dict]:
    """Use the LLM to split contract text into clauses. For very long
    contracts, segment in windows and concatenate.

    Window sizing is paired with the per-window output budget below.
    Rough heuristic for German legal text: ~3 chars/token in, and the
    JSON-segmented output (clauses + verbatim text) tends to be ~1.2x
    the input. So 8000 chars ≈ 2700 input tokens → ~3300 output tokens
    needed. We allocate 6000 as a comfortable headroom — a single
    truncated window silently drops every clause in it (parse fails),
    which is what produced the dropped-second-window bug on the Enercon
    Wartungsvertrag run."""
    clauses: list[dict] = []
    text = contract_text
    if len(text) <= max_chars:
        windows = [text]
    else:
        # Split on double-newline boundaries to avoid mid-clause cuts
        windows = []
        cursor = 0
        while cursor < len(text):
            end = min(cursor + max_chars, len(text))
            if end < len(text):
                # Pull back to nearest double-newline
                back = text.rfind("\n\n", cursor, end)
                if back > cursor + max_chars // 2:
                    end = back
            windows.append(text[cursor:end])
            cursor = end

    for wi, win in enumerate(windows):
        msgs = [
            {"role": "system", "content": CLAUSE_SEGMENT_SYSTEM},
            {"role": "user",   "content": win},
        ]
        out, _, _ = llm_generate(msgs, max_new_tokens=6000)
        parsed = parse_json_lenient(out)
        if isinstance(parsed, list):
            for c in parsed:
                if isinstance(c, dict) and c.get("text"):
                    cid = f"{wi}.{c.get('id', len(clauses)+1)}"
                    clauses.append({
                        "id": cid,
                        "title": c.get("title", "")[:200],
                        "text": c.get("text", ""),
                    })
    return clauses


def analyze_clause(clause_text: str) -> dict:
    """One LLM call to classify + identify issues for a clause."""
    msgs = [
        {"role": "system", "content": CLAUSE_ANALYZE_SYSTEM},
        {"role": "user",   "content": clause_text},
    ]
    out, _, _ = llm_generate(msgs, max_new_tokens=400)
    parsed = parse_json_lenient(out)
    if isinstance(parsed, dict):
        # Normalize fields
        return {
            "type": parsed.get("type", "Sonstiges"),
            "summary": parsed.get("summary", ""),
            "issues": parsed.get("issues", []) if isinstance(parsed.get("issues"), list) else [],
            "citations": parsed.get("citations", []) if isinstance(parsed.get("citations"), list) else [],
        }
    return {"type": "Sonstiges", "summary": "", "issues": [], "citations": []}


def check_playbook(clause_types_present: set[str]) -> list[dict]:
    """Compare against required clauses for wind-farm leases. Returns missing."""
    missing = []
    for required, reason in WIND_LEASE_PLAYBOOK:
        if required not in clause_types_present:
            missing.append({
                "severity": "high",
                "type": required,
                "description": f"Klausel zum Thema '{required}' fehlt im Vertrag.",
                "reason": reason,
            })
    return missing


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class QueryReq(BaseModel):
    question: str
    session_id: Optional[str] = None
    top_k: int = 3
    candidate_k: int = 30
    force_mode: Optional[str] = None  # "rag" | "chat" | None (auto)


class ChunkOut(BaseModel):
    text: str
    section: str
    law_refs: list[str]
    sources: list[str]
    similarity: float
    rerank_score: float


class TimingsOut(BaseModel):
    embed_s: float
    retrieve_s: float
    rerank_s: float
    generate_s: float
    total_s: float


class TokensOut(BaseModel):
    prompt: int
    completion: int


class QueryResp(BaseModel):
    answer: str
    chunks: list[ChunkOut]
    timings: TimingsOut
    tokens: TokensOut
    session_id: str
    mode: str  # "chat" | "rag" | "contract" | "rag+contract"


class UploadResp(BaseModel):
    session_id: str
    filename: str
    pages: int
    chunks: int
    message: str


class IssueOut(BaseModel):
    severity: str
    description: str
    recommendation: Optional[str] = None
    reason: Optional[str] = None
    type: Optional[str] = None


class ClauseOut(BaseModel):
    id: str
    title: str
    text: str
    type: str
    summary: str
    issues: list[IssueOut]
    citations: list[str]


class AnalyzeReq(BaseModel):
    session_id: str
    version: Optional[str] = None  # "1" | "2" | None (defaults to env-driven)


class AnalyzeResp(BaseModel):
    session_id: str
    filename: str
    n_clauses: int
    clauses: list[ClauseOut]
    missing_required_clauses: list[IssueOut]
    elapsed_s: float
    analyzer_version: str = "1.0"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Persistence — open/create the sessions DB before anything else so
    # endpoints can rely on it from request 1.
    db_path = LAI_DIR / "processed" / "sessions.db"
    uploads_dir = LAI_DIR / "processed" / "uploads"
    persistence.init(db_path, uploads_dir)
    print(f"[startup]   persistence: db={db_path}  uploads={uploads_dir}", flush=True)

    print("[startup] loading embeddings...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(DB), check_same_thread=False)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    corpus = load_embeddings(conn)
    ensure_bm25(corpus, conn)
    parent_text = load_parent_texts(conn)
    print(f"[startup]   embeddings + bm25 + parent_text: {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")
    print(f"[startup]   reranker: {time.time()-t0:.1f}s", flush=True)

    LLM_API_URL = os.environ.get("LLM_API_URL")
    LLM_MODEL = os.environ.get(
        "LLM_MODEL",
        "/data/projects/lai/models/qwen25-7b-legal-merged",
    )

    if LLM_API_URL:
        # Remote vLLM endpoint — verify it's reachable; no in-process load.
        print(f"[startup]   LLM: remote endpoint {LLM_API_URL} (model={LLM_MODEL})", flush=True)
        try:
            r = httpx.get(f"{LLM_API_URL.rstrip('/')}/v1/models", timeout=5)
            if r.status_code != 200:
                raise RuntimeError(f"LLM endpoint returned {r.status_code}")
        except Exception as e:
            raise RuntimeError(f"LLM endpoint {LLM_API_URL} not reachable: {e}")
        STATE.update(corpus=corpus, conn=conn, parent_text=parent_text,
                     reranker=reranker, lm=None, tok=None,
                     llm_api_url=LLM_API_URL, llm_model_name=LLM_MODEL)
    else:
        t0 = time.time()
        print(f"[startup]   LLM: loading {LLM_MODEL}", flush=True)
        tok = AutoTokenizer.from_pretrained(LLM_MODEL, trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        lm = AutoModelForCausalLM.from_pretrained(
            LLM_MODEL, torch_dtype=torch.bfloat16, device_map="cuda", trust_remote_code=True,
        ).eval()
        print(f"[startup]   LLM ready in {time.time()-t0:.1f}s", flush=True)
        STATE.update(corpus=corpus, conn=conn, parent_text=parent_text,
                     reranker=reranker, lm=lm, tok=tok,
                     llm_api_url=None, llm_model_name=LLM_MODEL)

    # Analyzer V2 config — optional. If env not set, V2 is unavailable
    # and /analyze-contract falls back to V1 regardless of `version` flag.
    analyzer_cfg = analyzer_llm.from_env()
    if analyzer_cfg is not None:
        try:
            r = httpx.get(f"{analyzer_cfg.api_url.rstrip('/')}/v1/models", timeout=5)
            if r.status_code != 200:
                raise RuntimeError(f"analyzer endpoint returned {r.status_code}")
            print(f"[startup]   analyzer LLM: {analyzer_cfg.api_url} (model={analyzer_cfg.model})", flush=True)
            STATE["analyzer_cfg"] = analyzer_cfg
            STATE["analyzer_version_default"] = os.environ.get("ANALYZER_VERSION_DEFAULT", "2")
        except Exception as e:
            print(f"[startup]   analyzer LLM unreachable ({e}) — V2 disabled, V1 default", flush=True)
    else:
        print("[startup]   analyzer LLM not configured (ANALYZER_LLM_API_URL unset) — V1 only", flush=True)

    # Warm the LLM with a dummy completion so the first user request doesn't
    # eat a 20-30s cold path (kernel autotune + first-batch JIT).
    try:
        t0 = time.time()
        llm_generate(
            [{"role": "user", "content": "Hallo"}],
            max_new_tokens=8,
        )
        print(f"[startup]   LLM warmup: {time.time()-t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[startup]   LLM warmup failed (non-fatal): {e}", flush=True)

    print("[startup] READY", flush=True)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    llm_ready = STATE["lm"] is not None or STATE["llm_api_url"] is not None
    return {
        "ok": True,
        "loaded": llm_ready,
        "llm_backend": "remote" if STATE["llm_api_url"] else "local",
        "llm_model": STATE["llm_model_name"],
        "n_sessions": persistence.count_sessions(),
    }


def _do_rag(question: str, top_k: int, candidate_k: int) -> tuple[list[ChunkOut], list[str], TimingsOut]:
    """Run hybrid+rerank retrieval, return chunks, source texts for prompt, timings."""
    corpus: Corpus = STATE["corpus"]
    parent_text = STATE["parent_text"]
    reranker = STATE["reranker"]

    t0 = time.time()
    qvec = embed_query(question, with_prefix=True)
    embed_s = time.time() - t0

    t0 = time.time()
    d_idx, _ = retrieve_dense(qvec, corpus, candidate_k)
    b_idx, _ = retrieve_bm25(question, corpus, candidate_k)
    fused = rrf_fuse([d_idx, b_idx])[:candidate_k]
    cand_idx = [c for c, _ in fused]
    retrieve_s = time.time() - t0

    t0 = time.time()
    pairs = [(question, parent_text.get(int(corpus.parent_ids[c]), "")[:2000])
             for c in cand_idx]
    rerank_scores = reranker.score(pairs)
    order = np.argsort(-np.asarray(rerank_scores))
    reranked = [cand_idx[j] for j in order]
    top_parents = dedupe_by_parent(reranked, corpus, top_k)
    rerank_s = time.time() - t0

    chunks_out: list[ChunkOut] = []
    seen = set()
    for j, idx in enumerate(reranked):
        pid = int(corpus.parent_ids[idx])
        if pid in seen or pid not in top_parents:
            continue
        seen.add(pid)
        text = parent_text.get(pid, "")[:1500]
        chunks_out.append(ChunkOut(
            text=text, section=f"Parent {pid}", law_refs=[],
            sources=["dense", "bm25"] if idx in d_idx and idx in b_idx else (
                ["dense"] if idx in d_idx else ["bm25"]),
            similarity=float(rerank_scores[order[j]]),
            rerank_score=float(rerank_scores[order[j]]),
        ))
        if len(chunks_out) >= top_k:
            break

    sources = [parent_text.get(int(p), "")[:1500] for p in top_parents]
    return chunks_out, sources, TimingsOut(
        embed_s=round(embed_s, 3),
        retrieve_s=round(retrieve_s, 3),
        rerank_s=round(rerank_s, 3),
        generate_s=0.0, total_s=0.0,
    )


@app.post("/query", response_model=QueryResp)
def query(req: QueryReq):
    if STATE["lm"] is None and STATE["llm_api_url"] is None:
        raise HTTPException(503, "Service still loading")

    sid = req.session_id or str(uuid.uuid4())
    t_total0 = time.time()

    # Decide mode
    use_contract = session_uses_contract(sid, req.question)
    if req.force_mode in ("rag", "chat"):
        use_rag = req.force_mode == "rag"
    elif use_contract:
        # Question is about the uploaded doc. Only also fire RAG when the
        # user explicitly asks for external law/precedent context — without
        # this guard, "tell me about this contract" pulled in chunks from
        # other VDR contracts and the model conflated them with the upload.
        use_rag = bool(EXTERNAL_LAW_REFS.search(req.question))
    else:
        use_rag = needs_rag(req.question)

    chunks_out: list[ChunkOut] = []
    rag_sources: list[str] = []
    timings = TimingsOut(embed_s=0.0, retrieve_s=0.0, rerank_s=0.0,
                        generate_s=0.0, total_s=0.0)

    if use_rag:
        chunks_out, rag_sources, t = _do_rag(req.question, req.top_k, req.candidate_k)
        timings.embed_s = t.embed_s
        timings.retrieve_s = t.retrieve_s
        timings.rerank_s = t.rerank_s

    # Decide mode label + build prompt
    contract_text = ""
    if use_contract:
        contract_sess = persistence.load_session(sid)
        if contract_sess and contract_sess.get("contract_text"):
            contract_text = contract_sess["contract_text"][:8000]

    if use_rag and use_contract:
        mode = "rag+contract"
        # Make the upload's authority explicit so the model doesn't conflate
        # it with retrieved chunks from OTHER contracts in the corpus.
        contract_block = (
            "[HOCHGELADENER VERTRAG — dies ist DER konkrete Vertrag, "
            "nach dem der Nutzer fragt. Behandle ihn als primäre Quelle.]\n"
            + contract_text
        )
        bg_blocks = [
            "[Hintergrund-Quelle aus dem Korpus — nur für Kontext, NICHT der Vertrag des Nutzers]\n" + s
            for s in rag_sources
        ]
        msgs = build_rag_messages(req.question, [contract_block] + bg_blocks)
    elif use_rag:
        mode = "rag"
        msgs = build_rag_messages(req.question, rag_sources)
    elif use_contract:
        mode = "contract"
        msgs = build_rag_messages(
            req.question,
            ["[HOCHGELADENER VERTRAG]\n" + contract_text],
        )
    else:
        mode = "chat"
        msgs = build_chat_messages(req.question)

    t0 = time.time()
    answer, prompt_tokens, completion_tokens = llm_generate(
        msgs, max_new_tokens=600 if (use_rag or use_contract) else 200
    )
    timings.generate_s = round(time.time() - t0, 3)
    timings.total_s = round(time.time() - t_total0, 3)

    # Persist chat messages so the UI can rehydrate the thread on refresh.
    # If there's no session row yet (e.g. chat-only, no upload), create a
    # bare one first so the messages have somewhere to attach. Without this
    # every chat that didn't follow an /upload was getting silently dropped.
    # Best-effort; never fail the request because of a write hiccup.
    try:
        if not persistence.session_exists(sid):
            persistence.save_session(sid, {
                "filename": None,         # chat-only session, no upload
                "contract_text": None,
                "n_pages": 0,
                "tables": [],
                "uploaded_at": time.time(),
                "clauses": None,
                "analysis": None,
            })
        persistence.add_message(sid, "user", req.question, mode=mode)
        persistence.add_message(sid, "assistant", answer, mode=mode)
    except Exception as e:
        print(f"[warn] failed to persist messages for {sid}: {e}", flush=True)

    return QueryResp(
        answer=answer, chunks=chunks_out, timings=timings,
        tokens=TokensOut(prompt=prompt_tokens, completion=completion_tokens),
        session_id=sid, mode=mode,
    )


@app.post("/upload", response_model=UploadResp)
async def upload(file: UploadFile = File(...), session_id: str | None = Form(None)):
    sid = session_id or str(uuid.uuid4())
    contents = await file.read()
    if len(contents) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 50 MB)")
    fname = file.filename or "uploaded.pdf"

    # Run Docling in a thread to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    try:
        md, num_pages, tables = await loop.run_in_executor(None, docling_convert, contents, fname)
    except Exception as e:
        raise HTTPException(422, f"Could not parse document: {e}")

    # Keep the original file on disk for audit / re-OCR / later re-render
    upload_ext = persistence.save_upload(sid, contents, fname)

    persistence.save_session(sid, {
        "filename": fname,
        "contract_text": md,
        "n_pages": num_pages,
        "tables": tables,    # used by analyzer V2
        "uploaded_at": time.time(),
        "clauses": None,     # filled by /analyze-contract
        "analysis": None,
        "upload_ext": upload_ext,
    })
    return UploadResp(
        session_id=sid, filename=fname, pages=num_pages,
        chunks=md.count("\n\n") + 1,  # rough paragraph count
        message=f"Vertrag eingelesen ({len(md):,} Zeichen, {num_pages} Seiten).",
    )


def _v1_issue_to_out(i: dict) -> IssueOut:
    """Coerce V2 Issue dict (severity int 1-5, has rationale) into V1 IssueOut.

    V1 IssueOut expects severity as a string ('low'|'medium'|'high'). Map
    1-2 → low, 3 → medium, 4-5 → high.
    """
    sev = i.get("severity")
    if isinstance(sev, int):
        sev_s = "low" if sev <= 2 else "medium" if sev == 3 else "high"
    else:
        sev_s = str(sev or "medium")
    desc = i.get("description") or i.get("title") or ""
    rec = i.get("suggested_redline") or i.get("recommendation")
    rationale = i.get("rationale") or i.get("reason")
    typ = i.get("type") or (i.get("title", "")[:80] if i.get("title") else None)
    return IssueOut(severity=sev_s, description=desc, recommendation=rec,
                    reason=rationale, type=typ)


def _analyze_v1(req: AnalyzeReq) -> AnalyzeResp:
    sess = persistence.load_session(req.session_id)
    if sess is None:
        raise HTTPException(404, "session_id not found")
    t0 = time.time()
    text = sess["contract_text"]

    clauses_raw = segment_clauses(text)
    clauses_out: list[ClauseOut] = []
    types_present: set[str] = set()

    for c in clauses_raw:
        analysis = analyze_clause(c["text"])
        types_present.add(analysis["type"])
        clauses_out.append(ClauseOut(
            id=c["id"],
            title=c["title"],
            text=c["text"],
            type=analysis["type"],
            summary=analysis["summary"],
            issues=[IssueOut(**i) for i in analysis["issues"] if isinstance(i, dict)],
            citations=analysis["citations"],
        ))

    missing = [IssueOut(**m) for m in check_playbook(types_present)]
    sess["clauses"] = [c.model_dump() for c in clauses_out]
    sess["analysis"] = {
        "n_clauses": len(clauses_out),
        "missing_required_clauses": [m.model_dump() for m in missing],
    }
    persistence.save_session(req.session_id, sess)
    return AnalyzeResp(
        session_id=req.session_id,
        filename=sess["filename"],
        n_clauses=len(clauses_out),
        clauses=clauses_out,
        missing_required_clauses=missing,
        elapsed_s=round(time.time() - t0, 1),
        analyzer_version="1.0",
    )


def _analyze_v2(req: AnalyzeReq) -> AnalyzeResp:
    sess = persistence.load_session(req.session_id)
    if sess is None:
        raise HTTPException(404, "session_id not found")
    cfg = STATE["analyzer_cfg"]
    t0 = time.time()

    # Reuse fast-path clause segmentation — V2 reasons over the segmentation
    # result rather than re-segmenting. Cheaper and consistent across versions.
    clauses_raw = segment_clauses(sess["contract_text"])

    result = analyzer_pipeline.analyze(
        contract_text=sess["contract_text"],
        cfg=cfg,
        clauses_input=clauses_raw,
        docling_tables=sess.get("tables") or [],
        n_pages=sess.get("n_pages") or 0,
    )

    # Project V2 result onto the existing AnalyzeResp shape — UI keeps working.
    clauses_out: list[ClauseOut] = []
    for c in result.clauses:
        clauses_out.append(ClauseOut(
            id=c.id, title=c.title, text=c.text, type=c.type,
            summary=c.summary,
            issues=[_v1_issue_to_out(i.model_dump()) for i in c.issues],
            citations=[],  # V2 carries legal_basis on each Issue instead
        ))
    missing = [_v1_issue_to_out(i.model_dump()) for i in result.missing_required_clauses]

    # Surface extraction-quality warning at the top of missing-clauses so
    # reviewers see it before the (possibly noisy) per-clause list. A real
    # high-severity flag — bad extraction is genuinely high-impact for the
    # downstream interpretation, even though no individual clause is broken.
    if result.extraction_quality and result.extraction_quality.confidence == "low":
        missing.insert(0, IssueOut(
            severity="high",
            type="Extraktionsqualität",
            description=(
                "⚠️ Niedrige Extraktionsqualität — die folgenden 'Fehlt'-Befunde "
                "sind möglicherweise falsch positiv. " + result.extraction_quality.reason
            ),
            recommendation=(
                "PDF-Extraktion prüfen (z.B. besseren OCR-Pass oder Original-Quelle nutzen), "
                "bevor fehlende Klauseln als tatsächlich fehlend behandelt werden."
            ),
            reason=None,
        ))

    # Persist the full V2 result on the session for richer UI consumption later
    sess["clauses"] = [c.model_dump() for c in clauses_out]
    sess["analysis"] = result.model_dump()
    sess["extraction_quality"] = (
        result.extraction_quality.model_dump() if result.extraction_quality else None
    )
    persistence.save_session(req.session_id, sess)

    return AnalyzeResp(
        session_id=req.session_id,
        filename=sess["filename"],
        n_clauses=len(clauses_out),
        clauses=clauses_out,
        missing_required_clauses=missing,
        elapsed_s=round(time.time() - t0, 1),
        analyzer_version="2.0",
    )


@app.post("/analyze-contract", response_model=AnalyzeResp)
def analyze_contract(req: AnalyzeReq):
    sess = persistence.load_session(req.session_id)
    if not sess:
        raise HTTPException(404, "session_id not found — upload a document first")
    if not sess.get("contract_text"):
        raise HTTPException(400, "no contract text in session")

    requested = (req.version or STATE["analyzer_version_default"]).strip()
    use_v2 = requested == "2" and STATE["analyzer_cfg"] is not None
    return _analyze_v2(req) if use_v2 else _analyze_v1(req)


@app.get("/analyze-contract/full")
def analyze_contract_full(session_id: str):
    """Return the full V2 ContractAnalysis for a session (parcels, tables,
    cross-clause findings — fields the legacy AnalyzeResp doesn't carry)."""
    sess = persistence.load_session(session_id)
    if not sess:
        raise HTTPException(404, "session_id not found")
    analysis = sess.get("analysis")
    if not analysis or analysis.get("analyzer_version") != "2.0":
        raise HTTPException(409, "no V2 analysis on this session — call /analyze-contract with version='2' first")
    return analysis


# ---------------------------------------------------------------------------
# Session listing + rehydration endpoints (UI persistence across refresh)
# ---------------------------------------------------------------------------

@app.get("/sessions")
def list_sessions(limit: int = 50):
    """Recent sessions for a sidebar — light payload, no contract_text."""
    return {"sessions": persistence.list_sessions(limit=limit)}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Full session payload for UI rehydration after a refresh.
    Returns the contract metadata + last analysis + message history."""
    sess = persistence.load_session(session_id)
    if not sess:
        raise HTTPException(404, "session_id not found")
    messages = persistence.list_messages(session_id)
    return {
        "session_id": session_id,
        "filename": sess.get("filename"),
        "n_pages": sess.get("n_pages") or 0,
        "uploaded_at": sess.get("uploaded_at"),
        "has_analysis": sess.get("analysis") is not None,
        "analyzer_version": (sess.get("analysis") or {}).get("analyzer_version"),
        "messages": messages,
    }


@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    if not persistence.session_exists(session_id):
        raise HTTPException(404, "session_id not found")
    return {"messages": persistence.list_messages(session_id)}


@app.delete("/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    if not persistence.session_exists(session_id):
        raise HTTPException(404, "session_id not found")
    persistence.delete_session(session_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    # Default to loopback only — uploaded contracts and chat history are
    # sensitive. Bind via SSH tunnel ("ssh -L 18000:localhost:18000") for
    # remote access, or override with LAI_BIND_HOST if you need to expose
    # to a trusted local network and have separate auth in front.
    p.add_argument("--host", default=os.environ.get("LAI_BIND_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=18000)
    args = p.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
