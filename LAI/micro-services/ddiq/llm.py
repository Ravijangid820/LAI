"""LLM + embedding infrastructure for the DDiQ pipeline.

Moved out of ``ddiq_report`` in H-5 phase 2. This module owns the
module-level singletons (so every extractor reuses one httpx
connection pool) plus the thin :func:`llm_call` / :func:`llm_json`
shims that wrap :class:`lai.common.llm.SyncLlmClient` with the
DDiQ-flavoured error handling (return ``""`` / ``{}`` on failure
rather than letting one network blip kill the 30-60 min pipeline).

What's here:

* :data:`LLM_URL` / :data:`LLM_MODEL` / :data:`EMBEDDING_URL`
  / :data:`RERANKER_URL` — env config.
* :data:`_LLM_CLIENT` / :data:`_EMBEDDING_CLIENT` — module-level
  ``lai.common`` clients with tenacity + Prometheus.
* :func:`llm_call` / :func:`llm_json` — single-shot + JSON-coerced
  variants. ``llm_json`` runs the two-shot salvage path.
* :func:`embed_texts` / :func:`embed_single` — passthrough to the
  embedding client.
* :data:`EXTRACTION_SYSTEM` — the German-DD-lawyer system prompt
  every extractor sends as ``system``.

What stays in ``ddiq_report``:

* :data:`_PDF_EXTRACTOR` / :data:`_CHUNKER` — only the upload path
  uses these; they belong with the upload handler.
* :data:`_NOMINATIM_CLIENT` / :data:`_ALKIS_CLIENT` — only the
  heavily-coupled geocoding + parcel extractors use these; they
  stay near :func:`ddiq_report.geocode_address` /
  :func:`ddiq_report.alkis_query_parcels` until those move too.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from lai.common.exceptions import LlmError
from lai.common.llm import (
    ChatMessage,
    LlmConfig,
    SyncLlmClient,
    salvage_json,
)
from lai.common.embedding import EmbeddingConfig, SyncEmbeddingClient

__all__ = [
    "EMBEDDING_URL",
    "EXTRACTION_SYSTEM",
    "LLM_MODEL",
    "LLM_URL",
    "RERANKER_URL",
    "embed_single",
    "embed_texts",
    "get_embedding_client",
    "get_llm_client",
    "llm_call",
    "llm_json",
]


_log = logging.getLogger("ddiq")


# ── Env config ───────────────────────────────────────────────────────


LLM_URL: str       = os.getenv("LLM_URL", "http://localhost:8001/v1")
LLM_MODEL: str     = os.getenv("LLM_MODEL", "legal-lora")
EMBEDDING_URL: str = os.getenv("EMBEDDING_URL", "http://localhost:8002")
RERANKER_URL: str  = os.getenv("RERANKER_URL", "http://localhost:8004")


# ── Singleton clients ────────────────────────────────────────────────
# Single module-level clients. Each uvicorn worker process (and each
# Celery worker process) instantiates its own pair and reuses one
# httpx connection pool across all extraction passes. The underlying
# ``httpx.Client`` is thread-safe.
#
# Config is built from the legacy DDiQ env vars (``LLM_URL`` /
# ``LLM_MODEL`` / ``EMBEDDING_URL``) rather than the ``LAI_LLM_*``
# prefix lai.common defaults to, so this module's wire contract
# matches the docker-compose envs without touching compose.


# thinking_mode_enabled=False (E1): every DDiQ pass is structured JSON
# extraction (sections, findings, timeline, Rückbau, Grundbuch, WEA,
# infra, specs, metadata) — none need the Qwen3 <think> reasoning trace,
# which roughly DOUBLES per-call latency. With thinking on, the live §14
# re-smoke ran 100-160s per findings call and blew the Celery 120-min
# hard limit before the report could finish. Disabling it sends
# ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}``
# server-side, ~halving every call. The contract analyzer (serve_rag)
# keeps thinking on via its own client; this flag is DDiQ-local.
_LLM_CONFIG = LlmConfig(
    base_url=LLM_URL, model=LLM_MODEL, thinking_mode_enabled=False,
)
_LLM_CLIENT: SyncLlmClient = SyncLlmClient(_LLM_CONFIG)


# Construction note: the live container env sets ``EMBEDDING_URL`` to
# ``http://lai_embedding:8000`` (no ``/v1`` suffix because the legacy
# code appended it per-call). :class:`EmbeddingConfig.base_url` expects
# the full OpenAI base, so we re-add ``/v1`` here — existing env-var
# contract is preserved.
#
# Compared to the pre-H-1 hand-rolled ``_embed_via_openai`` /
# ``_embed_via_tei`` block we gain:
#   * tenacity retry with exponential backoff
#   * dimension validation against ``EmbeddingConfig.dimension=4096``
#   * Prometheus metrics
#   * typed exceptions (``EmbeddingError`` hierarchy)
#
# What we lose: the legacy fallback to HuggingFace TEI's ``/embed``
# shape. The live ``lai_embedding`` container is vLLM-based and serves
# the OpenAI shape directly, so the TEI fallback path was already dead
# in production.
_EMBEDDING_CLIENT: SyncEmbeddingClient = SyncEmbeddingClient(
    EmbeddingConfig(
        base_url=EMBEDDING_URL.rstrip("/") + "/v1",
        model="Qwen/Qwen3-Embedding-8B",
        # 4096-d for DDiQ's own ``ddiq_doc_chunks.embedding vector(4096)``
        # column. The corpus-side ``corpus_child_chunks.embedding
        # halfvec(4000)`` truncates to 4000 — that's done by the
        # migration / topup script, NOT here.
        dimension=4096,
        # Match vLLM's per-request limit; legacy ``batch_size=8`` was
        # very conservative. 32 is what live ``Qwen3-Embedding-8B``
        # accepts per POST without OOM risk.
        max_batch_size=32,
    )
)


def get_llm_client() -> SyncLlmClient:
    """Return the module-level :class:`SyncLlmClient` singleton.

    Public accessor so tests can swap the client by monkey-patching
    ``ddiq.llm._LLM_CLIENT`` and call sites still see the
    replacement via :func:`get_llm_client`.
    """
    return _LLM_CLIENT


def get_embedding_client() -> SyncEmbeddingClient:
    """Return the module-level :class:`SyncEmbeddingClient` singleton."""
    return _EMBEDDING_CLIENT


# ── Shared system prompt ─────────────────────────────────────────────


EXTRACTION_SYSTEM = """You are a senior German legal due-diligence analyst for wind-energy
projects. Read the supplied document context and answer like a Berufsanwalt working
on an acquisition red-flag report.

Rules:
- ALWAYS return valid JSON only. No markdown, no preamble, no trailing text.
- Cite specific German statutes when relevant: BImSchG (§§4,6,10,15,52), BauGB (§35
  privileged use, §35 Abs. 5 Rückbau), BNatSchG (§44 Zugriffsverbote, §45 Ausnahme),
  UVPG, EEG (Marktwert, Marktprämie §20, Direktvermarktung §35a), TA Lärm,
  22./32. BImSchV, AVV Kennzeichnung, VwGO §70 (Widerspruchsfrist), §550 BGB Schriftform.
- For every fact-bearing answer, identify the supporting context chunks by their
  [#N] index from the supplied context and return them in the "evidence_chunks" array.
  Empty array if you have no source. Never fabricate citations.
- Use null for unknown optional fields. Don't guess monetary amounts or dates.
- Distinguish formal status (BImSchG §6 erteilt) from construction status (errichtet)
  from operational status (in Betrieb genommen) — these are different things.
- A project / wind farm / WEA SITE address is the geographic location where the
  turbines stand or are planned (Lageplan, Erläuterungsbericht, Standort,
  Gemarkung). It is NOT the same as a party's registered office (Sitz,
  Geschäftsadresse, HRB-Sitz, Hauptsitz, Postanschrift) of the
  Pächterin/Verpächter/Eigentümer/Projektgesellschaft. When asked for a site or
  project location, NEVER return a corporate office address — return null if
  the doc only mentions the office and no site location."""


# ── Chat completions ─────────────────────────────────────────────────


def llm_call(
    system: str,
    user: str,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """Single-shot chat completion. Returns the stripped string content.

    Backed by :class:`lai.common.llm.SyncLlmClient`, which adds retry
    with exponential backoff, server-side ``<think>`` stripping,
    structured logging, and Prometheus metrics over what the legacy
    hand-rolled ``requests.post`` provided.

    Returns ``""`` on retry-exhausted / transport / invalid-response
    failure, matching the legacy behaviour of returning an empty
    string on null content. The caller's JSON parse will then take its
    own error path instead of crashing the pipeline.
    """
    try:
        return _LLM_CLIENT.generate(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except LlmError as exc:
        _log.warning(f"llm_call failed ({type(exc).__name__}): {exc}")
        return ""


def llm_json(
    system: str,
    user: str,
    temperature: float = 0.0,
) -> Any:
    """Two-shot JSON-structured completion. Returns ``dict`` / ``list`` or ``{}``.

    Strategy:
      1. Call the LLM, strip code fences, ``json.loads``.
      2. On parse failure, run the salvage path
         (:func:`lai.common.llm.salvage_json`) which extracts the
         first balanced JSON substring with full string-context
         awareness.
      3. On second parse failure, retry once with a strengthened
         instruction (mirrors the legacy two-shot behaviour).
      4. If everything fails, return ``{}`` rather than raising — the
         legacy uncaught :class:`json.JSONDecodeError` on the second
         attempt would crash the entire pipeline mid-report.
    """
    def _attempt(sys_prompt: str, user_prompt: str) -> Any:
        raw = llm_call(sys_prompt, user_prompt, temperature, max_tokens=4096)
        if not raw:
            return None
        # Strip ```json fences before parse; salvage_json handles them
        # too, but doing it here keeps the fast path cheap.
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # salvage_json already calls json.loads internally and
            # returns the parsed value — wrapping it in another
            # ``json.loads(...)`` was a long-standing bug that always
            # raised TypeError on success.
            #
            # On unrecoverable input, salvage raises
            # ``lai.common.exceptions.LlmJsonParseError`` (subclass of
            # ``Exception``, NOT ``ValueError``). Catch broadly so a
            # truly malformed response just returns None — the caller
            # retries once with a strengthened prompt and then falls
            # through to ``{}``. Without the broad catch the documented
            # "return {} on total failure" contract is broken and a
            # single bad response kills the whole report.
            try:
                return salvage_json(raw)
            except Exception:
                return None

    parsed = _attempt(system, user)
    if parsed is not None:
        return parsed

    parsed = _attempt(system + "\n\nCRITICAL: Return ONLY valid JSON.", user)
    if parsed is not None:
        return parsed

    _log.warning("llm_json: both attempts failed to produce valid JSON; returning {}")
    return {}


# ── Embeddings ───────────────────────────────────────────────────────


def embed_texts(
    texts: list[str],
    batch_size: int = 8,
) -> list[list[float]]:
    """Embed a list of texts; returns ``list[list[float]]`` in input order.

    Thin shim over :class:`lai.common.embedding.SyncEmbeddingClient`.
    The legacy ``batch_size`` argument is accepted for backwards
    compatibility but is **ignored** — the shared client batches
    internally based on ``EmbeddingConfig.max_batch_size`` (32), which
    is what vLLM actually accepts per request.

    Empty input list returns an empty list cheaply (the client would
    raise ``ValueError`` on an empty ``inputs``; we short-circuit so
    callers like ``embed_texts([])`` in dead-branch paths don't crash
    the route).
    """
    if not texts:
        return []
    if batch_size != 8:
        _log.warning(
            "embed_texts called with batch_size=%s (ignored — "
            "SyncEmbeddingClient uses max_batch_size=32). Update the "
            "caller or extend the shim if a non-default is genuinely "
            "needed.",
            batch_size,
        )
    results = _EMBEDDING_CLIENT.embed(texts)
    return [r.embedding for r in results]


def embed_single(text: str) -> list[float]:
    """Embed a single text. Returns the bare ``list[float]`` (4096-d).

    Routes through the shared client's ``embed_one`` for the cleanest
    code path. Equivalent to ``embed_texts([text])[0]`` but avoids the
    list-of-lists round-trip.
    """
    return _EMBEDDING_CLIENT.embed_one(text)
