# LAI — Deep Re-Verification Pass

**Date:** 2026-05-14
**Task:** Re-check every substantive claim from `AUDIT.md`, `TECH_STACK.md`,
`DDIQ_ROADMAP.md`, `DEEP_RESEARCH.md`, `VERIFICATION.md`,
`ARCHITECTURE_BRIEF.md` against the actual code and live system. Flag any
claim that doesn't hold; flag any that came from agent estimates rather than
direct measurement; report what's changed since the original probes.

**Verdict:** the bulk of prior findings hold. **Seven specific corrections** are
needed (listed below). The biggest is that the "live system is broken right
now" finding (`AUDIT.md` C3) **was true at probe time but is no longer current**
— someone has brought the runtime stack back up.

---

## A — Claims confirmed by direct re-verification

These were probed again and the original finding holds.

| Claim | Verification |
|-------|--------------|
| `pipeline_local.db` is the live corpus, 350 GB, **9.46M embedded** chunks at 4096-dim | Re-probed: `child_embeddings` = 9,462,540 rows; `child_chunks` = 49,953,830; `parent_chunks` = 13,807,675; the JOIN `child_embeddings × child_chunks` that `eval.py:104` loads = 9,462,540 — exactly what `serve_rag` loads at startup. |
| 155 GB RAM load | 9,462,540 × 4096 × 4 bytes = **155.04 GB**. Math holds. |
| `ddiq_report.py` is **2,463 lines / 129,035 bytes** | `wc -l -c` confirms exactly. |
| `parse_wea_count` is the first-int regex bug | Read directly at line 839: `m = re.search(r"(\d+)", value); return int(m.group(1)) if m else 0`. Confirmed. |
| `request_fingerprint` index is **not UNIQUE** (TOCTOU exists) | Read DDL directly: `CREATE INDEX IF NOT EXISTS ddiq_reports_fingerprint_idx ON ddiq_reports(request_fingerprint) WHERE …` — plain INDEX, not UNIQUE. Confirmed. |
| `lai.api.main` imported by nothing live | Grep across `LAI/src`, `LAI/scripts`, `LAI/ops`, `LAI/micro-services` returned **zero** non-README hits. |
| `citation_verifier` not in live code | Grep for `citation_verifier|CitationVerifier|verify_citation` in `serve_rag.py` and `ddiq_report.py` returned **zero**. |
| `config.py` `pool_max_size` default 10 at line 40 | Confirmed (exec-summary's "line 37" was wrong; that error was already flagged). |
| Zero automated tests | Re-checked `LAI/tests/**` and `LAI-UI/src/**` — no `.test.*`, `.spec.*`, or `test_*.py` files anywhere. |
| Lamstedt vs Bremen geographic claim | Web-verified Lamstedt = 53.6228 N / 9.1479 E; Bremen Überseestadt = ~53.09 / 8.78. Δ ≈ **65 km**. The smoke-test geocoding-to-Bremen claim is rock-solid. |
| HF token in `Docker/inference_engine/.env:11` | Re-read; still there. **Rotate.** |
| Auth is fake (frontend) / absent (backends) | `AuthContext.tsx:55-82` and the absence of `Authorization`/`Bearer` in both backend route sets — re-confirmed earlier this session. |

---

## B — Corrections to prior reports (seven items)

### B1 — "The live system is broken right now" → NO LONGER CURRENT

`AUDIT.md` C3 said `lai-backend` was up-but-crashed because it couldn't resolve
`lai_postgres_main`. **That was true when probed.** It is no longer true.

Now (just probed):
- **`lai_postgres_main`** (`pgvector/pgvector:pg16`) — Up ~1 hour, healthy, port 5434
- **`lai_analyzer_llm`** — Up ~1 hour, healthy, port 8005
- **`lai_embedding`** — Up ~1 hour, healthy, port 8003
- **`lai_redis`** — Up ~1 hour, healthy
- **`lai-backend`** — **Up 25 hours, healthy.** `/health` returns
  `{"status":"ok","model":"qwen3.6-27b"}`
- **`serve_rag`** — running on host (PID 3413088, since 11:28). `/health` returns
  `{"ok":true,"loaded":true,"llm_backend":"remote","llm_model":"qwen3.6-27b","n_sessions":12}`
- **Still unhealthy:** only `lai-user-doc-processor`

The full intended runtime topology (per `LAI/docker-compose.yml`) is now
actually running, and the network membership matches the compose definition.
Phase 0's "fix the outage" item is largely resolved.

### B2 — "Dead code: ~6,000 LOC" → actually **~3,200 LOC**

Re-counted directly:

| Module | LOC |
|--------|-----|
| `api/main.py` | 119 |
| `api/pipeline.py` | 128 |
| `auth/` | 168 |
| `documents/` | 634 |
| `extraction/` | 597 |
| `generation/` | 548 |
| `infra/` | 338 |
| `search/{routes,repository,hybrid_search,reranker,query_analyzer}.py` | 625 |
| **Total** | **3,157** |

The agent's "~6,000" was overstated nearly 2×. Still substantial, still worth
deleting — but the number was wrong. *Note:* I excluded `__init__.py` files
from the recount; including them would add ~50 lines.

### B3 — DDiQ router endpoints: **12, not 14**

Counted `@router.{get,post,delete,…}` decorators in `ddiq_report.py`: exactly
**12**. The agent (and I, repeating it) said 14 — the agent miscounted by
including the two `@router.on_event` lifecycle hooks as endpoints. Corrected.

### B4 — `SECTION_QUESTIONS` count: **37, not 39**

Counted by direct file read and `"label"` extraction:

| Section | Questions |
|---------|-----------|
| `overview` | **11** (was claimed 12) |
| `land` | **8** ✓ |
| `permits` | **8** ✓ |
| `economics` | **10** (was claimed 11) |
| **Total** | **37** |

Both my prior reports and the agent's report had 39; the actual is 37. This
matches the smoke-test PDF's section count.

### B5 — LLM calls per report: **~45, not ~49**

Consequence of B4: 37 section calls + 1 metadata + 1 WEA + 1 infra + 1
cadastral contract + 1 findings + 1 timeline + 1 cross-doc + 1 Rückbau + 1
Grundbuch = **~45 LLM calls per full report** (more with retries). The earlier
"~49" was based on the wrong section count.

The compounding-failure math still holds qualitatively (and was explicitly
marked illustrative): with 8 single-point-of-failure passes plus 37 graceful
section calls, at p=0.97 per critical pass `p^8 ≈ 0.78` → roughly **22% of
reports lose a whole chapter** to LLM-JSON fragility alone. Slightly milder than
"1 in 4" but the same order of magnitude.

### B6 — `_parse_alkis_feature` severity: **Medium, not Critical**

Re-read lines 700-715 directly. The agent's diagnosis is mechanically correct —
on parse *success* the for-loop continues (could overwrite with a later
matching key); on parse *failure* the `except: pass; break` exits the loop. But
calling it "essentially never read correctly" overstates the impact: it only
manifests when **multiple of the candidate keys are simultaneously present** in
one ALKIS feature (e.g. both `flurnummer` and `flur`). For most ALKIS records
only one is present and the function returns the right value. **Real bug,
limited blast radius.** Severity Medium, not Critical.

### B7 — "Live serve_rag not running" → was true at the time, no longer

Earlier probe found no `serve_rag` process. Now: running (PID 3413088, port
18000). Update prior reports accordingly.

---

## C — New finding worth surfacing

### Step 6 (corpus embedding) is genuinely incomplete

`Q6` in the roadmap was: is Step 6 embedding complete, or still running? Now
verified directly by reading the step-6 SQL in `cli.py:917-933`:

- `child_embeddings` is populated by copying from `child_chunks.embedding` where
  it is *not* NULL.
- Step 6 itself iterates `WHERE embedding IS NULL` in `child_chunks` and fills
  them.
- Current state: 50,000,000 child_chunks; **9.46M with embeddings; ~40.5M with
  `embedding IS NULL`.**

So **~81% of child chunks are still awaiting embedding** — Step 6 has clearly
not finished (or has been intentionally paused at this checkpoint). The
recently-modified `resume_step6.sh` is consistent with embedding being a
work-in-progress.

**Implication for Phase 2 / the keystone (Track B):** the corpus-to-pgvector
migration must decide whether to migrate the existing 9.46M now and let Step 6
keep filling forward, or to finish Step 6 first then migrate. The first option
unblocks Phase 2 sooner; the second avoids dual-write complexity. **This is a
sizing decision for Track B that needs to be made.**

---

## D — Things explicitly *not* re-verified (honest gaps)

- **The ~1,500–2,000 LOC of duplicated logic** figure is an *agent estimate*. I
  did not count line-by-line. The qualitative claim (PDF/embed/rerank/LLM helpers
  duplicated 2-4× across `serve_rag.py`, `api.py`, `ddiq_report.py`) is solid;
  the absolute number isn't independently measured.
- **The compounding-failure probability math** (`p^n`) was explicitly flagged
  illustrative in `DEEP_RESEARCH.md` and remains so; `p` is not measured.
- **DDiQ row counts** (how many reports/documents are actually in
  `lai_postgres_main` now): the Postgres is up but operational data state
  beyond "tables exist" was not probed in depth this pass.
- **The "fabricated 15.8% citations" finding** comes from the project's own
  `PROJECT_STATUS.md` and the audit script in `scripts/archive/`; we did not
  rerun the audit.

---

## E — Implications for the existing documents

| Document | Action |
|----------|--------|
| `AUDIT.md` C3 ("live system broken") | Add a "Status (2026-05-14): resolved — full LAI runtime stack now running healthy" note. |
| `AUDIT.md` H1 ("~6k LOC dead code") | Correct to "~3,200 LOC". |
| `DDIQ_ROADMAP.md` Phase 0 | "Fix the outage" is largely done. Auth + tenant isolation remain the Phase 0 items. |
| `DDIQ_ROADMAP.md` §7 Q6 (Step 6 status) | **Resolved:** Step 6 is incomplete — 9.46M of 50M embedded, ~40.5M pending. |
| `DEEP_RESEARCH.md` C.5 (LLM-call math) | Update count to 37 + 8 = 45 calls; `p^8 ≈ 22%` chapter-loss rate. |
| `DEEP_RESEARCH.md` C.4 (`_parse_alkis_feature`) | Reduce severity from Critical to Medium with the multiple-keys-present caveat. |
| `DEEP_RESEARCH.md` D.1 (~6,000 LOC dead) | Correct to ~3,200. |
| `TECH_STACK.md` §14 (gaps) | Update item #1 ("live runtime ≠ compose"): the runtime stack is now up; the divergence is resolved. |

---

## F — Net assessment

The qualitative shape of the architecture, the failure analysis, the roadmap
sequencing, and the verification of the external research are **all still
valid**. The corrections above are point-precision: smaller numbers, finer
severities, current-state updates. **No major claim was refuted; one was found
to be temporally stale (the outage is fixed).**

The roadmap's keystone (corpus → pgvector + `lai.retrieval`) is *more* useful
to start now that Postgres is healthy — the immediate blocker is gone. And the
genuinely new finding (Step 6 still has 40.5M chunks to go) is a real sizing
input for Track B that wasn't on the table before.
