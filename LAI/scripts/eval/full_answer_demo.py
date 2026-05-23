"""Full RAG pipeline demo with pgvector provenance proof.

Runs the real chain — embed (Qwen3-Embedding-8B :8003) → pgvector dense
search → LLM (Qwen3.6-27B :8005) — for a set of lawyer/user questions in
German and English, and prints, for every answer:

  1. the pgvector retrieval evidence (child_id, parent_id, similarity)
  2. a direct re-query of corpus_child_chunks proving those exact ids
     live in pgvector (provenance, not a cache)
  3. the [C-n] sources fed to the LLM
  4. the full LLM answer (no truncation)
  5. timings

Reranker is omitted (it is in-process in serve_rag and GPU-resident);
this proves the answer is grounded in pgvector-retrieved text, which is
the claim under test.
"""

from __future__ import annotations

import time

import httpx
import psycopg2

from lai.search.eval import embed_query
from lai.common.retrieval import RetrievalClient


def llm_answer(system: str, user: str, max_tokens: int = 1200) -> str:
    """Call vLLM exactly as serve_rag's streaming path does — top-level
    chat_template_kwargs:{enable_thinking:false} (the form vLLM honours)."""
    r = httpx.post(
        "http://localhost:8005/v1/chat/completions",
        json={
            "model": "qwen3.6-27b",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=180.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# Mirror serve_rag's RAG_SYSTEM (citation discipline) — kept short here;
# the live server uses the full prompt incl. statutory-grounding.
RAG_SYSTEM = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage ausschließlich auf "
    "Grundlage der unten bereitgestellten Quellen. Jede Quelle trägt ein "
    "Handle [C-n]. Zitiere bei JEDER inhaltlichen Aussage das passende "
    "Handle. Verwende AUSSCHLIESSLICH Handles, die unten erscheinen. Wenn "
    "die Quellen die Frage nicht beantworten, sage das ehrlich."
)
EN_DIRECTIVE = (
    "\n\nWICHTIG: Antworte auf Englisch, zitiere deutsche Gesetze/Urteile "
    "aber im Original."
)

QUESTIONS = [
    ("Lawyer", "de",
     "Ist für die Errichtung einer Windenergieanlage mit einer Gesamthöhe "
     "von mehr als 50 Metern eine Genehmigung nach dem Bundes-"
     "Immissionsschutzgesetz erforderlich, und welches Verfahren ist "
     "einschlägig?"),
    ("User", "de",
     "Was passiert mit der Windkraftanlage am Ende der Vertragslaufzeit "
     "und wer trägt die Kosten für den Rückbau?"),
    ("Lawyer", "en",
     "Does erecting a wind turbine taller than 50 metres require a permit "
     "under the German Federal Immission Control Act, and which procedure "
     "applies?"),
    ("User", "en",
     "As a landowner, what should I check before signing a lease for a "
     "wind turbine on my land?"),
]

PG = dict(host="127.0.0.1", port=5434, dbname="lai_db",
          user="lai_user", password="lai_test_password_2024")


def render_sources(rows):
    parts = []
    for i, (cid, pid, sim, text) in enumerate(rows, 1):
        parts.append(f"[C-{i}]  Rechtskorpus\n{text}")
    return "\n\n".join(parts)


def main():
    rc = RetrievalClient()
    pg = psycopg2.connect(**PG)

    for who, lang, q in QUESTIONS:
        print("=" * 100)
        print(f"QUESTION ({who}, {lang.upper()}): {q}")
        print("=" * 100)

        t0 = time.perf_counter()
        qv = embed_query(q, with_prefix=True)
        t_embed = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        hits = rc.dense_search(qv.tolist(), top_k=4)
        t_ret = (time.perf_counter() - t0) * 1000
        pt = rc.fetch_parent_texts([h.parent_id for h in hits if h.parent_id])

        rows = []
        for h in hits:
            txt = (pt.get(h.parent_id) or h.content or "").replace("\n", " ").strip()
            rows.append((h.child_id, h.parent_id, h.similarity, txt[:1200]))

        # ── PGVECTOR PROVENANCE PROOF ────────────────────────────────────
        # Re-query corpus_child_chunks for the exact ids the ANN search
        # returned. If these rows come back, the passages demonstrably
        # live in pgvector (not a cache, not a fixture).
        ids = [r[0] for r in rows]
        cur = pg.cursor()
        cur.execute(
            "SELECT id, parent_id, length(content) FROM corpus_child_chunks "
            "WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        proof = cur.fetchall()
        cur.close()

        print(f"\n[PGVECTOR EVIDENCE] embed {t_embed:.0f}ms · dense_search "
              f"{t_ret:.0f}ms · table=corpus_child_chunks")
        for cid, pid, sim, _ in rows:
            print(f"  child_id={cid}  parent_id={pid}  cosine_sim={sim:.4f}")
        print(f"  ↳ re-query corpus_child_chunks WHERE id=ANY({ids}) →")
        for pid_, parent_, clen in proof:
            print(f"     ✓ id={pid_} exists in pgvector  parent_id={parent_}  content_len={clen}")

        # ── LLM ANSWER GROUNDED IN THOSE PASSAGES ───────────────────────
        src_block = render_sources(rows)
        system = RAG_SYSTEM + (EN_DIRECTIVE if lang == "en" else "")
        user = f"Quellen:\n{src_block}\n\nFrage: {q}"
        t0 = time.perf_counter()
        answer = llm_answer(system, user, max_tokens=1200)
        t_llm = (time.perf_counter() - t0) * 1000

        print(f"\n[C-n SOURCES FED TO LLM] (verbatim from pgvector)")
        for i, (cid, pid, sim, txt) in enumerate(rows, 1):
            print(f"  [C-{i}] (child_id={cid}): {txt[:200]}…")

        print(f"\n[ANSWER] (LLM {t_llm:.0f}ms · Qwen3.6-27B, grounded in the "
              f"[C-n] above)\n")
        print(answer.strip())
        print()

    rc.close()
    pg.close()


if __name__ == "__main__":
    main()
