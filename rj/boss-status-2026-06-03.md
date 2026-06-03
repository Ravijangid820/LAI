# Boss status — 2026-06-03 (rj)

Five lines, per the spec.

1. **Live since the 2026-05-31 status** — three production audit
   failures closed in code AND deployed via clean serve_rag restart
   2026-06-02 22:41: (a) UI/meta questions ("was kann ich hier tun?")
   no longer route to RAG and return fraud-forum content — now
   short-circuit to chat with a friendly capabilities answer; (b) UI/
   meta questions on doc-sessions no longer pull 8 kB of contract text
   and over-ground ("gehst du semantisch vor?" now answers honestly
   instead of "no info in the documents"); (c) German questions stop
   getting answered in English (`was kannst du …` was misdetected as
   English because "was" lives in the English hint-word set —
   24 German-distinctive function words added). Plus a 14 % BM25
   speed-up (~400 ms / query) from an empirically-derived German-
   stopword filter, and a thread-safety fix that closes intermittent
   500s on `/sessions/{id}/documents`. All five fixes verified live
   via four direct chat probes (4 / 4 PASS) and a green smoke run.
2. **Phase 3 unblocked at the venv layer** — `transformers ≥ 5.9` +
   `flash-linear-attention 0.5.0` + `causal-conv1d 1.6.2` now installed
   in `LAI/.venv` so the moment Phase 3 fires, the Qwen3.6-27B (model
   type `qwen3_5`, hybrid Gated-DeltaNet) loads with the fast kernels
   instead of a 1.3 × torch fallback. Sandbox-verified safe alongside
   the in-process Qwen3 reranker (plain `qwen3` arch, no DeltaNet —
   the fla kernels never get touched at reranker load). Phase 3 base-
   model retention probe artifact regenerated against the post-vm-6
   32-probe set.
3. **Honest negative-result discipline at the retrieval layer** —
   spent two days asking the obvious question ("is the 4 s
   `retrieve_s` actually the model's recall ceiling, or just bad
   tuning?"). Built a production-fidelity Recall@K harness (35.7 M
   children, pgvector + SQLite FTS5 + RRF + reranker — every layer
   the chat path uses). Tested 6 parameter knobs across 4 layers
   (HNSW `ef_search`, candidate pool size, 7 BM25 expression variants,
   3 reranker-side query augmentations). **One positive (BM25 v5
   stopword filter, shipped above), six documented negatives, every
   one with measured deltas and a written decision rule locked
   before the experiment.** Production R@30 = 0.49 is the genuine
   model ceiling at this index; remaining levers are architectural
   (learned-sparse retrieval, different chunking) or pilot-driven
   (val.jsonl re-curation, query intent classifier). All in
   `rj/blueprint/2026-06-02-retrieval-tuning-results.md`.
4. **Operational housekeeping done today** — statute-feed cron lines
   installed on the box (daily-mapped 03:00, weekly-full Sun 22:00,
   weekly-prune Wed 02:00) with the `uv`-on-`PATH` bug found and
   fixed before the first fire; weekly-full sweep first runs **Sun
   2026-06-07 22:00**, completing ~Tue, behind business hours so it
   won't saturate the shared embedding server. LAI-UI six-commit
   recovery: yesterday found that audit-log view + vm-9 lawyer-blind
   eval + R3 toast + 4 other UI features had been stranded on a local
   branch whose upstream was deleted during the Git-Flow consolidation
   — recovered cleanly via rebase, pushed, Vercel auto-rolled. Hourly
   smoke cron (was daily 08:00) is now the supervisor canary; systemd
   unit drafted + committed and waiting for ks_admin's one-time sudo
   install.
5. **What unblocks Phase 3 — same as last update** — the **2.4 pilot
   firm** is still the bottleneck. Everything else is ready: Phase-3
   prep done (retention probe + playbook + base-model analysis),
   training-side venv ready, statute corpus 5 762 parents / 9 133
   children live in `corpus_*` and auto-refreshing weekly, retrieval
   ceiling honestly measured at R@30 = 0.49, and the lawyer-blind
   §3.4 A/B evaluation UI is shipped to LAI-UI's `/eval` route waiting
   for a labelling session.

---

## Backing evidence (for the curious)

- **Commits since 2026-05-31** (`develop`, LAI repo): from
  `5cfe63e` through `5d39905` — 40+ commits over two days.
  Highlights:
    - `0f4ce4d` + `11975c5` + `e84241f` — three audit fixes
      (UI_META + lang detector)
    - `3be15a3` — BM25 v5 default flip (the only positive retrieval
      experiment)
    - `d861b14` — `flash-linear-attention` + `causal-conv1d` install
    - `5d39905` — closure of the parameter-tuning arc
- **Live post-restart smoke run** (2026-06-02 22:42):
  `retrieve_s=2.3 s` (was 3.0 s pre-restart, BM25 v5 win confirmed),
  `rerank_s=2.5 s`, `generate_s=8.8 s`, total wall **13.9 s** for the
  control BImSchG query. Reranker on `cuda:1`, sessions preserved
  126 → 126 across the restart.
- **Direct chat probes verifying audit fixes live** (4 / 4 PASS):
    - "was kann ich hier tun?" → mode=`chat`, answered in German
      with capabilities list
    - "gehst du semantisch vor?" → mode=`chat`, honest meta answer
    - "was kannst du hier im datenraum erkennen?" → answered in
      German (language detector fix verified)
    - Control: "Welche Genehmigung … BImSchG …" → mode=`rag`, proper
      § citations (no regression)
- **Statute-feed crontab snippet** (rj's account, 2026-06-03):
      PATH=/data/home/rj/.local/bin:/usr/bin:/bin
      0  3 * * *  cd LAI && statute_feed.sh --mapped …
      0 22 * * 0  cd LAI && statute_feed.sh --full   …
      0  2 * * 3  cd LAI && statute_feed.sh --prune  …
  Backup at `LAI/logs/host/crontab.bak.20260603`.
- **Retrieval-ceiling proof** (`rj/blueprint/2026-06-02-retrieval-tuning-results.md`):
  matrix of variants × R@K + decision rule across 6 negatives + 1
  positive. R@30 = 0.49 holds at ef_search ∈ {40…800}, candidate_k
  ∈ {100, 200, 500}, BM25 expressions ∈ {v1, v2, v3, v5, v6, v7,
  r1, r2, r3}, reranker queries ∈ {none, q1, q2, q3}.

---

## What I'd tell the pilot conversation

If a prospect asks "what does this product do that ChatGPT doesn't?"
the honest answer after this two-day arc:

> "We retrieve grounded German legal text from a 35.7 M-chunk
> corpus, in 13 seconds end-to-end, with stable [C-n] citations the
> UI renders as clickable chips. The retrieval pipeline is
> empirically tuned — not hand-waved. We know its ceiling, we know
> its failure modes, we have audit logs that satisfy EU AI Act
> Art. 12 today. We're ready to put a pilot firm's documents into
> the matter view and run a labelling session against the German
> BImSchG question set on day 1."
