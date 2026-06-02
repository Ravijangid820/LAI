# Plan — mode-router-quality (Phase 1.x candidate)

**Date:** 2026-06-02 · **Owner:** rj · **Status:** SCOPED — awaiting prioritization
**Why a plan:** the 2026-06-01 ks/as production audit
([PROGRESS_V2.md:309-344](../harsh/PROGRESS_V2.md#L309)) surfaced a
real failure mode that's NOT an LLM problem — it's a **routing**
problem. The fix is in `serve_rag`, not the model.

## The bug, concretely

**"was kann ich hier tun?"** (a generic UI question) routed to RAG.
The pipeline did exactly what it was told: embed → dense ANN + BM25 →
RRF → reranker → LLM with retrieved chunks. The reranker happily found
the most lexically/semantically similar chunks to "was kann ich tun"
in a 35.7 M-row corpus that includes 1909 German fraud-forum scrape
data; the LLM dutifully answered the navigation question with a
criminal-law lecture about *"Konto zur Verfügung stellen"*.

The user ended the answer with `(unbelegt)` (good: the citation
validator caught that the answer wasn't grounded) — but by then the
LLM had already burned a 30-second turn producing the wrong-topic
response. The mode router was the wrong layer to catch this.

## Why the current router misses it

[serve_rag.py:2015-2039](../LAI/src/lai/api/serve_rag.py#L2015) —
`needs_rag(question)` falls through this ladder:

1. `len(q) < 4` → no RAG
2. `CONVERSATIONAL` regex match → no RAG
3. `LEGAL_KEYWORDS` regex match → RAG
4. `"?" in q and len(q) > 20` → RAG
5. Fallback: 4-token LLM classifier
6. Exception → RAG (default helpful)

`CONVERSATIONAL` ([serve_rag.py:1048-1053](../LAI/src/lai/api/serve_rag.py#L1048))
covers greetings, "wer bist du", "was kannst du" — but NOT "was kann
**ich** hier tun?", "wie funktioniert das?", "gehst du semantisch
vor?". These slipped past every rule and the LLM classifier said
"RAG" (because the prompt is biased toward RAG for ambiguous cases).

## Goal

Reduce the rate of UI / meta / navigation questions that incorrectly
route to RAG, **without** lowering the rate of real-content questions
that correctly route to RAG. Measured against a small labelled probe
set (see below).

## Two-phase approach

### Phase 1 — regex prefilter for UI / meta intents (~½ day)

Extend `CONVERSATIONAL` (or split into a new `UI_META` companion
regex) to catch the observed failure modes plus a handful of
preventive ones:

* "was kann ich (hier|tun|machen)" — what can I do here
* "wie funktioniert (das|es|dieser|diese)" — how does this work
* "(gehst|verstehst|erkennst|liest) du (semantisch|den|die|das)" — meta about AI
* "(kannst|kannst du) du (mir|mir helfen|helfen)" — generic help ask
* "(was|welche) (sind das|ist das|für …)" — list-the-context questions
* "warum (das|hier|jetzt)" — meta-why
* English mirrors: "what can I do here", "how does this work", "do you understand"

Each pattern becomes a unit-tested case. The Phase 2.4 pilot demo path
gets these right by Day 0.

**Scope discipline:** the regex MUST NOT eat legitimate legal
questions. False positives that block legal queries are worse than
false negatives that retrieve too much. Each pattern goes in only
after a sanity check against a small "definitely-RAG" gold list (the
EEG/BImSchG-style questions vm-9 already curated for
`bimschg_50.jsonl`). If a pattern hits ANY gold-RAG question, it's
rejected or narrowed.

**Out of scope for Phase 1:**

* Geography-knowledge grounding (the "Treuenbrietzen" gap — separate
  Phase-3 prep concern).
* Language-drift handling (English answers to German questions — a
  prompt + system-message issue, not routing).
* The over-grounding failure on "gehst du semantisch vor?" already
  goes through Phase 1's meta regex once the pattern lands.

### Phase 2 — embedding-based intent classifier (2–3 days)

If Phase 1 closes >70 % of the observed failures, Phase 2 may not be
worth the build cost. If it doesn't (i.e. the regex is brittle on
typos / paraphrases), implement:

* A small (~200-question) labelled probe set:
  `{intent: ui|meta|content, question}`.
* A binary classifier: query the embedding service, k-NN against
  centroids of the labelled set. Returns `intent ∈ {chat, rag}` plus a
  confidence. Below a threshold → fall back to the current LLM
  classifier.
* Per-bucket precision/recall reported via a new `scripts/eval/
  mode_router_recall.py` so this can be re-tuned the same way BM25
  variants are tuned.

Phase 2's data-quality story: the labelled set must include the
adversarial cases that motivate it ("was kann ich hier tun?",
"gehst du semantisch vor?"), the gold-RAG cases vm-9 already has, and
~50 in-the-wild questions sampled from `sessions.db` (no
de-anonymization needed — the user_id / session_id are fine to drop).

## Evaluation

Single labelled probe file, used by both phases:
`LAI/training/fine_tuning/eval/probes/mode_router_probes.jsonl`.

Schema:

```json
{"id": "router_001", "question": "was kann ich hier tun?", "expected_mode": "chat", "category": "ui_meta", "notes": "observed in 2026-05-25 ks session"}
```

Both phases score against the same gates:

* `ui_meta_precision` = correct chat-routes / total chat-routes
* `ui_meta_recall` = correct chat-routes / total true-chat in probe set
* `content_precision` = correct rag-routes / total rag-routes (must
  stay ≥ Phase 0 baseline — no regression)

Ship Phase 1 if it lifts `ui_meta_recall` ≥ 0.7 *and* leaves
`content_precision` within 1 pp of baseline.

## Why this isn't urgent

* **Not blocking the pilot.** The 2026-05-31 audit verified rj-2 smoke
  E2E green; the LLM answered the off-topic question with `(unbelegt)`
  attached, so the user-visible damage was "wrong topic, but honest."
  A pilot can tolerate that for 1 turn; a paying customer can't.
* **Doesn't block Phase 3** (LoRA training) — orthogonal subsystem.
* **Sequencing:** ideally lands AFTER the 2.4 pilot conversation
  starts, because the pilot's first-week feedback will tell us which
  routing failure modes matter to a paying user.

## Decision the project should make

| | Cost | Outcome |
|---|---|---|
| **Phase 1 only** | ½ day | Catches the named failures + a few near-paraphrases. Brittle on typos but cheap to maintain. |
| **Phase 1 + Phase 2** | 3 days | Survives paraphrase and typo, costs 1 extra embed call per query, needs labelled data. |
| **Skip both, defer to pilot feedback** | 0 | Pilot users may see the same failure; we get real data instead of guessing. |

**Recommendation:** Phase 1 NOW (½ day, ships the day after pilot
lands or in any spare half-day before that), Phase 2 only if pilot
feedback says "this is still happening on slight phrasings."
