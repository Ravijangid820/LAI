"""Unit tests for lai.search.rerank_query.

Mirrors the structure of test_query_rewriter.py. The augmentation
must:

* Pass through unchanged when the variant is ``none`` or the env is
  unset (production safety net).
* Append synonyms / morphology with the documented German prefixes.
* Be robust to LLM failures (the underlying ``get_safe_expansions``
  returns [] on any error; ``augment`` must then degrade to the
  bare query, not error).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-rerank-q-0123456789abcdef")

import httpx
import pytest

from lai.search import query_rewriter as qr
from lai.search.rerank_query import augment

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the rewriter's LLM call so this test never reaches a real
    network. Programmable per-test via the returned state dict."""
    state: dict[str, Any] = {
        "variants_by_call": [],  # popped left-to-right
        "raises": None,
        "calls": 0,
    }

    def fake_post(url: str, *, json: dict, timeout: float = 0) -> httpx.Response:
        import json as _json

        state["calls"] += 1
        if state["raises"]:
            raise state["raises"]
        if state["variants_by_call"]:
            variants = state["variants_by_call"].pop(0)
        else:
            variants = []
        body = {
            "choices": [
                {"message": {"content": _json.dumps({"variants": variants})}}
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(qr.httpx, "post", fake_post)
    return state


# ── Passthrough cases ───────────────────────────────────────────────────


def test_none_variant_returns_original_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    q = "Welche Genehmigung ist erforderlich?"
    assert augment(q, "none") == q


def test_env_unset_defaults_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env set → behaves as production (no augmentation)."""
    monkeypatch.delenv("LAI_RERANK_QUERY_VARIANT", raising=False)
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    q = "Welche Genehmigung ist erforderlich?"
    assert augment(q) == q


def test_unknown_variant_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown variant tag → safe passthrough, no error."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    assert augment("Test", "garbage") == "Test"


def test_empty_query_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    assert augment("", "q1") == ""
    assert augment("   ", "q3") == "   "


# ── Synonym / morphology composition ────────────────────────────────────


def test_q1_appends_synonyms_with_german_prefix(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    # r2 = whole-query synonyms = 1 LLM call
    mock_llm["variants_by_call"] = [["Bewilligung", "Erlaubnis", "Konzession"]]
    out = augment("Welche Genehmigung?", "q1")
    assert out.startswith("Welche Genehmigung?\n")
    assert "Verwandte Begriffe:" in out
    for v in ("Bewilligung", "Erlaubnis", "Konzession"):
        assert v in out


def test_q2_appends_morphology_with_german_prefix(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    # r1 = per-token morphology — top-3 tokens, 1 call each
    mock_llm["variants_by_call"] = [
        ["Genehmigungsverfahren", "Genehmigungsbescheid", "genehmigt"],
        ["Windenergieanlagen", "Windenergieanlagens", "Windenergieanlagentechnik"],
        ["erforderlich", "Erforderlichkeit", "erforderlichenfalls"],
    ]
    q = "Welche Genehmigung ist nach dem BImSchG für eine Windenergieanlage erforderlich?"
    out = augment(q, "q2")
    assert out.startswith(q)
    assert "Verwandte Formen:" in out
    assert "Genehmigungsverfahren" in out


def test_q3_includes_both_sections(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    # q3 calls r2 first (whole-query synonyms) then r1 (top-3 morphology)
    mock_llm["variants_by_call"] = [
        ["Bewilligung", "Erlaubnis"],  # r2 synonyms
        ["Genehmigungsverfahren"],  # r1 token 1
        ["Windenergieanlagen"],  # r1 token 2
        ["erforderlich"],  # r1 token 3
    ]
    q = "Welche Genehmigung ist für eine Windenergieanlage erforderlich?"
    out = augment(q, "q3")
    assert "Verwandte Begriffe:" in out
    assert "Verwandte Formen:" in out
    assert "Bewilligung" in out
    assert "Genehmigungsverfahren" in out


# ── Robustness ──────────────────────────────────────────────────────────


def test_augment_passes_through_on_llm_error(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LLM call fails (get_safe_expansions → []), augment returns
    the original query unchanged. NEVER blocks retrieval."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["raises"] = httpx.ConnectError("down")
    q = "Welche Genehmigung?"
    assert augment(q, "q1") == q


def test_empty_expansions_yield_no_german_prefix(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LLM returned no variants, the formatter should not append
    a dangling 'Verwandte Begriffe:' header — just return the query."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants_by_call"] = [[]]  # LLM returned 200 but no variants
    q = "Welche Genehmigung?"
    out = augment(q, "q1")
    assert out == q
    assert "Verwandte Begriffe" not in out


def test_env_variant_takes_effect(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the caller passes ``variant=None`` and env is set, env wins."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LAI_RERANK_QUERY_VARIANT", "q1")
    mock_llm["variants_by_call"] = [["Bewilligung"]]
    out = augment("Welche Genehmigung?")  # no variant arg
    assert "Bewilligung" in out
