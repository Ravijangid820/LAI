"""Unit tests for lai.search.query_rewriter.

The rewriter is best-effort by design — every LLM failure path must
return safely without breaking the BM25 expression downstream. These
tests pin that behaviour against a mock LLM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-rewriter-0123456789abcdef")

import httpx
import pytest

from lai.search import query_rewriter as qr

pytestmark = pytest.mark.unit


# ── _is_safe_fts5_token + filter_safe_expansions ────────────────────────


@pytest.mark.parametrize(
    "tok,expected",
    [
        ("Genehmigung", True),
        ("Windenergieanlagentechnik", True),
        ("a", False),  # too short
        ("", False),
        ("x" * 90, False),  # too long
        ("Genehm*", False),  # FTS5 prefix glob (parser-unsafe)
        ('"quoted"', False),  # bare quotes
        ("(Genehmigung)", False),  # FTS5 paren operator
        ("Antrag^2", False),  # ^ is FTS5 boost
        ("Genehmigung-Bescheid", True),  # hyphen is fine
    ],
)
def test_is_safe_fts5_token(tok: str, expected: bool) -> None:
    assert qr._is_safe_fts5_token(tok) is expected


def test_filter_safe_expansions_drops_unsafe() -> None:
    raw = ["Genehmigung", "Genehm*", "Bewilligung", "(invalid)", "x", "Antrag^2"]
    assert qr.filter_safe_expansions(raw) == ["Genehmigung", "Bewilligung"]


# ── rewrite_bm25_expr ────────────────────────────────────────────────────


def test_rewrite_bm25_expr_appends_or_join() -> None:
    base = '"Genehmigung" OR "Antrag"'
    out = qr.rewrite_bm25_expr(base, ["Genehmigungsverfahren", "beantragt"])
    assert out == '("Genehmigung" OR "Antrag") OR "Genehmigungsverfahren" OR "beantragt"'


def test_rewrite_bm25_expr_passthrough_on_empty_expansions() -> None:
    base = '"Genehmigung"'
    assert qr.rewrite_bm25_expr(base, []) == base


def test_rewrite_bm25_expr_passthrough_on_empty_base() -> None:
    """If v5 returned None (empty query), no expansion should turn that
    into a valid expression — we must keep returning None upstream."""
    assert qr.rewrite_bm25_expr("", ["Genehmigungsverfahren"]) == ""


# ── _select_top_tokens ──────────────────────────────────────────────────


def test_select_top_tokens_picks_longest_distinct() -> None:
    q = "Welche Genehmigung ist nach dem BImSchG für eine Windenergieanlage erforderlich?"
    toks = qr._select_top_tokens(q, n=3, min_len=4)
    assert len(toks) == 3
    assert "Windenergieanlage" in toks
    assert all(len(t) > 4 for t in toks)


# ── get_expansions / get_safe_expansions ────────────────────────────────


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace httpx.post with a deterministic stub. Returns a dict so
    individual tests can program the response."""
    state: dict[str, Any] = {"variants": ["foo", "bar", "baz"], "raises": None, "calls": 0}

    def fake_post(url: str, *, json: dict, timeout: float = 0) -> httpx.Response:
        state["calls"] += 1
        if state["raises"]:
            raise state["raises"]
        body = {
            "choices": [
                {
                    "message": {
                        "content": __import__("json").dumps(
                            {"variants": state["variants"]}
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(qr.httpx, "post", fake_post)
    return state


def test_get_expansions_returns_empty_on_none_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    assert qr.get_expansions("anything", "none") == []


def test_get_expansions_r1_makes_one_call_per_top_token(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants"] = ["x", "y", "z"]
    out = qr.get_expansions("Genehmigung Antrag Windenergieanlage", "r1")
    # 3 LLM calls (one per top-3 token), each returns 3 variants;
    # dedupe yields 3 unique entries.
    assert mock_llm["calls"] == 3
    assert set(out) == {"x", "y", "z"}


def test_get_expansions_r2_makes_one_call_for_whole_query(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants"] = ["Bewilligung", "Erlaubnis"]
    out = qr.get_expansions("Welche Genehmigung ist erforderlich?", "r2")
    assert mock_llm["calls"] == 1
    assert out == ["Bewilligung", "Erlaubnis"]


def test_get_expansions_cached_on_second_call(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants"] = ["a", "b"]
    qr.get_expansions("Genehmigung", "r2")
    first_calls = mock_llm["calls"]
    qr.get_expansions("Genehmigung", "r2")  # same query, should hit cache
    assert mock_llm["calls"] == first_calls, "second call must hit disk cache"


def test_get_expansions_empty_on_llm_error(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection error / 5xx / JSON garbage must NEVER raise — must
    return []. A failed rewrite cannot block retrieval."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["raises"] = httpx.ConnectError("down")
    assert qr.get_expansions("Genehmigung", "r2") == []


def test_get_expansions_empty_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The LLM said 200 but returned garbage — must still return []."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))

    def fake_post(url: str, *, json: dict, timeout: float = 0) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not valid json {"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(qr.httpx, "post", fake_post)
    assert qr.get_expansions("Genehmigung", "r2") == []


def test_get_safe_expansions_filters_unsafe_tokens(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants"] = ["Bewilligung", "Genehm*", "Erlaubnis"]
    out = qr.get_safe_expansions("Genehmigung", "r2")
    assert out == ["Bewilligung", "Erlaubnis"]


def test_failed_llm_call_does_not_poison_cache(
    mock_llm: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx → [] response must NOT be cached as []; the next call should
    try the LLM again. Otherwise a transient outage permanently breaks
    the rewriter for every query that came in during the window."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["raises"] = httpx.ConnectError("down")
    out_fail = qr.get_expansions("Genehmigung", "r2")
    assert out_fail == []

    mock_llm["raises"] = None
    mock_llm["variants"] = ["Bewilligung"]
    out_recovery = qr.get_expansions("Genehmigung", "r2")
    assert out_recovery == ["Bewilligung"], (
        "post-recovery call should hit the LLM, not the (poisoned) cache"
    )


# ── eval.py integration (smoke) ─────────────────────────────────────────


def test_bm25_match_expr_passthrough_when_rewrite_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without LAI_QUERY_REWRITE_VARIANT, behaviour is exactly v5 (current
    production). This is the safety net that keeps prod inert."""
    monkeypatch.delenv("LAI_QUERY_REWRITE_VARIANT", raising=False)
    from lai.search.eval import _bm25_match_expr, _bm25_match_expr_v5

    q = "Welche Genehmigung ist erforderlich?"
    assert _bm25_match_expr(q) == _bm25_match_expr_v5(q)


def test_bm25_match_expr_adds_expansions_when_env_set(
    mock_llm: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With LAI_QUERY_REWRITE_VARIANT=r2 the expression gains an OR-clause
    with the LLM's expansions appended to the v5 base."""
    monkeypatch.setenv("LAI_QUERY_REWRITE_VARIANT", "r2")
    monkeypatch.setenv("LAI_QUERY_REWRITE_CACHE_DIR", str(tmp_path))
    mock_llm["variants"] = ["Bewilligung"]
    from lai.search.eval import _bm25_match_expr

    out = _bm25_match_expr("Welche Genehmigung ist erforderlich?")
    assert out is not None
    assert '"Bewilligung"' in out
    assert "Genehmigung" in out
