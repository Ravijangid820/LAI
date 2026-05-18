"""Retrieval-sanity check against the 5 golden German questions.

Strategy doc §5.3 — runs the canonical set of wind-energy questions
against ``serve_rag``'s ``POST /query`` and reports for each:

* whether the response came back at all
* how many expected keywords appear in the top-K retrieved chunks
* end-to-end latency for the turn

Exits 0 when every question meets its ``min_keyword_hits`` bar (the
strategy doc's "3/5 of top-5 chunks" rule of thumb, but per-question
configurable in the fixture). Exits 1 otherwise — so this can be
wired into CI as a pre-demo gate.

Usage
-----

    # Default: hit the host-mode serve_rag at :18000
    python -m scripts.eval.golden_retrieval_sanity

    # Override the endpoint
    LAI_SERVE_RAG_URL=http://localhost:18000 \\
        python -m scripts.eval.golden_retrieval_sanity

    # Print every retrieved chunk's first line, not just pass/fail
    python -m scripts.eval.golden_retrieval_sanity --verbose

The runner is intentionally sync + stdlib-only (apart from ``httpx``,
which ``serve_rag`` already vendors) so it can be invoked from a
freshly-cloned check-out without a full dev environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# Resolve repo root from this file's location.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "golden_de.json"
DEFAULT_URL = os.environ.get("LAI_SERVE_RAG_URL", "http://localhost:18000")
DEFAULT_TOP_K = 5
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class QuestionResult:
    """One scored question."""

    id: str
    question: str
    passed: bool
    keyword_hits: int
    min_required: int
    matched_keywords: list[str]
    missed_keywords: list[str]
    n_chunks: int
    total_seconds: float
    error: str | None = None


def _score_question(
    client: httpx.Client,
    base_url: str,
    spec: dict[str, Any],
    top_k: int,
    verbose: bool,
) -> QuestionResult:
    """Run one golden question and score it against ``expected_keywords``."""
    qid = str(spec["id"])
    question = str(spec["q"])
    expected = [str(k) for k in spec.get("expected_keywords", [])]
    min_required = int(spec.get("min_keyword_hits", 1))

    body = {"question": question, "top_k": top_k}
    t0 = time.perf_counter()
    try:
        resp = client.post(f"{base_url.rstrip('/')}/query", json=body)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return QuestionResult(
            id=qid, question=question, passed=False,
            keyword_hits=0, min_required=min_required,
            matched_keywords=[], missed_keywords=expected,
            n_chunks=0, total_seconds=time.perf_counter() - t0,
            error=f"transport: {exc}",
        )
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        return QuestionResult(
            id=qid, question=question, passed=False,
            keyword_hits=0, min_required=min_required,
            matched_keywords=[], missed_keywords=expected,
            n_chunks=0, total_seconds=elapsed,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        return QuestionResult(
            id=qid, question=question, passed=False,
            keyword_hits=0, min_required=min_required,
            matched_keywords=[], missed_keywords=expected,
            n_chunks=0, total_seconds=elapsed,
            error=f"non-JSON response: {exc}",
        )
    chunks = data.get("chunks") or []
    # Look for each expected keyword anywhere in any chunk text.
    # Case-insensitive substring — the strategy doc's "expected
    # keywords" list is loose by design (it's a sanity check, not a
    # benchmark). The 350 GB corpus chunks themselves contain
    # statute names verbatim so casefold matching is sufficient.
    haystack = "\n".join(str(c.get("text") or "") for c in chunks).casefold()
    matched: list[str] = []
    missed: list[str] = []
    for kw in expected:
        if kw.casefold() in haystack:
            matched.append(kw)
        else:
            missed.append(kw)
    passed = len(matched) >= min_required
    if verbose:
        print(f"  Q: {question}")
        print(f"  → {len(chunks)} chunks in {elapsed:.2f}s, mode={data.get('mode')}")
        for i, c in enumerate(chunks[:3], 1):
            text = str(c.get("text") or "").replace("\n", " ")[:120]
            print(f"      [{i}] {text}…")
    return QuestionResult(
        id=qid, question=question, passed=passed,
        keyword_hits=len(matched), min_required=min_required,
        matched_keywords=matched, missed_keywords=missed,
        n_chunks=len(chunks), total_seconds=elapsed,
    )


def _print_summary(results: list[QuestionResult]) -> None:
    """Tabular summary suitable for CI logs."""
    print()
    print(f"{'ID':<28} {'pass':<5} {'hits':<6} {'chunks':<7} {'t (s)':<8} {'notes'}")
    print("-" * 92)
    for r in results:
        status = "✅" if r.passed else "❌"
        hits = f"{r.keyword_hits}/{len(r.matched_keywords) + len(r.missed_keywords)}"
        note = r.error or (", ".join(f"−{k}" for k in r.missed_keywords) if r.missed_keywords else "")
        print(f"{r.id:<28} {status:<5} {hits:<6} {r.n_chunks:<7} {r.total_seconds:<8.2f} {note}")
    n_pass = sum(1 for r in results if r.passed)
    print("-" * 92)
    print(f"PASS {n_pass}/{len(results)}    (threshold: per-question ``min_keyword_hits``)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE,
                   help=f"Path to golden_de.json (default: {DEFAULT_FIXTURE.relative_to(REPO_ROOT)})")
    p.add_argument("--url", default=DEFAULT_URL,
                   help=f"serve_rag base URL (default: {DEFAULT_URL})")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help=f"top_k requested per question (default: {DEFAULT_TOP_K})")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                   help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})")
    p.add_argument("--verbose", action="store_true",
                   help="Print top-3 chunk previews per question.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of the text table.")
    args = p.parse_args(argv)

    if not args.fixture.exists():
        print(f"error: fixture not found at {args.fixture}", file=sys.stderr)
        return 2
    with args.fixture.open() as f:
        specs = json.load(f)
    if not isinstance(specs, list) or not specs:
        print("error: fixture must be a non-empty JSON array", file=sys.stderr)
        return 2

    print(f"Running {len(specs)} golden question(s) against {args.url}")
    results: list[QuestionResult] = []
    with httpx.Client(timeout=args.timeout) as client:
        for spec in specs:
            results.append(_score_question(client, args.url, spec, args.top_k, args.verbose))

    if args.json:
        print(json.dumps(
            [
                {
                    "id": r.id, "passed": r.passed,
                    "keyword_hits": r.keyword_hits, "min_required": r.min_required,
                    "matched_keywords": r.matched_keywords,
                    "missed_keywords": r.missed_keywords,
                    "n_chunks": r.n_chunks,
                    "total_seconds": round(r.total_seconds, 3),
                    "error": r.error,
                }
                for r in results
            ],
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_summary(results)

    n_fail = sum(1 for r in results if not r.passed)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
