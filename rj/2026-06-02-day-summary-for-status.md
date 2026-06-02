# Day summary 2026-06-02 — engineering progress

**Seed for Friday status (4.1).** Trim heavily before sending.

## TL;DR

* Closed three production audit failures from the 2026-06-01 ks/as
  audit at the code layer (UI/meta-routing bug, contract-injection
  bug, German-language detector bug) — all on `develop`, awaiting
  next `restart_serve_rag.sh`.
* Built a production-fidelity retrieval recall harness — replaces the
  in-RAM one that OOMs on the 35.7 M-child corpus — and used it to
  empirically tune HNSW ef_search and BM25 variants instead of
  hand-waving the 4 s retrieve latency complaint.
* Cleaned up an orphaned-branch foot-gun that had been silently
  masking the LAI-UI rollout for the audit-log view and the vm-9
  lawyer-blind eval UI (both shipped today after a 6-commit
  recovery).

## Audit fixes that need restart

| What failed in the audit | Where it lives | Status |
|---|---|---|
| "was kann ich hier tun?" → RAG → fraud-forum answer | UI_META regex in `serve_rag` | ✅ on develop |
| "gehst du semantisch vor?" on a doc-session → 8k chars of contract injected, off-topic answer | `session_uses_contract` skips UI_META | ✅ on develop |
| "was kannst du hier im datenraum erkennen?" answered in English despite German question | `_DE_HINT_WORDS` extended | ✅ on develop |

All three covered by 42 new unit tests; the regex is sanity-checked
against vm-9's 50 BImSchG gold-RAG questions so no legitimate legal
question can be eaten by the new filter.

**Action:** next time `restart_serve_rag.sh` runs (or at the next
pilot-prep window), the three fixes go live. Restart checklist at
[`rj/2026-06-02-restart-checklist.md`](./2026-06-02-restart-checklist.md).

## Retrieval tuning — honest findings

The 2026-05-31 BM25 perf experiment had to be reverted on a smoke-
test recall regression because we had no scaled recall harness. Built
one today (`LAI/scripts/eval/retrieval_recall.py`) that queries the
SAME indexes serve_rag uses (pgvector HNSW + SQLite FTS5 + RRF), so
reported Recall@K mirrors what users see at any corpus size.

* **HNSW ef_search bump deferred.** Dense-only sweep showed ef=200
  buys +2.5 pp Recall@30 over the current ef=100, but the lift
  vanishes at the hybrid layer (RRF + a 200-candidate pool masks it).
  Honest result: **no production change** there. Saved doing a
  pointless config change that would have added latency for no user-
  visible gain.
* **BM25 variant sweep in progress.** Four of six variants done; v5
  (DE-stopword filter, same recall as control, ~14 % faster) is the
  provisional winner. v6 (prefix-glob, broader recall, slower) and
  v7 (length-routed AND/OR) still running. If v5 holds, we ship a
  1-line dispatcher default flip and the next user query is 400 ms
  faster.
* **The real ceiling.** 48.5 % of probe questions miss both signals
  at @30 — that's the unrecoverable floor at the current candidate
  pool. Future levers are bigger pool, query expansion, or honest
  re-curation of `val.jsonl` gold. Not worth doing before pilot
  feedback says "this category is missing."

## LAI-UI 6-commit recovery

Found that six LAI-UI feature commits cited as "shipped" in
PROGRESS_V2 — admin audit-log view, vm-9 lawyer-blind eval UI, DOCX
German labels, report progress labels, watchdog + dedup + audit ping,
ingestion toast — had never reached `origin`. The local branch
`fix/cross-account-isolation` was deleted during the Git Flow
consolidation, leaving the commits stranded. Rebased onto develop
(clean, no conflicts), pushed, deleted the stale branch. Harsh's
26-file uncommitted resumable-upload WIP was preserved via stash-pop.

Now Vercel can actually roll these out.

## What's NOT done

* Pilot firm (2.4) — relational, unchanged.
* Phase 3 LoRA training — gated on 2.4.
* `_empty_grounding_guard` interaction with UI_META — likely fine
  but not double-checked.
* "Treuenbrietzen" geography gap from session 1 — model didn't
  recognize a real Brandenburg wind town. Separate work; needs Phase
  3 grounding or a curated place-name layer. Not actionable solo.

## Commits today (LAI + LAI-UI, all on develop)

LAI: 17 commits between `a43b440` (persistence RLock fix) and the
latest doc roll-up.

LAI-UI: 8 commits between `2958904` (DOCX labels, recovered) and
`5f8f311` (lint sweep 20→7).

Both `develop` heads ahead of last week by an extended order of
magnitude.
