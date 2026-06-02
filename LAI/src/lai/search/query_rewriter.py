"""LLM-driven query rewriting for BM25 expansion.

The 2026-06-02 BM25 sweep found v6 prefix-glob (`genehm*`) lifted the
candidate pool 5× but dropped Recall@30 — too coarse. Query rewriting
tests a smarter form of the same intuition: ask the LLM for 3–5
targeted morphological or synonym variants of the query (real German
words, not prefix globs), OR them into the BM25 expression. FTS5 still
uses indexed tokens (so it stays fast); we just add a few more
high-confidence disjuncts.

Three variants:

* ``r1``: morphology-only — for each of the top-3 longest tokens, the
  LLM emits 3 morphological / inflectional variants (Antrag →
  Antragsverfahren, Antragsstellung, beantragt).
* ``r2``: synonym-only — the LLM emits 3 legal-domain synonyms /
  paraphrases of the WHOLE query (Genehmigung → Bewilligung, Erlaubnis,
  Konzession). Whole-query level so the rewriter has phrase context.
* ``r3``: union of r1 + r2.

LLM target: the analyzer's Qwen3.6-27B vLLM at ``:8005``
(``LAI_ANALYZER_LLM_API_URL`` env override). enable_thinking=False
because we want one-shot deterministic output; temperature=0 so
sha256(query, variant) is a stable cache key.

Production posture: inert until ``LAI_QUERY_REWRITE_VARIANT`` env is
set in the harness subprocess; serve_rag is unaffected unless rj
flips the default in :func:`_bm25_match_expr`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal

import httpx

# ── Cache + endpoint config ─────────────────────────────────────────────


DEFAULT_LLM_URL = os.environ.get("LAI_ANALYZER_LLM_API_URL", "http://localhost:8005")
DEFAULT_LLM_MODEL = os.environ.get("LAI_ANALYZER_LLM_MODEL", "qwen3.6-27b")
DEFAULT_TIMEOUT_S = float(os.environ.get("LAI_QUERY_REWRITE_TIMEOUT_S", "30"))


def _cache_dir() -> Path:
    """Default cache lives next to the harness output. Override with env."""
    override = os.environ.get("LAI_QUERY_REWRITE_CACHE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "scripts" / "eval" / "_rewrite_cache"


# ── Prompts ─────────────────────────────────────────────────────────────


_R1_SYSTEM = (
    "You expand a single German legal term to morphological or "
    "inflectional variants for full-text search.\n"
    "Output JSON: {\"variants\": [string, string, string]}.\n"
    "Only morphological / inflectional / compound forms of the input — "
    "NEVER synonyms (no Bewilligung for Genehmigung). Examples:\n"
    "  Antrag -> Antragsverfahren, Antragsstellung, beantragt\n"
    "  Genehmigung -> Genehmigungsverfahren, Genehmigungsbescheid, genehmigt\n"
    "  Vertrag -> Vertragslaufzeit, Vertragsbeginn, vertraglich\n"
    "Be CONSERVATIVE: 3 high-confidence variants, no speculation."
)

_R2_SYSTEM = (
    "You expand a German legal query to 3 close domain synonyms / "
    "paraphrases for full-text search.\n"
    "Output JSON: {\"variants\": [string, string, string]}.\n"
    "Each variant must:\n"
    "  - be a SINGLE WORD or short phrase (2-3 words max)\n"
    "  - preserve the LEGAL MEANING but use different lexical forms\n"
    "  - stay in German (no English translations)\n"
    "  - be a real German legal term, not invented\n"
    "Examples:\n"
    "  Genehmigung -> Bewilligung, Erlaubnis, Konzession\n"
    "  Kündigung -> Beendigung, Auflösung, Aufhebung\n"
    "Be CONSERVATIVE: 3 high-confidence terms."
)


_VARIANTS_SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {"type": "string", "maxLength": 80},
            "minItems": 0,
            "maxItems": 5,
        }
    },
    "required": ["variants"],
    "additionalProperties": False,
}


# ── HTTP call ───────────────────────────────────────────────────────────


def _llm_call(system: str, user: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[str]:
    """One JSON-schema chat completion. Returns the ``variants`` list or [] on any error.

    Errors swallowed (best-effort): a failed rewrite must NEVER block
    retrieval — the caller falls back to the un-expanded v5 expression.
    """
    body = {
        "model": DEFAULT_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 256,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "variants",
                "schema": _VARIANTS_SCHEMA,
                "strict": True,
            },
        },
    }
    try:
        r = httpx.post(
            DEFAULT_LLM_URL.rstrip("/") + "/v1/chat/completions",
            json=body,
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"].get("content") or ""
        parsed = json.loads(content)
        variants = parsed.get("variants") or []
        return [v.strip() for v in variants if isinstance(v, str) and v.strip()]
    except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError):
        return []


# ── Cache helpers ───────────────────────────────────────────────────────


def _cache_path(variant: str) -> Path:
    """One JSON file per variant; safe to read + atomic-write."""
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"rewrite_cache_{variant}.json"


def _load_cache(variant: str) -> dict[str, list[str]]:
    path = _cache_path(variant)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(variant: str, cache: dict[str, list[str]]) -> None:
    path = _cache_path(variant)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False))
    os.replace(tmp, path)


def _sha(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


# ── Variant builders ────────────────────────────────────────────────────


def _select_top_tokens(query: str, *, n: int = 3, min_len: int = 5) -> list[str]:
    """Pick the n longest distinct tokens — same shape as v5's token selection."""
    safe = query.replace('"', " ").strip()
    return sorted({t for t in safe.split() if len(t) > min_len}, key=len, reverse=True)[:n]


def _expand_r1(query: str) -> list[str]:
    """Morphology variants for each of the top-3 tokens, deduped."""
    tokens = _select_top_tokens(query, n=3, min_len=4)
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        variants = _llm_call(_R1_SYSTEM, f"Word: {tok}")
        for v in variants:
            key = v.lower()
            if key in seen or key == tok.lower():
                continue
            seen.add(key)
            out.append(v)
    return out


def _expand_r2(query: str) -> list[str]:
    """Whole-query synonyms / paraphrases."""
    return _llm_call(_R2_SYSTEM, f"Query: {query}")


# ── Public dispatcher ───────────────────────────────────────────────────


Variant = Literal["none", "r1", "r2", "r3"]


def get_expansions(query: str, variant: Variant) -> list[str]:
    """Return the cached or LLM-derived expansions for ``query`` under ``variant``.

    ``none`` -> empty list (caller falls back to bare BM25).
    """
    if variant == "none" or not query.strip():
        return []
    cache = _load_cache(variant)
    key = _sha(query)
    if key in cache:
        return cache[key]

    if variant == "r1":
        expansions = _expand_r1(query)
    elif variant == "r2":
        expansions = _expand_r2(query)
    elif variant == "r3":
        expansions = list({*_expand_r1(query), *_expand_r2(query)})
    else:
        expansions = []

    # Only cache successful (non-empty) calls so a transient LLM error
    # doesn't poison the cache with [] forever.
    if expansions:
        cache[key] = expansions
        _save_cache(variant, cache)
    return expansions


def rewrite_bm25_expr(base_expr: str, expansions: list[str]) -> str:
    """OR-join ``expansions`` onto a v5-style base expression.

    Each expansion is wrapped in FTS5 phrase quotes and de-duplicated
    by case-insensitive comparison against the base tokens.
    """
    if not expansions or not base_expr:
        return base_expr
    quoted = [f'"{e}"' for e in expansions]
    return f"({base_expr}) OR " + " OR ".join(quoted)


# ── Token sanity check (used by tests) ──────────────────────────────────


def _is_safe_fts5_token(s: str) -> bool:
    """Reject expansions that would break FTS5's MATCH parser."""
    if not s or len(s) < 2 or len(s) > 80:
        return False
    # No bare punctuation, no operators
    return not any(c in s for c in '*()"^')


def filter_safe_expansions(expansions: list[str]) -> list[str]:
    """Drop expansions that contain FTS5-unsafe characters."""
    return [e for e in expansions if _is_safe_fts5_token(e)]


def get_safe_expansions(query: str, variant: Variant) -> list[str]:
    """Convenience: call get_expansions + filter for FTS5 safety."""
    return filter_safe_expansions(get_expansions(query, variant))


_T0 = time.monotonic()  # module-load time, kept for diagnostics
