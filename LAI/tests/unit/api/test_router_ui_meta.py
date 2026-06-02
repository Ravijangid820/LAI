"""Unit tests for serve_rag.UI_META — the UI/navigation/meta-AI router.

The router added 2026-06-02 after the ks/as production audit found
"was kann ich hier tun?" routed to RAG and was answered with random
fraud-forum content. UI/meta questions look like real questions (they
end with "?", they're often > 20 chars) but carry no legal intent —
the prior router fell through to the LLM classifier which biased
toward RAG.

Two invariants:

1. **Positive cases** — every observed-failure phrasing AND the
   preventive paraphrases from the blueprint must route to chat
   (``needs_rag → False``).
2. **Safety against gold-RAG** — every question in vm-9's BImSchG
   labelled set (``LAI/eval_questions/bimschg_50.jsonl``, 50 real
   legal questions) must still route to RAG. A regex that eats even
   one of these is worse than no regex at all and is rejected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-router-unit-0123456789abcdef")

import pytest

from lai.api import serve_rag as sr

pytestmark = pytest.mark.unit


# ── 1. UI / meta phrasings must route to chat ────────────────────────────


UI_META_HITS = [
    # The exact 2026-05-25 ks failure
    "was kann ich hier tun?",
    # Near-paraphrases of the same intent
    "was kann ich hier machen?",
    "was kann ich hier alles tun?",
    "Was kann ich tun?",
    # German UI / function questions
    "wie funktioniert das?",
    "Wie funktioniert dieser Chat hier?",
    "Wie funktioniert die Suche?",
    "wie nutze ich das hier?",
    # German meta about the AI itself
    "Gehst du semantisch vor?",
    "verstehst du die dokumente?",
    "Verstehst du das?",
    "liest du wirklich die Dokumente?",
    "bist du online?",
    "Bist du wach?",
    "wer hat dich gebaut?",
    # English mirrors
    "what can I do here?",
    "What can I do with this?",
    "how does this work?",
    "How does this function?",
    "do you understand this?",
    "Do you read the documents?",
]


@pytest.mark.parametrize("q", UI_META_HITS)
def test_ui_meta_routes_to_chat(q: str) -> None:
    assert sr.UI_META.match(q) is not None, f"UI_META failed to match: {q!r}"


# ── 2. Gold-RAG safety — bimschg_50.jsonl never matches UI_META ──────────


def _load_bimschg_questions() -> list[str]:
    """Load vm-9's labelled gold-RAG set; skip if not present (CI fixture)."""
    path = Path(__file__).resolve().parents[3] / "eval_questions" / "bimschg_50.jsonl"
    if not path.exists():
        pytest.skip(f"bimschg_50.jsonl not present at {path}")
    questions: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            q = row.get("question") or row.get("q") or ""
            if isinstance(q, str) and q.strip():
                questions.append(q.strip())
    return questions


def test_no_gold_rag_question_matches_ui_meta() -> None:
    """Every legitimate legal question must escape the UI_META filter.

    A pattern that eats even one legal question is worse than no pattern
    at all — it silently demotes paying-customer queries to chat-mode
    and they'll get a wrong-shape answer with no telemetry signal.
    """
    questions = _load_bimschg_questions()
    assert questions, "bimschg_50.jsonl loaded zero questions"
    offenders = [q for q in questions if sr.UI_META.match(q) is not None]
    assert not offenders, (
        f"UI_META incorrectly matched {len(offenders)} legal questions; "
        f"first 3: {offenders[:3]!r}"
    )


# ── 3. needs_rag integration ────────────────────────────────────────────


def test_needs_rag_returns_false_on_ui_meta_phrase() -> None:
    assert sr.needs_rag("was kann ich hier tun?") is False


def test_needs_rag_returns_false_even_with_legal_word_in_meta_question() -> None:
    """A legal keyword inside a clearly-meta question must NOT override
    the UI_META verdict — the meta check runs BEFORE the legal-keyword
    short-circuit. Without this ordering the prior router would route
    'wie funktioniert das mit BImSchG hier?' to RAG."""
    assert sr.needs_rag("Wie funktioniert das mit BImSchG hier?") is False


def test_needs_rag_still_returns_true_on_real_legal_question() -> None:
    """A real legal query must still route to RAG — the UI_META filter
    cannot regress LEGAL_KEYWORDS coverage."""
    assert sr.needs_rag("Welche Schutzgüter regelt § 1 BImSchG?") is True


# ── 4. session_uses_contract integration ────────────────────────────────


def test_session_uses_contract_skips_ui_meta_question(monkeypatch) -> None:
    """A meta question on a session WITH an uploaded document must NOT
    pull the document into the prompt — otherwise the 8k-char contract
    text feeds an off-topic doc-grounded answer to the meta question
    (the 2026-06-01 ks/as session-2 failure)."""
    monkeypatch.setattr(
        sr.persistence, "list_matter_documents", lambda sid, user_id=None: [object()]
    )
    assert sr.session_uses_contract("sess-1", "gehst du semantisch vor?") is False


def test_session_uses_contract_still_pulls_doc_on_real_question(monkeypatch) -> None:
    """A real content question on the same session keeps the contract
    injection — the UI_META exclusion cannot regress the core use
    case ("upload a PDF, then ask about it")."""
    monkeypatch.setattr(
        sr.persistence, "list_matter_documents", lambda sid, user_id=None: [object()]
    )
    assert sr.session_uses_contract("sess-1", "Was steht in der Genehmigung?") is True
