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
import io
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

# src/lai/api/serve_rag.py → parents[3] is the LAI/ project root.
LAI_DIR = Path(__file__).resolve().parents[3]
DB = LAI_DIR / "processed" / "pipeline_local.db"

from fastapi import Depends

from lai import persistence
from lai.analyzer import llm_client as analyzer_llm
from lai.analyzer import pipeline as analyzer_pipeline
from lai.api.admin_router import build_admin_router
from lai.api.auth_router import AuthDeps, build_auth_router, register_auth_exception_handlers
from lai.api.email import EmailConfig as _EmailConfig
from lai.api.metrics import default_metrics as rag_metrics
from lai.api.share_router import build_share_router

# ── Auth subsystem (AUTH_PLAN §4.1 + §9 step 4) ─────────────────────────────
# Module-level construction of the AuthConfig / TokenIssuer /
# get_current_user dependency so route handlers can reference the dep
# at decoration time (FastAPI resolves ``Depends(...)`` arguments at
# import). A missing or weak ``LAI_AUTH_JWT_ACCESS_SECRET`` raises
# here — a clear traceback at uvicorn start beats discovering at
# first request that auth is disabled.
from lai.common import audit
from lai.common.auth import (
    AuthConfig,
    CurrentUser,
    PasswordHasher,
    TokenIssuer,
    build_get_current_user,
)
from lai.common.auth.db import create_pool as _create_auth_pool
from lai.common.citation import validate_citations
from lai.common.exceptions import LlmError
from lai.common.jurisdiction import check_jurisdiction, detect_bundesland
from lai.common.llm import ChatMessage, LlmConfig, SyncLlmClient
from lai.common.retrieval import RetrievalClient, RetrievedChunk
from lai.search.eval import (
    Reranker,
    embed_query,
    ensure_bm25_fts,
    retrieve_bm25_ids,
    rrf_fuse,
)

_auth_config: AuthConfig = AuthConfig()
_token_issuer: TokenIssuer = TokenIssuer(_auth_config)
get_current_user = build_get_current_user(_token_issuer)

STATE: dict = {
    "conn": None,
    "retrieval_client": None,
    "reranker": None,
    # Local LLM (transformers) — used if LLM_API_URL is unset
    "lm": None,
    "tok": None,
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
    'z.B. "§ 35 Abs. 5 BauGB verlangt eine Rückbauverpflichtung [C-3]" '
    'oder "§ 7 des Pachtvertrags [M-1]". Verwende AUSSCHLIESSLICH '
    "Handles, die unten auch tatsächlich erscheinen — erfinde keine "
    "neuen. Wenn die Frage mit den Quellen nicht eindeutig beantwortet "
    "werden kann, gib das ehrlich an und markiere unbelegte Aussagen "
    'mit "(unbelegt)".\n'
    "\n"
    # ── Statutory grounding (Phase 2A / S-6) ────────────────────────────
    # The behavioural shift that turns dead-ends into actionable DD output.
    # Corpus retrieval ALWAYS fires (the [C-n] statutes are in context even
    # when the matter docs don't answer), so when the uploaded documents
    # are silent on a point the model must ground the gap in the law
    # rather than replying "keine Information".
    "Gesetzliche Verankerung bei Lücken: Wenn die Mandatsdokumente "
    "([M-n]) eine Frage NICHT beantworten, aber eine Rechtsquelle "
    "([C-n]) eine einschlägige Anforderung enthält, antworte NICHT mit "
    '"keine Information". Nenne stattdessen die gesetzliche Anforderung, '
    "zitiere die Fundstelle [C-n] und weise darauf hin, dass der "
    "entsprechende Nachweis im Datenraum fehlt und beim Mandanten "
    'angefordert werden sollte. Beispiel: "§ 35 Abs. 5 S. 2 BauGB '
    "verlangt eine Rückbauverpflichtung [C-3]; ein entsprechender "
    "Nachweis ist in den vorliegenden Unterlagen nicht enthalten und "
    'sollte beim Mandanten angefordert werden." So wird aus einer '
    "Informationslücke eine konkrete Handlungsempfehlung."
)

# Answer-language behaviour.
#
# The default (``None`` — what the frontend now always sends, having
# dropped the manual DE/EN toggle) tells the model to MIRROR the
# language of the user's question: ask in German → German answer, ask
# in English → English answer. LLMs do this reliably; a toggle was
# redundant ceremony. The directive is appended on every turn because
# the base prompt is written in German and would otherwise bias every
# answer toward German regardless of how the question was phrased.
#
# In all languages we keep statute / contract / judgment quotations
# VERBATIM in the German original: the lawyer's #1 trust requirement
# (UI_GUIDE.md §7.4) is that the cited text in the answer matches the
# source preview in the side panel; translating quoted German would
# break that match.
#
# Explicit ``de`` / ``en`` codes are still honoured as a hard override
# (kept for the API contract / tests and any programmatic caller), but
# the UI no longer emits them.
_MIRROR_DIRECTIVE = (
    "\n\nAntworte in derselben Sprache, in der die Nutzerfrage gestellt "
    "ist (Deutsch auf eine deutsche Frage, Englisch auf eine englische "
    "Frage). Zitiere Gesetzestexte, Vertragsklauseln und Urteile jedoch "
    "stets wörtlich im deutschen Original — übersetze Zitate nicht. "
    "Behalte die [M-n] und [C-n] Zitations-Handles unverändert bei."
)

_LANGUAGE_DIRECTIVES: dict[str, str] = {
    "de": (
        "\n\n### ANTWORTSPRACHE (zwingend)\n"
        "Antworte AUSSCHLIESSLICH auf DEUTSCH. Behalte die [M-n] und [C-n] "
        "Zitations-Handles unverändert bei."
    ),
    "en": (
        "\n\n### RESPONSE LANGUAGE (mandatory)\n"
        "Write your ENTIRE answer in ENGLISH, regardless of the language of "
        "the sources, document names, or these instructions. The explanatory "
        "prose MUST be English. Quote statutes / contract clauses / rulings "
        "verbatim in the original German (do not translate the quote), then "
        "explain in English. Keep the [M-n] and [C-n] citation handles "
        "unchanged."
    ),
}


def _language_directive(target_language: str | None) -> str:
    """Return the system-prompt suffix that controls answer language.

    With no explicit ``target_language`` (the default now the UI toggle
    is gone) the model is told to mirror the question's language. An
    explicit ``de`` / ``en`` is honoured as a hard override; anything
    else falls back to the mirror directive.
    """
    if not target_language:
        return _MIRROR_DIRECTIVE
    return _LANGUAGE_DIRECTIVES.get(target_language.lower(), _MIRROR_DIRECTIVE)


# Function words that reliably separate English from German. Used to detect
# the question's language SERVER-SIDE and emit an explicit "answer in X"
# directive — far more reliable than asking the model to "mirror" the
# question, which loses when the surrounding prompt (German system prompt,
# German document manifest, German filenames) drowns out the cue. That is
# exactly why an English "all the pdfs uploaded?" was answered in German.
_EN_HINT_WORDS = frozenset(
    (
        "the",
        "is",
        "are",
        "was",
        "were",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "do",
        "does",
        "did",
        "you",
        "your",
        "this",
        "that",
        "these",
        "those",
        "all",
        "any",
        "please",
        "can",
        "could",
        "would",
        "should",
        "show",
        "list",
        "give",
        "tell",
        "explain",
        "uploaded",
        "upload",
        "document",
        "documents",
        "file",
        "files",
        "contract",
        "permit",
        "turbine",
        "with",
        "from",
        "about",
    )
)
_DE_HINT_WORDS = frozenset(
    (
        "der",
        "die",
        "das",
        "und",
        "oder",
        "ist",
        "sind",
        "war",
        "waren",
        "welche",
        "welcher",
        "welches",
        "wie",
        "wann",
        "wo",
        "warum",
        "wer",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "den",
        "dem",
        "des",
        "für",
        "von",
        "mit",
        "auf",
        "nicht",
        "auch",
        "sich",
        "wird",
        "werden",
        "haben",
        "hochgeladen",
        "dokument",
        "dokumente",
        "datei",
        "dateien",
        "vertrag",
        "bitte",
        "zeige",
        "liste",
        "alle",
        "habe",
        "ich",
        "bin",
        "über",
    )
)


def _detect_question_language(question: str) -> str | None:
    """Best-effort detect ``"en"`` / ``"de"`` from a question, or ``None``
    when the signal is too weak to be sure (caller then falls back to the
    soft mirror directive).

    Cheap and dependency-free: count distinctive function words on each
    side, with umlauts/ß as a strong German signal. Only commits to a
    language on a clear margin; ties / no-signal return ``None``.
    """
    q = question.lower()
    toks = re.findall(r"[a-zäöüß]+", q)
    if not toks:
        return None
    de = sum(1 for t in toks if t in _DE_HINT_WORDS)
    en = sum(1 for t in toks if t in _EN_HINT_WORDS)
    if re.search(r"[äöüß]", q):
        de += 2  # umlaut/ß is almost never English
    if de > en and de >= 1:
        return "de"
    if en > de and en >= 1:
        return "en"
    return None


def _effective_language(req_lang: str | None, question: str) -> str | None:
    """Pick the answer language: an explicit client override wins, else the
    detected question language, else ``None`` (soft mirror directive)."""
    if req_lang:
        return req_lang
    return _detect_question_language(question)


# Document-only system prompt. Used when a document is uploaded and the
# user has NOT asked to look beyond it (the default once a Matter exists).
# Hard rule: answer STRICTLY from the uploaded [M-n] documents and, when
# they are silent, say so plainly — never paper over the gap with the
# legal corpus or general knowledge. The previous behaviour (always
# firing corpus retrieval whenever a document was present) produced
# actively misleading answers: a "What lease term is agreed?" question on
# an immission-control permit was answered "20 years [C-1]" by pulling a
# lease term out of an UNRELATED corpus contract. For a lawyer that is
# worse than "not in the document" — it invents a fact about their file.
RAG_SYSTEM_DOC_ONLY = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage AUSSCHLIESSLICH auf "
    "Grundlage der unten bereitgestellten hochgeladenen Dokumente.\n"
    "\n"
    "Jede Quelle trägt ein stabiles Zitations-Handle [M-n] (= ein vom "
    "Nutzer hochgeladenes Dokument des Mandats). Zitiere bei JEDER "
    'inhaltlichen Aussage das passende Handle, z.B. "§ 7 des Vertrags '
    '[M-1]". Verwende AUSSCHLIESSLICH Handles, die unten tatsächlich '
    "erscheinen — erfinde keine neuen.\n"
    "\n"
    "Wenn die hochgeladenen Dokumente die Frage NICHT beantworten, sage "
    'das klar und unmissverständlich, z.B. "Diese Information ist in den '
    'hochgeladenen Unterlagen nicht enthalten." Greife NICHT auf externe '
    "Rechtsquellen, Gesetzeskommentare, Rechtsprechung oder allgemeines "
    "Wissen zurück und nenne KEINE Zahlen, Fristen oder Klauseln aus "
    "anderen Dokumenten — es sei denn, der Nutzer fragt ausdrücklich "
    'danach. Lieber ehrlich "nicht enthalten" als eine erfundene oder '
    "aus fremden Quellen übernommene Antwort.\n"
    "\n"
    "Formatierungsregeln: Strukturiere die Antwort mit Markdown — Überschriften "
    "(##), Aufzählungen, Fettdruck für Schlüsselbegriffe. Wenn der Nutzer "
    'tabellarische Daten verlangt ("als Tabelle", "in Tabellenform", '
    '"Übersicht", "vergleiche … in einer Tabelle") oder die Daten von Natur '
    "aus tabellarisch sind (mehrere Parzellen, mehrere Vertragspartner, mehrere "
    "Fristen, mehrere Kennzahlen), gib eine GitHub-Flavored-Markdown-Tabelle "
    "aus. Beispiel:\n"
    "| Flurstück | Eigentümer | Vermerk |\n"
    "|---|---|---|\n"
    "| 1505 | Diedrich Söhl | Vormerkung Abt. II Nr. 14 [M-1] |\n"
    "Setze NIE umschließende ``**`` Fettmarker direkt um ein Zitations-Handle "
    "([M-n] / [C-n]) — das Handle erhält bereits eine eigene Hervorhebung.\n"
    "\n"
    "Bei nummerierten Listen schreibe Nummer UND Inhalt in EINE Zeile: "
    "``1. **Überschrift** Begleitext mit [M-n].`` — NIE die Nummer auf eine "
    "eigene Zeile setzen (``1.\\nÜberschrift``), das bricht das Listen-Rendering "
    "und führt zu wiederholten Nummerierungen (1, 1, 2 statt 1, 2, 3). "
    "Zwischen zwei Listenpunkten steht eine Leerzeile."
)


# Words that signal the user explicitly wants to go BEYOND the uploaded
# document — into the legal corpus, statutes, case law, market practice
# or a comparison with other contracts. Only then do we fire corpus
# retrieval on top of the matter documents. Default (no match) stays
# document-only. Conservative on purpose: a missed keyword just means a
# document-grounded answer, which is the safe failure.
_CORPUS_REQUEST_KEYWORDS = (
    # German
    "korpus",
    "rechtsprechung",
    "gesetzeslage",
    "gesetzlich vorgeschrieben",
    "üblich",
    "marktüblich",
    "branchenüblich",
    "im allgemeinen",
    "allgemein",
    "generell",
    "vergleich",
    "verglichen",
    "andere verträge",
    "anderen verträgen",
    "vergleichbare",
    "datenbank",
    "wissensdatenbank",
    "wissensbasis",
    "außerhalb",
    "über das dokument hinaus",
    "deiner kenntnis",
    "deinem wissen",
    "rechtslage",
    "was sagt das gesetz",
    # English
    "corpus",
    "case law",
    "case-law",
    "statute",
    "statutory",
    "market standard",
    "market practice",
    "usually",
    "in general",
    "generally",
    "compare",
    "comparable",
    "other contracts",
    "knowledge base",
    "your knowledge",
    "beyond the document",
    "outside the document",
    "what does the law",
    "legal requirement",
    "required by law",
)


def wants_corpus(question: str) -> bool:
    """Did the user explicitly ask to consult the legal corpus / law /
    market practice, rather than just the uploaded document?

    Used only when a document is in the session. When ``False`` (the
    default) the answer stays document-only; when ``True`` we add the
    corpus [C-n] sources and switch to the statutory-grounding prompt.
    """
    q = question.lower()
    return any(k in q for k in _CORPUS_REQUEST_KEYWORDS)


# Statute / regulation references — a German legal citation in the
# question is a strong signal the user wants the LAW, not just the
# contract. Matches a bare "§" or any of the common abbreviations.
_STATUTE_RE = re.compile(
    r"§|\b("
    r"bimschg|bimschv|baugb|baynbo|baybo|bnatschg|eeg|enwg|uvpg|vwgo|bgb|"
    r"whg|ta\s*lärm|ta\s*laerm|avv|dibt|grundbuchordnung|gewstg|ustg"
    r")\b",
    re.IGNORECASE,
)

# Legal-doctrine / applicability terms. A contract-extraction question
# ("Wie lange läuft der Vertrag?", "Welche Pacht?") needs only the
# matter [M-n]; a legal-knowledge question ("Gilt die 10H-Regelung?",
# "Welche Rückbaupflicht besteht?", "Ist die Anlage genehmigungs-
# pflichtig?") needs the corpus [C-n]. These tokens flag the latter.
_LEGAL_KNOWLEDGE_KEYWORDS = (
    "10h",
    "10-h",
    "abstandsregel",
    "abstandsfläche",
    "abstandsflaeche",
    "mindestabstand",
    "rückbauverpflichtung",
    "rueckbauverpflichtung",
    "rückbaupflicht",
    "rueckbaupflicht",
    "genehmigungspflicht",
    "genehmigungsbedürftig",
    "genehmigungsbeduerftig",
    "genehmigungspflichtig",
    "artenschutz",
    "immissionsschutz",
    "privilegiert",
    "privilegierung",
    "außenbereich",
    "aussenbereich",
    "zulässigkeit",
    "zulaessigkeit",
    "vorgeschrieben",
    "gesetzlich",
    "rechtlich",
    "vorschrift",
    "verordnung",
    "immissionsrichtwert",
    "richtwert",
    "schallschutz",
    "naturschutz",
    "umweltverträglichkeit",
    "umweltvertraeglichkeit",
    "bestandskraft",
    "widerspruchsfrist",
    "einschlägig",
    "einschlaegig",
    # English
    "setback rule",
    "permit requirement",
    "required by law",
    "species protection",
    "noise limit",
    "decommissioning obligation",
    "legally required",
)


def is_legal_knowledge_question(question: str) -> bool:
    """Option B routing: in a contract session, should we ALSO consult
    the legal corpus ([C-n])?

    True for questions that seek the LAW / regulations / jurisdiction /
    market practice — statute references, legal doctrine, or
    applicability ("does X rule apply?", "is a permit required?"). False
    for pure contract-extraction ("what does clause X say?"), which stays
    matter-only to avoid quoting an unrelated corpus contract at a
    document-specific question (see the routing comment at the call site).
    """
    q = question.lower()
    if wants_corpus(question):
        return True
    if _STATUTE_RE.search(question):
        return True
    return any(k in q for k in _LEGAL_KNOWLEDGE_KEYWORDS)


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
    "Vertragsdauer",
    "Pacht/Vergütung",
    "Kündigung",
    "Verlängerung",
    "Rückbau",
    "Genehmigungsrisiko",
    "Haftung",
    "Versicherung",
    "Wegerecht/Zufahrt",
    "Parzellen/Flurstücke",
    "Vorkaufsrecht",
    "Nutzungsausschluss",
    "Übertragung/Sukzession",
    "Steuern",
    "Gerichtsstand",
    "Sonstiges",
]

CLAUSE_SEGMENT_SYSTEM = (
    "Du bist ein juristischer Vertragsanalyst. Zerlege den folgenden "
    "Vertragstext in einzelne Klauseln. Antworte AUSSCHLIESSLICH mit "
    "einer JSON-Liste, in der jeder Eintrag konkrete Werte enthält "
    "(KEINE Platzhalter wie 'Kurztitel'). Format:\n"
    "[\n"
    '  {"id": "1", "title": "<Echter, aussagekräftiger Titel der Klausel>", "text": "<voller Originaltext>"},\n'
    '  {"id": "2", "title": "<…>", "text": "<…>"}\n'
    "]\n"
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
    "{\n"
    '  "type": "Haftung",\n'
    '  "summary": "Beschränkt die Haftung des Pächters auf Vorsatz und grobe Fahrlässigkeit.",\n'
    '  "issues": [\n'
    '    {"severity": "high", "description": "Pauschale Haftungsbeschränkung wäre nach § 309 Nr. 7 BGB unwirksam.", "recommendation": "Personenschäden ausnehmen."}\n'
    "  ],\n"
    '  "citations": ["§ 309 Nr. 7 BGB"]\n'
    "}\n\n"
    "Keine Markdown-Codeblöcke. Wenn keine Probleme: issues=[]."
)

# Minimal playbook for wind-farm Pachtverträge (German lease agreements).
# Each entry: required clause type + reason it must be present.
WIND_LEASE_PLAYBOOK = [
    (
        "Vertragsdauer",
        "Wind­farms haben typische Laufzeit von 25-30 Jahren; Fehlen kann zu vorzeitiger Beendigung führen.",
    ),
    ("Pacht/Vergütung", "Höhe und Anpassungsmechanismus müssen klar geregelt sein."),
    ("Rückbau", "Wer trägt nach Betriebsende die Rückbaukosten? Pflicht nach § 35 BauGB."),
    ("Genehmigungsrisiko", "Allokation des Risikos, falls Genehmigung versagt wird."),
    ("Wegerecht/Zufahrt", "Zugang zur WEA muss dauerhaft gesichert sein."),
    ("Übertragung/Sukzession", "Übergang der Rechte/Pflichten bei Eigentümerwechsel."),
    ("Haftung", "Haftungsverteilung zwischen Verpächter und Betreiber."),
    ("Vorkaufsrecht", "Schutz des Betreibers bei Veräußerung des Grundstücks."),
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


def _format_session_meta_prefix(meta: dict | None) -> str:
    """Render the pinned session metadata as a system-prompt prefix block.
    Returns '' when there's nothing useful to pin so we don't waste tokens
    on an empty header."""
    if not meta:
        return ""
    parts: list[str] = []
    if meta.get("user_name"):
        parts.append(f"- Name: {meta['user_name']}")
    if meta.get("organisation"):
        parts.append(f"- Organisation: {meta['organisation']}")
    if meta.get("role"):
        parts.append(f"- Role: {meta['role']}")
    if meta.get("project"):
        parts.append(f"- Project / matter: {meta['project']}")
    for kd in meta.get("key_dates") or []:
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
        "Do NOT contradict them.]\n" + "\n".join(parts) + "\n[/Session context]\n\n"
    )


def _maybe_refresh_session_metadata(session_id: str, user_id: str | None = None) -> None:
    """Re-extract the pinned profile if it's missing or stale (≥N new user
    turns since last extraction). Best-effort: any failure is logged and
    swallowed — chat must never break because the meta layer hiccuped.

    Phase B: ``org_id`` scopes every persistence call to the caller's firm
    so the meta refresh cannot read or write rows owned by a different
    firm (and so meta refresh works for any firm member, not just the
    session's original creator).
    """
    if not session_id:
        return
    try:
        msgs = persistence.list_messages(session_id, user_id=user_id)
    except Exception:
        return
    user_turn_count = sum(1 for m in msgs if m.get("role") == "user")
    if user_turn_count < 1:
        return
    existing = persistence.get_session_meta(session_id, user_id=user_id) or {}
    last_n = int(existing.get("_refreshed_at_n_user_turns", 0))
    if user_turn_count - last_n < META_REFRESH_EVERY_N_USER_TURNS and last_n > 0:
        return  # not stale enough, skip

    # Build the extraction context — last N messages, both roles, clipped.
    recent = msgs[-META_EXTRACT_LOOKBACK:]
    convo = "\n".join(
        f"[{m.get('role')}] {(m.get('content') or '')[:600]}" for m in recent if m.get("role") in ("user", "assistant")
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
            {"role": "user", "content": prompt},
        ]
        raw, _, _ = llm_generate(msgs, max_new_tokens=400)
        cleaned = re.sub(r"```json\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        # If the model wrapped the JSON in prose, find the first { and last }.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
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
        persistence.set_session_meta(session_id, merged, user_id=user_id)
    except Exception as e:
        print(f"[meta] session meta refresh for {session_id} failed: {e}", flush=True)


def _load_history(session_id: str | None, user_id: str | None = None) -> list[dict]:
    """Load prior user/assistant turns for a session in OpenAI chat format.
    Returns [] for a brand-new session or any persistence failure — chat
    must never break because the history layer hiccuped.

    Phase B: filters by firm-tenant key — every member of the firm sees
    the same prior history (so two associates can pick up each other's
    threads).
    """
    if not session_id:
        return []
    try:
        msgs = persistence.list_messages(session_id, user_id=user_id)
    except Exception:
        return []
    # Keep only the most recent window. Filter to user/assistant (drop any
    # mode-tag rows that aren't actual chat turns) and clip overlong messages.
    out: list[dict] = []
    for m in msgs[-HISTORY_MAX_MESSAGES:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
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


def build_rag_messages(
    question: str,
    sources: list[RetrievedSource],
    history: list[dict] | None = None,
    meta_prefix: str = "",
    target_language: str | None = None,
    system: str = RAG_SYSTEM,
) -> list[dict]:
    """Build the chat-completion message list for a RAG turn.

    ``sources`` carries the retrieved chunks already tagged with stable
    [M-n] / [C-n] handles; this function only needs to render them
    deterministically so the system prompt's citation instructions
    refer to handles that actually appear in the user message.

    ``target_language`` (``None`` / ``"de"`` / ``"en"``) appends a
    language-switch directive — see :func:`_language_directive`.

    ``system`` selects the system prompt: :data:`RAG_SYSTEM` (corpus +
    matter, with statutory grounding) or :data:`RAG_SYSTEM_DOC_ONLY`
    (uploaded documents only — the default once a Matter exists and the
    user hasn't asked to look beyond it).
    """
    src_block = _render_sources_block(sources)
    user = f"Quellen:\n{src_block}\n\nFrage: {question}"
    return [
        {"role": "system", "content": meta_prefix + system + _language_directive(target_language)},
        *(history or []),
        {"role": "user", "content": user},
    ]


def build_chat_messages(
    question: str, history: list[dict] | None = None, meta_prefix: str = "", target_language: str | None = None
) -> list[dict]:
    return [
        {"role": "system", "content": meta_prefix + CHAT_SYSTEM + _language_directive(target_language)},
        *(history or []),
        {"role": "user", "content": question},
    ]


def build_router_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": question},
    ]


def build_contract_uses_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": CONTRACT_USES_SYSTEM},
        {"role": "user", "content": question},
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
        return text[m.end() :].strip()
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
    tok = STATE["tok"]
    model = STATE["lm"]
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt", truncation=True, max_length=8192).to(model.device)
    prompt_tokens = int(inp.input_ids.shape[1])
    with torch.no_grad():
        out = model.generate(
            **inp,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=1.0,
            repetition_penalty=1.05,
            pad_token_id=tok.pad_token_id,
        )
    gen_ids = out[0][inp.input_ids.shape[1] :]
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
# UI / navigation / meta-AI questions that look like real questions but
# carry no legal content. Surfaced by the 2026-06-01 ks/as production
# audit ("was kann ich hier tun?" routed to RAG → answered with random
# fraud-forum content). Each pattern is intentionally specific so it
# can't eat a legal query — see tests/unit/api/test_router_ui_meta.py
# for the gold-RAG safety check against bimschg_50.jsonl.
UI_META = re.compile(
    r"^\s*("
    # German UI / navigation
    r"was\s+kann\s+ich\s+(hier|tun|machen|alles\s+(hier\s+)?tun)|"
    r"wie\s+funktioniert\s+(das|es|dieser|diese|der|die)\b|"
    r"wie\s+(geht|nutze)\s+ich\s+das|"
    # German meta about the AI
    r"(gehst|verstehst|liest|erkennst|denkst)\s+du\s+(semantisch|wirklich|das|die\s+(dokumente|frage|aufgabe|texte))|"
    r"bist\s+du\s+(online|wach|da)|"
    r"wer\s+hat\s+dich\s+(gebaut|gemacht|trainiert)|"
    # English mirrors
    r"what\s+can\s+i\s+do\s+(here|with\s+this)|"
    r"how\s+does\s+this\s+(work|function)|"
    r"do\s+you\s+(understand|read|know|see)\s+(this|that|the\s+(documents?|files?|context|question))"
    r")\b",
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


def _org_or_none(user: CurrentUser) -> str | None:
    """Phase B scope helper — stringified ``org_id`` for the persistence /
    DDiQ visibility filters, or ``None`` for an org-less user (open signup
    landing state) whose reads naturally return empty everywhere."""
    return str(user.org_id) if user.org_id else None


def _all_matter_handles(
    sid: str,
    uid: str | None,
    focus_doc_indexes: list[int] | None = None,
) -> set[str]:
    """Every uploaded matter document's ``[M-n]`` handle for this session.

    The citation validator's ``allowed`` set is otherwise built from the
    *retrieved* sources only (``matter_sources``), but the matter manifest
    prefix (:func:`_matter_manifest_prefix`) explicitly invites the model to
    cite ANY uploaded document by its ``[M-n]`` — searchable and citeable —
    even when this turn retrieved no passage from it (e.g. a "summarise every
    document" turn that out-runs the retrieval top-K). A handle that maps to a
    real uploaded document is therefore **never a fabrication**; widening
    ``allowed`` with this set stops the validator from stripping such handles
    and mislabelling correct, real-document citations ``(unbelegt)``.

    Baseline chunks for these documents already ride in ``chunks_out`` (see
    ``_matter_pgvector_context``), so the citation panel resolves them too.

    Best-effort: a lookup failure must never break the turn — returns an empty
    set so validation simply falls back to the retrieved-sources behaviour.
    Per-turn focus: when ``focus_doc_indexes`` is set, the allowed set
    narrows to those handles — the validator will strip any other [M-n]
    the model emits as "fabricated", which is what we want for a
    "this-document" turn.
    """
    try:
        docs = persistence.list_matter_documents(sid, user_id=uid)
        if focus_doc_indexes:
            in_scope = set(focus_doc_indexes)
            docs = [d for d in docs if d["doc_index"] in in_scope]
        return {_matter_cite_id(d["doc_index"]) for d in docs}
    except Exception as exc:
        print(f"[citation] matter-handle widen failed sid={sid}: {exc}", flush=True)
        return set()


def _split_into_pages(doc_text: str) -> list[tuple[int | None, str]]:
    """Split ingestion text on ``<!-- Seite N -->`` markers into
    ``[(page, text)]``. Text without markers (docling / legacy uploads)
    returns a single ``(None, whole_text)`` entry."""
    markers = list(_PAGE_MARKER_RE.finditer(doc_text))
    if not markers:
        return [(None, doc_text)]
    pages: list[tuple[int | None, str]] = []
    for i, m in enumerate(markers):
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(doc_text)
        pages.append((int(m.group(1)), doc_text[start:end].strip()))
    return pages


def _split_into_passages(doc_text: str) -> list[tuple[int | None, str]]:
    """Page-tagged paragraph-ish passages: ``[(page, passage_text)]``.

    Splits each page on blank lines. Tiny fragments (table rules, stray
    glyphs) are dropped; if a page yields nothing substantial its whole
    text is kept so no content is lost."""
    out: list[tuple[int | None, str]] = []
    for page, page_text in _split_into_pages(doc_text):
        kept_any = False
        for para in re.split(r"\n\s*\n", page_text):
            para = para.strip()
            if len(para) >= 40:
                out.append((page, para))
                kept_any = True
        if not kept_any and page_text.strip():
            out.append((page, page_text.strip()))
    return out or [(None, doc_text.strip())]


_WORD_RE = re.compile(r"\w{3,}", re.UNICODE)


def _score_passage_lexical(q_tokens: set[str], passage: str) -> int:
    """Lexical fallback used only when the embedding service is down:
    distinct query content-words present in the passage. Note this scores
    0 across languages (English question vs German doc) — which is exactly
    why the primary ranker is embedding-based."""
    if not q_tokens:
        return 0
    return len(q_tokens & {t.lower() for t in _WORD_RE.findall(passage)})


# Cap how many passages we embed per document per turn (keeps the
# query-time embedding batch bounded for very long uploads).
_MAX_PASSAGES_RANKED = 60


def _rank_passages(
    question: str,
    passages: list[tuple[int | None, str]],
) -> list[tuple[float, int, int | None, str]]:
    """Rank ``passages`` ([(page, text)]) by relevance to ``question``.

    Primary: semantic similarity via the multilingual embedding service —
    this is what lets an ENGLISH question ("which turbine type?") surface
    the right GERMAN passage ("neuer Anlagentyp: Enercon E-70 …"), which a
    keyword match never could. Falls back to lexical, then document order,
    if the embedding service is unavailable. Returns
    ``[(score, idx, page, text)]`` sorted best-first.
    """
    capped = passages[:_MAX_PASSAGES_RANKED]
    if not question.strip() or not capped:
        return [(0.0, i, p, t) for i, (p, t) in enumerate(capped)]
    try:
        from lai.search.eval import _get_embedding_client, embed_query

        qvec = embed_query(question, with_prefix=True)
        results = _get_embedding_client().embed([t for _, t in capped])
        scored: list[tuple[float, int, int | None, str]] = []
        for i, ((page, t), r) in enumerate(zip(capped, results, strict=False)):
            pv = np.asarray(r.embedding, dtype=np.float32)
            n = np.linalg.norm(pv)
            if n > 0:
                pv = pv / n
            scored.append((float(qvec @ pv), i, page, t))
        return sorted(scored, key=lambda x: (-x[0], x[1]))
    except Exception as e:
        print(f"[matter] embedding passage-rank failed ({e}); lexical fallback", flush=True)
        q_tokens = {t.lower() for t in _WORD_RE.findall(question)}
        scored = [(float(_score_passage_lexical(q_tokens, t)), i, page, t) for i, (page, t) in enumerate(capped)]
        return sorted(scored, key=lambda x: (-x[0], x[1]))


def _select_relevant_passages(
    question: str,
    doc_text: str,
    budget: int,
) -> tuple[str, int | None, str]:
    """Choose what the model reads and what the citation panel shows.

    Returns ``(prompt_text, best_page, display_text)``:
      * ``prompt_text`` — the document for the LLM. If it fits the budget
        it's included WHOLE (page-tagged) so the answer is never starved
        of context; only over-budget documents are reduced to their
        most-relevant passages.
      * ``best_page`` — page of the single most-relevant passage, so the
        citation panel scrolls the PDF preview to the cited page.
      * ``display_text`` — the top few most-relevant passages (page-tagged)
        for the citation panel, i.e. "the lines this answer rests on".
    """
    passages = _split_into_passages(doc_text)
    if not passages:
        head = doc_text[:budget]
        return head, None, head

    def tag(page: int | None, t: str) -> str:
        return f"(S. {page}) {t}" if page else t

    full_tagged = "\n\n".join(tag(p, t) for p, t in passages)
    ranked = _rank_passages(question, passages)
    best_page = ranked[0][2] if ranked else None

    if len(full_tagged) <= budget:
        prompt_text = full_tagged
    else:
        selected: list[tuple[int, int | None, str]] = []
        total = 0
        for _score, i, page, t in ranked:
            if total >= budget:
                break
            snippet = t[:budget] if len(t) > budget else t
            selected.append((i, page, snippet))
            total += len(snippet)
        selected.sort(key=lambda x: x[0])
        prompt_text = "\n\n".join(tag(p, t) for _, p, t in selected)

    display = "\n\n".join(tag(page, t) for _s, _i, page, t in ranked[:3])
    return prompt_text, best_page, display


# Matter retrieval tuning. ``candidate_k`` passages are pulled by dense
# KNN, reranked, then the top ``final_k`` are kept and grouped by document.
# Bounded regardless of data-room size — this is what lets a Matter scale
# from 3 PDFs to a 5000-document VDR with the same prompt budget.
_MATTER_CANDIDATE_K = 40
_MATTER_FINAL_K = 12


def _matter_adaptive_sizes(n_docs: int) -> tuple[int, int]:
    """Pick ``(candidate_k, fairness_cap)`` based on session doc count.

    Why this exists: at the original fixed ``candidate_k=40``, a session
    with 5000 docs × ~10 chunks each = 50K chunks → the top-40 candidates
    sample 0.08% of the matter, leaving most docs unreachable by retrieval
    regardless of how cleverly we select from the pool. The fairness pass
    can only redistribute chunks that made it INTO the pool. So as the
    matter grows we need a bigger pool, and a bigger per-doc cap.

    Latency budget: the cross-encoder reranker (Qwen3-Reranker-8B on GPU)
    runs at ~10ms per pair. 250 pairs = ~2.5s — tolerable as the heaviest
    item on the first-token path. We don't grow beyond that.

    Context budget: each surfaced doc adds ~250-500 tokens of chunk text.
    At ``fairness_cap=50`` that's ~25K tokens of matter context, plus
    manifest + corpus + question + system prompt. Stays inside the 32K
    window; above 50 we'd start squeezing other parts of the prompt.

    Returns ``(candidate_k, fairness_cap)``.
    """
    if n_docs <= 12:
        # Small matter: the legacy budget is already enough to cover
        # every doc with chunks to spare. Don't pay the rerank cost.
        return (40, 12)
    if n_docs <= 30:
        return (60, 24)
    if n_docs <= 100:
        return (120, 36)
    if n_docs <= 500:
        return (180, 48)
    # 500+ docs (true VDR scale): rerank up to 250 pairs, surface up to
    # 50 docs in the prompt. The remaining docs are accurately reported
    # as "not surveyed for this question" in the manifest so the LLM
    # tells the user the truth instead of pretending it covered them.
    return (250, 50)


def _matter_pgvector_context(
    sid: str,
    question: str,
    candidate_k: int = _MATTER_CANDIDATE_K,
    final_k: int = _MATTER_FINAL_K,
    focus_doc_indexes: list[int] | None = None,
) -> tuple[list[RetrievedSource], list[ChunkOut], str] | None:
    """Scalable matter retrieval: dense KNN over the per-session pgvector
    index + rerank, grouped back into one ``[M-doc_index]`` source per
    document so a data room of any size yields a bounded, citation-ready
    prompt.

    When ``focus_doc_indexes`` is set, post-retrieval hits are restricted
    to those doc indexes AND the baseline-chunk fill is also narrowed —
    so a "this document" turn doesn't silently pull in prior project
    docs the user didn't ask about. ``None``/empty ⇒ full session in
    scope (the prior behaviour).

    Returns ``None`` to signal "no indexed passages for this session" so
    the caller falls back to the legacy whole-document path (sessions
    uploaded before per-Matter indexing existed).
    """
    rc = STATE.get("retrieval_client")
    if rc is None or not question.strip():
        return None
    # Adaptive sizing: in-scope doc count drives both the candidate pool
    # and the per-doc fairness cap. Small matters keep tight prompts and
    # fast latency; VDR-scale matters (hundreds-to-thousands of docs)
    # get a proportionally larger pool so the dense KNN can actually
    # reach docs at the tail of the relevance ranking. Only pay this
    # cost when the caller didn't pin candidate_k themselves.
    n_docs_in_scope = 0
    try:
        all_docs = persistence.list_matter_documents(sid)
        if focus_doc_indexes:
            fset = set(focus_doc_indexes)
            n_docs_in_scope = sum(1 for d in all_docs if d["doc_index"] in fset)
        else:
            n_docs_in_scope = sum(1 for d in all_docs if d.get("status") != "failed")
    except Exception:
        n_docs_in_scope = 0
    adaptive_candidate_k, adaptive_cap = _matter_adaptive_sizes(n_docs_in_scope)
    # Caller overrides win (default is the module constant; anything else
    # came from a deliberate caller, e.g. a thin-context test path).
    effective_candidate_k = candidate_k if candidate_k != _MATTER_CANDIDATE_K else adaptive_candidate_k
    try:
        qvec = embed_query(question, with_prefix=True)
        hits = rc.matter_dense_search(sid, qvec, top_k=effective_candidate_k)
    except Exception as e:
        print(f"[matter] pgvector search failed ({e}); using fallback", flush=True)
        return None
    if not hits:
        return None

    # Per-turn focus filter: drop hits that aren't in the focused set.
    # Done AFTER the dense search (cheap) so the focused docs still come
    # back with their best passages; if none of the focused docs had a
    # passage that survived the search, the baseline-chunk fill below
    # still presents them to the panel.
    if focus_doc_indexes:
        focus_set = set(focus_doc_indexes)
        hits = [h for h in hits if h.doc_index in focus_set]
        if not hits:
            # No focused doc had a retrieved passage. Fall back to the
            # baseline path (where the focus filter is applied below).
            hits = []

    # Rerank (query, passage) pairs — same cross-encoder the corpus uses.
    reranker = STATE.get("reranker")
    pairs = [(question, h.content[:2000]) for h in hits]
    try:
        scores = reranker.score(pairs) if (reranker is not None and pairs) else []
        if scores:
            order = list(np.argsort(-np.asarray(scores)))
            hits = [hits[j] for j in order]
            rscores = [float(scores[j]) for j in order]
        else:
            rscores = [h.similarity for h in hits]
    except Exception as exc:
        print(f"[matter] rerank failed ({exc}); dense order", flush=True)
        rscores = [h.similarity for h in hits]
    # ── Per-document fairness selection ─────────────────────────────────
    # The OLD behaviour was ``hits = hits[:final_k]`` — a flat global cut.
    # In a session with many docs that all have hits, the highest-scoring
    # ~4-5 docs would dominate the top-12 and the rest never made it into
    # the prompt. The LLM then honestly told the user "Dok 6, 8, 9, 16,
    # 17… are left off from the provided source text" (real bug 2026-05-24,
    # 20-doc Lamstedt session — the chat handle was right, the retrieval
    # didn't feed it). Since the manifest still listed all docs, the user
    # was rightly confused: "I uploaded these — why can't you answer?"
    #
    # New behaviour: every doc that has at least one rerank hit gets at
    # least one chunk in the final set, up to a generous per-session cap.
    # Phase 1 picks the best-scoring chunk from each doc (in order of that
    # doc's top score). Phase 2 fills any remaining budget with the
    # next-best chunks regardless of doc. Total budget is the MAX of the
    # legacy ``final_k`` (12) and the number of docs with hits, capped at
    # 24 so a 100-doc session can't blow the prompt budget. Within these
    # bounds an N-doc session yields ~N relevant snippets — bounded,
    # citation-ready, and no doc is silently dropped.
    if hits:
        # Build doc → list of indexes-into-(hits, rscores), preserving the
        # reranker's order so [0] is each doc's best-scoring chunk.
        by_doc_pre: dict[int, list[int]] = {}
        for idx, h in enumerate(hits):
            by_doc_pre.setdefault(h.doc_index, []).append(idx)

        # Per-doc-fairness upper bound, driven by the adaptive sizing
        # helper. Small matters use a tight cap (12); VDR-scale matters
        # grow to ~50, which is the most we can fit without crowding out
        # corpus context. The pool size grew in lockstep above so up to
        # ``cap`` distinct docs can plausibly appear in the candidates.
        fairness_cap = adaptive_cap
        target_budget = max(final_k, min(len(by_doc_pre), fairness_cap))

        # Phase 1: at least one chunk per doc, doc order = best score.
        docs_by_score = sorted(
            by_doc_pre.keys(),
            key=lambda di: -rscores[by_doc_pre[di][0]],
        )
        picked: list[int] = []
        picked_set: set[int] = set()
        for di in docs_by_score:
            if len(picked) >= target_budget:
                break
            best_idx = by_doc_pre[di][0]
            picked.append(best_idx)
            picked_set.add(best_idx)

        # Phase 2: fill remaining slots with the next-best chunks (any doc).
        # This lets a high-relevance doc contribute more than one chunk
        # once everyone has had a turn.
        if len(picked) < target_budget:
            for idx in range(len(hits)):
                if idx in picked_set:
                    continue
                picked.append(idx)
                picked_set.add(idx)
                if len(picked) >= target_budget:
                    break

        # Re-order by original rerank rank so "best overall" reads first.
        picked.sort()
        hits = [hits[i] for i in picked]
        rscores = [rscores[i] for i in picked]

    # Group the retrieved passages back under their document so the handle
    # stays ``[M-doc_index]`` — consistent with the document list and the
    # citation panel's ``fetchMatterDocument(n)`` mapping.
    def _tag(pg: int | None, t: str) -> str:
        return f"(S. {pg}) {t}" if pg else t

    by_doc: dict[int, dict] = {}
    for rank, (h, rs) in enumerate(zip(hits, rscores, strict=False)):
        info = by_doc.setdefault(
            h.doc_index,
            {
                "filename": h.filename,
                "best_rank": rank,
                "best_sim": h.similarity,
                "best_rerank": rs,
                "best_page": h.page,
                "passages": [],
            },
        )
        info["passages"].append(_tag(h.page, h.content))

    matter_sources: list[RetrievedSource] = []
    matter_chunks: list[ChunkOut] = []
    combined_parts: list[str] = []
    for doc_index in sorted(by_doc, key=lambda di: by_doc[di]["best_rank"]):
        info = by_doc[doc_index]
        cite = _matter_cite_id(doc_index)
        prompt_text = "\n\n".join(info["passages"])
        label = f"[M-{doc_index}] {info['filename']}" if info["filename"] else f"Dokument M-{doc_index}"
        matter_sources.append(
            RetrievedSource(
                cite_id=cite,
                source_kind="matter",
                text=prompt_text,
                label=label,
            )
        )
        matter_chunks.append(
            ChunkOut(
                text=prompt_text[:2500],
                section=info["filename"] or label,
                law_refs=[],
                sources=["upload"],
                similarity=info["best_sim"],
                rerank_score=info["best_rerank"],
                cite_id=cite,
                source_kind="matter",
                page=info["best_page"],
            )
        )
        combined_parts.append(prompt_text)

    # Focus-mode full-text fallback: when ``focus_doc_indexes`` is set
    # and a focused doc didn't surface any chunks in the dense top-K
    # (because the question's embedding ranked OTHER docs in this matter
    # higher globally), pull the focused doc's full ``doc_text`` straight
    # from storage and add it as a real prompt source.
    #
    # Why this matters: the user explicitly attached a doc and said
    # "what's in this?" — they expect the model to see THAT doc's
    # content. Without this fallback, a 26-doc matter where the question
    # doesn't match the focused doc's vocabulary leaves the LLM with
    # zero focused content; it correctly refuses ("the content of this
    # image is not contained in the provided text excerpts"). This is
    # exactly the bug surfaced when a user attached a WhatsApp screenshot
    # and asked about it — the dense search returned 30 chunks of legal
    # contracts (which ranked higher embedding-wise for the generic
    # question) and the image's 5 chunks never made it in.
    if focus_doc_indexes:
        try:
            focus_set = set(focus_doc_indexes)
            missing = [di for di in focus_set if di not in by_doc]
            if missing:
                docs_with_text = persistence.list_matter_documents(
                    sid,
                    include_text=True,
                )
                by_di = {d["doc_index"]: d for d in docs_with_text}
                for di in missing:
                    d = by_di.get(di)
                    if not d:
                        continue
                    text = (d.get("doc_text") or "").strip()
                    if not text:
                        continue  # Doc legitimately has no content yet.
                    fname = d.get("filename") or ""
                    cite = _matter_cite_id(di)
                    label = f"[M-{di}] {fname}" if fname else f"Dokument M-{di}"
                    # Cap to ~8 KB so a freak 100-page doc can't blow the
                    # prompt; images / single-page scans land at 2-5 KB.
                    trimmed = text[:8000]
                    matter_sources.append(
                        RetrievedSource(
                            cite_id=cite,
                            source_kind="matter",
                            text=trimmed,
                            label=label,
                        )
                    )
                    matter_chunks.append(
                        ChunkOut(
                            text=trimmed[:2500],
                            section=fname or label,
                            law_refs=[],
                            sources=["upload"],
                            similarity=1.0,
                            rerank_score=1.0,
                            cite_id=cite,
                            source_kind="matter",
                            page=1,
                        )
                    )
                    combined_parts.append(trimmed)
                    # Mark in by_doc so the baseline-chunk fill below
                    # doesn't replace this with a "no passage" placeholder.
                    by_doc[di] = {"_synthetic": True}
        except Exception as e:
            print(f"[matter] focus full-text fallback failed ({e})", flush=True)

    # Baseline chunks for the other documents IN SCOPE this turn (those
    # with no retrieved passage). Without these, [M-n] handles the LLM
    # cites from the manifest render "not available" and the panel can't
    # open the PDF. These go ONLY into the frontend chunk set, not the
    # prompt sources (the prompt stays bounded to relevant passages).
    #
    # Per-turn focus: when ``focus_doc_indexes`` is set, only those docs
    # get a baseline chunk — otherwise the citation panel could resolve
    # an [M-n] for a doc the user didn't ask about, leaking it into the
    # answer's source list.
    try:
        in_scope = set(focus_doc_indexes) if focus_doc_indexes else None
        for d in persistence.list_matter_documents(sid):
            di = d["doc_index"]
            if di in by_doc:
                continue
            if in_scope is not None and di not in in_scope:
                continue
            fname = d.get("filename") or ""
            matter_chunks.append(
                ChunkOut(
                    text=(
                        f"{fname} — für diese Frage wurde keine spezifische "
                        "Textstelle gefunden. Klicken Sie, um das Dokument zu öffnen."
                    ),
                    section=fname or f"Dokument M-{di}",
                    law_refs=[],
                    sources=["upload"],
                    similarity=0.0,
                    rerank_score=0.0,
                    cite_id=_matter_cite_id(di),
                    source_kind="matter",
                    page=1,
                )
            )
    except Exception as e:
        print(f"[matter] baseline chunk fill failed ({e})", flush=True)

    return matter_sources, matter_chunks, "\n\n".join(combined_parts)


def _matter_manifest_prefix(
    sid: str,
    uid: str | None,
    focus_doc_indexes: list[int] | None = None,
) -> str:
    """A short list of the documents in this Matter, prepended to the system
    prompt so the model ALWAYS knows what the user has uploaded — even for
    meta-questions ("are the PDFs uploaded?", "which documents do I have?",
    "list my files") that retrieve no passages. Without this the model has
    only the retrieved snippets to go on and wrongly answers "no documents
    are uploaded". Documents still ingesting are flagged so the model can
    say a file is still being processed rather than that it's missing.

    Per-turn focus: when ``focus_doc_indexes`` is set, the manifest only
    lists those documents — the model literally doesn't see the rest, so
    it can't accidentally cite a pre-existing project doc in an answer
    that was supposed to be about the freshly-attached file.
    """
    docs = persistence.list_matter_documents(sid, user_id=uid)
    if focus_doc_indexes:
        in_scope = set(focus_doc_indexes)
        docs = [d for d in docs if d["doc_index"] in in_scope]
    # Suppress FAILED rows from the manifest. A failed row has no searchable
    # content, so listing it just confuses the model — it starts narrating
    # "Document [M-n] failed to process" notes in the answer (verified live:
    # a transient Docling temp-file race left an orphaned failed [M-8] beside
    # a successful [M-9] reupload of the same file, and the assistant wrote
    # the user an English "Important Note: Document Dok 8 failed to process"
    # paragraph). They're still visible in the Documents panel where the user
    # can retry/delete them, but the chat prompt only carries usable docs.
    docs = [d for d in docs if d.get("status") != "failed"]
    if not docs:
        return ""
    # Cap the listed documents so a large data room (hundreds of files)
    # doesn't bloat every prompt; the count line still tells the model the
    # true total.
    cap = 40
    lines = []
    for d in docs[:cap]:
        status = d.get("status", "done")
        note = "" if status == "done" else " (wird gerade verarbeitet)"
        lines.append(f"- [M-{d['doc_index']}] {d.get('filename') or ''}{note}")
    extra = len(docs) - cap
    if extra > 0:
        lines.append(f"- … und {extra} weitere Dokumente")
    # Honest scope-limitation hint at VDR scale. With 5000 docs in a
    # matter the per-question retrieval can realistically only surface
    # ~50 docs in the prompt (~25k tokens of chunk text already pushes
    # the context window). Without this note the LLM answers as if it
    # had read all 5000 — confident but unverified for the long tail.
    # Mirrors what ``_matter_adaptive_sizes`` actually surfaces for the
    # turn so the LLM's framing matches the underlying retrieval budget.
    n = len(docs)
    if n > 50 and not focus_doc_indexes:
        _, _surveyed = _matter_adaptive_sizes(n)
        scope_hint = (
            f"\n\nWICHTIG zum Umfang: Bei dieser Mandatsgröße ({n} Dokumente) "
            f"werden pro Frage die ~{_surveyed} relevantesten Dokumente "
            "untersucht. Wenn Ihre Antwort sich auf eine Stichprobe stützt, "
            "machen Sie das transparent — nicht so tun, als sei das gesamte "
            "Datenraum geprüft worden."
        )
    else:
        scope_hint = ""
    return (
        f"Der Nutzer hat {len(docs)} Dokument(e) in dieses Mandat (Matter) "
        "hochgeladen; sie sind durchsuchbar und per [M-n] zitierbar:\n" + "\n".join(lines) + scope_hint + "\n\n"
    )


def _empty_grounding_guard(
    mode: str,
    matter_sources: list,
    rag_sources: list,
    sid: str,
    uid: str | None,
    lang: str | None,
) -> str | None:
    """Empty-retrieval guard for doc-scoped chat turns.

    A ``contract`` / ``rag+contract`` turn whose grounded source set is
    EMPTY must NOT be answered from the model's parametric knowledge —
    that is the "confident generic boilerplate on a freshly-uploaded
    document" failure: the user uploaded a scanned PDF, asked about it
    before OCR/indexing finished (so ``matter_chunks`` had no rows yet),
    and the model invented plausible contract terms. Verified live: the
    first answer preceded chunk creation by ~2 minutes.

    Returns an honest message to send INSTEAD of generating, or ``None``
    when generation should proceed normally. Scoped to Matter turns only
    — pure ``rag`` (corpus) and ``chat`` turns are left untouched.
    """
    if mode not in ("contract", "rag+contract"):
        return None
    sources = rag_sources if mode == "rag+contract" else matter_sources
    if sources:
        return None  # we have grounded content — generate normally

    en = lang == "en"
    try:
        docs = persistence.list_matter_documents(sid, user_id=uid)
    except Exception:
        docs = []

    def _status(d) -> str:
        return (d.get("status") or "").lower()

    def _chunks(d) -> int:
        return int(d.get("n_chunks") or 0)

    # Partition the matter's documents by ingestion state. Statuses are
    # set by persistence: 'queued' -> 'processing' -> 'done' | 'failed'.
    # A 'done' doc with 0 chunks extracted nothing searchable, so it is
    # effectively a failure (the live "ALT F III" case).
    still_processing = [d for d in docs if _status(d) in ("queued", "processing", "uploading")]
    usable = [d for d in docs if _status(d) in ("done", "ready") and _chunks(d) > 0]
    failed = [d for d in docs if _status(d) == "failed" or (_status(d) in ("done", "ready") and _chunks(d) == 0)]

    # 1. Something is still ingesting → tell the user to wait, with the live
    #    page count. Scanned PDFs are OCR'd page-by-page (slow), so this is
    #    expected, not an error. (The frontend additionally holds the question
    #    and re-runs it automatically once ingestion completes.)
    if still_processing:
        done = sum(int(d.get("pages_done") or 0) for d in still_processing)
        total = sum(int(d.get("pages_total") or 0) for d in still_processing)
        if en:
            prog = f" (page {done}/{total})" if total else ""
            return (
                f"Your document is still being processed{prog} — scanned PDFs are "
                "transcribed page by page, which takes a moment. Please ask again shortly."
            )
        prog = f" (Seite {done}/{total})" if total else ""
        return (
            f"Ihr Dokument wird gerade verarbeitet{prog} — gescannte PDFs werden Seite "
            "für Seite per OCR erfasst, das dauert einen Moment. Bitte stellen Sie Ihre "
            "Frage gleich noch einmal."
        )

    # 2. Ingestion FAILED and nothing usable exists → say so honestly (the old
    #    message wrongly implied "still processing" forever). Surface the reason.
    if failed and not usable:
        why = next((d.get("error") for d in failed if (d.get("error") or "").strip()), "")
        if en:
            tail = f" (reason: {str(why).strip()})" if why else ""
            return (
                "I couldn’t process your document — text extraction produced nothing"
                f"{tail}. Please re-upload it (a text-based PDF or a clearer scan)."
            )
        tail = f" (Grund: {str(why).strip()})" if why else ""
        return (
            "Ihr Dokument konnte nicht verarbeitet werden — die Textextraktion war leer"
            f"{tail}. Bitte laden Sie es erneut hoch (ein textbasiertes PDF oder einen "
            "besseren Scan)."
        )

    # 3. Documents are indexed with content, but retrieval found nothing
    #    relevant to THIS question → refuse rather than answer ungrounded.
    if en:
        return (
            "I couldn’t find any relevant passage for this question in your uploaded "
            "documents. I won’t answer from general knowledge — please rephrase, or "
            "confirm the document contains this information."
        )
    return (
        "Zu Ihrer Frage finde ich in den hochgeladenen Dokumenten keine relevante "
        "Textstelle. Ich antworte hier bewusst nicht aus allgemeinem Wissen — bitte "
        "formulieren Sie die Frage um oder prüfen Sie, ob das Dokument diese "
        "Information enthält."
    )


# Max time a doc-scoped chat turn will wait for a still-OCR'ing upload to
# finish indexing before it answers, instead of refusing with "still
# processing". A 10-page scan parallel-OCRs in ~90s; 180s covers larger
# scans with margin. Override with LAI_MATTER_WAIT_S; 0 disables the wait
# (revert to immediate refusal).
_MATTER_WAIT_S = float(os.environ.get("LAI_MATTER_WAIT_S", "180"))
_MATTER_WAIT_POLL_S = 1.5


def _matter_is_processing(sid: str, uid: str | None) -> bool:
    """True if any document in the session is still being ingested
    (queued/processing) — i.e. its searchable chunks don't exist yet.
    Phase B: scoped on the firm-tenant key."""
    try:
        docs = persistence.list_matter_documents(sid, user_id=uid)
    except Exception:
        return False
    return any((d.get("status") or "").lower() in ("queued", "processing", "uploading") for d in docs)


def _await_matter_ready(sid: str, uid: str | None, timeout_s: float) -> bool:
    """Block (in this sync, threadpool-run request) until no document in the
    session is still ingesting, or ``timeout_s`` elapses. Returns True if,
    afterwards, at least one document is ``done`` with searchable chunks (so
    re-retrieval will find content); False on timeout or all-failed.

    Endpoints are sync ``def`` (FastAPI runs them in a threadpool), so a
    blocking ``time.sleep`` here does not stall the event loop.
    Phase B: scoped on the firm-tenant key.
    """
    if timeout_s <= 0:
        return False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _matter_is_processing(sid, uid):
            break
        time.sleep(_MATTER_WAIT_POLL_S)
    try:
        docs = persistence.list_matter_documents(sid, user_id=uid)
    except Exception:
        docs = []
    return any((d.get("status") or "").lower() in ("done", "ready") and (d.get("n_chunks") or 0) > 0 for d in docs)


def _matter_progress(sid: str, uid: str | None) -> tuple[int, int]:
    """(pages_done, pages_total) summed over the session's still-ingesting
    documents — drives the live "Seite X/Y" progress shown while waiting.
    Phase B: scoped on the firm-tenant key."""
    try:
        docs = persistence.list_matter_documents(sid, user_id=uid)
    except Exception:
        return 0, 0
    proc = [d for d in docs if (d.get("status") or "").lower() in ("queued", "processing", "uploading")]
    return (
        sum(int(d.get("pages_done") or 0) for d in proc),
        sum(int(d.get("pages_total") or 0) for d in proc),
    )


def _build_turn_msgs(
    use_rag: bool,
    use_contract: bool,
    question: str,
    rag_sources: list,
    matter_sources: list,
    history,
    meta_prefix,
    answer_lang,
) -> tuple[str, list]:
    """Pick the chat mode and assemble the LLM messages for one turn. Shared
    by /query and /query/stream (and re-used after an in-stream OCR wait) so
    the two paths can't drift."""
    if use_rag and use_contract:
        return "rag+contract", build_rag_messages(
            question, rag_sources, history=history, meta_prefix=meta_prefix, target_language=answer_lang
        )
    if use_rag:
        return "rag", build_rag_messages(
            question, rag_sources, history=history, meta_prefix=meta_prefix, target_language=answer_lang
        )
    if use_contract:
        return "contract", build_rag_messages(
            question,
            matter_sources,
            history=history,
            meta_prefix=meta_prefix,
            target_language=answer_lang,
            system=RAG_SYSTEM_DOC_ONLY,
        )
    return "chat", build_chat_messages(question, history=history, meta_prefix=meta_prefix, target_language=answer_lang)


def _build_matter_context(
    sid: str,
    uid: str | None,
    use_contract: bool,
    question: str = "",
    focus_doc_indexes: list[int] | None = None,
) -> tuple[list[RetrievedSource], list[ChunkOut], str]:
    """Assemble the matter side of a chat turn.

    Primary path: :func:`_matter_pgvector_context` — dense retrieval over
    the per-session index, which scales to a real data room. Fallback (for
    sessions uploaded before per-Matter indexing): select the most
    relevant passages from each document's stored text, one ``[M-n]`` per
    document.

    Returns ``(matter_sources, matter_chunks, combined_text)``; the third
    value is concatenated matter text used only for Bundesland detection in
    the jurisdiction check. Per-turn focus: when ``focus_doc_indexes`` is
    set, both the primary (pgvector) and fallback (legacy text) paths are
    narrowed to ONLY those documents — so the model can't reach back into
    the rest of the matter when the user asked "analyse this document".
    """
    matter_sources: list[RetrievedSource] = []
    matter_chunks: list[ChunkOut] = []
    combined_parts: list[str] = []
    if not use_contract:
        return matter_sources, matter_chunks, ""

    # Primary: scalable pgvector retrieval across the indexed data room.
    pg = _matter_pgvector_context(sid, question, focus_doc_indexes=focus_doc_indexes)
    if pg is not None:
        return pg

    # ── Fallback: legacy whole-document selection (unindexed sessions) ──
    docs = persistence.list_matter_documents(sid, user_id=uid, include_text=True)
    if focus_doc_indexes:
        in_scope = set(focus_doc_indexes)
        docs = [d for d in docs if d["doc_index"] in in_scope]

    if not docs:
        # Legacy path: session has an inline contract_text but no
        # matter_documents rows (uploaded before the Matter feature).
        sess = persistence.load_session(sid, user_id=uid)
        if sess and (sess.get("contract_text") or ""):
            full = sess.get("contract_text") or ""
            fname = sess.get("filename") or ""
            label = f"Hochgeladenes Dokument — {fname}" if fname else "Hochgeladenes Dokument"
            cite = _matter_cite_id(1)
            prompt_text, page, display = _select_relevant_passages(question, full, 16000)
            matter_sources.append(
                RetrievedSource(
                    cite_id=cite,
                    source_kind="matter",
                    text=prompt_text,
                    label=label,
                )
            )
            matter_chunks.append(
                ChunkOut(
                    text=display[:2500],
                    section=label,
                    law_refs=[],
                    sources=["upload"],
                    similarity=1.0,
                    rerank_score=1.0,
                    cite_id=cite,
                    source_kind="matter",
                    page=page,
                )
            )
            combined_parts.append(full)
        return matter_sources, matter_chunks, "\n\n".join(combined_parts)

    # Multi-document matter: one [M-n] per document, n == doc_index.
    # Per-document budget shrinks as the matter grows so a many-document
    # matter doesn't blow the prompt window; floor keeps each document
    # meaningfully represented.
    per_doc_budget = max(4000, 16000 // max(1, len(docs)))
    for d in docs:
        full = d.get("doc_text") or ""
        if not full.strip():
            continue
        fname = d.get("filename") or ""
        label = f"[M-{d['doc_index']}] {fname}" if fname else f"Dokument M-{d['doc_index']}"
        cite = _matter_cite_id(d["doc_index"])
        prompt_text, page, display = _select_relevant_passages(question, full, per_doc_budget)
        matter_sources.append(
            RetrievedSource(
                cite_id=cite,
                source_kind="matter",
                text=prompt_text,
                label=label,
            )
        )
        matter_chunks.append(
            ChunkOut(
                text=display[:2500],
                section=label,
                law_refs=[],
                sources=["upload"],
                similarity=1.0,
                rerank_score=1.0,
                cite_id=cite,
                source_kind="matter",
                page=page,
            )
        )
        combined_parts.append(full)
    return matter_sources, matter_chunks, "\n\n".join(combined_parts)


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
    # UI / meta-AI questions look like real questions (have "?", > 20 chars)
    # but carry no legal content. Check BEFORE LEGAL_KEYWORDS so a legal-word
    # buried in a meta question ("wie funktioniert das mit BImSchG?") doesn't
    # leak through — the meta pattern still wins. See 2026-06-01 ks/as audit.
    if UI_META.match(q):
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
    """Whether to pull the session's uploaded document(s) into the prompt.

    The core use case is "upload a PDF, then ask about it", so the rule is
    deliberately inclusive: if the session HAS any uploaded document we
    inject it for every substantive question — in ANY language. We only
    skip it for pure greetings / smalltalk, where adding ~8k chars of
    contract text is wasted budget.

    This replaces an earlier German-keyword + LLM-classifier gate that
    silently dropped the uploaded document on English questions ("how
    many turbines does the permit cover?", "which turbine type is
    stated?"). That was the worst possible failure for this product: the
    model answered from the corpus only and told the user the uploaded
    PDF "is not in the provided context". When in doubt, include the
    user's own document — the model simply won't cite [M-n] if it's
    irrelevant.
    """
    if not session_id:
        return False
    # A document is present if there's a matter_documents row OR the
    # legacy inline contract_text (the first upload mirrors into it).
    has_docs = bool(persistence.list_matter_documents(session_id))
    if not has_docs:
        sess = persistence.load_session(session_id)
        has_docs = bool(sess and sess.get("contract_text"))
    if not has_docs:
        return False
    # Pure greeting / smalltalk needs no document context.
    # UI / meta-AI questions are also excluded — they look substantive
    # ("gehst du semantisch vor?") but adding 8k chars of contract text
    # and routing them through contract analysis turns them into
    # off-topic doc-grounded answers (2026-06-01 ks/as session-2 audit).
    q = question.strip()
    return not (len(q) < 4 or CONVERSATIONAL.match(q) or UI_META.match(q))


# ---------------------------------------------------------------------------
# Document ingestion
# ---------------------------------------------------------------------------
#
# Two paths:
#   • Vision-LLM OCR (default for scanned PDFs) — renders each page to an
#     image and transcribes it with the on-prem multimodal model. Classic
#     OCR (Tesseract) misreads degraded glyphs on old German scans — e.g.
#     "Enercon E-70 E4" came out "E-79" at every DPI/preprocessing combo,
#     because the scanned "0" is closed at the top. A vision model reads
#     the same pixels in context (it knows E-79 isn't a real model) and
#     gets it right. Page markers (``<!-- Seite N -->``) are embedded in
#     the output so citations can resolve to a page (see _matter passages).
#   • Docling — used for text-layer PDFs (fast, no OCR needed) and all
#     non-PDF formats (DOCX, HTML, …).

import base64 as _base64
import subprocess as _subprocess

# Page marker embedded between pages of VLM-OCR output. Parsed back out
# when building per-page matter passages; harmless if the model sees it.
_PAGE_MARKER_RE = re.compile(r"<!--\s*Seite\s+(\d+)\s*-->")

# Toggle: set LAI_VLM_OCR=0 to force the legacy docling/Tesseract path.
_VLM_OCR_ENABLED = os.environ.get("LAI_VLM_OCR", "1") not in ("0", "false", "False")
# Render DPI for VLM OCR — 200 is plenty for the model and keeps the
# image token count modest. Override with LAI_VLM_OCR_DPI.
_VLM_OCR_DPI = int(os.environ.get("LAI_VLM_OCR_DPI", "200"))
# Pages are transcribed concurrently — each page is one independent vision-LLM
# call. Kept LOW (3): the analyzer LLM is shared with the corpus migration /
# embedding / chat, and firing too many concurrent OCR requests made individual
# pages time out (Read timed out) and forced a docling fallback / truncation.
# Override with LAI_VLM_OCR_WORKERS.
_VLM_OCR_WORKERS = max(1, int(os.environ.get("LAI_VLM_OCR_WORKERS", "3")))
# Per-page retry budget: a page that times out under transient contention is
# retried (serialized) before the whole OCR degrades to docling.
_VLM_OCR_PAGE_ATTEMPTS = max(1, int(os.environ.get("LAI_VLM_OCR_PAGE_ATTEMPTS", "3")))

_VLM_OCR_PROMPT = (
    "Du bist ein präzises OCR-System für gescannte deutsche Rechts- und "
    "Behördendokumente. Transkribiere den GESAMTEN sichtbaren Text dieses "
    "Seitenbildes exakt und vollständig.\n"
    "- Gib die Struktur als Markdown wieder (Überschriften, Absätze, Listen; "
    "Tabellen als Markdown-Tabellen).\n"
    "- Wenn ein Zeichen durch die Scan-Qualität mehrdeutig ist, wähle die "
    "anhand des Kontexts plausibelste Lesart (z.B. Typenbezeichnungen, "
    "Eigennamen, Gesetzeszitate, Zahlen). Erfinde aber KEINEN Inhalt.\n"
    "- Übersetze nicht, fasse nicht zusammen, kommentiere nicht.\n"
    "Gib ausschließlich die reine Transkription aus."
)

# Image-upload prompt — used by ``_vlm_ocr_image_file`` for raw .png / .jpg /
# .webp / .tiff / .bmp uploads. Two reasons it can't share ``_VLM_OCR_PROMPT``:
#
#   1. Pure OCR returns an EMPTY string on a no-text image (a photo of a
#      wind turbine, a diagram, a site map) — which then indexes zero
#      passages, retrieval finds nothing, and the model refuses to answer
#      "what is in this image?". Useless for the demo.
#   2. An image upload is the user's deliberate way of saying "look at
#      THIS thing" — they want the model to understand the visual content,
#      not just transcribe pixels-as-glyphs. Scanned PDFs (handled by the
#      strict OCR prompt above) have the opposite contract: be faithful
#      to the printed text, don't editorialize.
#
# The prompt asks for BOTH a sober visual description AND a verbatim
# transcription of any visible text, in one Markdown response — so the
# resulting passages cover "what does the image depict?" and "what does
# the visible text say?" with one ingestion pass and one embed.
_VLM_IMAGE_DESCRIBE_PROMPT = (
    "Du analysierst ein hochgeladenes Bild für ein deutsches Rechts- und "
    "Energierecht-RAG-System (Schwerpunkt Windenergie, Genehmigungen, "
    "Verträge). Erzeuge eine vollständige Markdown-Beschreibung, die "
    "zwei Aufgaben erfüllt:\n"
    "\n"
    "1. **Visueller Inhalt** — beschreibe sachlich, was zu sehen ist: "
    "Objekte, Personen, Szenen, technische Anlagen, Gebäude, Fahrzeuge, "
    "Diagramme, Karten, Tabellen, Layouts. Nenne Hersteller, Anlagen- "
    "oder Modelltypen, Standorte, wenn erkennbar.\n"
    "2. **Sichtbarer Text** — transkribiere JEDEN sichtbaren Text exakt: "
    "Schilder, Beschriftungen, Stempel, Unterschriften, Vertragsinhalt, "
    "Tabellen, Formulare. Strukturiere als Markdown (Überschriften, "
    "Tabellen, Listen wie im Bild sichtbar).\n"
    "\n"
    "Regeln:\n"
    "- Antworte auf Deutsch.\n"
    "- Erfinde NICHTS — wenn etwas unklar oder unleserlich ist, schreibe "
    "das ehrlich.\n"
    "- Übersetze nicht, kommentiere nicht über das Beschriebene hinaus.\n"
    "- Wenn ein Abschnitt nicht zutrifft (z.B. kein sichtbarer Text, "
    "oder keine identifizierbaren Objekte), lass ihn weg.\n"
    "- Wenn das Bild leer, schwarz, oder völlig unleserlich ist, gib "
    "exakt zurück: 'Bild ist nicht interpretierbar.'"
)


def _pdf_has_text_layer(file_bytes: bytes) -> bool:
    """True if the PDF carries an extractable text layer (i.e. it is NOT a
    pure scan). Such PDFs go to docling directly — no OCR needed and far
    faster. A scan returns near-empty text here and is routed to VLM OCR.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            out = _subprocess.run(
                ["pdftotext", "-q", tmp_path, "-"],
                capture_output=True,
                timeout=60,
            )
            text = out.stdout.decode("utf-8", errors="replace")
            # A real text layer yields hundreds of chars; a scan yields a
            # handful of stray glyphs at most.
            return len(text.strip()) >= 200
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except Exception:
        # If pdftotext is missing / errors, assume scan and let VLM handle it.
        return False


def _render_pdf_to_images(file_bytes: bytes, dpi: int = _VLM_OCR_DPI) -> list[bytes]:
    """Render every PDF page to a PNG (via poppler's pdftoppm). Returns the
    page images as PNG bytes, in page order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "in.pdf"
        pdf_path.write_bytes(file_bytes)
        prefix = Path(tmpdir) / "pg"
        _subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)],
            capture_output=True,
            timeout=600,
            check=True,
        )
        pngs = sorted(Path(tmpdir).glob("pg*.png"))
        return [p.read_bytes() for p in pngs]


def _vlm_ocr_image(png_bytes: bytes, prompt: str | None = None) -> str:
    """Run one image through the on-prem vision LLM. Returns the model's
    response as Markdown text. Raises on transport / HTTP error so the
    caller can fall back to docling.

    ``prompt`` selects the task:
      • ``None`` / omitted → :data:`_VLM_OCR_PROMPT` (strict OCR for a
        scanned-PDF page; this is the legacy ``_vlm_ocr_pdf`` path).
      • :data:`_VLM_IMAGE_DESCRIBE_PROMPT` → describe + transcribe for a
        raw image upload, so non-text images yield indexable content.
    """
    if STATE["llm_api_url"] is None:
        raise RuntimeError("VLM OCR requires the remote vLLM endpoint")
    url = STATE["llm_api_url"].rstrip("/") + "/v1/chat/completions"
    b64 = _base64.b64encode(png_bytes).decode()
    body = {
        "model": STATE["llm_model_name"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or _VLM_OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = httpx.post(url, json=body, timeout=300.0)
    r.raise_for_status()
    obj = r.json()
    return (obj["choices"][0]["message"]["content"] or "").strip()


def _vlm_ocr_image_file(
    file_bytes: bytes,
    filename: str,
    on_progress=None,
) -> tuple[str, int, list[dict]]:
    """OCR a raw image file (.png, .jpg, .webp, .tiff, .bmp) with the vision LLM.

    Single-page formats produce a 1-page transcription; multi-page TIFFs
    are iterated frame-by-frame so a scanned-and-faxed multi-page TIFF
    works the same as a scanned PDF would. The output mirrors
    :func:`_vlm_ocr_pdf`: ``(markdown, num_pages, tables)`` with each page
    prefixed by a ``<!-- Seite N -->`` marker so the existing chunker /
    citation pipeline attaches a page number to every passage. Tables
    are left empty (the analyzer already lifts Markdown tables out of
    the OCR markdown when present).

    Normalization details:
      • Decoded via Pillow → converted to RGB (drop alpha; some VLMs
        choke on RGBA / palette mode).
      • Downsized to a 2400 px longest edge so a giant phone photo
        doesn't blow the VLM's input-token budget while preserving
        legible glyph detail.
      • Re-encoded as PNG before being handed to ``_vlm_ocr_image`` —
        which is hard-coded to send ``data:image/png;base64``. Doing the
        normalization here lets that function stay unchanged.

    Failure modes match :func:`_vlm_ocr_pdf`: if a page transcription
    raises, retry up to ``_VLM_OCR_PAGE_ATTEMPTS`` times; only if it
    STILL fails do we propagate so the caller can degrade to docling.
    """
    # Local PIL import keeps Pillow off the cold-path import graph —
    # nothing else in this file needs it.
    from PIL import Image, ImageSequence

    _MAX_EDGE_PX = 2400
    img = Image.open(io.BytesIO(file_bytes))
    # Materialize all frames up-front. Pillow's ImageSequence.Iterator is
    # lazy and shares the underlying file handle; copying each frame
    # decouples them so a sequence-rewind can't corrupt later reads.
    frames: list = [frame.copy() for frame in ImageSequence.Iterator(img)]
    total = len(frames) or 1
    if on_progress is not None:
        on_progress(0, total)

    def _to_png_bytes(frame) -> bytes:
        # Drop alpha / palette quirks → RGB only.
        if frame.mode not in ("RGB", "L"):
            frame = frame.convert("RGB")
        # Downsize if oversized — thumbnail() preserves aspect ratio and
        # is a no-op when the image is already within bounds.
        w, h = frame.size
        if max(w, h) > _MAX_EDGE_PX:
            frame.thumbnail((_MAX_EDGE_PX, _MAX_EDGE_PX), Image.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    parts: list[str] = [""] * total
    pending = list(range(total))
    done = 0
    for attempt in range(1, _VLM_OCR_PAGE_ATTEMPTS + 1):
        if not pending:
            break
        failed: list[int] = []
        # For images we run the pages serially — most uploads are one
        # page (single photo / single scan), and even a multi-page TIFF
        # is usually short. A worker pool would only matter at >5 pages
        # and we'd want to share the same retry contract as ``_vlm_ocr_pdf``.
        for idx in pending:
            try:
                png = _to_png_bytes(frames[idx])
                # Image uploads use the describe-AND-transcribe prompt so a
                # non-text image (photo of an anlage, a diagram, a map)
                # still yields indexable text — strict OCR would return
                # empty markdown and the model would have nothing to cite.
                parts[idx] = _vlm_ocr_image(png, prompt=_VLM_IMAGE_DESCRIBE_PROMPT)
                if attempt == 1:
                    done += 1
                    if on_progress is not None:
                        on_progress(done, total)
            except Exception as exc:
                if attempt == _VLM_OCR_PAGE_ATTEMPTS:
                    raise RuntimeError(f"VLM OCR failed for image {filename!r} page {idx + 1}/{total}: {exc}") from exc
                failed.append(idx)
        pending = failed

    # Only emit a page marker for pages that produced real content. If a
    # page came back empty (e.g. the model judged the frame "nicht
    # interpretierbar"), suppress its marker so ``_split_into_passages``
    # doesn't index a hollow ``<!-- Seite N -->`` HTML comment as a
    # phantom passage — that previously made the chip flip to "done"
    # with ``n_chunks=1`` even though there was nothing searchable.
    md_parts: list[str] = []
    for i in range(total):
        text = (parts[i] or "").strip()
        if not text:
            continue
        md_parts.append(f"<!-- Seite {i + 1} -->\n{text}")
    return "\n\n".join(md_parts), total, []


def _vlm_ocr_pdf(file_bytes: bytes, on_progress=None) -> tuple[str, int, list[dict]]:
    """OCR a scanned PDF page-by-page with the vision LLM.

    Returns ``(markdown, num_pages, tables)``. Pages are joined with
    ``<!-- Seite N -->`` markers so downstream passage-building can attach
    a page number to each [M-n] citation. Tables are left to the analyzer
    (the OCR markdown already contains Markdown tables inline).

    ``on_progress(done, total)`` — if given, called once the page count is
    known (``0, total``) and after each page completes, driving the UI
    progress bar. Pages are OCR'd concurrently (``_VLM_OCR_WORKERS``), so
    ``done`` counts *completions* (which may finish out of page order); the
    final markdown is still assembled in page order.

    A page that fails (e.g. a Read timeout under contention) is retried up to
    ``_VLM_OCR_PAGE_ATTEMPTS`` times — retry passes run serialized so they don't
    re-induce the contention. Only if a page STILL fails after retries does the
    failure propagate so ``convert_document`` degrades to docling for the WHOLE
    document — we never return a transcription silently missing pages.
    """
    images = _render_pdf_to_images(file_bytes)
    total = len(images)
    if on_progress is not None:
        on_progress(0, total)
    parts: list[str] = [""] * total
    pending = list(range(total))  # page indices still needing a result
    done = 0
    for attempt in range(1, _VLM_OCR_PAGE_ATTEMPTS + 1):
        if not pending:
            break
        workers = min(_VLM_OCR_WORKERS, len(pending)) if attempt == 1 else 1
        failed: list[int] = []
        # The as_completed loop runs in this (calling) thread, so ``done`` and
        # ``on_progress`` need no lock — only ``_vlm_ocr_image`` runs on workers.
        with _futures.ThreadPoolExecutor(max_workers=workers) as ex:
            fut_to_idx = {ex.submit(_vlm_ocr_image, images[i]): i for i in pending}
            for fut in _futures.as_completed(fut_to_idx):
                idx = fut_to_idx[fut]
                try:
                    page_text = fut.result()
                except Exception as exc:
                    failed.append(idx)
                    print(f"[ingest] VLM OCR page {idx + 1}/{total} attempt {attempt} failed: {exc}", flush=True)
                    continue
                parts[idx] = f"<!-- Seite {idx + 1} -->\n{page_text}"
                done += 1
                if on_progress is not None:
                    on_progress(done, total)
        pending = failed
        if pending and attempt < _VLM_OCR_PAGE_ATTEMPTS:
            time.sleep(2.0 * attempt)  # back off so the analyzer can drain
    if pending:
        # Unrecovered pages → returning now would silently truncate the doc.
        raise RuntimeError(
            f"VLM OCR: {len(pending)} of {total} page(s) failed after "
            f"{_VLM_OCR_PAGE_ATTEMPTS} attempts ({sorted(p + 1 for p in pending)})"
        )
    md = "\n\n".join(parts)
    return md, total, []


_DOCLING_CONVERTER = None  # lazy


def docling_convert(file_bytes: bytes, filename: str) -> tuple[str, int, list[dict]]:
    """Convert uploaded document to markdown + structured tables.

    Plain text and markdown are decoded directly (Docling refuses .txt).
    Everything else goes through Docling (PDF, DOCX, HTML, etc.).
    Returns (markdown_text, num_pages, tables).
        tables: list of {"title", "rows": [{col_label: cell, ...}, ...]}
    """
    # Early empty-bytes guard. Without this, an empty file (zero bytes
    # received from the multipart, or a 0-byte file on disk) flows into
    # Docling/pdfium which surfaces the cryptic "Input document /tmp/…
    # is not valid." — same message Docling uses for genuinely corrupt
    # PDFs. The retry below then dutifully repeats the failure with a
    # second meaningless temp path. Make the actual cause loud so the
    # error reaches the chip + log as "PDF is empty (0 bytes)" instead.
    if not file_bytes:
        raise RuntimeError(
            f"Uploaded file '{filename}' is empty (0 bytes) — "
            "nothing to parse. The file was likely truncated or aborted "
            "mid-upload; please try again."
        )
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        try:
            return file_bytes.decode("utf-8", errors="replace"), 0, []
        except Exception as e:
            raise RuntimeError(f"Could not decode text file: {e}") from e

    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TesseractCliOcrOptions,
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

    # Single retry with a freshly-written temp file: live runs show Docling
    # occasionally raises "Input document /tmp/tmpXXXX.pdf is not valid." on
    # the FIRST attempt for a perfectly valid PDF (the same bytes re-uploaded
    # a moment later ingest cleanly — verified for SDL-Gutachten_Lamstedt_156pg.pdf
    # where doc_index 8 failed and doc_index 9 succeeded with 156 pages /
    # 818 chunks). The temp file is recreated on retry so a clobbered/
    # unflushed buffer can't be the cause.
    last_err: Exception | None = None
    for attempt in (1, 2):
        with tempfile.NamedTemporaryFile(suffix=suffix_for_tmp, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            with suppress(OSError):
                os.fsync(tmp.fileno())  # be sure bytes are on disk before Docling reads
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
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            transient = "is not valid" in msg or "input document" in msg
            if attempt == 1 and transient:
                print(
                    f"[docling] convert failed transiently on attempt {attempt} for {filename!r} ({e}) — retrying once",
                    flush=True,
                )
                continue
            raise
        finally:
            tmp_path.unlink(missing_ok=True)
    # Unreachable (the loop either returns or raises), but satisfies the type checker.
    raise RuntimeError(f"docling_convert exhausted retries: {last_err}")


_IMAGE_OCR_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp"}


def convert_document(
    file_bytes: bytes,
    filename: str,
    on_progress=None,
) -> tuple[str, int, list[dict]]:
    """Top-level ingestion entry point. Routes scanned PDFs and raw images
    through the vision-LLM OCR path (accurate on degraded German scans
    where Tesseract fails) and everything else through docling.

    Returns ``(markdown, num_pages, tables)`` — same contract as
    :func:`docling_convert`, so callers are unaffected. ``on_progress(done,
    total)`` is forwarded to the page-by-page OCR loop when the scanned-PDF
    or image path is taken (the docling path has no per-page hook).

    Routing matrix:
      • ``.pdf`` with no text layer → :func:`_vlm_ocr_pdf` (per-page VLM).
      • ``.png/.jpg/.jpeg/.webp/.tiff/.bmp`` → :func:`_vlm_ocr_image_file`
        (one VLM call per image / TIFF frame). Docling has no robust
        image-input pipeline pre-configured here, so the VLM is the
        only safe path — and on a failure we degrade gracefully (the
        ingestion row is marked ``failed`` upstream so the user sees
        a red chip instead of a silently-empty doc).
      • Everything else (text-layer PDF, .docx, .xlsx, .txt, .csv, .md)
        → docling, unchanged.
    """
    suffix = Path(filename).suffix.lower()
    if (
        _VLM_OCR_ENABLED
        and suffix == ".pdf"
        and STATE["llm_api_url"] is not None
        and not _pdf_has_text_layer(file_bytes)
    ):
        try:
            md, num_pages, tables = _vlm_ocr_pdf(file_bytes, on_progress=on_progress)
            if md.strip():
                print(f"[ingest] VLM OCR: {num_pages} page(s) transcribed for {filename!r}", flush=True)
                return md, num_pages, tables
            print(f"[ingest] VLM OCR returned empty for {filename!r} — falling back to docling", flush=True)
        except Exception as e:
            print(f"[ingest] VLM OCR failed for {filename!r} ({e}) — falling back to docling", flush=True)
    elif _VLM_OCR_ENABLED and suffix in _IMAGE_OCR_EXTS and STATE["llm_api_url"] is not None:
        # Image-only ingestion. Docling can't usefully OCR a raw PNG/JPG
        # without an extra OCR engine wired in; the VLM path already
        # handles scanned-PDF pages as images, so reusing it for raw
        # images is the minimal-surface route.
        try:
            md, num_pages, tables = _vlm_ocr_image_file(
                file_bytes,
                filename,
                on_progress=on_progress,
            )
            if md.strip():
                print(f"[ingest] VLM OCR (image): {num_pages} page(s) transcribed for {filename!r}", flush=True)
                return md, num_pages, tables
            print(f"[ingest] VLM OCR (image) returned empty for {filename!r}", flush=True)
            # No useful text — return empty markdown so the chip flips
            # to "done" with n_chunks=0 (honest). The previous sentinel
            # ``<!-- Seite 1 -->`` got indexed as a phantom passage and
            # made the chip claim 1 chunk that wasn't searchable.
            return "", num_pages or 1, []
        except Exception as e:
            print(f"[ingest] VLM OCR (image) failed for {filename!r} ({e})", flush=True)
            # Re-raise so ``_ingest_document_job`` marks the doc 'failed'
            # with a real error message — docling has no usable fallback
            # for a raw image here, and silently returning empty would
            # mask the failure in the UI.
            raise
    return docling_convert(file_bytes, filename)


# Max chars per passage we embed/store in the matter index. Paragraph-sized
# passages are almost always shorter; this just bounds a pathological wall
# of text (e.g. a giant OCR'd table) so one passage can't dominate.
_PASSAGE_EMBED_MAXLEN = 2000


def index_matter_document(sid: str, doc_index: int, filename: str, md: str) -> int:
    """Chunk a document into page-tagged passages, embed them, and store
    them in the per-Matter pgvector index.

    This is what makes a Matter scale to a real data room: questions
    retrieve the most relevant passages across ALL uploaded documents
    (dense KNN scoped to the session) instead of stuffing every document
    into the prompt. Best-effort — on any failure we log and return 0; the
    chat path still has a whole-document fallback so the upload is never
    blocked. Returns the number of passages indexed.
    """
    rc = STATE.get("retrieval_client")
    if rc is None:
        return 0
    passages = _split_into_passages(md)
    rows: list[tuple[int | None, str]] = [
        (page, text[:_PASSAGE_EMBED_MAXLEN]) for page, text in passages if text.strip()
    ]
    if not rows:
        return 0
    try:
        from lai.search.eval import _get_embedding_client

        results = _get_embedding_client().embed([t for _, t in rows])
        indexed = rc.index_matter_document(
            sid,
            doc_index,
            filename,
            [(page, text, r.embedding) for (page, text), r in zip(rows, results, strict=False)],
        )
        print(f"[ingest] indexed {indexed} passage(s) for [M-{doc_index}] {filename!r} in session {sid}", flush=True)
        return indexed
    except Exception as e:
        print(f"[ingest] matter indexing failed for {filename!r} ({e})", flush=True)
        return 0


# ── Background ingestion queue ───────────────────────────────────────────
#
# Uploads return the instant the bytes are on disk; the slow work (OCR +
# embed + pgvector index) runs here so the UI never blocks. A bounded
# thread pool gives controlled concurrency — multiple pages/documents OCR
# at once and vLLM batches the concurrent vision requests on the GPU, so a
# big data room ingests as fast as the GPU allows without a queue of one.
# Threads (not processes) are right: every step is I/O-bound (HTTP to the
# vision model + embedding service, Postgres) so the GIL is released
# throughout. Status/progress live in ``matter_documents`` so the client
# just polls — no websocket to babysit, and progress survives a refresh.
_INGEST_WORKERS = int(os.environ.get("LAI_INGEST_WORKERS", "4"))
import concurrent.futures as _futures

_INGEST_EXECUTOR = _futures.ThreadPoolExecutor(
    max_workers=_INGEST_WORKERS,
    thread_name_prefix="ingest",
)


def _ingest_document_job(
    sid: str,
    doc_id: int,
    doc_index: int,
    filename: str,
    is_first: bool,
) -> None:
    """Background job: OCR (with live progress) → store text → embed+index.

    Walks the document through ``processing`` → ``done`` (or ``failed``),
    writing progress to ``matter_documents`` so the UI's poll sees a live
    page counter and a final checkmark. Never raises — a failure is
    recorded on the row so the user can retry that one document without the
    upload (or the rest of the data room) being affected.
    """
    try:
        persistence.update_matter_progress(doc_id, status="processing")
        # Pass the exact extension derived from the filename instead of
        # letting ``matter_document_path`` guess. Without this, image
        # uploads (.jpeg/.png/.webp/.tiff/.bmp) failed at ingestion with
        # "uploaded file not found on disk" because the fallback list
        # only knew document extensions. With ``ext`` passed explicitly
        # we always look up the right path regardless of which formats
        # the fallback list knows about.
        ext_hint = Path(filename).suffix.lower() or None
        path = persistence.matter_document_path(sid, doc_id, ext=ext_hint)
        if path is None:
            raise RuntimeError("uploaded file not found on disk")
        contents = path.read_bytes()

        def _on_page(done: int, total: int) -> None:
            persistence.update_matter_progress(doc_id, pages_done=done, pages_total=total)

        md, num_pages, _tables = convert_document(contents, filename, on_progress=_on_page)
        # First document mirrors into sessions.contract_text for the legacy
        # single-document paths (analyze-contract, old preview).
        if is_first:
            try:
                persistence.set_session_contract(sid, md, num_pages)
            except Exception as e:
                print(f"[ingest] contract mirror failed for {sid}: {e}", flush=True)
        n_chunks = index_matter_document(sid, doc_index, filename, md)
        persistence.finalize_matter_document(
            doc_id,
            doc_text=md,
            n_pages=num_pages,
            n_chunks=n_chunks,
        )
        print(
            f"[ingest] done [M-{doc_index}] {filename!r} session={sid}: {num_pages} page(s), {n_chunks} chunk(s)",
            flush=True,
        )
    except Exception as e:
        print(f"[ingest] FAILED [M-{doc_index}] {filename!r} session={sid}: {e}", flush=True)
        with suppress(Exception):
            persistence.fail_matter_document(doc_id, str(e))


def _enqueue_ingestion(
    sid: str,
    doc_id: int,
    doc_index: int,
    filename: str,
    is_first: bool,
) -> None:
    """Submit a document to the background ingestion pool."""
    _INGEST_EXECUTOR.submit(
        _ingest_document_job,
        sid,
        doc_id,
        doc_index,
        filename,
        is_first,
    )


def _recover_unfinished_ingestion() -> None:
    """Re-enqueue documents an interrupted process left queued/processing.

    Called at startup so a restart mid-ingestion doesn't strand documents
    in a spinner forever. ``is_first`` is treated as False on recovery (the
    session's contract_text was already set on the original first pass, or
    will be harmlessly re-set)."""
    try:
        pending = persistence.list_unfinished_matter_documents()
    except Exception as e:
        print(f"[ingest] recovery scan failed: {e}", flush=True)
        return
    for d in pending:
        print(f"[ingest] recovering [M-{d['doc_index']}] {d['filename']!r} session={d['session_id']}", flush=True)
        _enqueue_ingestion(
            d["session_id"],
            d["id"],
            d["doc_index"],
            d["filename"] or "",
            False,
        )


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
            {"role": "user", "content": win},
        ]
        out, _, _ = llm_generate(msgs, max_new_tokens=6000)
        parsed = parse_json_lenient(out)
        if isinstance(parsed, list):
            for c in parsed:
                if isinstance(c, dict) and c.get("text"):
                    cid = f"{wi}.{c.get('id', len(clauses) + 1)}"
                    clauses.append(
                        {
                            "id": cid,
                            "title": c.get("title", "")[:200],
                            "text": c.get("text", ""),
                        }
                    )
    return clauses


def analyze_clause(clause_text: str) -> dict:
    """One LLM call to classify + identify issues for a clause."""
    msgs = [
        {"role": "system", "content": CLAUSE_ANALYZE_SYSTEM},
        {"role": "user", "content": clause_text},
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
            missing.append(
                {
                    "severity": "high",
                    "type": required,
                    "description": f"Klausel zum Thema '{required}' fehlt im Vertrag.",
                    "reason": reason,
                }
            )
    return missing


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class QueryReq(BaseModel):
    question: str
    session_id: str | None = None
    top_k: int = 3
    candidate_k: int = 30
    force_mode: str | None = None  # "rag" | "chat" | None (auto)
    # Optional answer-language override. ``None`` / ``"de"`` keeps the
    # default German prompts; ``"en"`` switches the model to English
    # while keeping German statute / contract quotations verbatim (see
    # ``_language_directive``). Unknown codes fall back to German so a
    # forward-compat frontend can ship new codes without crashing the
    # backend mid-rollout.
    target_language: str | None = None
    # Per-turn focus: when the FE just attached one or more documents in
    # the chat composer, it sends their doc_indexes here so this answer is
    # scoped to ONLY those documents — the manifest, retrieval, and the
    # validator's allowed-handles all narrow. Prevents the model from
    # silently pulling in pre-existing project docs when the user asks
    # "analyse this document". ``None``/empty ⇒ full session is in scope
    # (the prior behaviour).
    focus_doc_indexes: list[int] | None = None


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
    # 1-based page number this chunk's text was OCR'd from, when known
    # (matter documents transcribed by the VLM-OCR path). Lets the
    # citation panel scroll the PDF preview to the cited page. 0/None when
    # unknown (corpus chunks, text-layer PDFs, legacy uploads).
    page: int | None = None


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


class JurisdictionWarningOut(BaseModel):
    """One Bundesland-specific rule the model cited that doesn't match
    the matter's jurisdiction. Drives an amber warning chip above the
    bubble — same family as ``(unbelegt)`` but for jurisdictional
    sanity rather than source attribution.

    Populated by :func:`lai.common.jurisdiction.check_jurisdiction`. The
    canonical case is "10H BayBO" cited for a Niedersachsen matter —
    the lawyer's #2 v0 complaint.

    Attributes:
        rule_label: Human-readable rule name.
        rule_bundesland: The Bundesland the cited rule belongs to.
        expected_bundesland: The Bundesland the matter is actually in.
        excerpt: ~80 chars of context around the matching substring.
    """

    rule_label: str
    rule_bundesland: str
    expected_bundesland: str
    excerpt: str


class QueryResp(BaseModel):
    answer: str
    chunks: list[ChunkOut]
    timings: TimingsOut
    tokens: TokensOut
    session_id: str
    mode: str  # "chat" | "rag" | "contract" | "rag+contract"
    citation_validation: CitationValidationOut | None = None
    # Empty list when no Bundesland was detected for the matter OR when
    # the model didn't cite anything jurisdictionally suspect. Non-empty
    # is the actionable signal for the UI.
    jurisdiction_warnings: list[JurisdictionWarningOut] = []
    # ``messages.id`` of the persisted assistant row. Lets the UI scope
    # POST /feedback to a specific bubble rather than the whole session.
    # ``None`` only when the assistant message somehow failed to persist
    # (best-effort path; we never fail a query because of a write
    # hiccup) — the UI silently downgrades to session-level feedback in
    # that case.
    message_id: int | None = None


class UploadResp(BaseModel):
    session_id: str
    filename: str
    pages: int
    chunks: int
    message: str
    # The stable [M-n] handle the just-uploaded document was assigned.
    # The FE captures this on attachment so the very-next chat turn can
    # send ``focus_doc_indexes=[n]`` — scoping the answer to ONLY the
    # docs the user just attached, instead of every prior matter doc.
    doc_index: int
    # Set when the upload was a same-name match against an existing
    # matter_documents row. The FE pre-filter blocks most duplicates
    # already; this flag covers the second-tab / direct-API / race
    # cases where the bytes still hit the server. When true the server
    # SKIPPED save_matter_upload + _enqueue_ingestion (no double bytes,
    # no double chunks), so the row's state is whatever it already was,
    # and the FE renders a "already in the project" toast.
    deduplicated: bool = False


class SessionCreatedResp(BaseModel):
    session_id: str


class IssueOut(BaseModel):
    severity: str
    description: str
    recommendation: str | None = None
    reason: str | None = None
    type: str | None = None


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
    version: str | None = None  # "1" | "2" | None (defaults to env-driven)


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

    # ── Retrieval backend (Track-B pgvector swap, S-1) ──────────────────
    # The dense corpus retrieval now runs against pgvector
    # (corpus_child_chunks, HNSW halfvec(4000)) instead of the ~144 GB
    # in-RAM numpy matrix that load_embeddings() used to hold. The
    # SQLite connection is kept ONLY for lexical BM25 over the FTS5
    # index (small) and for parent-text is no longer needed in RAM —
    # parent passages are fetched from pgvector on demand per query.
    #
    # RetrievalClient reads the shared DB_* env (same DB the migration
    # wrote to). It opens its pool lazily on first query, so startup
    # stays fast and a transiently-unavailable Postgres doesn't block
    # boot — the first /query surfaces the connection error cleanly.
    print("[startup] wiring pgvector retrieval + BM25 FTS5...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(DB), check_same_thread=False)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    ensure_bm25_fts(conn)
    retrieval_client = RetrievalClient()
    if retrieval_client.ping():
        print("[startup]   pgvector reachable", flush=True)
        # Per-Matter (data-room) document index lives in the same pgvector
        # DB as the corpus. Ensure the table exists so uploads can index.
        try:
            retrieval_client.ensure_matter_table()
            print("[startup]   matter_chunks table ready", flush=True)
        except Exception as e:
            print(f"[startup]   WARNING matter_chunks ensure failed: {e}", flush=True)
    else:
        print("[startup]   WARNING pgvector not reachable yet — first /query will retry/fail cleanly", flush=True)
    print(f"[startup]   retrieval wiring: {time.time() - t0:.1f}s", flush=True)

    t0 = time.time()
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")
    print(f"[startup]   reranker: {time.time() - t0:.1f}s", flush=True)

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
        print(
            f"[startup]   LLM: loading LOCAL model from {LLM_LOCAL_PATH} "
            "(opt-in via LLM_LOCAL_PATH; remote endpoint disabled)",
            flush=True,
        )
        tok = AutoTokenizer.from_pretrained(LLM_LOCAL_PATH, trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        lm = AutoModelForCausalLM.from_pretrained(
            LLM_LOCAL_PATH,
            dtype=torch.bfloat16,
            device_map="cuda",
            trust_remote_code=True,
        ).eval()
        print(f"[startup]   LLM ready in {time.time() - t0:.1f}s", flush=True)
        STATE.update(
            conn=conn,
            retrieval_client=retrieval_client,
            reranker=reranker,
            lm=lm,
            tok=tok,
            llm_api_url=None,
            llm_model_name=LLM_LOCAL_PATH,
            llm_client=None,
        )
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
            ) from e
        # Build the shared SyncLlmClient. The OpenAI-compatible base
        # URL for vLLM is the endpoint root plus ``/v1``. Other
        # SyncLlmClient knobs (retry, timeout) come from
        # ``LAI_LLM_*`` env vars if the operator wants to tune them;
        # the defaults match the previous hand-rolled ``httpx.post``
        # behaviour closely enough for drop-in compatibility.
        # thinking_mode_enabled=False: the non-streaming /query path goes
        # through this client (llm_generate → client.generate). Without it
        # the Qwen3.x analyzer runs in thinking mode and returns
        # content=null (the whole token budget is spent on the <think>
        # trace), so llm_generate sees empty output, exhausts its retries,
        # and /query returns HTTP 500. The streaming + analyzer paths
        # already disable thinking (see the chat_template_kwargs blocks);
        # this path was the gap. Mirrors the DDiQ fix in 8d9c3e5.
        llm_client = SyncLlmClient(
            LlmConfig(
                base_url=f"{LLM_API_URL.rstrip('/')}/v1",
                model=LLM_MODEL,
                thinking_mode_enabled=False,
            ),
        )
        STATE.update(
            conn=conn,
            retrieval_client=retrieval_client,
            reranker=reranker,
            lm=None,
            tok=None,
            llm_api_url=LLM_API_URL,
            llm_model_name=LLM_MODEL,
            llm_client=llm_client,
        )

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
        print(f"[startup]   LLM warmup: {time.time() - t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[startup]   LLM warmup failed (non-fatal): {e}", flush=True)

    # Re-enqueue any document ingestion a previous process left mid-flight,
    # now that STATE (retrieval client, LLM url) is fully wired so the
    # background worker has everything it needs.
    _recover_unfinished_ingestion()

    # ── Auth subsystem (AUTH_PLAN §9 step 1-3) ──────────────────────────
    # Reuses the module-level :data:`_auth_config` and
    # :data:`_token_issuer` so the route-time ``get_current_user`` and
    # the per-request ``AuthDeps`` share a single issuer (one secret,
    # one verifier, no drift). Email config is optional: if
    # ``LAI_EMAIL_*`` env is absent, /auth/forgot-password still issues
    # reset tokens but nothing is mailed (logged loudly).
    print("[startup] auth: wiring router...", flush=True)
    try:
        email_config: _EmailConfig | None = _EmailConfig()
        print("[startup]   auth: email config loaded (Brevo enabled)", flush=True)
    except Exception as e:
        email_config = None
        print(f"[startup]   auth: email config NOT loaded ({e}) — /auth/forgot-password will not mail", flush=True)
    auth_pool = await _create_auth_pool()
    auth_deps = AuthDeps(
        auth_config=_auth_config,
        email_config=email_config,
        hasher=PasswordHasher(_auth_config),
        issuer=_token_issuer,
        pool=auth_pool,
    )
    app.include_router(build_auth_router(auth_deps, get_current_user=get_current_user))
    app.include_router(build_admin_router(auth_deps, get_current_user=get_current_user))
    app.include_router(build_share_router(auth_deps, get_current_user=get_current_user))
    app.state.auth_deps = auth_deps
    app.state.get_current_user = get_current_user
    print("[startup]   auth: router mounted at /auth/*", flush=True)

    # ── Resumable upload (tus 1.0) — Phase 2 of upload reliability ──────
    # Self-contained impl; the completion hook calls the same ingest
    # pipeline /upload uses. Gated client-side by VITE_RESUMABLE_UPLOAD.
    # See LAI/docs/UPLOAD_RESUMABLE_DESIGN.md.
    from lai.api.upload_tus import build_tus_router, gc_stale_uploads

    app.include_router(
        build_tus_router(
            get_current_user=get_current_user,
            finalize=_finalize_tus_upload,
        ),
        prefix="/tus",
    )
    # Best-effort cleanup of any uploads abandoned across a serve_rag
    # restart. Safe to run synchronously — typically a no-op or a handful
    # of orphan dirs.
    try:
        purged = gc_stale_uploads()
        if purged:
            print(f"[startup]   tus: gc'd {purged} stale upload(s)", flush=True)
    except Exception as e:
        print(f"[startup]   tus: gc skipped ({e})", flush=True)
    print("[startup]   tus: router mounted at /tus/files/*", flush=True)

    print("[startup] READY", flush=True)
    try:
        yield
    finally:
        # Shutdown — close the asyncpg auth pool and the pgvector
        # retrieval pool we opened above.
        await auth_pool.close()
        retrieval = STATE.get("retrieval_client")
        if retrieval is not None:
            retrieval.close()


app = FastAPI(lifespan=lifespan)
# Translate auth-module exceptions (InvalidCredentialsError, …) into
# uniform 401s at the app level. APIRouter has no app-scoped
# exception-handler API, so this lives here, not inside the router.
register_auth_exception_handlers(app)
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or [
    "http://192.168.178.82:5173",
    "http://localhost:5173",
    "http://localhost:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # tus 1.0 returns several headers the browser must let the SPA read.
    # Without this list, fetch() on the same origin works but cross-origin
    # tus calls (dev → 192.168.x.y) see ``upload.url`` as null and the
    # ``Upload-Metadata`` round-trip silently drops the doc_index.
    expose_headers=[
        "Location",
        "Upload-Offset",
        "Upload-Length",
        "Upload-Metadata",
        "Tus-Resumable",
        "Tus-Version",
        "Tus-Max-Size",
        "Tus-Extension",
    ],
)

# ── Prometheus HTTP-level instrumentation (TRACK_B_TIMING §6) ───────────────
# ``prometheus-fastapi-instrumentator`` adds ``http_requests_total`` +
# ``http_request_duration_seconds`` per route and exposes them at
# ``/metrics``. Domain-level RAG counters (validator alarms, feedback
# verdicts, retrieval depth) live in :mod:`lai.api.metrics` and are
# registered against the same default registry the instrumentator
# scrapes, so a single Prometheus scrape picks up both.
#
# ``/health`` and ``/metrics`` are excluded from histogramming — both
# are scraped on a tight cadence and would dominate the request
# histograms with high-frequency near-zero values that buries the
# /query signal we actually care about.
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    # Library missing in some constrained dev envs — log and continue;
    # the domain counters still emit, the /metrics endpoint is the only
    # casualty. Production wheels include the dependency.
    print("[warn] prometheus-fastapi-instrumentator not installed; /metrics disabled", flush=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    llm_ready = STATE["lm"] is not None or STATE["llm_api_url"] is not None
    retrieval = STATE.get("retrieval_client")
    retrieval_ready = retrieval.ping() if retrieval is not None else False
    return {
        "ok": True,
        "loaded": llm_ready,
        "llm_backend": "remote" if STATE["llm_api_url"] else "local",
        "llm_model": STATE["llm_model_name"],
        "retrieval_backend": "pgvector",
        "retrieval_ready": retrieval_ready,
        "n_sessions": persistence.count_sessions(),
    }


# Glyph-garbage markers from failed PDF text extraction: PostScript glyph
# names ("/a214"), runs of dingbats/symbols ("✗✘✙"), the U+FFFD
# replacement char. Used to drop unreadable corpus chunks at retrieval.
_GARBLE_RE = re.compile(r"/a\d{2,}|[✀-➿←-⇿⌀-⏿]{3,}|�")


_COMMON_WORDS_RE = re.compile(
    r"\b(der|die|das|und|von|für|fuer|ist|nicht|den|dem|des|ein|eine|einen|mit|"
    r"auf|im|zu|nach|bei|durch|werden|wird|sind|the|and|of|in|to|is|for|with|on)\b",
    re.IGNORECASE,
)


def _is_readable_passage(text: str) -> bool:
    """True if a corpus passage looks like real prose rather than PDF-
    extraction garbage.

    Two failure modes seen in the embedded corpus:
      1. Glyph-name / symbol-run / replacement-char garbage ("/a214",
         "✗✘✙", "�") — caught by ``_GARBLE_RE`` + the char-ratio check.
      2. Letter-salad OCR scramble — looks letter-like ("deUNJeND
         Weyseyr|sseqy Bunsynisny…") so it passes the ratio check, but is
         meaningless. Real DE/EN prose of any non-trivial length always
         contains common function words (der/die/und/the/of/…); scrambled
         text essentially never does, so we require at least one.
    """
    if not text or not text.strip():
        return False
    if _GARBLE_RE.search(text):
        return False
    readable = sum(1 for ch in text if ch.isalnum() or ch.isspace() or ch in ".,;:!?()[]{}§%-–/€$\"'„“”’")
    if readable / len(text) < 0.7:
        return False
    # Letter-salad guard: a passage long enough to be a real chunk must
    # contain at least one common German/English function word.
    return not (len(text) > 120 and not _COMMON_WORDS_RE.search(text))


def _do_rag(
    question: str,
    top_k: int,
    candidate_k: int,
) -> tuple[list[ChunkOut], list[RetrievedSource], TimingsOut]:
    """Run hybrid+rerank retrieval, return chunks, prompt-ready sources, timings.

    The second return value carries the same chunks as the first but as
    :class:`RetrievedSource` objects with stable ``[C-n]`` citation
    handles — that is what :func:`build_rag_messages` inlines into the
    prompt so the LLM sees and emits the exact handles the UI then
    renders as chips.
    """
    retrieval: RetrievalClient = STATE["retrieval_client"]
    bm25_conn = STATE["conn"]
    reranker = STATE["reranker"]

    # ── Embed query ─────────────────────────────────────────────────────
    t0 = time.time()
    qvec = embed_query(question, with_prefix=True)
    embed_s = time.time() - t0

    # ── Hybrid candidate generation (dense pgvector + lexical BM25) ─────
    # Both rankings live in child_id space (corpus_child_chunks.id ==
    # FTS5 rowid == legacy child_chunks.id — preserved by the migration),
    # so RRF fuses them directly with no in-RAM index. The dense side
    # carries parent_id + content already; BM25-only ids are hydrated
    # from pgvector in one batch below.
    t0 = time.time()
    t_a = time.perf_counter()
    dense_hits = retrieval.dense_search(qvec, top_k=candidate_k)
    dense_by_child: dict[int, RetrievedChunk] = {h.child_id: h for h in dense_hits}
    dense_ranking = [h.child_id for h in dense_hits]
    t_b = time.perf_counter()

    bm25_pairs = retrieve_bm25_ids(question, bm25_conn, candidate_k)
    bm25_ranking = [cid for cid, _ in bm25_pairs]
    t_c = time.perf_counter()

    fused = rrf_fuse([dense_ranking, bm25_ranking])[:candidate_k]
    cand_ids = [cid for cid, _ in fused]
    t_d = time.perf_counter()

    # Hydrate BM25-only candidates (those the dense query didn't surface)
    # so every candidate has a parent_id + content for rerank/dedupe.
    missing = [cid for cid in cand_ids if cid not in dense_by_child]
    if missing:
        hydrated = retrieval.fetch_children_by_id(missing)
        dense_by_child.update(hydrated)
    # Drop any candidate we still couldn't resolve (e.g. an FTS5 rowid
    # whose pgvector row hasn't been migrated yet — possible while topup
    # is mid-stream).
    cand_ids = [cid for cid in cand_ids if cid in dense_by_child]
    t_e = time.perf_counter()
    retrieve_s = time.time() - t0
    # Diagnostic — find where retrieve_s actually goes. Direct DB benchmarks
    # (2026-05-31) show dense ANN ~3 ms warm + BM25 ~30 ms; the smoke test
    # reports retrieve_s ~4 s, so the slow stage is whichever one this line
    # surfaces. Remove once the hot spot is fixed.
    print(
        f"[retrieve] dense={1000 * (t_b - t_a):.0f}ms "
        f"bm25={1000 * (t_c - t_b):.0f}ms "
        f"fuse={1000 * (t_d - t_c):.0f}ms "
        f"hydrate={1000 * (t_e - t_d):.0f}ms "
        f"missing={len(missing)} cand_k={candidate_k} "
        f"total={retrieve_s * 1000:.0f}ms",
        flush=True,
    )

    # ── Parent texts (rerank + prompt context) ─────────────────────────
    # The reranker scores (query, parent_passage) pairs; the prompt
    # quotes the parent passage. Both come from pgvector now, fetched in
    # one batched query keyed by the candidates' parent_ids.
    t0 = time.time()
    parent_ids = [dense_by_child[cid].parent_id for cid in cand_ids if dense_by_child[cid].parent_id is not None]
    parent_text = retrieval.fetch_parent_texts(parent_ids)

    # Rerank against parent text (falls back to child content for orphan
    # children whose parent_id is NULL or whose parent text is missing).
    def _passage_for(cid: int) -> str:
        chunk = dense_by_child[cid]
        if chunk.parent_id is not None and chunk.parent_id in parent_text:
            return parent_text[chunk.parent_id]
        return chunk.content

    # Drop candidates whose text is PDF-extraction garbage (glyph names
    # like "/a214", dingbat/symbol runs "✗✘✙", replacement chars). ~0.75%
    # of the embedded corpus is such garbage, and when a query happens to
    # retrieve it the model can only say "the sources are unintelligible
    # symbols" — a useless non-answer. Filtering here keeps the reranker
    # and the prompt clean. If EVERY candidate is garbage we keep them
    # (the model then honestly refuses rather than us returning nothing).
    _readable = [cid for cid in cand_ids if _is_readable_passage(_passage_for(cid))]
    if _readable:
        cand_ids = _readable

    pairs = [(question, _passage_for(cid)[:2000]) for cid in cand_ids]
    # ``reranked_ids`` + ``reranked_scores`` are kept aligned (one score
    # per id, in final rank order) so the output loop never has to index
    # back through ``order`` — that indexing is what made the fallback
    # path crash with UnboundLocalError.
    try:
        rerank_scores = reranker.score(pairs) if (reranker is not None and pairs) else []
        if rerank_scores:
            order = list(np.argsort(-np.asarray(rerank_scores)))
            reranked_ids = [cand_ids[j] for j in order]
            reranked_scores = [float(rerank_scores[j]) for j in order]
        else:
            reranked_ids = list(cand_ids)
            reranked_scores = [0.0] * len(cand_ids)
    except Exception as exc:
        # If the reranker fails — most commonly a CUDA OutOfMemoryError
        # when this in-process reranker shares a GPU with the analyzer
        # vLLM + the Step-6 embedding job — DO NOT let corpus retrieval
        # die. Killing it here silently turns LAI from a legal agent into
        # a document-only Q&A (every corpus question degrades to "not in
        # the uploaded documents", no [C-n]). Fall back to the hybrid RRF
        # order (dense + BM25), which is already a sound ranking; the
        # lawyer still gets cited [C-n] corpus passages, just without the
        # reranker's final re-ordering. Free the CUDA cache so a transient
        # spike doesn't wedge subsequent queries.
        print(
            f"[_do_rag] reranker.score failed ({type(exc).__name__}: {exc}); "
            f"falling back to RRF order — corpus retrieval continues",
            flush=True,
        )
        try:
            import torch as _torch

            if _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        except Exception:
            pass
        reranked_ids = list(cand_ids)
        # Descending RRF-position proxy so the score field still carries
        # a sensible ordering signal when the reranker is unavailable.
        reranked_scores = [1.0 / (i + 1) for i in range(len(reranked_ids))]
    rerank_s = time.time() - t0

    # ── Dedupe to top_k unique parents, build outputs ──────────────────
    dense_id_set = set(dense_by_child) & set(dense_ranking)
    bm25_id_set = set(bm25_ranking)
    chunks_out: list[ChunkOut] = []
    sources: list[RetrievedSource] = []
    seen_parents: set[int] = set()
    for rank_pos, cid in enumerate(reranked_ids):
        chunk = dense_by_child[cid]
        # Dedupe key: parent_id when present, else the child id itself
        # (orphan children have no parent to collapse on).
        dedupe_key = chunk.parent_id if chunk.parent_id is not None else -cid
        if dedupe_key in seen_parents:
            continue
        seen_parents.add(dedupe_key)

        passage = _passage_for(cid)[:1500]
        score = reranked_scores[rank_pos]
        in_dense = cid in dense_id_set
        in_bm25 = cid in bm25_id_set
        srcs = ["dense", "bm25"] if in_dense and in_bm25 else ["dense"] if in_dense else ["bm25"]
        section = f"Parent {chunk.parent_id}" if chunk.parent_id is not None else f"Child {cid}"
        cite_id = _corpus_cite_id(len(chunks_out) + 1)
        chunks_out.append(
            ChunkOut(
                text=passage,
                section=section,
                law_refs=[],
                sources=srcs,
                similarity=score,
                rerank_score=score,
                cite_id=cite_id,
                source_kind="corpus",
            )
        )
        sources.append(
            RetrievedSource(
                cite_id=cite_id,
                source_kind="corpus",
                text=passage,
                label="Rechtskorpus",
            )
        )
        if len(chunks_out) >= top_k:
            break

    return (
        chunks_out,
        sources,
        TimingsOut(
            embed_s=round(embed_s, 3),
            retrieve_s=round(retrieve_s, 3),
            rerank_s=round(rerank_s, 3),
            generate_s=0.0,
            total_s=0.0,
        ),
    )


@app.post("/query", response_model=QueryResp)
def query(req: QueryReq, user: CurrentUser = Depends(get_current_user)):
    if STATE["lm"] is None and STATE["llm_api_url"] is None:
        raise HTTPException(503, "Service still loading")

    sid = req.session_id or str(uuid.uuid4())
    uid = str(user.id)
    org_id = _org_or_none(user)
    # If the caller supplied a session_id, it MUST belong to them.
    # AUTH_PLAN G4: the session id alone is not a capability.
    if req.session_id and not persistence.session_exists(sid, user_id=uid):
        raise HTTPException(404, "session_id not found")
    t_total0 = time.time()

    # Decide mode.
    #
    # Document-first: once a document is uploaded, every question is
    # answered from the uploaded document(s) ALONE — the corpus stays
    # silent unless the user explicitly asks to look beyond their file
    # (``wants_corpus``: "compare with…", "what does the law require",
    # "is this market standard", "rechtsprechung", etc.). The corpus is
    # ready and one phrase away, but volunteering it produces misleading
    # answers — e.g. answering a lease-term question on a permit by
    # quoting an UNRELATED corpus contract. For a lawyer "not in the
    # document" beats a confidently wrong cross-source figure.
    use_contract = session_uses_contract(sid, req.question)
    # Answer language: explicit client override → detected question
    # language → soft mirror. Detecting server-side and emitting an
    # explicit directive is what stops an English question being
    # answered in German under a heavily-German prompt/manifest.
    answer_lang = _effective_language(req.target_language, req.question)
    if req.force_mode in ("rag", "chat"):
        use_rag = req.force_mode == "rag"
    elif use_contract:
        # Option B: a contract is in session, so the matter [M-n] is
        # always available (built below). Additionally consult the legal
        # corpus [C-n] when the question seeks legal/statutory knowledge
        # (statute refs, doctrine, applicability) — that's what makes LAI
        # a legal agent rather than a document-only Q&A. Pure
        # contract-extraction questions stay matter-only (corpus off) so
        # we don't answer a "what does this clause say?" with an
        # unrelated corpus contract.
        use_rag = is_legal_knowledge_question(req.question)
    else:
        use_rag = needs_rag(req.question)

    corpus_chunks: list[ChunkOut] = []
    corpus_sources: list[RetrievedSource] = []
    timings = TimingsOut(embed_s=0.0, retrieve_s=0.0, rerank_s=0.0, generate_s=0.0, total_s=0.0)

    if use_rag:
        corpus_chunks, corpus_sources, t = _do_rag(
            req.question,
            req.top_k,
            req.candidate_k,
        )
        timings.embed_s = t.embed_s
        timings.retrieve_s = t.retrieve_s
        timings.rerank_s = t.rerank_s

    # Matter side: fan out [M-1]..[M-n] across EVERY document in the
    # session ("Matter"), not just one. ``combined_text`` is the
    # concatenation used only for Bundesland detection below.
    matter_sources, matter_chunks, contract_text = _build_matter_context(
        sid,
        uid,
        use_contract,
        req.question,
        focus_doc_indexes=req.focus_doc_indexes,
    )

    # Wait-and-answer: a doc-scoped turn with no matter content yet because the
    # upload is still being OCR'd → wait (bounded) for ingestion to finish and
    # re-retrieve, so we answer automatically instead of refusing with "still
    # processing". A scanned PDF OCRs in ~90s. If it never becomes ready
    # (timeout/failure), the empty-retrieval guard below gives the honest
    # message. (Endpoints are sync/threadpool, so the blocking wait is safe.)
    if use_contract and not matter_sources and _matter_is_processing(sid, uid):
        print(
            f"[wait] session={sid} doc still ingesting — waiting up to {int(_MATTER_WAIT_S)}s before answering",
            flush=True,
        )
        if _await_matter_ready(sid, uid, _MATTER_WAIT_S):
            matter_sources, matter_chunks, contract_text = _build_matter_context(
                sid,
                uid,
                use_contract,
                req.question,
                focus_doc_indexes=req.focus_doc_indexes,
            )

    # Prior chat turns for the same session — gives the model conversational
    # memory across requests. Without this, every turn is stateless and
    # follow-ups like "tell me more about it" or "answer in English from now on"
    # are silently dropped. Loaded BEFORE we inject the current question so
    # the new turn isn't double-counted.
    history = _load_history(sid, user_id=uid)

    # Pinned session metadata — stable facts (user name, project, deadlines)
    # that survive even when their original turn rolls out of the 32-msg
    # rolling window. Cheap when the session is short or when the previous
    # extraction is still fresh; the refresh fires AFTER persisting the new
    # turn (below) so the freshly stated facts make it into the next refresh.
    meta_prefix = _matter_manifest_prefix(
        sid, uid, focus_doc_indexes=req.focus_doc_indexes
    ) + _format_session_meta_prefix(persistence.get_session_meta(sid, user_id=uid))

    # Matter sources come first so the LLM sees the user's own document
    # before the supporting corpus excerpts — and so the [M-n] handles
    # appear in the prompt in numerical order.
    rag_sources = matter_sources + corpus_sources

    if use_rag and use_contract:
        mode = "rag+contract"
        msgs = build_rag_messages(
            req.question, rag_sources, history=history, meta_prefix=meta_prefix, target_language=answer_lang
        )
    elif use_rag:
        mode = "rag"
        msgs = build_rag_messages(
            req.question, rag_sources, history=history, meta_prefix=meta_prefix, target_language=answer_lang
        )
    elif use_contract:
        mode = "contract"
        msgs = build_rag_messages(
            req.question,
            matter_sources,
            history=history,
            meta_prefix=meta_prefix,
            target_language=answer_lang,
            system=RAG_SYSTEM_DOC_ONLY,
        )
    else:
        mode = "chat"
        msgs = build_chat_messages(req.question, history=history, meta_prefix=meta_prefix, target_language=answer_lang)

    chunks_out: list[ChunkOut] = matter_chunks + corpus_chunks

    # Empty-retrieval guard: a doc-scoped turn with zero grounded sources
    # gets an honest "still processing / nothing relevant" message instead
    # of an LLM answer fabricated from general knowledge. See
    # :func:`_empty_grounding_guard`.
    guard_msg = _empty_grounding_guard(
        mode,
        matter_sources,
        rag_sources,
        sid,
        uid,
        answer_lang,
    )

    t0 = time.time()
    if guard_msg is not None:
        print(f"[guard] session={sid} mode={mode} empty-retrieval → honest refusal (no LLM call)", flush=True)
        answer, prompt_tokens, completion_tokens = guard_msg, 0, 0
    else:
        answer, prompt_tokens, completion_tokens = llm_generate(
            msgs, max_new_tokens=3000 if (use_rag or use_contract) else 1800
        )
    timings.generate_s = round(time.time() - t0, 3)

    # Day-4 server-side citation validator. Strip any [C-n]/[M-n] handles
    # the model emitted that did NOT appear among the prompt's actual
    # sources (i.e. fabricated handles), and mark the surrounding
    # sentence ``(unbelegt)`` so the reader knows the claim has no
    # underlying source. Only runs on grounded-mode turns (chat-only
    # has no sources to validate against, so nothing to strip); skipped
    # for a guard message (no LLM output, no sources).
    citation_validation_out: CitationValidationOut | None = None
    if guard_msg is None and rag_sources:
        # Allow every retrieved source PLUS every real uploaded matter document
        # (the manifest invites citing any [M-n], retrieved or not) — see
        # _all_matter_handles. Prevents real-document citations being stripped
        # as fabricated / mislabelled (unbelegt).
        allowed_handles = {src.cite_id for src in rag_sources} | _all_matter_handles(
            sid, uid, focus_doc_indexes=req.focus_doc_indexes
        )
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

    # Day-4 jurisdiction sanity gate. Catches the "10H BayBO cited for
    # a Niedersachsen project" failure family — independent of the
    # citation validator above (a citation can be perfectly resolved
    # against an [C-n] in the prompt and still be JURISDICTIONALLY
    # wrong if the cited statute is from the wrong Bundesland). Skipped
    # for a guard message (deterministic text, no statutes to check).
    jurisdiction_warnings = (
        []
        if guard_msg is not None
        else _run_jurisdiction_check(
            answer=answer,
            contract_text=contract_text,
            question=req.question,
            mode=mode,
            sid=sid,
        )
    )

    timings.total_s = round(time.time() - t_total0, 3)

    # Persist chat messages so the UI can rehydrate the thread on refresh.
    # If there's no session row yet (e.g. chat-only, no upload), create a
    # bare one first so the messages have somewhere to attach. Without this
    # every chat that didn't follow an /upload was getting silently dropped.
    # Best-effort; never fail the request because of a write hiccup.
    assistant_message_id: int | None = None
    try:
        if not persistence.session_exists(sid, user_id=uid):
            persistence.save_session(
                sid,
                {
                    "user_id": uid,  # Phase B: created_by (attribution).
                    "org_id": org_id,  # Phase B: firm-tenant visibility key.
                    "filename": None,  # chat-only session, no upload
                    "contract_text": None,
                    "n_pages": 0,
                    "tables": [],
                    "uploaded_at": time.time(),
                    "clauses": None,
                    "analysis": None,
                },
            )
        persistence.add_message(sid, "user", req.question, mode=mode, user_id=uid)
        # Capture the assistant row id so QueryResp can hand it to the
        # UI for POST /feedback wiring. ``add_message`` returns 0 only
        # on the ownership-check failure path (shouldn't fire here —
        # we just save_session'd above) which we map to None.
        _aid = persistence.add_message(
            sid, "assistant", answer, mode=mode, user_id=uid, chunks=[c.model_dump() for c in chunks_out]
        )
        assistant_message_id = _aid if _aid > 0 else None
    except Exception as e:
        print(f"[warn] failed to persist messages for {sid}: {e}", flush=True)

    # After persisting the new turn, refresh the pinned session metadata if
    # it's stale (every N user turns). This way the facts the user JUST
    # stated are part of the extraction context, and the next /query call
    # picks up the refreshed pin. Inline because it's a single LLM call;
    # if it ever becomes hot enough to matter we can move it to a worker.
    _maybe_refresh_session_metadata(sid, user_id=uid)

    # ── Domain-level metrics (TRACK_B_TIMING §6) ────────────────────────
    # Emit AFTER the request is fully assembled so failure-path requests
    # never bump the success counters (they raise above and never reach
    # here). Status is therefore always ``success`` at this point.
    _emit_query_metrics(
        mode=mode,
        language=answer_lang or "de",
        timings=timings,
        session_id=sid,
        focus_doc_indexes=req.focus_doc_indexes,
        chunks_returned=len(chunks_out),
        citation_validation=citation_validation_out,
        jurisdiction_warnings=jurisdiction_warnings,
        user_id=str(user.id),
        org_id=_org_or_none(user),
    )

    return QueryResp(
        answer=answer,
        chunks=chunks_out,
        timings=timings,
        tokens=TokensOut(prompt=prompt_tokens, completion=completion_tokens),
        session_id=sid,
        mode=mode,
        citation_validation=citation_validation_out,
        jurisdiction_warnings=jurisdiction_warnings,
        message_id=assistant_message_id,
    )


# Slow-query telemetry threshold (Phase 1.5). A turn whose end-to-end latency
# meets or exceeds this emits one structured JSON line to stdout (→
# logs/host/serve_rag.log) with the per-stage breakdown, so a regression like
# the reranker silently falling back to CPU (which shows up as a huge
# rerank_ms) is visible in the logs before a user complains. Override with
# LAI_SLOW_QUERY_S.
_SLOW_QUERY_S = float(os.getenv("LAI_SLOW_QUERY_S", "30"))


def _emit_query_metrics(
    *,
    mode: str,
    language: str,
    timings: TimingsOut,
    session_id: str,
    chunks_returned: int,
    citation_validation: CitationValidationOut | None,
    jurisdiction_warnings: list[JurisdictionWarningOut],
    focus_doc_indexes: list[int] | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """Bump every domain-level counter / histogram for one completed turn, emit
    a structured slow-query record when the turn crosses _SLOW_QUERY_S, and
    append a best-effort audit row.

    Centralised so the two query endpoints (/query and /query/stream)
    emit identical metrics — divergence would silently break the Grafana
    dashboard the moment a user switched between JSON and SSE.
    """
    rag_metrics.query_total.labels(
        mode=mode,
        language=language,
        status="success",
    ).inc()
    rag_metrics.query_latency_seconds.labels(mode=mode).observe(timings.total_s)
    rag_metrics.retrieval_chunks_returned.observe(chunks_returned)

    if citation_validation and citation_validation.sentences_flagged > 0:
        rag_metrics.citation_unbelegt_responses_total.inc()
        rag_metrics.citation_unbelegt_sentences_total.inc(citation_validation.sentences_flagged)

    if jurisdiction_warnings:
        rag_metrics.jurisdiction_warnings_responses_total.inc()
        rag_metrics.jurisdiction_warnings_total.inc(len(jurisdiction_warnings))

    # Slow-query structured log — one JSON line per slow turn (Phase 1.5). The
    # per-stage breakdown is what makes it actionable: a slow turn that is all
    # rerank_ms points straight at the reranker (e.g. CPU fallback), whereas
    # all generate_ms points at the LLM.
    if timings.total_s >= _SLOW_QUERY_S:
        print(
            json.dumps(
                {
                    "event": "slow_query",
                    "session_id": session_id,
                    "mode": mode,
                    "focus_doc_indexes": focus_doc_indexes,
                    "embed_ms": round(timings.embed_s * 1000),
                    "retrieve_ms": round(timings.retrieve_s * 1000),
                    "rerank_ms": round(timings.rerank_s * 1000),
                    "generate_ms": round(timings.generate_s * 1000),
                    "total_ms": round(timings.total_s * 1000),
                    "chunks_returned": chunks_returned,
                }
            ),
            flush=True,
        )

    # Append-only audit trail (Phase 2.3) — best-effort, never blocks the turn.
    audit.record_sync(
        action="query",
        user_id=user_id,
        org_id=org_id,
        session_id=session_id,
        latency_ms=round(timings.total_s * 1000),
        detail={"mode": mode, "chunks": chunks_returned},
    )


def _run_jurisdiction_check(
    *,
    answer: str,
    contract_text: str,
    question: str,
    mode: str,
    sid: str,
) -> list[JurisdictionWarningOut]:
    """Detect the matter's Bundesland from session context and warn on
    Bundesland-specific rules cited for a different state.

    The matter's Bundesland is inferred from the uploaded contract text
    first (most reliable), the user's question second, falling back to
    None — which disables the check.
    """
    detected = detect_bundesland(contract_text or "") or detect_bundesland(question)
    if detected is None:
        return []
    warnings = check_jurisdiction(answer, detected)
    if not warnings:
        return []
    print(
        f"[jurisdiction] session={sid} mode={mode} expected={detected} warnings={[w.rule_label for w in warnings]}",
        flush=True,
    )
    return [
        JurisdictionWarningOut(
            rule_label=w.rule_label,
            rule_bundesland=w.rule_bundesland,
            expected_bundesland=w.expected_bundesland,
            excerpt=w.excerpt,
        )
        for w in warnings
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Streaming chat (Day-2 strategy doc deliverable)
# ─────────────────────────────────────────────────────────────────────────────
#
# Parallel SSE endpoint to ``/query``. Behaviour is identical apart from
# the wire shape: the answer arrives as a stream of ``event: token``
# deltas while the model generates, followed by a single ``event:
# complete`` carrying chunks + citation-validation + timings + tokens.
#
# Why a parallel endpoint and not a flag on /query:
#   1. SSE response shape is fundamentally different from JSON. Folding
#      both into one route would force every caller through the SSE
#      parser even when they just want the JSON.
#   2. The :class:`SyncLlmClient` does not currently expose streaming
#      (its body always sets ``stream: False``). Adding streaming to
#      ``lai.common.llm`` is a larger refactor; we bypass the client
#      here and call vLLM directly with ``httpx.stream()`` so the demo
#      gets the perceived-speed win without a foundation rewrite.
#   3. Citation validator runs on the COMPLETE answer (it splits on
#      sentence boundaries and rewrites). So the stream emits raw
#      tokens; only the terminal ``complete`` event carries the
#      validated answer + ``citation_validation`` summary. The
#      frontend renders rough text during stream, then swaps in the
#      sanitised version + chips on ``complete``.
#
# Local-transformers path: streaming is not implemented. Callers hit
# the standard ``/query`` endpoint for that backend — the
# transformers model path is opt-in and very rarely used in
# production.


def _sse_event(event: str, data: object) -> bytes:
    """Encode one SSE message. ``event:`` line then ``data:`` payload,
    terminated with a blank line. Always UTF-8.
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _stream_vllm_chat(
    messages: list[dict],
    max_new_tokens: int,
):
    """Generator that yields token deltas from the remote vLLM endpoint.

    Yields raw ``str`` content fragments. Caller wraps in SSE.

    Uses ``httpx.stream`` against the OpenAI-compatible
    ``/v1/chat/completions`` endpoint with ``stream: True``. vLLM
    emits OpenAI-shaped ``data:`` SSE lines; each carries one chunk
    of the response with ``choices[0].delta.content``. The terminal
    line is ``data: [DONE]``.
    """
    if STATE["llm_api_url"] is None:
        raise RuntimeError("streaming requires the remote vLLM path; local transformers path is non-streaming")

    url = STATE["llm_api_url"].rstrip("/") + "/v1/chat/completions"
    msgs = _messages_for_remote_model(messages, STATE["llm_model_name"])
    body = {
        "model": STATE["llm_model_name"],
        "messages": msgs,
        "max_tokens": max_new_tokens,
        "temperature": 0.0,
        "stream": True,
        # Match the non-streaming path: thinking mode off for /query.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.stream("POST", url, json=body, timeout=600.0) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            # vLLM SSE lines come as ``data: { ... }`` or ``data: [DONE]``;
            # blank lines and ``: keepalive`` style comments are dropped.
            if not raw_line:
                continue
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                return
            try:
                obj = json.loads(payload)
            except ValueError:
                # Defensive: skip a malformed chunk rather than blow up
                # the whole stream. The model occasionally emits
                # zero-length deltas at the boundaries.
                continue
            try:
                delta = obj["choices"][0].get("delta") or {}
            except (KeyError, IndexError, TypeError):
                continue
            content = delta.get("content")
            if content:
                yield content


@app.post("/query/stream")
def query_stream(req: QueryReq, user: CurrentUser = Depends(get_current_user)):
    """SSE companion to :func:`query`. Wire format:

        event: token
        data: {"delta": "..."}

        event: token
        data: {"delta": "..."}

        ...

        event: complete
        data: {
            "answer": "<validated, with [C-n]/[M-n] tags>",
            "chunks": [...],                       # same as /query
            "citation_validation": {...} | null,   # same as /query
            "timings": {...},
            "tokens": {"prompt": int, "completion": int},
            "session_id": str,
            "mode": "rag" | "rag+contract" | "contract" | "chat"
        }

    The ``token`` events carry the RAW model output (before citation
    validation) so the UI can render progressively. The ``complete``
    event carries the validated answer — the frontend swaps the
    rough text for the validated version once it arrives.

    Error events:
        event: error
        data: {"detail": "..."}
    """
    if STATE["llm_api_url"] is None:
        # Local-transformers path doesn't stream; tell the caller
        # cleanly so they can fall back to /query.
        raise HTTPException(
            501,
            "streaming requires the remote vLLM path (LLM_API_URL); local transformers path is non-streaming. "
            "Fall back to POST /query.",
        )

    sid = req.session_id or str(uuid.uuid4())
    uid = str(user.id)
    org_id = _org_or_none(user)
    if req.session_id and not persistence.session_exists(sid, user_id=uid):
        raise HTTPException(404, "session_id not found")

    # ── Same retrieval + matter assembly as /query ──────────────────────
    t_total0 = time.time()
    # Document-first (mirrors /query): doc-only by default once a Matter
    # exists; corpus [C-n] only when the user explicitly asks for it.
    use_contract = session_uses_contract(sid, req.question)
    # Answer language: explicit client override → detected question
    # language → soft mirror. Detecting server-side and emitting an
    # explicit directive is what stops an English question being
    # answered in German under a heavily-German prompt/manifest.
    answer_lang = _effective_language(req.target_language, req.question)
    if req.force_mode in ("rag", "chat"):
        use_rag = req.force_mode == "rag"
    elif use_contract:
        # Match /query: consult the corpus for any legal-knowledge question
        # (statute refs, legal doctrine, "what does X mean legally / is it
        # gesetzlich geregelt", market practice), not only the narrow
        # explicit-corpus phrases. Pure contract-extraction stays doc-only.
        use_rag = is_legal_knowledge_question(req.question)
    else:
        use_rag = needs_rag(req.question)

    # Heavy retrieval (embed + dense/BM25 + rerank) and matter assembly run
    # INSIDE the generator below — not here — so the SSE response starts
    # immediately and we can emit ``status`` frames during that (rerank-bound)
    # window instead of leaving the client on "thinking…" dots until the first
    # token. A status frame doubles as a watchdog re-arm on the client.

    def _generator():
        """Yield SSE bytes for the lifetime of the request."""
        t0 = time.time()
        accumulated: list[str] = []

        # ── Retrieval + matter assembly. Narrate it: emit "searching" first
        #    so the user sees activity instead of dead air before the first
        #    token. Variables are local to the generator; the in-stream OCR
        #    wait below rebinds them after re-retrieval.
        yield _sse_event(
            "status",
            {
                "state": "retrieving",
                "message": "Durchsuche Dokumente und Wissensbasis…",
            },
        )
        corpus_chunks: list[ChunkOut] = []
        corpus_sources: list[RetrievedSource] = []
        timings = TimingsOut(embed_s=0.0, retrieve_s=0.0, rerank_s=0.0, generate_s=0.0, total_s=0.0)
        try:
            if use_rag:
                corpus_chunks, corpus_sources, t = _do_rag(
                    req.question,
                    req.top_k,
                    req.candidate_k,
                )
                timings.embed_s = t.embed_s
                timings.retrieve_s = t.retrieve_s
                timings.rerank_s = t.rerank_s
            # Matter side: [M-1]..[M-n] across every document in the session.
            matter_sources, matter_chunks, contract_text = _build_matter_context(
                sid,
                uid,
                use_contract,
                req.question,
                focus_doc_indexes=req.focus_doc_indexes,
            )
        except Exception as exc:
            yield _sse_event("error", {"detail": f"retrieval: {exc}"})
            return

        history = _load_history(sid, user_id=uid)
        meta_prefix = _matter_manifest_prefix(
            sid, uid, focus_doc_indexes=req.focus_doc_indexes
        ) + _format_session_meta_prefix(persistence.get_session_meta(sid, user_id=uid))
        rag_sources = matter_sources + corpus_sources
        mode, msgs = _build_turn_msgs(
            use_rag,
            use_contract,
            req.question,
            rag_sources,
            matter_sources,
            history,
            meta_prefix,
            answer_lang,
        )
        chunks_out: list[ChunkOut] = matter_chunks + corpus_chunks
        max_new_tokens = 3000 if (use_rag or use_contract) else 1800
        # Empty-retrieval guard: a doc-scoped turn with no grounded sources is
        # answered with an honest message, not an LLM fabrication.
        guard_msg = _empty_grounding_guard(
            mode,
            matter_sources,
            rag_sources,
            sid,
            uid,
            answer_lang,
        )

        # In-stream wait-and-answer: if a doc-scoped turn has no matter content
        # yet because the upload is still being OCR'd, emit a ``status`` event
        # each poll — it carries the live "Seite X/Y" page progress AND, being
        # a real SSE frame, doubles as a heartbeat so a reverse proxy / client
        # doesn't time out the wait. Then re-retrieve + reassemble the turn so
        # the answer streams in automatically. Guard handles timeout/failure.
        if use_contract and not matter_sources and _matter_is_processing(sid, uid):
            print(
                f"[wait] session={sid} stream=1 doc ingesting — streaming progress up to {int(_MATTER_WAIT_S)}s",
                flush=True,
            )
            deadline = time.time() + _MATTER_WAIT_S
            _notice_sent = False
            while time.time() < deadline and _matter_is_processing(sid, uid):
                pdone, ptotal = _matter_progress(sid, uid)
                # 'status' event: live page progress (rendered once the client
                # handles 'status') AND a real SSE frame that keeps a reverse
                # proxy from timing out the wait.
                yield _sse_event(
                    "status",
                    {
                        "state": "processing",
                        "pages_done": pdone,
                        "pages_total": ptotal,
                        "message": (
                            f"Dokument wird verarbeitet… Seite {pdone}/{ptotal}"
                            if ptotal
                            else "Dokument wird verarbeitet…"
                        ),
                    },
                )
                # The client's 60s stall-watchdog is re-armed only on 'token'
                # events, so emit one here too. The first carries a visible
                # notice; the rest are empty (re-arm only). These are streamed
                # but NOT added to ``accumulated`` — the saved/validated answer
                # stays clean (the client replaces the bubble on 'complete').
                if not _notice_sent:
                    yield _sse_event(
                        "token",
                        {
                            "delta": (
                                "⏳ *Ihr Dokument wird noch verarbeitet — einen Moment, "
                                "ich beantworte Ihre Frage automatisch, sobald es bereit ist.*\n\n"
                            )
                        },
                    )
                    _notice_sent = True
                else:
                    yield _sse_event("token", {"delta": ""})
                time.sleep(_MATTER_WAIT_POLL_S)
            matter_sources, matter_chunks, contract_text = _build_matter_context(
                sid, uid, use_contract, req.question, focus_doc_indexes=req.focus_doc_indexes
            )
            rag_sources = matter_sources + corpus_sources
            mode, msgs = _build_turn_msgs(
                use_rag, use_contract, req.question, rag_sources, matter_sources, history, meta_prefix, answer_lang
            )
            chunks_out = matter_chunks + corpus_chunks
            guard_msg = _empty_grounding_guard(mode, matter_sources, rag_sources, sid, uid, answer_lang)

        if guard_msg is not None:
            print(
                f"[guard] session={sid} mode={mode} stream=1 empty-retrieval → honest refusal (no LLM call)", flush=True
            )
            accumulated.append(guard_msg)
            yield _sse_event("token", {"delta": guard_msg})
        else:
            # Retrieval/rerank is done; bridge the LLM time-to-first-token gap
            # so the bubble shows "formulating" rather than reverting to bare
            # dots (self-suppresses on the FE once the first token arrives).
            yield _sse_event(
                "status",
                {
                    "state": "generating",
                    "message": "Formuliere Antwort…",
                },
            )
            try:
                for delta in _stream_vllm_chat(msgs, max_new_tokens):
                    # ``<think>`` traces only appear when thinking mode is
                    # ON; we explicitly disable it above so streaming
                    # output is the user-facing answer directly. As a
                    # belt-and-braces measure the post-stream strip below
                    # still applies ``strip_think`` so a stray trace
                    # survived in the recorded answer wouldn't leak.
                    accumulated.append(delta)
                    yield _sse_event("token", {"delta": delta})
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                yield _sse_event("error", {"detail": f"transport: {exc}"})
                return
            except httpx.HTTPStatusError as exc:
                yield _sse_event(
                    "error",
                    {
                        "detail": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                    },
                )
                return
            except Exception as exc:
                yield _sse_event("error", {"detail": str(exc)})
                return

        # ── Post-stream: validate, persist, emit terminal event ──────
        raw_answer = "".join(accumulated)
        # In case Qwen3 leaks a partial <think> trace despite the
        # config flag, strip it. ``_strip_reasoning_trace`` mirrors
        # the non-streaming path's behaviour.
        answer = _strip_reasoning_trace(raw_answer).strip()
        timings.generate_s = round(time.time() - t0, 3)

        citation_validation_out: CitationValidationOut | None = None
        if guard_msg is None and rag_sources:
            # Allow every retrieved source PLUS every real uploaded matter
            # document (the manifest invites citing any [M-n], retrieved or
            # not) — see _all_matter_handles. Prevents real-document citations
            # being stripped as fabricated / mislabelled (unbelegt).
            allowed = {src.cite_id for src in rag_sources} | _all_matter_handles(
                sid, uid, focus_doc_indexes=req.focus_doc_indexes
            )
            validation = validate_citations(answer, allowed)
            if validation.fabricated:
                print(
                    f"[citation] session={sid} mode={mode} stream=1 "
                    f"fabricated={list(validation.fabricated)} "
                    f"flagged_sentences={validation.sentences_flagged}",
                    flush=True,
                )
            answer = validation.text
            citation_validation_out = CitationValidationOut(
                allowed=sorted(allowed),
                emitted=list(validation.emitted),
                fabricated=list(validation.fabricated),
                sentences_flagged=validation.sentences_flagged,
            )

        jurisdiction_warnings = (
            []
            if guard_msg is not None
            else _run_jurisdiction_check(
                answer=answer,
                contract_text=contract_text,
                question=req.question,
                mode=mode,
                sid=sid,
            )
        )

        timings.total_s = round(time.time() - t_total0, 3)

        # Persist exactly as /query does. Best-effort — never fail
        # the SSE stream because of a write hiccup.
        assistant_message_id: int | None = None
        try:
            if not persistence.session_exists(sid, user_id=uid):
                persistence.save_session(
                    sid,
                    {
                        "user_id": uid,
                        "org_id": org_id,
                        "filename": None,
                        "contract_text": None,
                        "n_pages": 0,
                        "tables": [],
                        "uploaded_at": time.time(),
                        "clauses": None,
                        "analysis": None,
                    },
                )
            persistence.add_message(sid, "user", req.question, mode=mode, user_id=uid)
            _aid = persistence.add_message(
                sid, "assistant", answer, mode=mode, user_id=uid, chunks=[c.model_dump() for c in chunks_out]
            )
            assistant_message_id = _aid if _aid > 0 else None
        except Exception as exc:
            print(f"[warn] stream: failed to persist messages for {sid}: {exc}", flush=True)

        _maybe_refresh_session_metadata(sid, user_id=uid)

        # Domain-level metrics — same emission as the non-streaming
        # path so a Grafana panel that sums over /query and
        # /query/stream sees one coherent number.
        _emit_query_metrics(
            mode=mode,
            language=answer_lang or "de",
            timings=timings,
            session_id=sid,
            focus_doc_indexes=req.focus_doc_indexes,
            chunks_returned=len(chunks_out),
            citation_validation=citation_validation_out,
            jurisdiction_warnings=jurisdiction_warnings,
            user_id=uid,
            org_id=org_id,
        )

        # Token counts: prompt is approximate from the assembled
        # messages; completion is approximate from the answer. Same
        # rationale as the non-streaming path's helpers.
        prompt_chars = sum(len(m.get("content") or "") for m in msgs)
        complete_payload: dict[str, object] = {
            "answer": answer,
            "chunks": [c.model_dump() for c in chunks_out],
            "citation_validation": (citation_validation_out.model_dump() if citation_validation_out else None),
            "jurisdiction_warnings": [w.model_dump() for w in jurisdiction_warnings],
            "timings": timings.model_dump(),
            "tokens": {
                "prompt": _approx_token_count_from_chars(prompt_chars),
                "completion": _approx_token_count(answer),
            },
            "session_id": sid,
            "mode": mode,
            # Same field /query returns — lets the UI scope POST /feedback
            # to a specific bubble. None when persistence failed (best-effort).
            "message_id": assistant_message_id,
        }
        yield _sse_event("complete", complete_payload)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering so events arrive promptly. nginx,
            # cloudflare etc. otherwise hold SSE in 8 KB buffers and
            # the stream feels broken on cold connections.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/sessions", response_model=SessionCreatedResp)
async def create_session(
    user: CurrentUser = Depends(get_current_user),
) -> SessionCreatedResp:
    """Mint an empty session row and return its id.

    Why: the folder/multi-file drop flow needs to know ``session_id``
    BEFORE any upload's response arrives, so the FE can fire all N
    uploads in parallel against ONE session. Without this, parallel
    uploads to a brand-new conversation each race to create their own
    session and N files scatter across N data rooms.

    The row is created empty (no filename / contract_text). The first
    upload that follows treats it exactly like a pre-existing session —
    no legacy ``save_session`` overwrite of the filename, no soft race
    on the legacy single-doc disk blob. Owner is the caller; org is
    stamped from their JWT.
    """
    sid = str(uuid.uuid4())
    persistence.save_session(
        sid,
        {
            "user_id": str(user.id),
            "org_id": _org_or_none(user),
            "filename": None,
            "contract_text": None,
            "n_pages": 0,
            "tables": [],
            "uploaded_at": time.time(),
            "clauses": None,
            "analysis": None,
        },
    )
    return SessionCreatedResp(session_id=sid)


def _finalize_tus_upload(
    user: CurrentUser,
    info: dict,
    file_bytes: bytes,
) -> dict:
    """Completion hook for the tus router. Mirrors the synchronous half of
    POST /upload — validate session ownership, register a matter_documents
    row, persist the blob, enqueue background ingestion — and returns
    ``{"doc_index", "session_id"}`` for the client.

    Kept inline in serve_rag (not in upload_tus.py) because it touches
    nearly every module-local helper (persistence, _enqueue_ingestion,
    _org_or_none) — moving it would force a bunch of cross-imports for
    no readability gain. The legacy /upload body could be refactored to
    share this helper later; for now they are intentional duplicates so
    a fix to one doesn't accidentally break the other.
    """
    uid = str(user.id)
    org_id = _org_or_none(user)
    sid = info.get("session_id") or str(uuid.uuid4())
    session_already_exists = persistence.session_exists(sid, user_id=uid)
    if info.get("session_id") and not session_already_exists:
        raise HTTPException(404, "session_id not found")
    if len(file_bytes) == 0:
        # Same guard as POST /upload — refuse to register a matter row
        # for a zero-byte upload. The tus router shouldn't normally let
        # a 0-byte upload reach completion (Upload-Length=0 is rejected
        # at create), but a half-aborted PATCH leaving 0 bytes can
        # technically still land here.
        raise HTTPException(422, "Uploaded file is empty (0 bytes received)")
    if len(file_bytes) > 100 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 100 MB)")
    fname = Path(info.get("filename") or "uploaded.bin").name or "uploaded.bin"

    is_legacy_first_upload = not session_already_exists
    upload_ext = (
        persistence.save_upload(sid, file_bytes, fname)
        if is_legacy_first_upload
        else (Path(fname).suffix.lower() or ".bin")
    )
    if is_legacy_first_upload:
        persistence.save_session(
            sid,
            {
                "user_id": uid,
                "org_id": org_id,
                "filename": fname,
                "contract_text": None,
                "n_pages": 0,
                "tables": [],
                "uploaded_at": time.time(),
                "clauses": None,
                "analysis": None,
                "upload_ext": upload_ext,
            },
        )
    doc = persistence.add_matter_document(
        sid,
        filename=fname,
        doc_text="",
        n_pages=0,
        upload_ext=upload_ext,
        user_id=uid,
        org_id=org_id,
        status="queued",
    )
    if doc is None:
        raise HTTPException(404, "session_id not found")
    # See POST /upload above — same rationale: skip blob copy and
    # ingestion enqueue when add_matter_document reported a same-name
    # hit, so we don't overwrite the on-disk bytes or duplicate the
    # matter_chunks rows for the existing [M-n].
    if doc.get("__dedup_existing"):
        return {
            "doc_index": str(doc["doc_index"]),
            "session_id": sid,
            "deduplicated": True,
        }
    # Failed-row retry — wipe pgvector chunks (defensive), reset state,
    # then fall through to overwrite the blob + re-enqueue ingestion.
    # See POST /upload for the long rationale.
    if doc.get("__dedup_failed_retry"):
        rc = STATE.get("retrieval_client")
        if rc is not None:
            try:
                rc.delete_matter_chunks(sid, doc_index=int(doc["doc_index"]))
            except Exception as exc:
                print(
                    f"[retry] matter_chunks cleanup failed for {sid}/M-{doc['doc_index']}: {exc}",
                    flush=True,
                )
        persistence.reset_matter_document_for_retry(doc["id"])
    persistence.save_matter_upload(sid, doc["id"], file_bytes, fname)
    persistence.set_session_filename_if_unset(sid, fname)
    _enqueue_ingestion(sid, doc["id"], doc["doc_index"], fname, True)
    return {"doc_index": str(doc["doc_index"]), "session_id": sid}


@app.post("/upload", response_model=UploadResp)
async def upload(
    file: UploadFile = File(...),
    session_id: str | None = Form(None),
    user: CurrentUser = Depends(get_current_user),
):
    uid = str(user.id)
    org_id = _org_or_none(user)
    sid = session_id or str(uuid.uuid4())
    # If the caller supplied an existing session_id, it MUST be theirs.
    session_already_exists = persistence.session_exists(sid, user_id=uid)
    if session_id and not session_already_exists:
        raise HTTPException(404, "session_id not found")

    contents = await file.read()
    if len(contents) == 0:
        # Reject empty uploads BEFORE the matter row is created. The
        # browser sometimes sends a multipart with a 0-byte body (drag
        # cancelled mid-stream, file was concurrently deleted on disk,
        # or a flaky network closed the stream after headers but before
        # bytes). Without this guard the row gets created, an empty
        # file gets persisted, Docling chokes 30s later with the
        # cryptic "Input document … is not valid", and the user sees
        # "Fehlgeschlagen" with no actionable reason. 422 is the right
        # status — the request was syntactically valid but semantically
        # rejected.
        raise HTTPException(422, "Uploaded file is empty (0 bytes received)")
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 100 MB)")
    # ``Path(...).name`` strips any folder prefix the browser may have baked
    # into the multipart filename. Observed in Chromium on macOS when a user
    # drags a FOLDER: the resulting ``File.name`` was the relative path
    # (e.g. ``lai-test-drop/01_foo.pdf``) instead of the leaf. The prefix
    # then flowed through to ``matter_documents.filename`` and the FE chip's
    # filename⇄backend lookup missed (filenames didn't match), so every file
    # showed "Not in this session — upload again" forever. Normalizing here
    # makes the leaf canonical regardless of which browser or drag-source
    # built the multipart. The FE has the same leaf-rebuild in the folder
    # walker (``lib/dropFiles.ts``); this is the server-side belt to its
    # client-side suspenders.
    fname = Path(file.filename or "uploaded.pdf").name or "uploaded.pdf"

    # ── Non-blocking ingestion ──────────────────────────────────────────
    # We do ONLY the fast, synchronous work here (save bytes, create the
    # session + a 'queued' matter_documents row) and return immediately.
    # The slow OCR + embed + index runs in the background pool so a single
    # upload — or 2000 of them — never hangs the UI. The client polls
    # GET /sessions/{id}/documents for per-document status + progress.
    #
    # Legacy single-doc bookkeeping (the ``<sid><ext>`` blob on disk +
    # ``sessions.filename``) is only relevant when this /upload is the
    # one creating the session — i.e. the historical "POST /upload with
    # no session_id" flow. When the caller pre-created the session via
    # POST /sessions (the parallel-upload flow), we skip both: writing
    # them concurrently from N parallel uploads would race on
    # ``sessions.filename`` (last-write-wins) and on the legacy disk
    # path. ``add_matter_document`` itself is race-safe (atomic
    # MAX(doc_index)+1 under the connection lock + a UNIQUE index).
    is_legacy_first_upload = not session_already_exists

    upload_ext = (
        persistence.save_upload(sid, contents, fname)
        if is_legacy_first_upload
        else (Path(fname).suffix.lower() or ".bin")
    )

    if is_legacy_first_upload:
        # Session created with empty contract_text; the worker fills it
        # from the first document once OCR completes.
        persistence.save_session(
            sid,
            {
                "user_id": uid,
                "org_id": org_id,
                "filename": fname,
                "contract_text": None,
                "n_pages": 0,
                "tables": [],
                "uploaded_at": time.time(),
                "clauses": None,
                "analysis": None,
                "upload_ext": upload_ext,
            },
        )

    doc = persistence.add_matter_document(
        sid,
        filename=fname,
        doc_text="",
        n_pages=0,
        upload_ext=upload_ext,
        user_id=uid,
        org_id=org_id,
        status="queued",
    )
    if doc is None:
        raise HTTPException(404, "session_id not found")
    # Same-name dedup hit — the file is already in this matter. Skip the
    # blob copy + ingestion enqueue so we don't (a) overwrite the on-disk
    # bytes with whatever the second caller uploaded (which may be
    # different content under the same name — silent overwrite is worse
    # than rejecting), and (b) re-index the doc, which would append a
    # second set of chunks to matter_chunks under the same doc_id.
    # Returns the existing [M-n] handle so the FE can mark its
    # optimistic chip "ready" without re-uploading.
    if doc.get("__dedup_existing"):
        return UploadResp(
            session_id=sid,
            filename=fname,
            pages=int(doc.get("n_pages") or 0),
            chunks=0,
            doc_index=doc["doc_index"],
            deduplicated=True,
            message=(f"„{fname}“ ist bereits im Matter ([M-{doc['doc_index']}]) — erneutes Hochladen übersprungen."),
        )
    # Same-name match against a FAILED row — user is retrying after the
    # previous attempt died (commonly: 0-byte multipart, corrupted bytes,
    # Docling exception). Defensively wipe any stale pgvector entries
    # (failed rows usually never indexed, but partial chunking is
    # possible), reset the row's state to 'queued', then fall through
    # to the normal save + enqueue path so the worker re-processes the
    # fresh bytes. ``doc_index`` is preserved so the FE chip / any open
    # citations don't shift.
    if doc.get("__dedup_failed_retry"):
        rc = STATE.get("retrieval_client")
        if rc is not None:
            try:
                rc.delete_matter_chunks(sid, doc_index=int(doc["doc_index"]))
            except Exception as exc:
                print(
                    f"[retry] matter_chunks cleanup failed for {sid}/M-{doc['doc_index']}: {exc}",
                    flush=True,
                )
        persistence.reset_matter_document_for_retry(doc["id"])
    # Per-document file copy on disk — the worker reads this back for OCR,
    # and every [M-n] has a previewable original.
    persistence.save_matter_upload(sid, doc["id"], contents, fname)
    # Sidebar-title fallback. For sessions minted via POST /sessions we
    # skipped the legacy save_session above; without this, the sidebar
    # would show "Untitled chat" because COALESCE(title, filename, …)
    # has no filename. Race-safe across parallel uploads (UPDATE …
    # WHERE filename IS NULL → first-arriving wins, rest no-op).
    persistence.set_session_filename_if_unset(sid, fname)

    # Hand off to the background pool and return at once. Pass
    # ``is_first=True`` unconditionally — :func:`persistence.set_session_contract`
    # is guarded by ``WHERE contract_text IS NULL`` so only the first
    # ingestion to finish actually writes it; subsequent calls are
    # no-ops. This preserves the legacy analyze-contract path on
    # pre-created sessions (POST /sessions then a single /upload) while
    # staying race-safe under parallel uploads to one session.
    _enqueue_ingestion(sid, doc["id"], doc["doc_index"], fname, True)

    audit.record_sync(
        action="upload",
        user_id=uid,
        org_id=org_id,
        session_id=sid,
        detail={"filename": fname, "doc_index": doc["doc_index"], "bytes": len(contents)},
    )

    return UploadResp(
        session_id=sid,
        filename=fname,
        pages=0,
        chunks=0,
        doc_index=doc["doc_index"],
        message=(f"Dokument [M-{doc['doc_index']}] hochgeladen — wird verarbeitet…"),
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
    return IssueOut(severity=sev_s, description=desc, recommendation=rec, reason=rationale, type=typ)


def _analyze_v1(req: AnalyzeReq, uid: str | None) -> AnalyzeResp:
    sess = persistence.load_session(req.session_id, user_id=uid)
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
        clauses_out.append(
            ClauseOut(
                id=c["id"],
                title=c["title"],
                text=c["text"],
                type=analysis["type"],
                summary=analysis["summary"],
                issues=[IssueOut(**i) for i in analysis["issues"] if isinstance(i, dict)],
                citations=analysis["citations"],
            )
        )

    missing = [IssueOut(**m) for m in check_playbook(types_present)]
    sess["clauses"] = [c.model_dump() for c in clauses_out]
    # Phase B: ``sess`` already carries user_id (created_by) AND org_id from
    # _row_to_session, so save_session preserves both via the COALESCE upsert
    # — no need to re-stamp here.
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


def _analyze_v2(req: AnalyzeReq, uid: str | None) -> AnalyzeResp:
    sess = persistence.load_session(req.session_id, user_id=uid)
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
        # The analyzer's ``_emit`` carries ``step``/``current``/``total``/
        # ``elapsed_s``/``percent`` but never a ``status`` key (see
        # ``analyzer/pipeline.py:444``), so the hardcoded ``"running"`` above
        # would otherwise mask the pipeline's final tick (``step="done"``,
        # ``percent=1.0``) until the explicit done-write further below
        # lands — long enough for an in-flight FE poll to render a perpetual
        # "running" chip on a finished analysis. Re-pin status when the event
        # indicates completion so the FE sees the transition the moment it
        # arrives.
        state = {
            "status": "running",
            **event,
            "started_at": t0,
        }
        if event.get("step") == "done" or event.get("percent", 0.0) >= 1.0:
            state["status"] = "done"
        STATE["analyzer_progress"][sid] = state

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
        clauses_out.append(
            ClauseOut(
                id=c.id,
                title=c.title,
                text=c.text,
                type=c.type,
                summary=c.summary,
                issues=[_v1_issue_to_out(i.model_dump()) for i in c.issues],
                citations=[],  # V2 carries legal_basis on each Issue instead
            )
        )
    missing = [_v1_issue_to_out(i.model_dump()) for i in result.missing_required_clauses]

    # Surface extraction-quality warning at the top of missing-clauses so
    # reviewers see it before the (possibly noisy) per-clause list. A real
    # high-severity flag — bad extraction is genuinely high-impact for the
    # downstream interpretation, even though no individual clause is broken.
    if result.extraction_quality and result.extraction_quality.confidence == "low":
        missing.insert(
            0,
            IssueOut(
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
            ),
        )

    # Persist the full V2 result on the session for richer UI consumption later.
    # Phase B: ``sess`` already carries user_id (created_by) AND org_id from
    # _row_to_session, so save_session preserves both via the COALESCE upsert.
    sess["clauses"] = [c.model_dump() for c in clauses_out]
    sess["analysis"] = result.model_dump()
    sess["extraction_quality"] = result.extraction_quality.model_dump() if result.extraction_quality else None
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
def analyze_contract(req: AnalyzeReq, user: CurrentUser = Depends(get_current_user)):
    uid = str(user.id)
    _org_or_none(user)
    sess = persistence.load_session(req.session_id, user_id=uid)
    if not sess:
        raise HTTPException(404, "session_id not found — upload a document first")
    if not sess.get("contract_text"):
        raise HTTPException(400, "no contract text in session")

    requested = (req.version or STATE["analyzer_version_default"]).strip()
    use_v2 = requested == "2" and STATE["analyzer_cfg"] is not None
    return _analyze_v2(req, uid) if use_v2 else _analyze_v1(req, uid)


@app.get("/analyze-contract/progress")
def analyze_contract_progress(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Live progress for an in-flight V2 analysis. Returns the latest
    pipeline event. Returns ``status: "idle"`` when no analysis has
    run for this session — also when the session isn't owned by the
    caller (we don't leak existence).
    """
    if not persistence.session_exists(session_id, user_id=str(user.id)):
        return {"status": "idle", "session_id": session_id}
    progress = STATE["analyzer_progress"].get(session_id)
    if not progress:
        return {"status": "idle", "session_id": session_id}
    return {"session_id": session_id, **progress}


@app.get("/analyze-contract/full")
def analyze_contract_full(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return the full V2 ContractAnalysis for a session (parcels, tables,
    cross-clause findings — fields the legacy AnalyzeResp doesn't carry)."""
    sess = persistence.load_session(session_id, user_id=str(user.id))
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
def list_sessions(limit: int = 50, user: CurrentUser = Depends(get_current_user)):
    """Recent sessions for a sidebar — light payload, no contract_text.

    Scoped to the caller (AUTH_PLAN G1). ``persistence.list_sessions``
    accepts ``user_id`` natively; we always pass it.
    """
    return {"sessions": persistence.list_sessions(limit=limit, user_id=str(user.id))}


@app.get("/sessions/{session_id}")
def get_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Full session payload for UI rehydration after a refresh.
    Returns the contract metadata + last analysis + message history."""
    uid = str(user.id)
    _org_or_none(user)
    sess = persistence.load_session(session_id, user_id=uid)
    if not sess:
        raise HTTPException(404, "session_id not found")
    messages = persistence.list_messages(session_id, user_id=uid)
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
def get_session_messages(session_id: str, user: CurrentUser = Depends(get_current_user)):
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    return {"messages": persistence.list_messages(session_id, user_id=uid)}


class AppendMessageReq(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    mode: str | None = None  # free-form: "chat" | "rag" | "upload" | "analyze" | ...


@app.post("/sessions/{session_id}/messages")
def append_session_message(
    session_id: str,
    req: AppendMessageReq,
    user: CurrentUser = Depends(get_current_user),
):
    """Append an assistant- or user-side message to an existing session
    so refresh-replay sees every bubble the UI showed. Used for bubbles
    the backend doesn't generate itself — upload confirmation,
    rendered /analyze-contract output, etc.

    /query already self-persists; the UI shouldn't double-save those."""
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    if req.role not in ("user", "assistant"):
        raise HTTPException(400, "role must be 'user' or 'assistant'")
    if not req.content.strip():
        raise HTTPException(400, "content required")
    msg_id = persistence.add_message(
        session_id,
        req.role,
        req.content,
        mode=req.mode,
        user_id=uid,
    )
    return {"ok": True, "id": msg_id}


class FeedbackReq(BaseModel):
    """One lawyer-supplied verdict on an assistant turn.

    The frontend posts this when the user clicks the thumbs-up /
    thumbs-down icon under a bubble (or, on a free-form complaint
    dialog, the "Send feedback" button).

    Attributes:
        session_id: Chat the feedback belongs to. Must belong to the
            authenticated user — cross-tenant submissions are 404'd.
        message_id: Optional ``messages.id`` for the specific assistant
            bubble being rated. Omitted for session-level feedback
            (a one-off "the whole conversation was wrong" verdict).
            When supplied, must point to a message in ``session_id``.
        rating: ``1`` for thumbs-up, ``-1`` for thumbs-down. Star /
            multi-grade rating is intentionally NOT supported yet —
            the lawyer wants a thumb, not a Likert scale.
        reason: Optional short tag from the UI dropdown. Closed enum
            today (``wrong-citation`` / ``wrong-jurisdiction`` /
            ``hallucination`` / ``incomplete`` / ``other``); the column
            is free-text so we can add tags without a migration.
        comment: Optional free text, capped at 2 KB to keep the
            SQLite row size in check.
    """

    session_id: str
    message_id: int | None = None
    rating: int
    reason: str | None = None
    comment: str | None = None


# Closed-enum reasons the UI's dropdown offers. The route validates
# against this set so a typo-introduced new tag doesn't silently land
# in the table (which would break downstream aggregation queries).
# ``None`` is also valid — feedback may carry no reason.
_FEEDBACK_REASONS: frozenset[str] = frozenset(
    {
        "wrong-citation",
        "wrong-jurisdiction",
        "hallucination",
        "incomplete",
        "tone",
        "other",
    }
)


@app.post("/feedback")
def submit_feedback(
    req: FeedbackReq,
    user: CurrentUser = Depends(get_current_user),
):
    """Capture a lawyer's verdict on an assistant turn.

    Idempotent on ``(user_id, session_id, message_id)`` — repeat
    submissions overwrite via persistence's ON CONFLICT … DO UPDATE,
    so the UI can let the user toggle thumbs-up → thumbs-down without
    polluting the table.

    Returns:
        ``{"ok": True, "id": <feedback row id>}``

    Errors:
        400 — invalid rating, unknown reason tag, or oversize comment.
        404 — session does not belong to the caller, OR ``message_id``
              was supplied and does not belong to the session.
    """
    if req.rating not in (-1, 1):
        raise HTTPException(400, "rating must be -1 or 1")
    if req.reason is not None and req.reason not in _FEEDBACK_REASONS:
        raise HTTPException(400, f"reason must be one of {sorted(_FEEDBACK_REASONS)} or null")
    if req.comment is not None and len(req.comment) > 2048:
        raise HTTPException(400, "comment must be ≤ 2048 characters")

    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(req.session_id, user_id=uid):
        # 404 (not 403) so we don't leak existence across tenants.
        raise HTTPException(404, "session_id not found")
    if req.message_id is not None and not persistence.message_belongs_to_session(
        req.message_id,
        req.session_id,
    ):
        raise HTTPException(404, "message_id does not belong to session_id")

    row_id = persistence.record_feedback(
        session_id=req.session_id,
        user_id=uid,  # author + visibility check (Phase B reverted).
        rating=req.rating,
        message_id=req.message_id,
        reason=req.reason,
        comment=req.comment,
    )
    if row_id is None:
        # Defence-in-depth: persistence already rechecks ownership and
        # would return None if the session disappeared between the
        # check above and the insert. Surface as 404 so the UI can
        # retry cleanly.
        raise HTTPException(404, "session_id not found")

    # Metric: rating label is the two-valued enum the dashboard pivots
    # on — never the raw int (a future reviewer extending to e.g.
    # 5-star ratings should also rename the label values).
    rag_metrics.feedback_total.labels(
        rating="thumbs_up" if req.rating == 1 else "thumbs_down",
    ).inc()

    return {"ok": True, "id": row_id}


@app.get("/sessions/{session_id}/feedback")
def get_session_feedback(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """All feedback rows the calling user has left on a session.

    Used by the UI to render the persisted thumbs-up/down state under
    each assistant bubble after a reload — without this, the verdict
    visually resets every time the lawyer refreshes the page.

    Cross-tenant sessions return 404 rather than 403 so the response
    shape never leaks existence across tenants.
    """
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    return {"feedback": persistence.list_feedback(session_id, user_id=uid)}


@app.get("/sessions/{session_id}/document")
def get_session_document(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Stream the raw uploaded document bytes for a session.

    Backs the frontend's ``CitationPanel`` PDF preview: when the user
    clicks an ``[M-n]`` chip, the panel mounts ``<Document file={url}>``
    with the URL of this endpoint. The browser fetches the PDF over the
    same origin (no CORS), pdf.js renders it client-side.

    Returns the file with an ``inline`` content disposition so the
    browser displays it in the page rather than triggering a download.
    Content-Type is inferred from the upload extension — PDFs render
    natively in ``react-pdf``; other types (.docx, .txt) the UI falls
    back to the chunk excerpt the chat already showed.

    Auth: scoped to the calling user (AUTH_PLAN G1). A session ID
    belonging to another tenant returns 404 rather than 403 so we never
    leak session existence across tenants.

    Errors:
        404 — session not found, or session owner mismatch, or the
        upload file has been GC'd / never existed (e.g. chat-only
        session with no upload).
    """
    uid = str(user.id)
    _org_or_none(user)
    sess = persistence.load_session(session_id, user_id=uid)
    if not sess:
        raise HTTPException(404, "session_id not found")
    ext = sess.get("upload_ext")
    if not ext:
        # Chat-only session never had an upload — distinguish from
        # 404 by message so the frontend can render a friendlier
        # "no document attached" state in the panel.
        raise HTTPException(404, "no document attached to this session")
    path = persistence.upload_path(session_id, ext)
    if path is None or not path.exists():
        # Row says there was an upload but the file is gone from disk.
        # This is a real-world failure mode (manual cleanup, disk
        # restore from a snapshot that predates the upload). Tell the
        # frontend so it can fall back to the chunk excerpt instead of
        # showing an empty PDF viewer.
        raise HTTPException(404, "upload file no longer available")

    # Map known extensions to canonical media types. Unknown extensions
    # fall back to application/octet-stream — the browser will refuse
    # inline preview, which is the right behaviour for anything we
    # can't render.
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")

    # Filename for the inline disposition. Falls back to the session id
    # when the original filename is somehow null on the row.
    display_name = sess.get("filename") or f"{session_id}{ext}"
    return FileResponse(
        path=path,
        media_type=media_type,
        # ``inline`` lets the browser render in-page rather than
        # forcing a download; the frontend wants this.
        headers={"Content-Disposition": f'inline; filename="{display_name}"'},
    )


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
}


class MatterDocumentOut(BaseModel):
    """One document in a Matter, as the UI document list renders it."""

    doc_index: int  # the n in [M-n]
    cite_id: str  # "M-1", "M-2", …
    filename: str
    n_pages: int
    created_at: float
    # Async-ingestion status so the UI can show a live progress bar while a
    # document is OCR'd/indexed and a checkmark when it's ready.
    status: str = "done"  # queued | processing | done | failed
    pages_done: int = 0
    pages_total: int = 0
    n_chunks: int = 0
    error: str | None = None


@app.get("/sessions/{session_id}/documents")
def list_matter_documents_endpoint(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Every document attached to a Matter, ordered by ``[M-n]``.

    Drives the workspace Documents tab + the chat's matter chip legend.
    The client polls this while documents ingest — each carries a
    ``status`` + page progress so the UI renders a spinner/bar then a
    checkmark. Returns ``{"documents": [...]}``. Cross-tenant sessions 404.
    """
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    docs = persistence.list_matter_documents(session_id, user_id=uid)
    out = [
        MatterDocumentOut(
            doc_index=d["doc_index"],
            cite_id=_matter_cite_id(d["doc_index"]),
            filename=d["filename"] or f"Dokument M-{d['doc_index']}",
            n_pages=d["n_pages"],
            created_at=d["created_at"],
            status=d.get("status", "done"),
            pages_done=d.get("pages_done", 0),
            pages_total=d.get("pages_total", 0),
            n_chunks=d.get("n_chunks", 0),
            error=d.get("error"),
        )
        for d in docs
    ]
    return {"documents": [o.model_dump() for o in out]}


@app.get("/sessions/{session_id}/documents/{doc_index}")
def get_matter_document_endpoint(
    session_id: str,
    doc_index: int,
    user: CurrentUser = Depends(get_current_user),
):
    """Stream one Matter document's bytes by its ``[M-n]`` index.

    The multi-document companion to ``/sessions/{id}/document``: clicking
    an ``[M-3]`` chip fetches ``/sessions/{id}/documents/3``. Inline
    disposition so the browser previews it. 404 on cross-tenant, unknown
    index, or a file GC'd from disk.
    """
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.session_exists(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    doc = persistence.get_matter_document(session_id, doc_index, user_id=uid)
    if not doc:
        raise HTTPException(404, "document index not found in this matter")
    ext = doc.get("upload_ext")
    path = persistence.matter_document_path(session_id, doc["id"], ext)
    if path is None or not path.exists():
        raise HTTPException(404, "document file no longer available")
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    display_name = doc.get("filename") or f"{session_id}_m{doc['id']}{ext}"
    return FileResponse(
        path=path,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{display_name}"'},
    )


@app.delete("/sessions/{session_id}/documents/{doc_index}")
def delete_matter_document_endpoint(
    session_id: str,
    doc_index: int,
    user: CurrentUser = Depends(get_current_user),
):
    """Hard-delete one matter document from a session.

    Removes three things atomically-from-the-user's-perspective:
      1. The pgvector embeddings for that ``[M-n]`` (so retrieval can no
         longer surface its passages on later turns).
      2. The ``matter_documents`` row.
      3. The on-disk file bytes.

    Owner-only — sharing is view-only in v1 (Path A Step 2), so a shared
    collaborator gets a 404 (no existence leak). The ``doc_index`` value
    is NOT reused after deletion: ``add_matter_document`` always
    allocates ``MAX(doc_index)+1``, so any existing ``[M-n]`` citations
    in old chat history won't silently re-point at a different doc.

    Order matters: embeddings come down BEFORE the row, so a concurrent
    query can't briefly retrieve passages from a doc whose row has
    already vanished (it would render in the citation panel as
    "not available" — confusing). Disk file last (best-effort).
    """
    uid = str(user.id)
    rc = STATE.get("retrieval_client")
    if rc is not None:
        try:
            rc.delete_matter_chunks(session_id, doc_index=int(doc_index))
        except Exception as e:
            print(f"[delete] matter_chunks cleanup failed for {session_id}/M-{doc_index}: {e}", flush=True)
    info = persistence.delete_matter_document(
        session_id,
        int(doc_index),
        user_id=uid,
    )
    if info is None:
        raise HTTPException(404, "document not found in this session")
    return {"ok": True, "doc_index": int(doc_index), "filename": info.get("filename")}


@app.delete("/sessions/{session_id}")
def delete_session_endpoint(session_id: str, user: CurrentUser = Depends(get_current_user)):
    uid = str(user.id)
    _org_or_none(user)
    if not persistence.delete_session(session_id, user_id=uid):
        raise HTTPException(404, "session_id not found")
    # Drop the Matter's pgvector index too — it lives in Postgres, which
    # persistence.delete_session (SQLite + files) doesn't touch. Best-effort:
    # the SQLite delete already succeeded, so a pgvector hiccup shouldn't
    # 500 the request; orphaned vectors are scoped to a now-dead session id
    # and never retrieved.
    rc = STATE.get("retrieval_client")
    if rc is not None:
        try:
            rc.delete_matter_chunks(session_id)
        except Exception as e:
            print(f"[delete] matter_chunks cleanup failed for {session_id}: {e}", flush=True)
    return {"ok": True}


class RenameReq(BaseModel):
    title: str


@app.patch("/sessions/{session_id}")
def rename_session(
    session_id: str,
    req: RenameReq,
    user: CurrentUser = Depends(get_current_user),
):
    """Set a user-facing title for the conversation. Empty string clears
    the override and the display title falls back to filename / first
    user message / 'Untitled chat'."""
    if not persistence.update_session_title(session_id, req.title, user_id=str(user.id)):
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
