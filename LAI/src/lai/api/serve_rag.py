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
    CUDA_VISIBLE_DEVICES=1 .venv/bin/python -m lai.api.serve_rag [--port 18000]

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
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
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

# src/lai/api/serve_rag.py → parents[3] is the LAI/ project root.
LAI_DIR = Path(__file__).resolve().parents[3]
DB      = LAI_DIR / "processed" / "pipeline_local.db"

from lai.search.eval import (
    Corpus, load_embeddings, ensure_bm25, embed_query,
    retrieve_dense, retrieve_bm25, rrf_fuse, Reranker,
    load_parent_texts, dedupe_by_parent,
)
from lai.analyzer import pipeline as analyzer_pipeline
from lai.analyzer import llm_client as analyzer_llm
from lai.common.citation import validate_citations
from lai.common.exceptions import LlmError
from lai.common.llm import ChatMessage, LlmConfig, SyncLlmClient
from lai import persistence

STATE: dict = {
    "corpus": None, "conn": None, "parent_text": None,
    "reranker": None,
    # Local LLM (transformers) — used if LLM_API_URL is unset
    "lm": None, "tok": None,
    # Remote LLM (vLLM container, OpenAI-compatible) — preferred when set.
    # ``llm_api_url`` / ``llm_model_name`` are read for diagnostics
    # (``/health``); the real I/O goes through ``llm_client``, a shared
    # :class:`lai.common.llm.SyncLlmClient` that adds retry, ``<think>``
    # stripping, structured logging, and Prometheus metrics over the
    # raw ``httpx.post`` this path previously used.
    "llm_api_url": None,
    "llm_model_name": None,
    "llm_client": None,
    # Analyzer V2 — separate vLLM endpoint, Qwen3.6-27B with thinking mode
    "analyzer_cfg": None,
    "analyzer_version_default": "1",  # "1" | "2"
    # Per-session live progress for /analyze-contract V2. Keyed by
    # session_id; the analyzer pipeline updates this via its on_progress
    # callback while the long-running synchronous request executes, and
    # the UI polls /analyze-contract/progress in parallel.
    "analyzer_progress": {},
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
    "Grundlage der unten bereitgestellten Quellen.\n"
    "\n"
    "Jede Quelle trägt ein stabiles Zitations-Handle:\n"
    "  • [M-n] = Dokument aus dem Mandat (vom Nutzer hochgeladen — "
    "primäre, autoritative Quelle für DIESEN Fall).\n"
    "  • [C-n] = Auszug aus dem deutschen Rechtskorpus (Gesetze, "
    "Urteile, Kommentare — Hintergrund, NICHT der Vertrag des Nutzers).\n"
    "\n"
    "Zitiere bei JEDER inhaltlichen Aussage das passende Handle, "
    "z.B. \"§ 35 Abs. 5 BauGB verlangt eine Rückbauverpflichtung [C-3]\" "
    "oder \"§ 7 des Pachtvertrags [M-1]\". Verwende AUSSCHLIESSLICH "
    "Handles, die unten auch tatsächlich erscheinen — erfinde keine "
    "neuen. Wenn die Frage mit den Quellen nicht eindeutig beantwortet "
    "werden kann, gib das ehrlich an und markiere unbelegte Aussagen "
    "mit \"(unbelegt)\"."
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


# Conversational memory: how much prior history to inject into the LLM prompt.
# 32 messages = 16 turns of back-and-forth. Per-msg clip is 2000 chars so
# worst-case footprint stays at ~21 k tokens (32 × 666 tok), the same ceiling
# as the previous 16 × 1300-tok config — but typical chat messages are 100-500
# chars so in real use 32 messages comfortably cover ~30 turns.
#
# For long-running sessions where T1 facts (user name, project, etc.) eventually
# roll out of even this 32-msg window, see _maybe_refresh_session_metadata
# below — it pins stable facts to the system prompt so they survive forever.
HISTORY_MAX_MESSAGES = 32
MAX_HIST_CHARS_PER_MSG = 2000


# ── Pinned session metadata ────────────────────────────────────────────────
# The 32-msg rolling window above is enough for ~16 turns of detailed history,
# but stable facts stated very early in the conversation (the user's name,
# their company, the project they're working on, the signing deadline) still
# roll off in long sessions. This pinned metadata lives in the system prompt
# instead of the rolling history, so it survives forever — extracted by the
# LLM from the first few turns and refreshed every few user turns.

# How often (in user turns since last refresh) to re-extract the metadata.
# Smaller = facts get pinned faster; larger = fewer extra LLM calls. 3 user
# turns means meta is fresh by ~turn 3-4 of a new chat.
META_REFRESH_EVERY_N_USER_TURNS = 3

# How many recent messages to feed the extraction LLM. Bumped from 20 to 60
# (~30 turns of back-and-forth) because the previous cap was too tight: in a
# 28-turn conversation, the establishing T1 facts (name, role) had already
# rolled out of the lookback by the time the meta refresh fired, so the model
# couldn't extract user_name even though it was clearly stated. Sticky-merge
# below keeps any field once-extracted, so even this larger lookback can't
# cause a previously-saved fact to vanish.
META_EXTRACT_LOOKBACK = 60

META_EXTRACT_SYSTEM = (
    "You extract a short, stable profile of a user and their work context "
    "from a chat transcript. Return ONLY valid JSON. Be conservative — only "
    "include facts the user explicitly stated; never guess or invent."
)


def _format_session_meta_prefix(meta: Optional[dict]) -> str:
    """Render the pinned session metadata as a system-prompt prefix block.
    Returns '' when there's nothing useful to pin so we don't waste tokens
    on an empty header."""
    if not meta:
        return ""
    parts: list[str] = []
    if meta.get("user_name"):     parts.append(f"- Name: {meta['user_name']}")
    if meta.get("organisation"):  parts.append(f"- Organisation: {meta['organisation']}")
    if meta.get("role"):          parts.append(f"- Role: {meta['role']}")
    if meta.get("project"):       parts.append(f"- Project / matter: {meta['project']}")
    for kd in (meta.get("key_dates") or []):
        if isinstance(kd, dict) and kd.get("date"):
            label = (kd.get("what") or "").strip()
            parts.append(f"- Key date: {kd['date']}" + (f" — {label}" if label else ""))
    for kf in (meta.get("key_facts") or [])[:5]:
        if isinstance(kf, str) and kf.strip():
            parts.append(f"- {kf.strip()}")
    if not parts:
        return ""
    return (
        "[Session context — stable facts about this user and conversation; "
        "use these to address the user appropriately and apply continuity. "
        "Do NOT contradict them.]\n"
        + "\n".join(parts)
        + "\n[/Session context]\n\n"
    )


def _maybe_refresh_session_metadata(session_id: str) -> None:
    """Re-extract the pinned profile if it's missing or stale (≥N new user
    turns since last extraction). Best-effort: any failure is logged and
    swallowed — chat must never break because the meta layer hiccuped."""
    if not session_id:
        return
    try:
        msgs = persistence.list_messages(session_id)
    except Exception:
        return
    user_turn_count = sum(1 for m in msgs if m.get("role") == "user")
    if user_turn_count < 1:
        return
    existing = persistence.get_session_meta(session_id) or {}
    last_n = int(existing.get("_refreshed_at_n_user_turns", 0))
    if user_turn_count - last_n < META_REFRESH_EVERY_N_USER_TURNS and last_n > 0:
        return  # not stale enough, skip

    # Build the extraction context — last N messages, both roles, clipped.
    recent = msgs[-META_EXTRACT_LOOKBACK:]
    convo = "\n".join(
        f"[{m.get('role')}] {(m.get('content') or '')[:600]}"
        for m in recent if m.get("role") in ("user", "assistant")
    )
    prompt = (
        "Read this conversation excerpt and identify the user's stable "
        "context. Return ONLY a JSON object with these optional fields "
        "(omit any field if not stated):\n"
        '  "user_name"     – first name or full name\n'
        '  "organisation"  – company / firm / employer\n'
        '  "role"          – job title or function\n'
        '  "project"       – the project, deal, or matter they are working on\n'
        '  "key_dates"     – array of {"date": "YYYY-MM-DD or text", "what": "what this date is"}\n'
        '  "key_facts"     – array of short factual statements (max 5)\n\n'
        f"Conversation:\n{convo}\n\nJSON:"
    )
    try:
        # serve_rag has no llm_json wrapper (that lives in DDiQ-land); use
        # llm_generate directly. Note: llm_generate already passes
        # `chat_template_kwargs: {enable_thinking: False}` for /query calls,
        # so this extraction runs without thinking-mode. A separate bench
        # confirmed thinking=ON exhausts even 1500 tokens on this prompt and
        # never emits the JSON; thinking=OFF returns valid JSON in 270 tokens
        # at ~10s. 400 is generous headroom — typical output is ~270 tokens.
        msgs = [
            {"role": "system", "content": META_EXTRACT_SYSTEM},
            {"role": "user",   "content": prompt},
        ]
        raw, _, _ = llm_generate(msgs, max_new_tokens=400)
        cleaned = re.sub(r"```json\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        # If the model wrapped the JSON in prose, find the first { and last }.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
        result = json.loads(cleaned)
        if not isinstance(result, dict):
            return

        # Sticky-merge: each refresh STARTS from the previously-saved profile
        # and overlays any newly-extracted fields. This way, when a stable
        # fact like user_name was extracted at refresh #1 but the establishing
        # turn has since rolled out of the META_EXTRACT_LOOKBACK window by
        # refresh #5, the field doesn't get nuked from the saved row. Newly
        # stated info still wins (overlays old), so corrections (e.g. "the
        # signing date is actually September 30") still take effect.
        merged: dict = {k: v for k, v in existing.items() if k != "_refreshed_at_n_user_turns"}
        for k in ("user_name", "organisation", "role", "project", "key_dates", "key_facts"):
            if result.get(k):
                merged[k] = result[k]
        merged["_refreshed_at_n_user_turns"] = user_turn_count
        persistence.set_session_meta(session_id, merged)
    except Exception as e:
        print(f"[meta] session meta refresh for {session_id} failed: {e}", flush=True)


def _load_history(session_id: str | None) -> list[dict]:
    """Load prior user/assistant turns for a session in OpenAI chat format.
    Returns [] for a brand-new session or any persistence failure — chat
    must never break because the history layer hiccuped."""
    if not session_id:
        return []
    try:
        msgs = persistence.list_messages(session_id)
    except Exception:
        return []
    # Keep only the most recent window. Filter to user/assistant (drop any
    # mode-tag rows that aren't actual chat turns) and clip overlong messages.
    out: list[dict] = []
    for m in msgs[-HISTORY_MAX_MESSAGES:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "")
        if len(content) > MAX_HIST_CHARS_PER_MSG:
            content = content[:MAX_HIST_CHARS_PER_MSG] + "\n[...truncated]"
        out.append({"role": role, "content": content})
    return out


def _render_sources_block(sources: list[RetrievedSource]) -> str:
    """Render the retrieved-source block the LLM sees, with handles intact.

    Each entry opens with its stable handle on its own line so the model
    sees an unambiguous anchor before reading the chunk text — and so
    the (future) validator can resolve emitted handles back to a chunk
    by exact-string match against the prompt.
    """
    parts: list[str] = []
    for src in sources:
        header = f"[{src.cite_id}]"
        if src.label:
            header = f"{header}  {src.label}"
        parts.append(f"{header}\n{src.text}")
    return "\n\n".join(parts)


def build_rag_messages(question: str, sources: list[RetrievedSource],
                       history: list[dict] | None = None,
                       meta_prefix: str = "") -> list[dict]:
    """Build the chat-completion message list for a RAG turn.

    ``sources`` carries the retrieved chunks already tagged with stable
    [M-n] / [C-n] handles; this function only needs to render them
    deterministically so the system prompt's citation instructions
    refer to handles that actually appear in the user message.
    """
    src_block = _render_sources_block(sources)
    user = f"Quellen:\n{src_block}\n\nFrage: {question}"
    return [
        {"role": "system", "content": meta_prefix + RAG_SYSTEM},
        *(history or []),
        {"role": "user",   "content": user},
    ]


def build_chat_messages(question: str,
                        history: list[dict] | None = None,
                        meta_prefix: str = "") -> list[dict]:
    return [
        {"role": "system", "content": meta_prefix + CHAT_SYSTEM},
        *(history or []),
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


def _approx_token_count_from_chars(char_count: int) -> int:
    """Same approximation as :func:`_approx_token_count` but from a
    pre-computed character total — avoids redundant ``sum(len(...))``
    when callers already have that figure."""
    return max(1, char_count // 4)


def _approx_token_count(text: str) -> int:
    """Cheap character-based token approximation.

    Used when the remote LLM path is served via :class:`SyncLlmClient`,
    which intentionally does not surface vLLM's ``usage`` block to its
    callers (ADR 0001 keeps the client surface minimal). Tokenisers for
    German + English chat content sit around 3-4 chars/token on average;
    we use 4 so the figure errs on the conservative side. This is for
    UI display + diagnostics only — never used for billing or routing.
    """
    return _approx_token_count_from_chars(len(text))


def llm_generate(messages: list[dict], max_new_tokens: int = 400) -> tuple[str, int, int]:
    """Two backends:

    1. Remote (LLM_API_URL set) — :class:`lai.common.llm.SyncLlmClient`
       hits an OpenAI-compatible ``/v1/chat/completions`` endpoint with
       tenacity retry, ``<think>`` stripping, and Prometheus metrics.
    2. Local — load via transformers (legacy opt-in path; only used if
       ``LLM_LOCAL_PATH`` is set and ``LLM_API_URL`` is unset).

    Returns ``(text, prompt_tokens, completion_tokens)``. On the remote
    path the token counts are approximated from char length; the local
    path returns the tokenizer's exact counts.
    """
    if STATE["llm_client"] is not None:
        client: SyncLlmClient = STATE["llm_client"]
        # Gemma-family models reject the system role in the chat
        # template; if the active model is one of them, merge system
        # into the first user message before handing off to the client.
        msgs = _messages_for_remote_model(messages, STATE["llm_model_name"])
        chat_msgs = [ChatMessage(role=m["role"], content=m["content"]) for m in msgs]
        try:
            text = client.generate(chat_msgs, max_tokens=max_new_tokens, temperature=0.0)
        except LlmError as exc:
            # Surface the failure to the caller as the legacy path did
            # (an unhandled httpx.HTTPError) so the FastAPI exception
            # handler returns 5xx. Catching here just so the error chain
            # is one line shorter in the logs.
            raise RuntimeError(f"llm_generate failed: {exc}") from exc
        prompt_chars = sum(len(m["content"]) for m in msgs)
        return text.strip(), _approx_token_count_from_chars(prompt_chars), _approx_token_count(text)

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


# ── Citation handles ──────────────────────────────────────────────────────
# Every chunk that reaches the LLM carries a stable handle the model is
# instructed to cite verbatim and the UI renders as a clickable chip:
#
#   [M-n]  matter   — user-uploaded document (per-session contract for v1;
#                     per-Matter document collection in v1.1)
#   [C-n]  corpus   — chunk from the 350 GB legal corpus
#
# Day 4 of the demo plan adds a server-side validator that strips
# unresolved handles ("(unverified)" fallback); today we only emit them.


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    """One chunk handed to the LLM with a stable citation handle.

    Attributes:
        cite_id: Stable handle, e.g. ``"C-1"`` or ``"M-1"``. Must be
            unique within a single request and must match the form the
            ``RAG_SYSTEM`` prompt teaches the model to emit.
        source_kind: ``"corpus"`` (legal corpus) or ``"matter"`` (user
            upload). Drives UI rendering (different chip colour) and the
            forthcoming validator's jurisdiction checks.
        text: The chunk body that will be inlined into the prompt.
        label: Optional one-line provenance label rendered next to the
            handle inside the prompt — gives the model a human-readable
            hint of where the chunk comes from (statute / ruling / the
            user's contract). Distinct from ``cite_id`` because the UI
            never renders this; it's a prompt-side aid only.
    """

    cite_id: str
    source_kind: str
    text: str
    label: str | None = None


# Per-request handle factories. Centralised so the format stays in lock-step
# with the ``RAG_SYSTEM`` instructions above.
def _corpus_cite_id(n: int) -> str:
    return f"C-{n}"


def _matter_cite_id(n: int) -> str:
    return f"M-{n}"


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
    # ── Citation handles (Day-1 demo addition) ─────────────────────────
    # Stable identifier the LLM is instructed to cite verbatim and the
    # UI renders as a clickable chip. Defaults preserve wire compat for
    # any older client that doesn't ask for citations yet.
    cite_id: str = ""
    source_kind: str = "corpus"  # "corpus" | "matter"


class TimingsOut(BaseModel):
    embed_s: float
    retrieve_s: float
    rerank_s: float
    generate_s: float
    total_s: float


class TokensOut(BaseModel):
    prompt: int
    completion: int


class CitationValidationOut(BaseModel):
    """Structured summary of the Day-4 citation validator pass.

    Populated only on grounded-mode turns (i.e. when the prompt actually
    carried sources). ``None`` on plain-chat turns where validation
    would have nothing to compare against.

    Attributes:
        allowed: Handles that the prompt presented to the LLM, e.g.
            ``["C-1", "C-2", "M-1"]``. The UI uses this as a sanity
            check when rendering chips — a chip for a handle not in
            this list should never appear in a well-validated answer.
        emitted: Handles the model actually emitted in its answer
            (deduplicated, in first-seen order).
        fabricated: Subset of ``emitted`` that were NOT in ``allowed``
            and were therefore stripped from the answer text. The
            surrounding sentence was rewritten to end "(unbelegt)" so
            the reader is aware the claim has no source.
        sentences_flagged: Number of sentences the validator rewrote
            with the "(unbelegt)" marker. Drives a one-line badge in
            the UI ("2 unverifiable claims removed").
    """

    allowed: list[str]
    emitted: list[str]
    fabricated: list[str]
    sentences_flagged: int


class QueryResp(BaseModel):
    answer: str
    chunks: list[ChunkOut]
    timings: TimingsOut
    tokens: TokensOut
    session_id: str
    mode: str  # "chat" | "rag" | "contract" | "rag+contract"
    citation_validation: CitationValidationOut | None = None


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

    # Default to the 27B analyzer container running locally — Qwen3.6-27B
    # with thinking-mode is the only model approved for chat. The 7B legal
    # fine-tune showed identity-tracking failures past ~15 turns of complex
    # content (smoke test 2026-04-30) and is no longer used.
    #
    # To opt INTO loading a local model in-process you must set BOTH:
    #   LLM_LOCAL_PATH=/path/to/checkpoint   (explicit, no default)
    #   LLM_API_URL=                          (must be empty)
    # Anything else uses the remote endpoint.
    LLM_API_URL = os.environ.get("LLM_API_URL", "http://localhost:8005")
    LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-27b")
    LLM_LOCAL_PATH = os.environ.get("LLM_LOCAL_PATH")  # opt-in only, no default

    use_local = bool(LLM_LOCAL_PATH) and not LLM_API_URL
    if use_local:
        # Explicitly requested local-load path — verify the user actually
        # meant it and warn loudly that this isn't the default.
        t0 = time.time()
        print(f"[startup]   LLM: loading LOCAL model from {LLM_LOCAL_PATH} "
              "(opt-in via LLM_LOCAL_PATH; remote endpoint disabled)", flush=True)
        tok = AutoTokenizer.from_pretrained(LLM_LOCAL_PATH, trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        lm = AutoModelForCausalLM.from_pretrained(
            LLM_LOCAL_PATH, torch_dtype=torch.bfloat16, device_map="cuda", trust_remote_code=True,
        ).eval()
        print(f"[startup]   LLM ready in {time.time()-t0:.1f}s", flush=True)
        STATE.update(corpus=corpus, conn=conn, parent_text=parent_text,
                     reranker=reranker, lm=lm, tok=tok,
                     llm_api_url=None, llm_model_name=LLM_LOCAL_PATH,
                     llm_client=None)
    else:
        # Remote vLLM endpoint (default) — verify it's reachable; no in-process load.
        print(f"[startup]   LLM: remote endpoint {LLM_API_URL} (model={LLM_MODEL})", flush=True)
        try:
            r = httpx.get(f"{LLM_API_URL.rstrip('/')}/v1/models", timeout=5)
            if r.status_code != 200:
                raise RuntimeError(f"LLM endpoint returned {r.status_code}")
        except Exception as e:
            raise RuntimeError(
                f"LLM endpoint {LLM_API_URL} not reachable: {e}\n"
                "  → Start the analyzer container: cd Docker/llm-analyzer && docker compose up -d\n"
                "  → Or override LLM_API_URL to point at a reachable endpoint."
            )
        # Build the shared SyncLlmClient. The OpenAI-compatible base
        # URL for vLLM is the endpoint root plus ``/v1``. Other
        # SyncLlmClient knobs (retry, timeout) come from
        # ``LAI_LLM_*`` env vars if the operator wants to tune them;
        # the defaults match the previous hand-rolled ``httpx.post``
        # behaviour closely enough for drop-in compatibility.
        llm_client = SyncLlmClient(
            LlmConfig(base_url=f"{LLM_API_URL.rstrip('/')}/v1", model=LLM_MODEL),
        )
        STATE.update(corpus=corpus, conn=conn, parent_text=parent_text,
                     reranker=reranker, lm=None, tok=None,
                     llm_api_url=LLM_API_URL, llm_model_name=LLM_MODEL,
                     llm_client=llm_client)

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


def _do_rag(
    question: str, top_k: int, candidate_k: int,
) -> tuple[list[ChunkOut], list[RetrievedSource], TimingsOut]:
    """Run hybrid+rerank retrieval, return chunks, prompt-ready sources, timings.

    The second return value carries the same chunks as the first but as
    :class:`RetrievedSource` objects with stable ``[C-n]`` citation
    handles — that is what :func:`build_rag_messages` inlines into the
    prompt so the LLM sees and emits the exact handles the UI then
    renders as chips.
    """
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
    sources: list[RetrievedSource] = []
    seen: set[int] = set()
    for j, idx in enumerate(reranked):
        pid = int(corpus.parent_ids[idx])
        if pid in seen or pid not in top_parents:
            continue
        seen.add(pid)
        text = parent_text.get(pid, "")[:1500]
        cite_id = _corpus_cite_id(len(chunks_out) + 1)
        chunks_out.append(ChunkOut(
            text=text, section=f"Parent {pid}", law_refs=[],
            sources=["dense", "bm25"] if idx in d_idx and idx in b_idx else (
                ["dense"] if idx in d_idx else ["bm25"]),
            similarity=float(rerank_scores[order[j]]),
            rerank_score=float(rerank_scores[order[j]]),
            cite_id=cite_id,
            source_kind="corpus",
        ))
        sources.append(RetrievedSource(
            cite_id=cite_id,
            source_kind="corpus",
            text=text,
            label="Rechtskorpus",
        ))
        if len(chunks_out) >= top_k:
            break

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

    # Decide mode.
    #
    # Strategy doc Day 1: when an uploaded document is in session, always
    # fire RAG against the corpus too. The previous EXTERNAL_LAW_REFS
    # regex gate silently dropped retrieval for English questions
    # ("cross-check this with your database") and German conversational
    # phrasings that didn't mention §/BImSchG/BauGB — the corpus was
    # there but unused. Defaulting RAG on whenever a contract exists is
    # what turns the chat from "GPT with a German glossary" into
    # "answers grounded in both your contract and the law".
    use_contract = session_uses_contract(sid, req.question)
    if req.force_mode in ("rag", "chat"):
        use_rag = req.force_mode == "rag"
    elif use_contract:
        use_rag = True
    else:
        use_rag = needs_rag(req.question)

    corpus_chunks: list[ChunkOut] = []
    corpus_sources: list[RetrievedSource] = []
    timings = TimingsOut(embed_s=0.0, retrieve_s=0.0, rerank_s=0.0,
                        generate_s=0.0, total_s=0.0)

    if use_rag:
        corpus_chunks, corpus_sources, t = _do_rag(
            req.question, req.top_k, req.candidate_k,
        )
        timings.embed_s = t.embed_s
        timings.retrieve_s = t.retrieve_s
        timings.rerank_s = t.rerank_s

    # Pull the uploaded contract text (if any) so we can attach it as a
    # [M-1] matter source. v1 has one upload per session, so a single
    # M-1 handle is sufficient; the v1.1 Matter workspace will fan this
    # out to [M-1]..[M-n] per uploaded document.
    contract_text = ""
    contract_filename = ""
    if use_contract:
        contract_sess = persistence.load_session(sid)
        if contract_sess:
            contract_text = (contract_sess.get("contract_text") or "")[:8000]
            contract_filename = contract_sess.get("filename") or ""

    matter_sources: list[RetrievedSource] = []
    matter_chunks: list[ChunkOut] = []
    if use_contract and contract_text:
        m_cite = _matter_cite_id(1)
        matter_label = f"Hochgeladener Vertrag — {contract_filename}" if contract_filename else "Hochgeladener Vertrag"
        matter_sources.append(RetrievedSource(
            cite_id=m_cite,
            source_kind="matter",
            text=contract_text,
            label=matter_label,
        ))
        # Also surface the matter document to the UI as a chunk so the
        # frontend has a stable target to render the [M-1] chip against.
        # Excerpt only; the full text is already cached server-side in
        # the session and the side panel fetches it on click.
        matter_chunks.append(ChunkOut(
            text=contract_text[:1500],
            section=matter_label,
            law_refs=[], sources=["upload"],
            similarity=1.0, rerank_score=1.0,
            cite_id=m_cite,
            source_kind="matter",
        ))

    # Prior chat turns for the same session — gives the model conversational
    # memory across requests. Without this, every turn is stateless and
    # follow-ups like "tell me more about it" or "answer in English from now on"
    # are silently dropped. Loaded BEFORE we inject the current question so
    # the new turn isn't double-counted.
    history = _load_history(sid)

    # Pinned session metadata — stable facts (user name, project, deadlines)
    # that survive even when their original turn rolls out of the 32-msg
    # rolling window. Cheap when the session is short or when the previous
    # extraction is still fresh; the refresh fires AFTER persisting the new
    # turn (below) so the freshly stated facts make it into the next refresh.
    meta_prefix = _format_session_meta_prefix(persistence.get_session_meta(sid))

    # Matter sources come first so the LLM sees the user's own document
    # before the supporting corpus excerpts — and so the [M-n] handles
    # appear in the prompt in numerical order.
    rag_sources = matter_sources + corpus_sources

    if use_rag and use_contract:
        mode = "rag+contract"
        msgs = build_rag_messages(req.question, rag_sources,
                                  history=history, meta_prefix=meta_prefix)
    elif use_rag:
        mode = "rag"
        msgs = build_rag_messages(req.question, rag_sources,
                                  history=history, meta_prefix=meta_prefix)
    elif use_contract:
        mode = "contract"
        msgs = build_rag_messages(req.question, matter_sources,
                                  history=history, meta_prefix=meta_prefix)
    else:
        mode = "chat"
        msgs = build_chat_messages(req.question, history=history, meta_prefix=meta_prefix)

    chunks_out: list[ChunkOut] = matter_chunks + corpus_chunks

    t0 = time.time()
    answer, prompt_tokens, completion_tokens = llm_generate(
        msgs, max_new_tokens=600 if (use_rag or use_contract) else 200
    )
    timings.generate_s = round(time.time() - t0, 3)

    # Day-4 server-side citation validator. Strip any [C-n]/[M-n] handles
    # the model emitted that did NOT appear among the prompt's actual
    # sources (i.e. fabricated handles), and mark the surrounding
    # sentence ``(unbelegt)`` so the reader knows the claim has no
    # underlying source. Only runs on grounded-mode turns (chat-only
    # has no sources to validate against, so nothing to strip).
    citation_validation_out: CitationValidationOut | None = None
    if rag_sources:
        allowed_handles = {src.cite_id for src in rag_sources}
        validation = validate_citations(answer, allowed_handles)
        if validation.fabricated:
            print(
                f"[citation] session={sid} mode={mode} "
                f"fabricated={list(validation.fabricated)} "
                f"flagged_sentences={validation.sentences_flagged}",
                flush=True,
            )
        answer = validation.text
        # Attach the structured summary to the response so the UI can
        # render fabricated-count badges + sanity-check chips against
        # the allowed-handle set. ``allowed`` is sorted for stable wire
        # output; ``emitted`` / ``fabricated`` keep first-seen order.
        citation_validation_out = CitationValidationOut(
            allowed=sorted(allowed_handles),
            emitted=list(validation.emitted),
            fabricated=list(validation.fabricated),
            sentences_flagged=validation.sentences_flagged,
        )

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

    # After persisting the new turn, refresh the pinned session metadata if
    # it's stale (every N user turns). This way the facts the user JUST
    # stated are part of the extraction context, and the next /query call
    # picks up the refreshed pin. Inline because it's a single LLM call;
    # if it ever becomes hot enough to matter we can move it to a worker.
    _maybe_refresh_session_metadata(sid)

    return QueryResp(
        answer=answer, chunks=chunks_out, timings=timings,
        tokens=TokensOut(prompt=prompt_tokens, completion=completion_tokens),
        session_id=sid, mode=mode,
        citation_validation=citation_validation_out,
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
    sid = req.session_id

    # Initialize progress so the UI can poll immediately. Subsequent
    # callbacks from the pipeline overwrite this.
    STATE["analyzer_progress"][sid] = {
        "status": "running",
        "step": "segmenting",
        "current": 0,
        "total": 0,
        "elapsed_s": 0.0,
        "percent": 0.0,
        "started_at": t0,
    }

    def _on_progress(event: dict) -> None:
        # Pipeline callback — write the latest event to the shared dict.
        STATE["analyzer_progress"][sid] = {
            "status": "running" if event.get("step") != "done" else "running",
            **event,
            "started_at": t0,
        }

    try:
        # Reuse fast-path clause segmentation — V2 reasons over the segmentation
        # result rather than re-segmenting. Cheaper and consistent across versions.
        clauses_raw = segment_clauses(sess["contract_text"])

        result = analyzer_pipeline.analyze(
            contract_text=sess["contract_text"],
            cfg=cfg,
            clauses_input=clauses_raw,
            docling_tables=sess.get("tables") or [],
            n_pages=sess.get("n_pages") or 0,
            on_progress=_on_progress,
        )
    except Exception as e:
        STATE["analyzer_progress"][sid] = {
            "status": "error",
            "step": "error",
            "error": str(e)[:500],
            "elapsed_s": time.time() - t0,
            "percent": 0.0,
            "started_at": t0,
        }
        raise

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

    # Mark progress as complete so any final UI poll sees a clean done state.
    STATE["analyzer_progress"][sid] = {
        "status": "done",
        "step": "done",
        "current": len(clauses_out),
        "total": len(clauses_out),
        "elapsed_s": round(time.time() - t0, 1),
        "percent": 1.0,
        "started_at": t0,
    }

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


@app.get("/analyze-contract/progress")
def analyze_contract_progress(session_id: str):
    """Live progress for an in-flight V2 analysis. Returns the latest
    pipeline event (step, current clause / total clauses, percent
    complete, elapsed seconds). Idempotent — UI polls this every few
    seconds while the long-running /analyze-contract POST is open.
    Returns ``status: "idle"`` when no analysis has run for this session."""
    progress = STATE["analyzer_progress"].get(session_id)
    if not progress:
        return {"status": "idle", "session_id": session_id}
    return {"session_id": session_id, **progress}


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


class AppendMessageReq(BaseModel):
    role: str   # "user" | "assistant"
    content: str
    mode: Optional[str] = None  # free-form: "chat" | "rag" | "upload" | "analyze" | ...


@app.post("/sessions/{session_id}/messages")
def append_session_message(session_id: str, req: AppendMessageReq):
    """Append an assistant- or user-side message to an existing session
    so refresh-replay sees every bubble the UI showed. Used for bubbles
    the backend doesn't generate itself — upload confirmation,
    rendered /analyze-contract output, etc.

    /query already self-persists; the UI shouldn't double-save those."""
    if not persistence.session_exists(session_id):
        raise HTTPException(404, "session_id not found")
    if req.role not in ("user", "assistant"):
        raise HTTPException(400, "role must be 'user' or 'assistant'")
    if not req.content.strip():
        raise HTTPException(400, "content required")
    msg_id = persistence.add_message(session_id, req.role, req.content, mode=req.mode)
    return {"ok": True, "id": msg_id}


@app.delete("/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    if not persistence.session_exists(session_id):
        raise HTTPException(404, "session_id not found")
    persistence.delete_session(session_id)
    return {"ok": True}


class RenameReq(BaseModel):
    title: str


@app.patch("/sessions/{session_id}")
def rename_session(session_id: str, req: RenameReq):
    """Set a user-facing title for the conversation. Empty string clears
    the override and the display title falls back to filename / first
    user message / 'Untitled chat'."""
    if not persistence.update_session_title(session_id, req.title):
        raise HTTPException(404, "session_id not found")
    return {"ok": True, "title": req.title.strip()}


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
