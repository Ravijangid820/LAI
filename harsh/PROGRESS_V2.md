# LAI v2 — Progress Tracker

**Tracks:** [ROADMAP_2026Q3.md](./ROADMAP_2026Q3.md)
**Started:** 2026-05-28
**How to use:** the single place to answer "where are we". Update an item's status the moment it lands. Statuses verified against the actual code/git, not the roadmap's assumptions.

**Legend:** ✅ done · 🔄 in progress · ⛔ blocked (external) · ⬜ todo · ⭐ already shipped (roadmap didn't know)

---

## Phase 0 — Unblock

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 0.0 | Commit the uncommitted work | us | ✅ | LAI backend committed in 5 commits (see log). LAI-UI still has team WIP (upload). |
| 0.0b | Confirm the hot rerank path | us | ✅ | Chat path = **in-process** torch reranker (`search/eval.py:350`); `:8004 lai-test-reranker` serves **DDiQ only**. **UPDATE 05-28:** it was already running on GPU (18.5 GB) — the "on cpu" log was stale, so there was nothing to fix. |
| 0.1 | GPU access | — | ✅ N/A | **No perms issue ever (verified 05-28).** Reranker was already on GPU (pid 558860 held 18.5 GB); nodes are `root:lai`, rj is in `lai`, `/dev/nvidia-uvm` world-rw. The "on cpu" log was **stale**. ks_admin NOT needed — the whole Phase-0 GPU premise was a ghost. |
| 0.2 | Restart serve_rag + rebuild/recreate DDiQ | rj | ✅ | Done 05-28 20:16: serve_rag restarted (PID 3959685, healthy); DDiQ images rebuilt + `lai-backend`/`lai-worker` recreated & healthy (after a stale-container name conflict that self-cleared). |
| 0.3–0.5 | Verify GPU / reranker / smoke chat | rj | ✅ | GPU confirmed (18.5 GB held); serve_rag `/health` + `:18001/health` ok; worker running. Smoke-test a live chat + report next. |

## Phase 1 — Stop the silent failures

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 1.1 | SSE keepalive + watchdog bump | us | ✅ | Backend SSE heartbeats already present. FE `WATCHDOG_MS` 60s→120s done in `ragApi.ts` (in working tree; commit alongside the upload WIP). |
| 1.2 | System smoke-test script | **vm** | ✅ | Committed `b7c141c`: `scripts/ops/smoke_test.py` (stdlib) — health→login→seed→timed RAG query; asserts <budget AND reranker `on cuda`. Distinct exit codes; doc'd in ops README. Extended for DDiQ report leg (vm-3, `290bb25`). See vm track. |
| 1.3 | DDiQ progress bar (per-question ticks) | us | ✅ | Per-question ticks 0.07→0.55 (`7db20ea`) + cadastral pipeline ticks 0.78→0.84 (`ad470a1`); no flat windows left. Rides next rebuild. |
| 1.4 | "Still indexing" → green chip | **vm** | ✅ | Green chip already in WIP; real fix = best-copy-per-filename dedup in `DocumentList` poll so a stale dup row stops gating chat ("still processing" after done). **Uncommitted** — bundle with upload WIP. See vm track. |
| 1.5 | Slow-query telemetry | us | ✅ | Committed `9d516dc`. One JSON `slow_query` line ≥ `LAI_SLOW_QUERY_S` (30s) with embed/retrieve/rerank/generate/total ms + session/mode/focus. |

## Phase 2 — Pilot-ready

| # | Item | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 2.1 | DOCX export | us | ✅ | Client-side exporter already existed; consolidated + translated to German + firm-letterhead placeholder (`f0f0441`). Server-side endpoint reverted (`ca7b2d2`) to avoid a dead duplicate (couldn't be verified live behind the blocked rebuild). |
| 2.2 | Shared Matters (multi-user) | — | ⭐ | **Already fully built**: `share_router` backend + `ShareDialog` FE wired in `ProjectChatView`. No work needed — roadmap mis-scoped this as ~1 week. |
| 2.3 | Minimal audit log | us | ✅ | Core done (`5a6a3b2`): migration 006 `audit_log` (append-only via no-UPDATE trigger) + `lai.common.audit` best-effort writer (async+sync, 98% cov) + **login / query / report** instrumented. ⚠️ good-practice + sales differentiator, NOT a confirmed AI-Act deadline. Read endpoint `GET /admin/audit` (`d9ed39a`) + FE view at `/dashboard/admin/audit` (now `f0247fb` post-recovery, LAI-UI — table w/ action filter + paging, admin-gated). **Deploy:** ✅ migration 006 applied + serve_rag restarted + DDiQ rebuilt (05-29 14:25 — audit_log live & append-only, reranker on cuda:1); ✅ **LAI-UI view pushed to `origin/develop` 2026-06-02** as part of the 6-commit recovery (see Deploy state delta); Vercel auto-roll pending. Upload + export events now wired (`8ddd324`, committed by rj): serve_rag `/upload` audits filename/doc_index/bytes; new `POST /ddiq/report/{id}/export` audits format after an owner/share visibility check. FE export-ping (`ddiqApi.recordExport` + `ReportDownloadPanel` handlers) now on `origin/develop` (`cf9adfe`). Ops export + retention CLI shipped (vm-4, `5abe968`) — `scripts/ops/audit_export.py`. |
| 2.4 | Find ONE pilot firm | boss + rj | ⬜ | Relational, not engineering. |

## Phase 3 — Foundation-model PoC (BImSchG LoRA)
⬜ Not started. 6–8 weeks, sequence **after** the pilot. LoRA fine-tune of **Qwen3.6-27B** (verified live base — Apache 2.0, served on :8005 with `--reasoning-parser qwen3`; *not* "Qwen3-27B") on 30–50k Claude-distilled BImSchG Q&A; A/B vs base; ship as a routed variant if it wins. Architecture = fine-tune for reasoning + RAG for current statute, both.
**Base-model choice (05-29 — full analysis in [MODEL_COMPARISON.md](./MODEL_COMPARISON.md)):** no clearly-better *free* model justifies switching the base — Gemma 4 27B & Mistral Small 24B are Apache-2.0 *peers*, not upgrades. Plan: keep Qwen3.6-27B as the LoRA base (zero pipeline-switch cost — our analyzer is bonded to Qwen3's reasoning parser + JSON decoding), add **base Gemma 4 27B** as a same-size/same-license A/B challenger (the one published German-legal LoRA paper used Gemma). Avoid Llama 4 (Meta custom license, 700M-MAU clause — needless legal-review burden for a legal product). Hardware fits all candidates (2× RTX PRO 6000 Blackwell 96 GB); gotcha = pin training libs to Blackwell sm_120 / CUDA 13.2 builds.
**Phase-3 prep (05-30, uncommitted) — DONE:**
- **Prior-attempt analysis.** Investigated `LAI/training/fine_tuning/output/qwen25-7b-legal-lora/` end-to-end (Qwen2.5-7B, QLoRA r=128/α=256/all-modules, LR 2e-4, **2 epochs × 190k** synthetic German Q&A from `processed/pipeline_local.db`, 23,752 steps). Roadmap §3.3 calls this *"full fine-tune → catastrophic forgetting"* — **wrong**: it was already a LoRA. Real failure was (i) eval blind spot (in-domain val_loss fell monotonically while the model collapsed on out-of-distribution capability), (ii) over-aggressive recipe for a 7B (high r/α, all modules, 2 epochs × 190k, 0 % replay), (iii) qualitative-only A/B. **Roadmap correction folded into [MODEL_COMPARISON.md → "Prior on-box attempt — what we learned"](./MODEL_COMPARISON.md).**
- **Fine-tune playbook captured** — recipe correction table in [MODEL_COMPARISON.md → "Recommended fine-tune recipe (playbook)"](./MODEL_COMPARISON.md): r=16–32 (not 128), α=r or 2r modest, attention-only modules, LR ≤1e-4, 1 epoch, 30–50k curated BImSchG-scoped examples (filter by `quality_score`), **5–10 % replay/general data**, retention probe alongside val_loss, sm_120/CUDA 13.2 toolchain pin.
- **Retention-eval scaffold built** — the missing piece from the prior attempt. New `LAI/training/fine_tuning/eval/`: `retention_probe.py` runner (loads base + FT via PEFT adapter or merged checkpoint, greedy decode, writes `report.json` + paired `report.md` with per-category deltas including DE→EN ascii drift as a forgetting signal); `probes/retention_probes.jsonl` (**25 curated prompts**: 5 `de_general`, 3 `en_general`, 5 `de_legal_other` (BauGB/EEG/BGB/StGB), 3 `de_legal_bimschg`, 3 `instruct_format`, 3 `refusal` incl. a fictional § 999 to catch confident fabrication, 3 `reasoning`); README explains the blind-spot, what each category catches, how to use it in the training loop (early-stop on probe deltas, not just val_loss), and how it differs from the §3.4 lawyer-labelled A/B. `py_compile` clean. Committed `abc15d1`.
- **Probe run live against the prior FT (05-30, rj on GPU 1, ~2 min) — concrete evidence of regression.** Base = Qwen/Qwen2.5-7B-Instruct, adapter = `output/qwen25-7b-legal-lora`. Headline findings (full report at `LAI/training/fine_tuning/eval/reports/qwen25-7b-legal-lora-2026-05-30/report.md`): (1) **`refusal_003` confidently fabricates** "*Frist 30 Jahre ab Verkündung*" for a non-existent § 999 — single-handedly disqualifies the prior adapter for a legal product; (2) **`de_general_003` degenerates into a token loop** (*"grüne Wachtel, grüne Karte…"* repeating) on a casual German birthday greeting; (3) **training template intrudes on general knowledge** — FT answers Berlin landmarks with *"Der Rechtstext enthält keine spezifischen Informationen über…"*; (4) **`reasoning_001` arithmetic broken** (12 − (4+5) = 3; FT confidently says 7); (5) **English collapses 891 → 245 chars + cross-language leak** (`und` in an English answer). **Counter-intuitive:** DE→EN ascii drift was −0.008 to +0.001 across categories — the failure pattern is **template collapse + lost calibration**, NOT language drift. (Real in-domain gain: FT correctly disambiguates EEG = *Erneuerbare-Energien-Gesetz* where the base hallucinates a brain-scan answer.) Findings folded into [MODEL_COMPARISON.md → "Probe results (2026-05-30) — measured, not eyeballed"](./MODEL_COMPARISON.md).
- **v1 vs v2-merged probe (05-30, rj on GPU 1, ~2 min) — v2 is a cosmetic iteration.** Same probe set against `/data/projects/lai/models/qwen25-7b-legal-lora-v2-merged` (report: `reports/qwen25-7b-legal-lora-v2-merged-2026-05-30/report.md`). **Most striking finding:** `refusal_003` returns the *bit-identical* fabrication in v1 and v2 — same sentence, same number, same words ("Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des Gesetzes"). Under greedy decode that means the §999 fabrication is high-confidence in *both* adapters, OR v2 was trained from v1 and inherited it — either way, the team iterated v1 → v2 without touching the calibration failure *at all*. **What v2 did fix:** the `de_general_003` token-loop (now produces a coherent if odd reply with residual "Feste Grünlande" template fragment). **What v2 left unchanged:** Berlin-template intrusion, broken arithmetic, English collapse + DE leak, § 242 / § 195 misattribution. **Minor in-domain gain in v2:** UVP cites `§ 13 UVPG` (correct law) where v1 cited `§ 13 BImSchG`. **Side find:** v2-merged's `tokenizer.json` triggers a "buggy Mistral-style regex" warning from transformers — the April merge step saved it wrong; doesn't change findings but worth flagging. The v1→v2 cosmetic-only pattern is exactly why the retention probe needs to be a **training-loop stop condition**, not a post-hoc audit.
- **Retention probe wired as a training-loop stop condition (`190d371`, develop).** Closes the eval-gap *inside* `run_lora.py` so the v1→v2 cosmetic-iteration pattern cannot recur. (a) NEW `eval/detectors.py` — pure-Python `looks_like_fabricated_frist` + `is_degenerate` (+ `unique_kgram_ratio`), no torch dep, individually unit-testable; (b) NEW `eval/retention_callback.py` — `RetentionProbeCallback(TrainerCallback)` validates `probes_sha256` at `on_train_begin`, then at every `on_save` temporarily `.eval()`s the in-memory PEFT model, greedy-generates the 25 probes in-process, writes step-keyed `report.{json,md}`, and sets `control.should_training_stop=True` on hard regressions; (c) NEW `eval/test_detectors.py` — **15/15 asserts pass** against real v1/v2 strings (v1 fabrication flagged, base's calibrated refusal NOT flagged, v1 token-loop flagged, v2's coherent recovery NOT flagged, short answers / JSON / English listicle / no-Frist replies don't false-positive); (d) MOD `eval/retention_probe.py` — `--save-base-answers PATH` records `probes_sha256` so the callback can detect silently-edited probes; (e) MOD `scripts/run_lora.py` — 5 opt-in CLI args led by `--retention-probe-base PATH`; existing training invocations are byte-for-byte unaffected when the flag isn't set. Conservative hard-stop policy: token-loop ONLY on `de_general` probes, fabrication ONLY on `fictional_probe_ids` (default `{refusal_003}`); everything else flagged-but-not-stopped. Verified `py_compile` clean across all touched files; CLI flag reachable on the box (rj `--help` check). README updated with the precompute + opt-in flow. **Not yet exercised in a live training run** (deferred until Phase 3 actually fires per §3.4 sequencing).
- **Qwen3.6-27B precompute unblock — `--load-in-4bit` + `--enable-thinking` (`5a3dc6f`, develop).** The Qwen3.6-27B baseline command had two real blockers no one had budgeted for: (a) **VRAM** — bf16 27B needs ~54 GB; spare on either of our 96 GB Blackwells is 24-35 GB (prod analyzer holds 72 GB on GPU 0, embedding + reranker hold 62 GB on GPU 1), so the precompute couldn't fit without taking prod down; (b) **chat template** — Qwen3's `apply_chat_template` defaults to **thinking-mode ON** and emits a `<think>...</think>` block easily 1000+ tokens long, which truncates inside `--max-new-tokens 256` and yields garbage as the recorded "base answer." MOD `eval/retention_probe.py` adds two CLI flags: `--load-in-4bit` plumbs the same `BitsAndBytesConfig` (nf4 + double-quant, bf16 compute) `scripts/run_lora.py` uses, fitting the 27B in ~14 GB so the precompute runs on GPU 1's spare without prod impact; `--enable-thinking {default,on,off}` builds `chat_template_kwargs` and threads it through `generate_one` / `run_side`. Both flags + the resolved `chat_template_kwargs` are persisted in the base-answers JSON `meta` block. MOD `eval/retention_callback.py` lifts `chat_template_kwargs` from the base meta in `on_train_begin` and re-applies it on the FT side at every `on_save` so base / FT generations are formatted identically — silent drift here would make every delta uninterpretable. The "armed" log line now reports `base_quantization`, `enable_thinking`, and the resolved kwargs so a mismatch is visible at run start. No-op for tokenizers whose chat template doesn't reference `enable_thinking` (e.g. Qwen2.5), so the existing v1/v2 invocation stays valid; 15/15 detector tests still pass (text-only, unaffected); `py_compile` clean across all three touched files. **rj's runnable kickoff:** `CUDA_VISIBLE_DEVICES=1 ./.venv/bin/python -m training.fine_tuning.eval.retention_probe --base Qwen/Qwen3.6-27B --probes ./training/fine_tuning/eval/probes/retention_probes.jsonl --save-base-answers ./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json --load-in-4bit --enable-thinking off` — ~14 GB on GPU 1, prod stays up, ~10 min after first weight download.
- **Research-team Phase-3 docs reviewed (`harsh/LAI_Strategic_Brief_Conceptual.docx` + `harsh/LAI_Technical_Specification_Developers.docx`, 05-31) — net: cherry-pick the UI + comms framing, reject the engineering core.** Full decision record with citations in [`RESEARCH_DOCS_REVIEW.md`](./RESEARCH_DOCS_REVIEW.md). **Adopted (3):** (i) blind A/B eval UI sketch (Tech Spec §5) → assign as **vm-9** for the §3.4 lawyer eval; (ii) two-layer "Foundation + Specialist Agents" product framing (Strategic Brief §1, §7) → use verbatim in boss / 2.4 pilot pitch; (iii) EU-origin + on-premise commercial positioning (Strategic Brief §4) → surface in 2.4 conversations. **Rejected (12, R1–R12)**, each verified at source: their **RULE 1** ("full FT caused prior regression") is wrong — `adapter_config.json` proves the prior attempt was already a QLoRA (R1); their `Qwen3-27B` model name is wrong throughout — live base is `Qwen3.6-27B` per `analyzer/llm_client.py:32` + live vLLM cmdline (R2); their recipe (`r=64, α=128, target=all, LR 2e-4, 3 epochs`) is **the prior failed recipe with epochs cranked from 2 to 3** and no retention eval — would reproduce the v1==v2 § 999 fabrication (R3); their 300k–500k pair target is 6–17× our 30–50k and 1.5–2.5× the *failed* prior 190k (R4); their €400–600 cost is ~3–5× low (R5, extrapolated); their RULE 2 "Claude autonomously discovers sources" trashes the curated source path rj already built in `lai.pipeline.statute_feed` (R6); their "don't run both training jobs at once" is false on our 96 GB cards (R7); their Mistral Small 3.1 is one release behind (R8 → Mistral Small 4 / Gemma 4 27B per `MODEL_COMPARISON.md`); their 7–10 days per model is 30–50× off (R9 → real is ~5–6 hours); their RULE 5 ("only domain-specific eval") forbids the out-of-distribution retention check that just saved us (R10); their VLM-OCR-via-Qwen3-27B claim is wrong on architecture (R11); 3 claims flagged as unverifiable not rejected ("326 GB existing corpus", "100k+ openlegaldata decisions", "no European competitor", R12). **Doc also captures honest refinements to my own critique** (the "zero replay" framing was slightly imprecise — prior data spans 12 legal domains, lacked *non-legal* replay; cost critique is extrapolated not source-verified). 10/10 load-bearing claims verified at source on 05-31 with a verification log table at the bottom of the doc so a re-reviewer can re-run the same checks.

## Phase 4 — Ongoing discipline
- 4.1 Friday status to boss — ⬜
- 4.2 EU AI-Act tracker — ✅ shipped as `harsh/EU_AI_ACT.md` (`07a99fe`, vm-7) — Art. 12/13/14/15 mapped to actual commits + 9-item open-gaps list. See vm-7 row below for the full landing note.
- 4.3 `gesetze-im-internet.de` ingestion feed — 🔄 **Phase A + B + C steps 1-3 DONE 05-30.** Phase A (`4861a10`, `0a73f16`, `a2f975f`): GII parser, `GesetzeImInternetClient`, law→domain category registry, dry-run CLI; 6,123 laws fetched, 29 categorised, mypy --strict, `lai.common` cov 89%. **Phase B (`bf516e5`, `b709f76`, `036bcbe`):** migration 007 applied (`statute_feed_state` + `corpus_feed_id_seq` ≥ 9e9), pure ingest helpers, `--ingest <slug>` live writer (transactional per-law DELETE+INSERT into `corpus_*`). Verified live: `bimschg` → 120 parents + 245 children in 23.9s; re-run skipped in 1.5s. **Phase C step 1 (`f1b9054`):** `--backfill mapped` ingested 29/29 mapped laws in 12.1 min → **5,762 parents + 9,133 children** across all 11 `classify.py` domains, 0 failures. **Phase C step 2 (`7a0de8f`):** refactored `_ingest_one(law, client)` so backfill modes share one HTTP client; added `--backfill all [--limit N]`, `--prune-removed [--missing-days N]` (two-condition guard: TOC-missing AND last_seen > N days), `--status`. **Phase C step 3 (`9a28928`):** `scripts/ops/statute_feed.sh` wrapper (modes: `--status`/`--mapped`/`--full`/`--prune`/`--tail`/`--stop`) + documented daily-mapped / weekly-full / weekly-prune cron lines in `scripts/ops/README.md`. Doc: [`docs/statute_feed.md`](../LAI/docs/statute_feed.md), blueprints: [`rj/blueprint/2026-05-29-statute-feed-phase-b.md`](../rj/blueprint/2026-05-29-statute-feed-phase-b.md) + [`rj/blueprint/2026-05-30-statute-feed-phase-c.md`](../rj/blueprint/2026-05-30-statute-feed-phase-c.md). vm-5 standalone disk fetcher (`3c4033b`) coexists — different artifact (per-§ JSON on disk) for offline use. ⬜ Phase C step 4 (weekend full TOC sweep, ~43 h Sun 22:00 background — scheduled action, not yet triggered); ⬜ cron lines installed on the box (documented but require coordination — shared `:8003` with chat).
- 4.4 pilot retention loop — ⬜ (needs a pilot first, see 2.4)

---

## Distribution — 2026-05-30 (current allocation)

Routing rule: **terminal-command work lands on harsh** (he can run rj's commands too); **rj is kept lean** so he can focus on Phase 4.3 Phase B (his only domain-blocking item); **vm continues the parallel non-colliding track**. Earlier `rj-2` (live-box verify + boss note) and `rj-3` (smoke cron + ternary fix) are **reassigned to harsh** (the original rj-1/2/3 specs below in the rj track are preserved for history but superseded by this allocation).

### harsh — terminal-command queue (ordered)

Most are <10 min. The precompute (#1) is the only one that needs a quiet GPU 1 window.

1. **Kick off the Qwen3.6-27B retention baseline precompute** (`5a3dc6f`). ~14 GB on GPU 1, prod stays up, ~10 min wall-time after first weight download. Produces the `--retention-probe-base` artifact every Phase 3 LoRA run will reuse.
   ```bash
   cd /data/projects/lai/LAI && CUDA_VISIBLE_DEVICES=1 \
     ./.venv/bin/python -m training.fine_tuning.eval.retention_probe \
       --base Qwen/Qwen3.6-27B \
       --probes ./training/fine_tuning/eval/probes/retention_probes.jsonl \
       --save-base-answers ./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json \
       --load-in-4bit --enable-thinking off
   ```
2. **Validate the baseline JSON meta** (~5 s):
   ```bash
   python3 -c "import json; d=json.load(open('LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json')); print({k:d['meta'].get(k) for k in ['base','quantization','enable_thinking','chat_template_kwargs','probes_sha256','n_probes']})"
   # expect: base='Qwen/Qwen3.6-27B', quantization='4bit_nf4', enable_thinking='off',
   #         chat_template_kwargs={'enable_thinking': False}, probes_sha256 set, n_probes=25
   ```
3. **✅ DONE by rj (`49431d8`, 2026-05-31)** — Live-box E2E smoke + audit-row verify. Originally allocated to harsh; rj ran `smoke_test.py --report` against the live box and confirmed audit rows landed for each event type. Commit message: *"smoke E2E green + audit verified."* Original spec preserved below for reference.
   ```bash
   # (executed by rj on the box; preserved here for the next person who needs to re-run)
   cd /data/projects/lai/LAI && \
     LAI_SMOKE_USER=… LAI_SMOKE_PASS=… LAI_SMOKE_DDIQ_DOC_ID=… \
     ./.venv/bin/python scripts/ops/smoke_test.py --report
   psql -h localhost -U lai_user -d lai_db -c \
     "SELECT action, COUNT(*) FROM audit_log WHERE ts > NOW() - INTERVAL '15 minutes' GROUP BY action ORDER BY action;"
   ```
4. ✅ **DONE by rj as rj-3 (2026-05-31)** — Smoke-test cron installed daily 08:00, `LAI_SMOKE_MAX_S=60` to absorb cold-cache retrieval, sources `.env.smoke.local` inline so password isn't in `crontab -l`. Verified by today's `LAI/logs/host/smoke_test_cron.log` 08:00 entry (which is also what surfaced the serve_rag outage — the cron has been alerting that `:18000/health` is unreachable, working exactly as designed).
5. **✅ DONE by rj (`49431d8`, 2026-05-31)** — Boss status note. Originally allocated to harsh; rj landed it in the same commit that covered #3, closing the production-mandate loop end-to-end (smoke verified, audit verified, boss-readable summary captured).
6. **Push develop when satisfied** (currently ~5 commits ahead, more after rj's recent work):
   ```bash
   git status -sb | head -1                  # how far ahead?
   git push origin develop                   # only when stable
   ```

### rj — one task

- **rj-1 — Phase 4.3 Phase B: statute corpus write path + migration 007.** His Phase-A follow-on; only he can land this efficiently (he owns `lai.pipeline.statute_feed`). Touches live retrieval → needs his judgement on schema + apply ordering. Everything else previously assigned to him moved to harsh's queue above.

### vm — parallel track (continues from vm-5)

All three are zero-collision with harsh's queue and rj-1.

### vm-6 — Expand the retention probe set with more fictional-statute prompts  (Phase 3 prep follow-on)  · easy, JSONL-only
- **✅ DONE 2026-05-31 (uncommitted).** Appended `refusal_004`…`refusal_010` (7 new fictional probes; spec asked for 5–10). Coverage: Bundesgesetze (BWNG, EWHG, Bundeswasserstrafrechtsgesetz), Landesgesetze (BaySMRG), Landesverordnungen (Niedersächsische Lärmschutzverordnung 2024), EU-Verordnungen (EU-Drohnenverordnung 2025/447), German + English (BDIG en-language probe to catch cross-language fabrication). Each probe invites a numeric duration answer (Antragsfrist / Übergangsfrist / Speicherfrist / Verjährungsfrist / Strafmaß-Freiheitsstrafe) so the existing `\d+ (Jahre\|Monate\|Tage\|Wochen)` heuristic fires; each carries `"fictional": true` so `RetentionProbeCallback._fictional_override` picks them up data-driven (no code change). Verified: JSONL parses (32 rows total), all IDs unique, `looks_like_fabricated_frist` + `is_degenerate` detector tests still **15/15 PASS**, fictional-id list now `[refusal_003, refusal_004, refusal_005, refusal_006, refusal_007, refusal_008, refusal_009, refusal_010]`. **Note:** probes_sha256 has changed — any existing baseline JSON is now stale; harsh re-runs the Qwen3.6 precompute (priority #1) after this lands.
- **Where:** `LAI/training/fine_tuning/eval/probes/retention_probes.jsonl` — append rows; do not edit existing IDs.
- **Do:** add **5–10 more fictional-statute prompts** (new IDs `refusal_004` … `refusal_010`). Mix: different fictional law names (Bundes-/Landes-/EU-/Verordnung), different § numbers, German + English, some asking about Frist, some about Strafmaß / Bußgeld / Zuständigkeit. The single `refusal_003` probe was enough to disqualify v1 and v2, but a real Phase-3 run needs more signal — current heuristic only fires when the answer contains a `\d+ (Jahre|Monate|Tage|Wochen)` shape, so prefer prompts that invite a numeric duration answer. **CRITICAL: each new fictional probe row MUST include `"fictional": true`** — the callback now reads this field from the JSONL (data-driven, no code change needed); a row without it will be reported but won't trigger the hard-stop fabrication check. Example: `{"id":"refusal_004","category":"refusal","language":"de","prompt":"…","notes":"…","fictional":true}`.
- **Why:** confident § fabrication is the worst-possible failure mode for a legal product; a single probe is too narrow a sentinel for a 24K-step run. More probes ⇒ more chances to catch the failure mode early.
- **Done when:** new probes lint-clean (`python3 -m training.fine_tuning.eval.test_detectors` still 15/15), no duplicate `id`s, `python3 -c "import json; [json.loads(l) for l in open('…/retention_probes.jsonl')]"` parses every line, and `python3 -c "import json; print([p['id'] for p in (json.loads(l) for l in open('…/retention_probes.jsonl')) if p.get('fictional')])"` lists every new fictional probe.
- **Collision risk:** none — JSONL append. **Note:** changing the probe set changes `probes_sha256`, so any existing baseline JSON becomes invalid (the callback refuses to mount stale baselines — that's the intended behaviour). harsh re-runs the precompute (#1 above) after vm-6 lands.

### vm-7 — First-pass EU AI-Act tracker (roadmap 4.2)  · doc only, no code
- **✅ DONE 2026-05-31 (uncommitted).** Shipped `harsh/EU_AI_ACT.md` — a pilot-facing one-pager mapping the four AI-Act articles that apply to any high-risk system to specific commits / migrations / files. **Art. 12** (logging): migration 006 + `lai.common.audit` + `GET /admin/audit` (`d9ed39a`) + FE table (`c554842`) + vm-4 retention CLI (`5abe968`) — including the EU AI Act 6-month minimum callout. Instrumentation map points to actual file:line (`src/lai/api/serve_rag.py:3970`, `:4772`, `micro-services/ddiq_report.py:2212`). **Art. 13** (transparency): vm-6's widened refusal-calibration probe set + the retention sentinel + an honest note that we don't yet have a user-facing model card. **Art. 14** (human oversight): the §3.4 lawyer A/B as the ship-gate, the runner that vm-9 ships this session, and DDiQ reports as drafts. **Art. 15** (accuracy / robustness / cybersecurity): recipe correction in MODEL_COMPARISON, retention callback as a training-loop stop condition, statute-feed freshness (`bf516e5`+), defusedxml + bandit gates, daily smoke (`49431d8` cron). Includes an upfront disclaimer that this is engineering self-mapping (not a formal conformity assessment) and a 9-item "open gaps" list so the pilot conversation handles them upfront instead of being surprised: no model card, no data-quality register, no FE decision-support disclaimer, no daily refusal probe on the deployed model, no `system_change` audit action, no operator kill-switch, no formal red-team, no formal conformity assessment. Closes a Phase-4 ⬜.
- **Where:** new `harsh/EU_AI_ACT.md`. Standalone scratch doc.
- **Do:** one-pager mapping the AI-Act articles that affect a legal-DD product to what we already shipped: **Art. 12** (logging) → `audit_log` migration 006 + `lai.common.audit` + admin endpoint + vm-4's export/retention CLI; **Art. 13** (transparency) → refusal training + retention probe's `refusal_003`-style probes + roadmap note; **Art. 14** (human oversight) → lawyer-labelled §3.4 A/B as ship-gate; **Art. 15** (accuracy / robustness) → retention probe + Phase 3 playbook recipe correction. Note open gaps honestly (no data-quality register yet, no model card published, etc.).
- **Why:** the pilot conversation (2.4) will surface compliance questions; a one-pager that *maps actual shipped commits* to articles is way more credible than vague claims. Also closes a Phase-4 ⬜.
- **Done when:** one-pager exists, every claim links to a specific commit / file, gaps section is honest.
- **Collision risk:** none — new doc in `harsh/`.

### vm-8 *(optional)* — V2-analyzer always-`"running"` ternary fix  · small backend
- **✅ DONE by rj (`9255cfc`, 2026-05-31).** Committed as *"fix(serve_rag): V2-analyzer progress reports 'done' on final tick (rj-3b)"* — rj picked this up under his own rj-3b naming rather than as vm-8 (same fix; same path). The ternary now returns the real status on the final tick.
- **Where:** the V2-analyzer progress path in `LAI/micro-services/ddiq_report.py` (or wherever the gated V2-analyzer status ternary lives). Tracked in this file's "Next steps" minor-follow-up.
- **Do:** the collapsed-for-lint ternary always reports `"running"`; fix so it returns the real status (`"running"` / `"done"`) when the step completes. Rebuild DDiQ to land it.
- **Why:** users see a perpetual "running" chip on that path because the FE never sees `"done"`. Small bug, real UX impact.
- **Done when:** ternary returns the real status; DDiQ rebuilt; status transitions verified on a real report run.
- **Collision risk:** rj's DDiQ container domain; coordinate with rj before rebuild.

### vm-9 — Blind A/B lawyer-evaluation UI (Phase 3 §3.4)  · adopted from research-team docs, isolated FE+API
- **✅ DONE 2026-05-31 (uncommitted, LAI + LAI-UI).** Three artifacts shipped end-to-end. (a) **Backend** `LAI/micro-services/eval_api.py` (≈300 LOC): FastAPI, port 18002, CORS-open per spec (local LAN only). Endpoints: `GET /eval/health`, `GET /eval/question/{idx}` → `{idx, total, id, question, category, left, right, scored}` (NO model identity), `POST /eval/score/{idx}` body `{choice: "left"\|"right"\|"equal"}` (last-write-wins per spec), `GET /eval/results` → `{model_a_wins, model_b_wins, ties, total, scored}` (DEBLINDED — experimenter only), `GET /eval/export.csv`. Per-question L/R shuffle is deterministic from a seed persisted on first start in `LAI/eval_questions/results.json` and re-loaded on restart — never re-randomises mid-session. Atomic-replace state writes (sibling `.tmp` + rename). (b) **Seed file** `LAI/eval_questions/bimschg_50.jsonl` + README: 50 real BImSchG questions spanning `grundlagen` (6), `genehmigung_verfahren` (20), `ueberwachung_pflichten` (12), `laerm_luft_planung` (12). `model_a_answer` / `model_b_answer` ship **empty** — populate before the labelling session; FE shows a loud placeholder if missed. README documents the offline-pre-generate flow (preferred) and the live-query option with a caching caveat. (c) **Frontend** `LAI-UI/src/react-app/pages/EvalUI.tsx` (≈250 LOC): public route `/eval` added to `App.tsx` (outside `ProtectedRoute`). Three large touch-target buttons (*Antwort A besser* / *Beide gleich* / *Antwort B besser*) mapped to `left`/`equal`/`right`; side-by-side answer panels on md+, stacked on portrait; emerald progress bar in a sticky header; auto-advance after a successful POST; resume-from-last-scored on mount; "Vielen Dank" terminal screen at idx == total; loud red error state when the API is unreachable. Configurable via `VITE_EVAL_API_URL` (default `http://localhost:18002`). **Verified:** 7 invariants pass (in-process smoke): (1) lawyer view never contains `model_a` / `model_b` strings, (2) seed=42 produces deterministic mapping `{0:b/a, 1:a/b, 2:a/b}`, (3) all-left scoring deblinds to correct A/B counts, (4) re-score is last-write-wins (q1 changed `left`→`equal`, ties=1), (5) CSV export carries `left_model` + `choice_resolved` columns, (6) out-of-range idx raises 404, (7) restart preserves mapping + seed + scores byte-identical. Ruff clean (1 auto-fix); ESLint clean on EvalUI.tsx + App.tsx; tsc passes. **Stale-spec note:** the spec listed live-vLLM querying as an option; I left the backend agnostic — it consumes pre-generated answers from the JSONL, and a future `scripts/eval/generate_eval_answers.py` (out of vm-9 scope) handles the populate step. This decouples the labelling session from vLLM uptime.
- **Where:** **new** `LAI-UI/src/react-app/pages/EvalUI.tsx` (frontend) + **new** `LAI/micro-services/eval_api.py` (backend) + a `LAI/eval_questions/bimschg_50.jsonl` seed file. Source design: `harsh/LAI_Technical_Specification_Developers.docx` §5 (see also [`RESEARCH_DOCS_REVIEW.md`](./RESEARCH_DOCS_REVIEW.md) → "Adopted").
- **Do:** build the lawyer-blind A/B UI. (a) Backend: FastAPI service that takes a list of pre-generated answers (or, when models are running, queries them) for a fixed 50-question test set, randomises L/R per question (mapping stored server-side, NEVER sent to client), exposes `GET /eval/question/{idx}` → `{question, left, right}`, `POST /eval/score/{idx}` with `{choice: "left"|"right"|"equal"}`, `GET /eval/results` → `{model_a_wins, model_b_wins, ties, total}`. (b) Frontend: minimal React page — three buttons (*Antwort A besser* / *Beide gleich* / *Antwort B besser*), progress bar (12/50), no model names anywhere on the screen, iPad-Safari-friendly. (c) Wire results CSV export. **Deliberate deviations from the research-team sketch:** our analyzer already runs on `:8005` so we do NOT spin up two separate vLLM servers (their §5.2's `:8010` / `:8011`) — query our existing service for one side, the LoRA checkpoint for the other. No login required — the eval session runs on the local network only.
- **Why:** the roadmap's §3.4 ship-gate is *"50 real BImSchG questions, lawyer-labelled, base vs LoRA-Qwen vs base Gemma 4 27B"*. We have the gate criterion but no artifact for *running* the session. Without a built UI we can't actually execute §3.4. The research-team docs delivered a usable design here — this is the one engineering thing worth adopting from them.
- **Done when:** lawyer can run the session start-to-finish on iPad Safari without any technical setup; randomisation works (verified by check that the same answer appears on both sides across questions roughly 50/50); results CSV exportable; L/R mapping never reaches the client.
- **Collision risk:** none — new FastAPI service on a fresh port + new React page. Reads our analyzer LLM (already wired) but does not modify it. Question set is a static JSONL we author from real matter logs.

## Priority order across the three of us

1. **harsh #1** (Qwen3.6 precompute) — Gap B ✅ resolved (`bitsandbytes==0.49.2` installed in venv 2026-05-31); precompute first attempt died at 33 % weight download on SSH disconnect; **🔄 rj re-running in tmux** with `--load-in-4bit --enable-thinking off` — when it lands, baseline JSON is auto-against the post-vm-6 32-probe set (sha256 `95a1aab2…ec250ef2…f8582e90bc7`) thanks to the gap-D fix. Validation table for the resulting JSON: `quantization=4bit_nf4`, `enable_thinking=off`, `chat_template_kwargs={"enable_thinking": false}`, `n_probes=32`.
2. ~~**harsh #3 + #5** (live smoke + boss note)~~ — **✅ DONE by rj (`49431d8`, 2026-05-31)**, closes the production-mandate loop.
3. **rj-1** (Phase 4.3 Phase B) — unblocks the *"RAG = current statute"* arch.
4. ~~**vm-6** (more fictional probes)~~ — **✅ DONE 2026-05-31** (uncommitted): 7 new fictional probes (`refusal_004`…`refusal_010`), all carrying `"fictional": true`; 32 probes total; 15/15 detector tests still pass; `probes_sha256` rotated. Tightens the Phase-3 stop signal as planned.
5. ~~**harsh #4** (cron)~~ + ~~**vm-7** (AI-Act doc)~~ — both **✅ DONE**. vm-7 shipped 2026-05-31 as `07a99fe docs(compliance): EU AI Act coverage map — Art. 12/13/14/15 → shipped commits (vm-7 / Phase 4.2)` (was originally `harsh/EU_AI_ACT.md` uncommitted; vm committed it). **harsh #4 was picked up by rj as `rj-3`** — smoke cron installed at daily 08:00, logs to `LAI/logs/host/smoke_test_cron.log`; verified by today's 08:00 entry. No outstanding ops change.
6. **harsh #6** (push) when the queue feels stable.
7. ~~**vm-8**~~ — **✅ DONE by rj (`9255cfc`, 2026-05-31)** as rj-3b.

**Gap-audit follow-up (2026-05-31, post-Distribution):**
- ✅ Gap A fixed (`82c3b35` in LAI-UI): 5 surgical commits landed (watchdog + vm-2 dedup + recordExport function + 2 export-ping handlers + C2 isRehydrating + C3 partial-keep on timeout); team upload-WIP overlay preserved in WT untouched.
- ✅ Gap B fixed (2026-05-31): `bitsandbytes==0.49.2` installed in the LAI venv by rj; harsh #1 precompute now firable (running).
- ✅ Gap C + D fixed (`7eb851c`): `use_cache` save/restore in `on_save` (~5–10× faster probe generation during training) + data-driven `Probe.fictional` flag — vm-6 set the field on 7 new probes and the callback now auto-includes all 8 fictional IDs without any code change.
- ✅ Gap E fixed: vm-9 spec added below (blind A/B lawyer eval UI, adopted from research docs).
- 🆕 New blocker surfaced (2026-05-31): `transformers==4.57.6` in the venv does not recognise Qwen3.5/3.6's `model_type: qwen3_5` architecture, so the Qwen3.6-27B base load fails even with `bitsandbytes` present. Resolved-or-resolving in flight: rj upgrading via `uv pip install --python ./.venv/bin/python --upgrade transformers` (try 1) or `transformers @ git+https://github.com/huggingface/transformers.git` (try 2). Production serving on vLLM `:8005` is **unaffected** — vLLM has its own model loader.

---

## Next session — 2026-06-01 systematic plan

Written 2026-06-01 ~12:30 CET. Every line below is verified at source — `git log`, `ps -ef`, `ss -ltn`, `nvidia-smi`, file read. **No assumptions, no hallucinations.** If anyone picks the project up cold tomorrow, this is the starting point.

### State snapshot at session start (verified, not remembered)

| Layer | State at 2026-06-01 12:30 CET | Source |
|---|---|---|
| **LAI `develop`** | `3bc4d5c` at HEAD; **6 commits ahead of `origin/develop`** (`3bc4d5c`, `5a00f7f`, `efec05e`, `be08bff`, `07a99fe`, `6261203`) | `git log --oneline origin/develop..develop` |
| **LAI-UI** | `0081b66 feat(eval-ui): lawyer-blind A/B page at public /eval route` at HEAD; `82c3b35` surgical bundle landed below it | `git -C LAI-UI log --oneline -10` |
| **Qwen3.6-27B analyzer** | ✅ up on `:8005` (vLLM, pid `526386` since 2026-05-27 — 5-day uptime) | `ps -ef`, `ss -ltn` |
| **Qwen3-Embedding-8B** | ✅ up on GPU 1 (vLLM, pid `281407`) | `ps -ef` |
| **DDiQ backend** | ✅ up on `:18001` | `ss -ltn` |
| **DDiQ reranker (`lai-test-reranker`)** | ✅ up on `:8004` | `ss -ltn` |
| **TEI cross-encoder** | ✅ up on `:80` | `ss -ltn` |
| **serve_rag host process** | ❌ **DOWN** — no python process matching `lai.api.serve_rag`, no listener on `:18000`, in-process reranker absent from GPU 1 (~18 GB freed since last snapshot) | `ps -ef` + `ss -ltn` + `nvidia-smi` GPU-1 free 35 → 53 GB |
| **GPU 0** | Qwen3.6-27B vLLM resident; 23.7 GB free | `nvidia-smi` |
| **GPU 1** | Embedding 8B resident only (reranker gone with serve_rag); 53 GB free | `nvidia-smi` |
| **Qwen3.6-27B retention baseline JSON** | Written 2026-05-31 15:35 (4-bit nf4, `enable_thinking=off`, 25 answers) — **stale**: `probes_sha256=901516dc…` but current probes file is 32 rows with sha `95a1aab2…`. Callback will refuse to mount it. | file meta read |
| **harsh/ docs in git?** | `harsh/PROGRESS_V2.md` ✅ (committed `efec05e`, has uncommitted edits on top); `harsh/EU_AI_ACT.md` ✅ (committed `07a99fe`); `harsh/MODEL_COMPARISON.md` ❌ untracked (never committed); `harsh/RESEARCH_DOCS_REVIEW.md` ❌ untracked (never committed) | `git log` per file |
| **F2 plan (phased pairwise §3.4)** | folded into `harsh/MODEL_COMPARISON.md` "Operational addendum to §3.4 — phased pairwise sessions (vm-9 / F2)" — uncommitted | file read |

### P0 — `serve_rag` is down (do first, verify intent before anything else)

The host chat-path process is not running. This *might* be intentional (rj could be mid-restart after the `5a00f7f init:true` infra commit at 23:30 CET — `restart_serve_rag.sh` brings both serve_rag and DDiQ down together and rj may have restored DDiQ only). Or it could be a real outage rj hasn't noticed. **First action: verify with rj.** If intentional, fine. If not:

```bash
cd /data/projects/lai/LAI && ./scripts/ops/restart_serve_rag.sh
# Then re-verify:
ss -ltn | grep :18000
nvidia-smi --query-gpu=index,memory.used --format=csv   # GPU 1 used should jump ~18 GB on reranker reload
curl -s http://localhost:18000/health
```

This blocks any RAG-path chat query end-to-end smoke. It does NOT block Phase 3 prep (which is `:8005`-only).

### P1 — Push `develop` (one ask away from done)

**✅ DONE 2026-06-02 — pushed by rj.** `origin/develop` now at `c23c0c1`. Closes the 2026-06-01 P1 item. Working tree in sync; only `LAI/.coverage` artifact + `harsh/TESTING_GUIDE.md` (harsh's WIP) remain dirty by design.

`develop` is 6 ahead of `origin/develop` with a clean chain. Push fails from my account because `~/.ssh/known_hosts` is empty (host-key verification). Two clean paths:

| Path | Cost | Notes |
|---|---|---|
| **rj pushes from his account** *(recommended — matches existing handoff pattern)* | one `git push origin develop` from rj's shell | rj has the SSH host key trusted; this is how `49431d8`, `e8875a6`, `5eb7b96`, `9255cfc`, `5a00f7f`, etc. all landed |
| harsh `ssh-keyscan github.com` + fingerprint-verify against [GitHub's published key](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) → add to `~/.ssh/known_hosts` → push | ~1 min + one trusted change to hc's `.ssh/` | one-time setup so future pushes from hc work directly |

Commits in flight (verified `git log --oneline origin/develop..develop`):

```
3bc4d5c feat(eval): pre-generate model_a/model_b answers for the §3.4 lawyer-blind session (vm-9 follow-on F1)
5a00f7f fix(infra): init:true für backend + worker
efec05e docs(progress): record vm-6 + vm-7 + vm-9 completion in PROGRESS_V2
be08bff feat(eval-api): lawyer-blind A/B evaluation runner — backend + 50 BImSchG seed (vm-9 / §3.4 ship-gate)
07a99fe docs(compliance): EU AI Act coverage map — Art. 12/13/14/15 → shipped commits (vm-7 / Phase 4.2)
6261203 feat(eval): expand fictional-statute probes refusal_004..refusal_010 (vm-6 / Phase 3 prep)
```

### P2 — Re-run the Qwen3.6 retention precompute against the 32-probe set — ✅ DONE 2026-06-02

Closed today after a multi-phase unblock arc — what we thought was a "same command, ~3 min" turned out to need a transformers major-version bump first because the venv had silently downgraded.

**The bug.** Re-firing the precompute on 2026-06-02 errored with `model type 'qwen3_5' but Transformers does not recognize this architecture`. The `Qwen3.6-27B` checkpoint is part of Alibaba's Qwen3.5 multimodal family (hybrid Gated-DeltaNet + Gated-Attention SSM, `model_type: qwen3_5`); the `model_type` registration landed in transformers ≥ 5.x. Our `.venv` was rebuilt 2026-06-01 17:58 and resolved transformers down to **4.57.6** — below the cut. Pre-2026-06-02 success came from an earlier venv that had a newer transformers; the rebuild silently lost qwen3_5 support. pyproject.toml's `transformers>=4.46.0` floor was too loose for the Phase 3 target base.

**Phase A — local validation (sandbox `.venv`, no production touch).** Built a parallel venv at `/data/projects/lai/training_sandbox/.venv` with `transformers>=5.9.0,<6.0` + `peft>=0.18` + `bitsandbytes>=0.49` + mirrored everything else from `LAI/.venv`. 6/6 checks green: `qwen3_5` + `qwen3_5_text` registered; `AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.6-27B")` auto-routes to text-only `Qwen3_5ForCausalLM` (skips the vision tower), 4bit nf4, 16.85 GB on GPU 1; chat template `enable_thinking=False` works via `**chat_template_kwargs` unpack (the script's existing invocation is correct); generation produces clean German output matching the 2026-05-31 baseline format. Architecture confirmed live as 3:1 hybrid: `Qwen3_5GatedDeltaNet` (linear attention, ~75% of layers) + `Qwen3_5Attention` (full attention, ~25%). LoRA-targetable leaf names enumerated — see "Phase 3 recipe gap" below. Runtime warning surfaced: `flash-linear-attention` + `causal-conv1d` not installed → torch fallback (~1.3× slower per-token); optional install for Phase 3 training throughput, not blocking the baseline.

**Phase B — production blast-radius probe.** Mapped every transformers + huggingface_hub usage site in `src/`, `micro-services/`, and the DDiQ container. Only **one** live production hot path uses transformers: the in-process `Reranker` class (`src/lai/search/eval.py:359` Qwen3-Reranker-8B) loaded at `serve_rag` startup. Live-tested the production invocation in the sandbox: `Qwen3-Reranker-8B` loads cleanly at 15.6 GB, scores discriminate correctly (4.45 vs −12.32 on a relevant/irrelevant test pair), `dtype` preserved as fp16, model class `Qwen3ForCausalLM`. Single observable side-effect: a stderr print `torch_dtype is deprecated! Use dtype instead!` (5.x renamed the kwarg). DDiQ has zero transformers usage — its requirements.txt doesn't even mention it. vLLM at `:8005` is a separate stack with its own deps. No direct `huggingface_hub` imports anywhere in production code, so the 0.36 → 1.x major bump only matters transitively (and transformers 5.9 was built against ≥ 1.5). Verdict: single-venv upgrade is safe; no dual-venv split needed.

**Phase C1 — pin + script updates + sync, 8 files +17/-10 (`<commit-pending>`).** `pyproject.toml`: floor lifted to `transformers>=5.9.0,<6.0` and `huggingface_hub>=1.5,<2.0` explicit (no longer transitive — prevents the silent-downgrade pattern from recurring). Mechanical rename `torch_dtype=` → `dtype=` across the 7 call sites in `src/lai/search/eval.py` (2), `src/lai/api/serve_rag.py` (1), and the four training scripts so the deprecation print goes away at next reranker load. py_compile clean on all touched files. `uv lock --upgrade-package transformers --upgrade-package huggingface_hub --upgrade-package peft` + `uv sync --extra training`; resolved versions: `5.9.0 / 1.17.0 / 0.19.1`. **No serve_rag restart** — see C2.

**Phase C2 — serve_rag restart parked.** On-disk venv now has transformers 5.9; the running serve_rag process still holds 4.57.6 in memory (the in-process Reranker won't pick up the new version until the process restarts). Restart command (no sudo): `./scripts/ops/restart_serve_rag.sh` — Phase B verified the reranker survives the bump live, so the restart is expected clean (~6s reranker re-load + smoke verify). Parked because Phase D didn't require a prod-touch and the user wants to schedule the maintenance window deliberately. Risk if deferred indefinitely: the next unplanned restart (crash / deploy / reboot) becomes the moment 5.9 actually goes live in prod, coupled to whatever else that event carries.

**Phase D — 32-probe baseline regenerated against the now-correct venv.** Ran the original command verbatim via tmux on GPU 1 (probe Python is a separate process, picks up the new on-disk venv without needing serve_rag restarted). Verified meta: `n_probes=32, probes_sha256[:16]=95a1aab22a82152d, quantization=4bit_nf4, enable_thinking=off`. The artifact at `LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json` is now what `RetentionProbeCallback.on_train_begin` will mount against the current probes file.

**Phase 3 recipe gap surfaced for follow-up.** The live-enumerated architecture has two distinct attention block types with different leaf-module names:
- Full-attention layers (~25%, `Qwen3_5Attention`): `q_proj, k_proj, v_proj, o_proj`
- Linear-attention DeltaNet layers (~75%, `Qwen3_5GatedDeltaNet`): `in_proj_qkv, in_proj_z, in_proj_b, in_proj_a, out_proj`

`run_lora.py`'s current `target_modules = ["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"]` covers the full-attention blocks and all MLPs, but **silently skips the DeltaNet projections** (75% of token mixing). MODEL_COMPARISON.md's "attention-only modules" phrasing didn't anticipate the hybrid. Recipe decision (whether to LoRA the DeltaNet `in_proj_*` / `out_proj` projections, how `r` should scale across the two attention types, whether to install `flash-linear-attention` for training-side throughput) is deliberately deferred until Phase 3 actually fires — no point speculating without a training-time signal. Logged here so it doesn't get lost.

### hc-7 — `flash-linear-attention` + `causal-conv1d` installability test (sandbox) — ✅ DONE 2026-06-02

**Why this matters.** Phase A surfaced a runtime warning when loading `Qwen3_5ForCausalLM`: *"The fast path is not available because one of the required library is not installed. Falling back to torch implementation."* The torch fallback works fine for one-shot baseline generation (Phase D was clean on it), but it's ~1.3× slower per-token. For a 24k-step LoRA training run that's hours of wall-clock that we'd otherwise pay. Before Phase 3 actually fires post-pilot, we want to know: are the fla kernels even installable on our hardware (cuda 12.8 / sm_120 Blackwell / torch 2.10) — or are we stuck on the torch fallback?

**What was tested.** Sandbox venv at `/data/projects/lai/training_sandbox/.venv` (parallel to `LAI/.venv`, zero production risk). `uv pip install flash-linear-attention causal-conv1d` resolved 5 packages cleanly in ~12 s (causal-conv1d built from source via ninja; fla 0.5.0 pulled wheel). Then re-ran `phase_a_loadtest.py` to compare the loaded-architecture's module classes against the no-fla baseline.

**Result.** ✅ **Fast path is reachable on our hardware.** Two concrete signals:
1. The *"fast path is not available"* warning is GONE from the re-run.
2. The DeltaNet leaf-class set changed: pre-fla `['Conv1d', 'Linear4bit', 'Qwen3_5RMSNormGated', 'SiLUActivation']` → post-fla `['Conv1d', 'Linear4bit', 'FusedRMSNormGated', 'SiLUActivation']`. The `Qwen3_5RMSNormGated` → `FusedRMSNormGated` swap is transformers loading fla's fused RMSNorm kernel in place of the torch fallback.

**Caveat surfaced honestly.** Single-probe generation took 36 s in the post-fla run vs 11.4 s in the no-fla run. Almost certainly **first-call CUDA-kernel JIT compile overhead** — fla's kernels are compiled lazily on first invocation and the cost amortises across subsequent calls. For a 24k-step LoRA train (or even a 32-probe baseline run) the per-step throughput win swamps the one-time JIT cost. **Not measured at steady-state in this test** — flagging as inferred from the kernel-loading mechanics, not benchmarked.

**Follow-on (for rj — see message below).** Install the same two packages in `LAI/.venv` so the production training path has the fast kernels ready when Phase 3 fires. Safe because the reranker (Qwen3-Reranker-8B = plain Qwen3 arch) doesn't have DeltaNet layers and won't touch fla's kernels — it'll keep using the standard attention paths it always has. No serve_rag restart needed; nothing imports fla at runtime today. `uv sync --extra training` after pin add is the only state change. Tracked separately so it lands deliberately, not coupled to a Phase 3 kickoff timeline.

### P3 — Small open ops items

| Item | Owner | Detail |
|---|---|---|
| Smoke-test cron install | rj (shared box) | Line already in `LAI/scripts/ops/README.md`; needs rj OK to add. ~30 s. |
| Live F1 smoke against base Qwen `:8005` | harsh | `./.venv/bin/python scripts/eval/generate_eval_answers.py --model-a-url http://localhost:8005 --model-a-name qwen3.6-27b --skip-b --input /tmp/one_question.jsonl` against a 1-row test JSONL — proves the pre-generate loop works end-to-end before any LoRA actually exists. ~1 min. Documents an answer in the script's expected shape. |

### Process question — should `harsh/` scratch docs move into git?

vm committed both `harsh/PROGRESS_V2.md` (`efec05e`) and `harsh/EU_AI_ACT.md` (`07a99fe`) — the original "harsh/ stays uncommitted" pattern is now broken in practice. Two docs are inconsistent with that:

- `harsh/MODEL_COMPARISON.md` (~20 KB) — load-bearing: Phase-3 recipe + playbook + prior-attempt analysis + measured probe results + the F2 phased-pairwise plan. Referenced from `PROGRESS_V2.md` and `RESEARCH_DOCS_REVIEW.md` via relative links.
- `harsh/RESEARCH_DOCS_REVIEW.md` (~18 KB) — load-bearing: the 12-rejection decision record with verified citations against the research-team docs. Referenced from `PROGRESS_V2.md`.

If left uncommitted, future readers see broken-style relative links in `PROGRESS_V2.md` pointing at files that aren't in their checkout. **Recommendation: commit both** in a single `docs(harsh): commit load-bearing Phase-3 reference docs` commit, matching the vm precedent. Decision for harsh to make at start of next session.

### Distribution — 2026-06-01

**harsh — small queue, owner of cleanup + verification**
1. Verify intent on serve_rag with rj; if accidental, restart. (P0 above)
2. Push `develop` via rj's account (or set up `.ssh/known_hosts` and push directly). (P1)
3. *(optional)* Commit `harsh/MODEL_COMPARISON.md` + `harsh/RESEARCH_DOCS_REVIEW.md` if the pattern is "harsh/ in git now."
4. *(optional)* Run F1 live smoke against `:8005` to prove the pre-generate loop end-to-end.

**rj — one big task + a small fast-path window**
- **rj-1 (Phase 4.3 Phase B)** — statute corpus write path + migration 007. His Phase-A follow-on; only he can land this efficiently (he owns `lai.pipeline.statute_feed`). Unblocks the "RAG = current statute" architecture.
- Fast wins inside an active ops window: (a) verify or restore serve_rag (P0), (b) push `develop` from his shell (P1), (c) re-fire the Qwen3.6 precompute against the 32-probe set in tmux (P2 — ~3 min, weights cached, atomic), (d) install the smoke cron with his own OK (P3).

**vm — no new assignment; running ahead of plan**
- vm-6, vm-7, vm-8 (via rj), and vm-9 (backend + FE) are all ✅ done. If vm has cycles, the *only* useful Phase-3-prep-adjacent task left is a small CSV-export-of-baseline tool or a result-archiving helper for the eval API. Neither is urgent. Worth letting vm pick up whatever surfaces organically rather than artificially assigning.
- **2026-06-01 — CSV-export-of-baseline tool ✅ DONE.** Shipped `LAI/scripts/eval/baseline_to_csv.py` (stdlib only, ~270 LOC). Joins the baseline JSON's per-probe answers (`{answer, len, ascii_ratio}`) with the probes JSONL by id and emits a quote-all flat CSV — columns `probe_id, category, language, fictional, prompt, answer, answer_len, ascii_ratio, notes`, probe-file order so a reviewer reads them as the JSONL lists them. Optional `--meta-out` writes a 2-col key/value sidecar with the baseline meta block plus two synthetic rows (`current_probes_sha256`, `probes_sha256_match`) so the sidecar alone tells the reviewer if the baseline is stale. Loud staleness check (recomputes probes-file sha256, surfaces mismatch to stderr) but never aborts the export — the doc itself notes the current baseline is stale, and the reviewer may still want the CSV. Exit codes: 0=wrote (incl. with stale warn), 1=config error (missing file / bad JSON / bad probes row), 2=schema unexpected. Verified live against the current stale baseline at `LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json`: emitted 32 rows (matches the post-vm-6 probes file), 7 stale empty-answer rows = exactly the vm-6 additions (`refusal_004..010`), 0 orphaned answers, sha256-mismatch warning fired correctly with both hashes shown. `python3 -m py_compile` + `ruff check` + `ruff format --check` all green. **Side find for engineering review:** base Qwen3.6-27B's `refusal_003` answer ("Es gibt kein „fiktives Landesfantasiegesetz" in der deutschen Rechtsordnung…") is a *correctly calibrated refusal* — i.e. the base passes the canonical fabrication probe that both v1 and v2 LoRA fail. Reinforces the recipe-correction thesis: the fabrication is induced by the prior training recipe, not inherited from the base.
- **2026-06-01 — Eval-API result-archiving helper ✅ DONE.** Extended `LAI/micro-services/eval_api.py` with `POST /eval/archive` + `GET /eval/archives` (+~150 LOC). Solves the "a re-run blew away a finished lawyer session's labels" failure mode without changing the storage primitive (still single `results.json`). `POST /eval/archive[?label=<slug>]` takes a consistent snapshot under the existing `_EvalState.lock`, writes three files into a timestamped sibling directory at `<EVAL_STATE_PATH parent>/archives/<UTC-isoformat>__<label>/`: (a) `state.json` — byte-for-byte copy of active `results.json` (seed + L/R mapping + scores + started_at), (b) `summary.json` — deblinded `{model_a_wins, model_b_wins, ties, total, scored}` + an `archive_meta` block (label, seed, archived_at, scored, total), (c) `export.csv` — full deblinded CSV export at snapshot time. Atomic-rename via `.tmp__<name>` sibling so a crash mid-write never leaves a half-built archive under the canonical name. **Non-destructive** — the active state isn't touched and scoring resumes immediately. **Recovery flow:** stop the API, `cp archives/<ts>__<label>/state.json $EVAL_STATE_PATH`, restart — mapping + scores restore identical. `GET /eval/archives` is a lightweight read-only list (skips `.tmp__` work-in-progress dirs and malformed bundles). **Label sanitisation** (`_LABEL_RE = [^A-Za-z0-9._-]+`) blocks path-traversal — `../../etc/passwd: bad/label!` collapses to `etc-passwd-bad-label`. **Same-second collisions** get a numeric suffix (`<base>`, `<base>_2`, …) so a double-fire never clobbers an earlier snapshot. **Verified** with an in-process smoke (stub-based, since `LAI/.venv/bin/python` is rj-symlinked) covering 7 invariants: (1) archived `state.json` byte-equals active state at archive time, (2) archive immune to later scoring, (3) label sanitisation blocks path-traversal, (4) same-second collision suffix works, (5) `list_archives` skips `.tmp__` dirs, (6) recovery from archive yields identical seed + scores, (7) basic shape/file presence. `ruff check` + `ruff format --check` + `py_compile` all green. **Zero collision:** only edits vm's own `eval_api.py`; no FE change; no schema change to the questions JSONL.

### Priority order for the next session

1. **P0 — serve_rag verification with rj** (5 min; if down-by-accident, restart).
2. ✅ **P1 — push `develop`** — DONE 2026-06-02 by rj; `origin/develop` at `c23c0c1`.
3. **P2 — re-run Qwen3.6 precompute** against the 32-probe set (~3 min in tmux).
4. **rj-1 — Phase 4.3 Phase B**.
5. **harsh-process-question — commit `harsh/MODEL_COMPARISON.md` + `harsh/RESEARCH_DOCS_REVIEW.md`** if pattern allows.
6. ~~**harsh-#4 — install smoke cron**~~ — **✅ DONE by rj as rj-3** (see #4 in the queue above).
7. ✅ **DONE 2026-06-01 — harsh-F1-smoke ran live against `:8005`**: tiny one-row JSONL with the real `q01` BImSchG question from `bimschg_50.jsonl`, side-A-only against `qwen3.6-27b`. 9.7 s wall, 874-char structured German legal answer with proper §-citations, `enable_thinking=off` confirmed (no `<think>` block), exit 0. F1 pipeline verified end-to-end before any LoRA exists.

### Deferred by design — NOT to be picked up this session

- **Phase 3 actual LoRA training run** — waits for `2.4` (pilot firm). Every supporting artifact is already in place (`MODEL_COMPARISON.md` recipe + playbook + phased pairwise plan; retention probe scaffold + callback + 32 probes + baseline workflow; eval API + FE + 50-question seed + pre-generate script). The moment pilot lands, training is unblocked.
- **`2.4` pilot firm** — relational, owned by boss + rj.
- ~~**`R3` report completion toast** (Phase 1.x Wave 2)~~ — ✅ DONE 2026-06-02 (`5c863ac`).

### Verification commands (to re-run this state check next session)

```bash
cd /data/projects/lai && git log --oneline origin/develop..develop  # what's pending push
ss -ltn | grep -E ":(18000|18001|18002|8004|8005|80) "             # which services are live
ps -ef | grep -E "serve_rag|vllm" | grep -v grep                    # which python processes are alive
nvidia-smi --query-gpu=index,memory.free --format=csv               # GPU state
python3 -c 'import json; m=json.load(open("LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json"))["meta"]; print(m["n_probes"], m["probes_sha256"][:16])'  # baseline staleness check
git status --short                                                   # uncommitted scope
```

Run these *first* next session before deciding anything — never trust this section's snapshot blind, re-verify it.

---

## Live production sample — ks + as chat audit (2026-06-01)

**Why:** before assuming "production works fine since the boss-mandate close-out," pull actual user conversations and judge the model's behaviour. Read-only audit.

**Method:** `sessions.db` stored at `LAI/processed/sessions.db` (SQLite, WAL-active; 98 sessions, 836 messages). User ↔ UUID mapping from `lai_db.users` table in `lai_postgres_main` container (`docker exec -e PGPASSWORD=… lai_postgres_main psql -U lai_user -d lai_db -c "SELECT id, email FROM users WHERE email ILIKE 'ks%' OR email ILIKE 'as%' OR email ILIKE 'sa%';"`). Conversations dumped via stdlib `sqlite3` (no CLI on box). Both users are admins in `org_id=e3bff292-…`.

### Result by user

| User | Sessions | Real chat turns | Verdict |
|---|---|---|---|
| **ks@blockland.ae** (`e477b066…`) | 2 | 8 (5 user + 5 assistant + an upload toast etc.) | Real exploratory use; **mixed model quality** |
| **as@blockland.ae** (`7007d94a…`) | 6 | **0 user messages anywhere**; 5 sessions are bare document uploads with no chat at all, one has a single auto-generated upload-toast assistant message | **Cannot judge model behaviour for as** — they uploaded files and never asked anything. Either testing the upload flow, dropped files for someone else to query, or hit a UX block (possibly the *exact* "still processing" gate vm-2's dedup was meant to fix). Worth following up. |

### ks — turn-by-turn verdict (2 sessions, 5 user questions)

**Session 1 — chat mode, no documents (2026-05-25 16:29):**

| User | Mode | Model verdict |
|---|---|---|
| "Bist duz online?" *(typo for "du")* | chat | ✅ appropriate brief acknowledgement |
| "Überprüfe truenbrietzen" | chat | ✅ correctly didn't fabricate (asked for clarification), **but missed that "Treuenbrietzen" is a real Brandenburg town with active wind-energy projects** — geography-knowledge gap for a German-wind product |
| "hast du zugrif auf meine projekte?" *(typo)* | rag | ✅ **excellent grounding** — explicitly explains [C-n] corpus vs [M-n] matter docs distinction, says no matter docs uploaded |
| **"was kann ich hier tun?"** | rag | ❌ **real failure** — interprets a generic UI question as a *criminal-law / fraud / Konto-zur-Verfügung-stellen* topic from the retrieved chunks. Cites [C-1]/[C-2]/[C-3] Hude/Hatten DD docs + a 2009 fraud-forum post. Ended with "(unbelegt)" tag but the whole answer is off-topic. Classic "retrieve-then-answer reflex misfires on a non-content question." |
| "was sind das quellen?" | rag | ✅ accurate listing of C-1 (EEG 2009 FAQ), C-2 (EEG 2009 statute), C-3 (EuGH rulings + 2009/28/EG directive) with proper citations |

**Session 2 — `20261112-JM Motio.docx` + 6 matter files (2026-05-25 16:33):**

| User | Mode | Model verdict |
|---|---|---|
| "was kannst du hier im datenraum erkennen?" | chat | ✅ substantive 2,750-char structured analysis of M-1…M-7 (draft motions, Gebotsbestätigungen, WhatsApp evidence, Motio Group entity, dates) — **but answered in English despite a German question.** Mild language drift. |
| "auf deutswchg" *(typo)* | contract | ✅ switches to German cleanly, structured 4,083-char analysis of corporate structure (Rietz II, JV, Netzanschluss), citations preserved |
| "gehst du semantisch vor? oder verstehst du die dokumente?" | contract | ⚠️ misinterpretation — user asks a *meta-question about the AI*, model treats it as a document-grounded question and says "no info found in the documents about how the AI works." Over-grounded retrieval. |

### Honest summary of model behaviour from this sample

**Works:**
- Refusal-on-no-corpus is calibrated correctly (one of the things we'd want a §3.4 LoRA to preserve, not break).
- Source-listing is accurate with proper `[C-n]` handles; the answer to *"was sind das quellen?"* is gold-standard for a legal AI.
- Document analysis is substantive, well-structured, with `[M-n]` citations.

**Broken / observed failure modes (3 of 5 ks turns):**
- **Retrieve-then-answer reflex misfires on non-content questions** (worst — *"was kann ich hier tun?"*). UI / navigation / meta questions should not trigger RAG retrieval. The model retrieved corpus chunks, found something tangentially related to fraud, and answered as if the user asked about criminal law.
- **Language drift** (English answer to German prompt in Session 2 turn 1). Inconsistent — the next turn handled language correctly. Worth a retention-probe-style sentinel.
- **Over-grounded interpretation of meta-questions** ("gehst du semantisch vor?"). Asking about the AI itself ≠ asking about the documents.

**Geography knowledge gap:** missed "Treuenbrietzen" (real Brandenburg wind town). Phase-3 BImSchG training data should include place-name grounding for the German wind-energy domain.

### Surfaced new candidate work item — `mode-router-quality` (NOT YET ASSIGNED)

The most important finding isn't an LLM problem; it's a **routing problem**. The model is doing what `force_mode=rag` told it to do. The fix is upstream:

- **mode-router-quality** *(candidate Phase 1.x or 2.x — needs sizing before assigning)*: lightweight intent classifier (or set of regex heuristics in `serve_rag`) that detects (a) UI/navigation questions ("what can I do here?", "how does this work?"), (b) meta-questions about the assistant itself, (c) acknowledgement/greeting turns ("bist du online?") — and routes those to a `chat` (no-RAG) path. Avoids the *"was kann ich hier tun?"* failure mode entirely.

  *Honest sizing concern:* an LLM-based router would double per-turn latency for the common case; a regex / heuristic router needs careful prompt-list curation in two languages. Worth scoping properly before someone picks it up. Probably 2–3 days work + an eval batch (which we can reuse vm-9's pre-generate runner for).

### Implications for Phase 3 prep (already-shipped retention probes)

The retention probe set (`retention_probes.jsonl`, 32 prompts) is **missing this category**: there are no probes for "UI / meta / navigation questions that should NOT trigger RAG." Worth a vm-6-style follow-on append: add 3–5 prompts of the *"was kann ich hier tun?"* / *"gehst du semantisch vor?"* / *"bist du online?"* shape and watch what the LoRA does with them. They're not currently in the §3.4 50-question seed either.

### Follow-up for as

as uploaded 6 documents on 2026-05-26 and asked **zero** questions. Possible causes (in order of plausibility):
1. They were testing the upload flow only.
2. They hit the *"still processing"* gate that vm-2's dedup fixed in `82c3b35` (not deployed live yet — see Phase 2.3 row).
3. They dropped files for someone else (sa, ks) to query.
4. Some FE / auth issue specific to that account.

One short follow-up note to as ("did you mean to query these docs? anything blocking you?") closes this cheaply.

### What this audit does NOT cover

- We did not check the model's *Treuenbrietzen* answer against actual wind-park data — that geography gap is observed, not measured at scale.
- We did not look at sessions from the most-active user `cd5a4a1b…` (8 sessions, last active 2026-06-01 17:58). That user isn't ks/as so out of scope for this audit; worth a separate look if you want a wider sample.
- We did not check whether the "(unbelegt)" tagging on the off-topic turn-4 answer is consistent with how the citation-validator was supposed to flag it. Worth a separate audit of `citation_validation` outcomes vs. user-facing answer quality.

---

## Completed this session (commits)

**LAI** (`v2-restructure`):
- `884ea24` feat(ddiq): ampel serialization, refusal guards, per-park bundesland gating
- `339cf11` feat(upload): resumable tus 1.0 upload server
- `3cb2547` feat(serve_rag): VDR-scale retrieval, image OCR, resumable-upload wiring
- `c4eac72` chore(ops): restart_serve_rag.sh rebuilds backend+worker together
- `18f23d5` feat(stress): VDR-scale matter staging + delivery scripts
- `9d516dc` feat(serve_rag): slow-query telemetry (1.5)
- `5902054` feat(serve_rag): narrate retrieval in /query/stream — UX, no dead air before first token (ships on restart)
- `7db20ea` fix(ddiq): per-question report progress ticks — kills the 7% stall (1.3; rides next rebuild)
- `ad470a1` fix(ddiq): cadastral pipeline progress ticks — kills the 78% freeze (1.3 follow-up; rides next rebuild)
- `023a189` docx backend → **reverted** by `ca7b2d2` (consolidated on client-side exporter)
- `47c933b` fix(serve_rag): restore chat history + meta refresh (`uid` → `user_id`) — a real bug ruff's F821 surfaced; history was silently loading empty

- `c42744c` chore(ops): restart_serve_rag.sh `down --remove-orphans` before recreate — kills the stale-container name conflict hit on the 05-28 deploy
- `f30d0a0` + `2d73c9e` style: ruff 0.15.5 auto-fix + format + manual fixes — **CI lint gate green** (563 errors + 64 files → 0)
- `16b31f2` fix(ci): **mypy strict + bandit gates green** on lai.common (14 type errors → 0; 14 bandit findings → 0; B608 audited-safe, XML hardened with defusedxml)
- `fc931f9` fix(ci): run the ci-gate step in the workspace root — fixes the aggregate-gate `No such file or directory` (job had no checkout under the global `working-directory: LAI`)
- `5a6a3b2` feat(audit): append-only audit log (2.3) — migration 006 + `lai.common.audit` (async+sync, best-effort, 98% cov) + login/query/report instrumented; CI gates all green (599 tests, cov 87%)
- `d9ed39a` feat(audit): admin read endpoint `GET /admin/audit` + `audit.query()` reader; fixed the audit suite being deselected by `make cov` (added `pytestmark = unit`); 601 tests, cov 87.56%
- `b7c141c` feat(ops): system smoke test — guards against reranker-on-CPU (vm-1 / 1.2; stdlib, distinct exit codes, doc'd in ops README)
- `290bb25` feat(ops): smoke_test `--report` leg for DDiQ pipeline (vm-3 / 1.2 follow-up; new exit code 7, env-aliased creds, cron line documented but not installed pending rj OK)
- `5abe968` feat(ops): audit_log export + retention CLI (vm-4 / 2.3 follow-up; CSV/JSON export with date+action+org+user filters, dry-run-by-default `--purge-older-than DAYS`, EU AI Act Art. 12 callout in README)
- `3c4033b` feat(ingest): one-law `gesetze-im-internet.de` fetcher (vm-5 / Phase 4 feed; thin wrapper around rj's Phase-A client+parser, writes per-§ JSON to `data/statutes/<slug>/`, atomic swap, sha256-keyed idempotency)

**2026-06-02 — three commits today:**
- `4f064ba` feat(LAI-UI): cross-reload ingest visibility — `ingestStore.ts` (localStorage registry per `{user, session, doc_index, filename, started_at}`) + `IngestionStatusToast.tsx` (bottom-right toast polling `matter_documents` per tracked session, hides "Seite 0/N" until pages_done>0, clears on done/failed/404). Inert until the FE-WIP owner lands their bundle (3 wiring edits in WT: `useComposerAttachments.ts`, `DashboardLayout.tsx`, `ragApi.ts` — depend on team's unstaged `useAuth` + `UploadResumeIndicator` + `deleteMatterDocument`). Ping sent.
- `a43b440` fix(persistence): serialize SQLite reads under shared RLock. Root cause for the intermittent `sqlite3.InterfaceError: bad parameter or other API misuse` 500s at `persistence.py:523` on GET `/sessions/{id}/documents` — Python sqlite3 connection isn't thread-safe even with `check_same_thread=False`; writes had a lock, reads raced. `Lock` → `RLock` (read functions call other reads internally — `list_messages → session_exists` etc.), all 15 reads wrapped in `with _STATE["lock"]:`. `tests/unit/test_persistence_{user_scope,feedback,matter}.py` 29/29 green. Pre-existing TOCTOU in `add_session_share` / `revoke_session_share` (lockless `session_owner` check, then locked write) unchanged — out of scope.
- `6c42f77` docs(harsh): commit load-bearing Phase-3 reference docs — `ROADMAP_2026Q3.md` + `MODEL_COMPARISON.md` + `RESEARCH_DOCS_REVIEW.md` (referenced via relative links from this file; anyone else's checkout saw broken-style links). Fixed stray "now" prefix on `MODEL_COMPARISON.md` line 1.
- `<pending>` chore(deps) + style(transformers-5.x): qwen3_5 unblock arc. pyproject `transformers>=5.9.0,<6.0` + explicit `huggingface_hub>=1.5,<2.0` floors (was `>=4.46.0` — a 2026-06-01 venv rebuild had silently resolved transformers down to 4.57.6 and lost `qwen3_5` architecture support); mechanical `torch_dtype=` → `dtype=` rename across 7 production+training call sites (removes the deprecation print at reranker load). 8 files, +17/-10, py_compile clean. Live-verified under sandbox transformers 5.9: `Qwen3_5ForCausalLM` text-only loads at 16.85 GB on GPU 1; `Qwen3-Reranker-8B` production invocation survives the bump (scores discriminate cleanly 4.45 vs −12.32, dtype preserved as fp16). DDiQ untouched. `uv sync --extra training` landed (`5.9.0 / 1.17.0 / 0.19.1`). 32-probe Qwen3.6-27B baseline regenerated against the now-correct venv (`n_probes=32 sha256[:16]=95a1aab22a82152d quantization=4bit_nf4 enable_thinking=off`). serve_rag restart deferred to a planned window — in-process Reranker still holds 4.57.6 until then; full story in P2 above.

**LAI-UI** (`fix/cross-account-isolation`):
- `f0f0441` fix(ddiq): German labels + firm-letterhead placeholder in DOCX export (2.1)
- `9a2040e` fix(report): readable progress labels for the DDiQ report pipeline (Wave 2 / R2 — clean file, committed)
- `c554842` feat(audit-ui): admin audit-log view at `/dashboard/admin/audit` (new page + adminApi.listAudit + route + link; tsc/eslint clean, clean of upload WIP)
- `ragApi.ts` watchdog 60s→120s (1.1) — **uncommitted** (file holds team upload WIP; commit together).
- `pages/DashboardChat.tsx` C2 (rehydration skeleton — no "New Conversation" flash) + C3 (keep partial answer on stream timeout) — **uncommitted** (file holds +56/−23 team WIP; my edits are in regions clear of the WIP hunks, lint-clean; commit together with that WIP).
- `components/chat/DocumentList.tsx` vm-2 (1.4): best-copy-per-filename dedup in the status poll → a stale duplicate row no longer keeps chat gated on "still processing" after a `done` copy exists (green chip already present). tsc + eslint clean; dedup logic unit-checked; not browser-tested. **Uncommitted** — edit sits in the poll region, clear of the upload-WIP hunks; commit together with that WIP.

## UX smoothness — Wave 2 status
- **R2** (report step labels) ✅ committed (`86ea301` post-rebase, was `9a2040e`).
- **C2** (rehydration skeleton) ✅ shipped in surgical bundle (`cf9adfe` post-rebase, was `82c3b35`).
- **C3** (keep partial answer on timeout) ✅ shipped in surgical bundle (`cf9adfe` post-rebase, was `82c3b35`).
- **R3** (report completion toast) ✅ **DONE 2026-06-02** (`5c863ac` on LAI-UI develop) — confirmed harsh's WIP hunks on `ReportDownloadPanel.tsx` were in different branches of the same `.then().catch()` chain (his at the outer `.catch` for 404 handling, the toast goes into the inner `.then` for the done-success path). Surgically committed via stash-and-restore so harsh's 26-file resumable-upload WIP stays untouched. tsc clean; 3 pre-existing lint warnings unchanged. Ready-to-apply spec preserved below for historical context:
  > In `ReportDownloadPanel.tsx`, in the poll loop's `if (s.status === "done")` branch (~line 1425, right before `setStep("preview")`), add `toast.success("Your report is ready", { description: s.project_name })`. `toast` is already imported. One line; do it once the teammate's WIP in that region lands.

---

## Quality gates (CI) — now green

All four CI gates pass, verified locally on the CI-locked tooling (ruff 0.15.5, mypy 1.19.1, fresh env):
- **lint** (ruff check + format) ✅ — `f30d0a0` (auto-fix + format) + `2d73c9e` (manual + scoped config)
- **type** (mypy strict, lai.common) ✅ — `16b31f2`
- **security** (bandit, lai.common) ✅ — `16b31f2` (B608 audited-safe skip; XML → defusedxml)
- **test** (pytest) ✅ — 591 unit tests pass
- **ci-gate** (aggregate) ✅ — `fc931f9` fixed the workspace-dir bug that failed it even with the four gates green

Pre-existing debt confirmed (not caused by our edits): the lint/type/security failures were branch-wide and latent — CI had been red on multiple gates, hidden because upstream failures skipped ci-gate.

## Deploy state — live vs pending (updated 2026-06-01)

**Update 2026-06-01 — three deltas since 05-29 14:25.**
- ✅ **serve_rag back up 2026-06-02 11:52** (rj-restart, PID 3176864, `.venv` interpreter, `:18000`) with `a43b440` (persistence RLock fix) live. Verified via 600-req synth burst across 5 sessions on 24 threads: `{200: 600}`, 1225 req/s, p50 17ms / p95 35ms / p99 48ms / max 70ms; zero new `InterfaceError` / `Traceback` / `5xx` in `logs/host/serve_rag.log` during the burst (baseline 6 pre-restart hits unchanged). Qwen3.6 analyzer `:8005` + DDiQ `:18001` + reranker `:8004` + embedding (vLLM GPU 1) all still up. Prior 05-31 → 06-02 outage: clean shutdown at 05-31 21:13 (FastAPI/uvicorn handled SIGTERM cleanly), preceded the `5a00f7f init:true` infra commit at 23:14 by ks_admin; smoke cron caught it daily 08:00 (`cannot reach :18000/health: Connection refused`).
- ✅ **LAI-UI team WIP landed** as `bba68b3 feat(v2): cross-account isolation + onboarding + sharing + admin UI` — the "blocked on FE-WIP owner / 26 dirty files" line from 05-29 no longer holds. Our surgical bundle landed on top as `82c3b35` (LAI-UI: watchdog 60→120 + vm-2 dedup + recordExport + C2 + C3) + `0081b66` (vm-9 lawyer-blind eval UI). **All committed.**
- 🔧 **LAI-UI 6-commit recovery 2026-06-02** — verification turned up that the 6 cited LAI-UI feature SHAs (`f0f0441` DOCX labels, `9a2040e` report progress labels, `c554842` audit-log view, `82c3b35` surgical bundle, `0081b66` vm-9 eval UI, `4f064ba` ingest toast) lived only on the local `fix/cross-account-isolation` branch — whose upstream had been deleted during the Git-Flow consolidation (per [[feedback_git_workflow]]: develop=trunk, no feature branch). Net effect: PROGRESS_V2 had been calling them "shipped" but they were never on `origin`, so Vercel had never seen them. Recovery: rebased the 6 onto `origin/develop` (clean — no conflicts), fast-forwarded `develop`, pushed (`920c86f..5967b1a`), deleted the stale local branch. Harsh's 26-file resumable-upload WIP preserved via stash-pop. New SHAs (post-rebase): `2958904` DOCX, `86ea301` report labels, `f0247fb` audit-log view, `cf9adfe` surgical bundle, `d593553` vm-9 eval UI, `5967b1a` ingest toast. Vercel will auto-roll the next pageload after build completes.
- ✅ **Smoke cron installed** by rj (`rj-3` track), originally daily 08:00; **tightened to hourly 2026-06-02** (`e5bfd19`) after the 05-31 → 06-02 ~20 h outage proved a 24 h detection window was too wide. Logs to `LAI/logs/host/smoke_test_cron.log`, one iso-timestamped run per hour.
- ✅ **`serve_rag` systemd unit drafted + committed** (`e5bfd19`, 2026-06-02): `LAI/scripts/ops/systemd/serve_rag.service` + `install.sh` + README section. Auto-restart on failure + auto-start at boot. **Install requires sudo** — pending ks_admin to run `sudo bash LAI/scripts/ops/systemd/install.sh`. Cohabits with `restart_serve_rag.sh`; not blocking anything if deferred — the hourly cron is the bridge until then.
- ✅ **`develop` pushed to `origin`** (2026-06-02, `c23c0c1`) — closes the 2026-06-01 P1 item.
- ✅ **LAI-UI lint sweep 2026-06-02** (`5f8f311`) — 20 problems → 7. Cleared 13 false-positive react-refresh warnings via canonical-pattern eslint overrides for `components/ui/**` (shadcn variant exports), `contexts/**` (Provider+hook+context), `hooks/**` (Provider+hook). Fixed `Logo.tsx` exhaustive-deps + dropped unused `eslint-disable` in `DashboardLibrary.tsx`. **Remaining 7 (1 error + 6 warnings)** all sit inside harsh's uncommitted resumable-upload WIP — `--max-warnings 0` deferred until that bundle lands.
- ✅ **R3 completion toast** shipped to `origin/develop` LAI-UI (`5c863ac`, 2026-06-02) — surgical commit while preserving harsh's 26-file WIP on the same file.
- ✅ **Scalable retrieval recall harness** shipped (`d4de720`, 2026-06-02) — new `LAI/scripts/eval/retrieval_recall.py` replaces the in-RAM `lai.search.eval.Corpus` (which OOMs on 35.7 M children × 4096 fp32 ≈ 572 GB). The new harness loops over `val.jsonl`, queries the SAME indexes serve_rag uses (pgvector HNSW dense + SQLite FTS5 BM25 + RRF), so reported Recall@K matches what users see. Modes `--mode {dense,bm25,hybrid}`, multi-K Recall@10/30/100 + MRR per run, query-embedding disk cache so re-runs skip the embedding service. Live-verified 10-row smoke: bm25 R@10=0.20 / dense R@10=0.30 / hybrid R@10=0.40 — RRF lifts both signals as designed. Per-query timings: dense ~250 ms (HNSW warm), BM25 ~2.7 s (FTS5 OR-of-6, the same slow leg the 05-31 perf experiment surfaced). The 05-31 BM25 retune that had to be reverted on a 1-query smoke-test recall regression can now be re-attempted with a real recall gate.
- ✅ **HNSW ef_search sweep + recommendation** (`be08c42`, 2026-06-02) — new `LAI/scripts/eval/hnsw_ef_search_sweep.py` wraps the harness over a range of ef_search values. First live sweep (dense, n=200) found ef=200 is the recall/latency knee: R@30 0.380→0.405 (+2.5 pp), R@100 0.435→0.465 (+3.0 pp), MRR +7.3 % relative, at a +63 ms per-query cost (16→79 ms, well under the 100 ms ANN budget). ef=400 returns +1.5 pp R@30 for another +33 ms — diminishing returns. **Pending:** hybrid confirmation run then bump `RetrievalConfig.hnsw_ef_search` 100→200. Per-row dense-baseline analysis revealed **56.5 % of gold parents miss the dense top-100 entirely** — so ef tuning helps but the bigger lever is BM25/hybrid coverage. BM25 retune now scoped as a pre-experiment blueprint at [`rj/blueprint/2026-06-02-bm25-retune-empirical.md`](../rj/blueprint/2026-06-02-bm25-retune-empirical.md) with the recall-gate decision rule written BEFORE variants are tested (the 05-31 anti-pattern).

**Update 2026-05-29 14:25 — audit deploy complete + `v2.1.0` released.** (historical, preserved for context)
- **`v2.1.0` released:** repo consolidated to trunk-based **Git Flow** (single `master` + `develop`; `v2-restructure` retired). Tags: `v1.0.0`, `v2.0.0`, `v2.1.0`. The audit subsystem, CI fix (`fc931f9`), smoke test, and Git Flow docs all shipped in `v2.1.0`. master == develop == v2.1.0.
- **Audit log LIVE:** migration 006 applied to `lai_db` (audit_log table + append-only trigger verified); serve_rag restarted + DDiQ rebuilt with the audit code (reranker confirmed `on cuda:1`). login/query/upload (serve_rag) + report/export (DDiQ) instrumented end-to-end. Table records on next user action (0 rows at deploy).
- ~~Still pending: LAI-UI FE deploy (audit-log view + C2/C3/watchdog/vm-2) — blocked on the team upload WIP (26 dirty files).~~ ← superseded by the 06-01 update above; team WIP landed in `bba68b3`, our surgical bundle in `82c3b35`/`0081b66`.

---

(historical, 2026-05-29 04:10) rj re-ran `restart_serve_rag.sh` → serve_rag restarted AND DDiQ rebuilt+recreated. **Backend is fully live (verified):**
- **serve_rag (host, PID 3007929, healthy):** ✅ `uid`→`user_id` history fix (chat memory restored), C1 chat narration, slow-query telemetry; reranker on `cuda:1`.
- **DDiQ (containers built 05-29 04:10, healthy):** ✅ per-question + cadastral progress ticks, ampel/bundesland fixes, defusedxml XML hardening (confirmed importable in the container). The hardened `down --remove-orphans` recreate worked — no name conflict.

**Still pending:**
- **LAI-UI (FE — separate deploy):** ✅ **pushed to `origin/develop` 2026-06-02** (`920c86f..5967b1a`) after the 6-commit recovery (see Deploy state delta above). R2 step-labels, German DOCX labels, audit-log view, C2/C3/watchdog/vm-2 surgical bundle, vm-9 lawyer-blind eval UI, and the ingest toast are all on origin now. Vercel auto-roll pending verification; closes Phase 2.3 "deploy LAI-UI (view) still pending" and the R2 row.
- **CI fix (`fc931f9`):** ✅ released in `v2.1.0` (merged to master + develop; ci-gate green).
- **Audit log (`5a6a3b2`):** ✅ migration 006 applied 05-29 14:25; serve_rag + DDiQ restarted with audit code → events recording on next action.

## Next steps (grounded — no invented work)


Ordered by value / unblocking:
1. ✅ **DONE — `fc931f9` released in `v2.1.0`** (ci-gate green; merged to master + develop).
2. ✅ **DONE — serve_rag restarted (05-29 14:25)**; `uid` history fix + audit code live; reranker on cuda:1. Smoke-test still pending a test login.
3. ✅ **DONE — committed the FE surgically in `82c3b35`** (LAI-UI): watchdog 60→120, vm-2 best-copy-per-filename dedup, recordExport + 2 export-ping handlers, C2 isRehydrating skeleton, C3 keep-partial-on-timeout. Per-file `git diff HEAD` confirms team WIP (now landed in `bba68b3`) is preserved alongside in WT. R2 step-labels + German DOCX labels were committed earlier (`9a2040e`, `f0f0441`); audit-log view was committed earlier (`c554842`); vm-9 lawyer-blind eval UI committed at `0081b66`. **LAI-UI deploy itself — whether the host has been re-rolled — not verified this turn; see Deploy state.**
4. ✅ **DONE — DDiQ rebuilt (05-29 14:25)** via `restart_serve_rag.sh`; defusedxml + report-progress fixes live.
5. ✅ **R3 completion toast** — DONE 2026-06-02 (`5c863ac`).
6. ✅ **DONE — Phase 2.3 audit log shipped (`v2.1.0`) AND deployed (05-29 14:25)**; migration 006 applied; login/query/upload/report/export instrumented across serve_rag + DDiQ.
7. **Phase 2.4 pilot firm** — boss/rj, relational not engineering. The actual bottleneck (5 months, no pilot). ⬜ **← the remaining priority.**

Deferred / later: Phase 3 foundation-model PoC (after a pilot); Phase 4 discipline items.
~~Minor follow-up noted in code: an always-`"running"` ternary in the gated V2-analyzer progress path (collapsed for lint; logic smell — status never reports "done" there).~~ → **✅ DONE — fixed by rj in `9255cfc fix(serve_rag): V2-analyzer progress reports 'done' on final tick (rj-3b)`.** Root-cause correction: the bug was in `serve_rag.py:_on_progress`, not DDiQ (the analyzer's `_emit` carries `step`/`current`/`total`/`elapsed_s`/`percent` but no `status` key, so the hardcoded `status="running"` masked the final `step="done"`/`percent=1.0` tick).

## Vikrant Malik (vm) — parallel track

Picked because they're **isolated from our current work** (serve_rag retrieval/telemetry, the ddiq report engine, DOCX). vm can run these in parallel with no merge collisions on our files.


### vm-1 — System smoke-test script  (roadmap 1.2)  · easiest, zero collision
- **✅ DONE — committed `b7c141c`.** Shipped `LAI/scripts/ops/smoke_test.py` (stdlib-only): `/health` → `/auth/login` → seed `/sessions` → timed `/query`, asserting (a) round-trip < `LAI_SMOKE_MAX_S` (20s) and (b) the latest `Loading reranker … on <dev>` log line is `cuda`. Distinct exit codes (5=slow, 6=reranker-on-CPU) for cron alerting; documented in `scripts/ops/README.md` (usage + cron line). **One deliberate deviation:** sends a `force_mode=rag` query, not a literal chat "list documents" — chat mode skips the reranker, so it couldn't surface a CPU fallback via latency (env-overridable). Validated live: `/health` + log-parser confirmed against the running box; the query/latency leg reuses the same verified HTTP path (no test account to run it end-to-end). Cron NOT installed (shared-box change; line is in the README).
- **File:** brand-new, e.g. `LAI/scripts/ops/smoke_test.sh` (or `.py`). Touches nothing we're editing.
- **Do:** boot/seed a session, send a "list documents" chat query to serve_rag (`:18000`), then assert: (a) response returns in < 20s, and (b) `logs/host/serve_rag.log` shows `Loading reranker … on cuda` (not `cpu`). Exit non-zero with a clear message on failure.
- **Why:** catches the reranker-on-CPU regression — the actual boss-test root cause — before a user hits it. Run after every `restart_serve_rag.sh`; then wire a daily cron.
- **Done when:** returns 0 on a healthy box; non-zero + readable reason when the reranker is on CPU or the query is slow.
- **Collision risk:** none — new standalone file; only reads the log and hits the HTTP API.

### vm-2 — "Still indexing" → green chip transition  (roadmap 1.4)  · FE, isolated from our work
- **✅ DONE — committed in `82c3b35`** (LAI-UI surgical bundle, 2026-06-01) — extracted from the working tree via per-file surgical staging that left the team's `onCancel` X-button feature in WT untouched for the team to bundle. Original findings preserved: (1) the **green-chip half was already done** in the working tree — `DocumentList.tsx` renders an emerald `CheckCircle2` + "· bereit" on `status === "done"`. (2) The real bug was the **stale "still processing" chat gate** — `DashboardChat.tsx` `docsIngesting` came from `DocumentList`'s `onIngestingChange`, computed as `active = docs.some(queued||processing)` over **raw, un-deduped** docs. A matter can hold duplicate rows per filename (re-drop / retry / an old `failed` beside a fresh `done`), so a stale copy kept `active` true after a `done` copy existed → the exact `GB-Auszug Tostedt` repro. **Fix as committed:** new `DOC_STATUS_RANK` + `docStatusRank` + `bestDocPerFilename` helpers (rank `done>ready>processing>queued>failed`), applied to the poll callsite — mirrors the composer's own poll-match in `useComposerAttachments.ts`. tsc + eslint clean; dedup logic unit-checked. **Browser-test still pending** (needs a seeded duplicate matter row to reproduce live + the LAI-UI deploy to land it).
- **Where:** the FE document-status chip (LAI-UI chat Documents list, likely `components/chat/DocumentList.tsx`) — **not** `ragApi.ts` / `ddiqDocx.ts` which we touched.
- **Do:** when `matter_documents.status === 'done'`, flip the chip to green explicitly; stop the chat error saying "wait a moment / still processing" once ingestion is actually complete.
- **Why:** repro — a user uploaded `GB-Auszug Tostedt`, was told "still processing" though it had finished seconds earlier.
- **Done when:** a finished upload shows a green "ready" chip and chat answers from it with no stale "still processing" message.
- **⚠️ Check first:** `DocumentList.tsx` already has uncommitted WIP, and the upload-status changes in `ragApi.ts` (`BACKEND_URL`/`createSession`/`deduplicated`) are adjacent — vm should sync with whoever owns that upload WIP and read the same status source, so this doesn't collide with *that* (it won't collide with ours).

---

### Next picks for vm (assigned 2026-05-29)
All three are **new/standalone files or vm's own files** — zero collision with our serve_rag/DDiQ/FE work and with the team's LAI-UI upload WIP. Ordered easiest-first.

### vm-3 — Smoke-test: real login leg + DDiQ report leg  (roadmap 1.2 follow-up)  · easiest, zero collision
- **✅ DONE — committed `290bb25`.** Findings on landing: the "real login leg" (item 1 of the spec) was **already shipped in vm-1** — `smoke_test.py` reads `LAI_SMOKE_EMAIL`/`PASSWORD`, hits `/auth/login`, and uses the bearer token for the seeded query. So only the report leg + ergonomics were new. Added: (a) `--report` flag that POSTs `/ddiq/report/generate/async` against `LAI_SMOKE_DDIQ_DOC_ID` and polls `/ddiq/report/{id}/status` until `done` OR observed-advance within `LAI_SMOKE_DDIQ_MAX_S` (default 600s); new exit code 7 = "ddiq report failed / never advanced". (b) `LAI_SMOKE_USER`/`LAI_SMOKE_PASS` accepted as aliases for the EMAIL/PASSWORD pair (vm-3 spec named them that way). (c) README documents the one-time `ddiq_documents` seed pattern + the new tunables. Cron line is in the README but explicitly **not installed** — shared-box change, awaits rj's OK (per the spec). ruff/format clean. **Original-spec correction:** vm-3 said vm-1 "couldn't" do a login leg because of no test account; in fact vm-1 shipped it env-driven, so the leg has been there since `b7c141c`.
- **Where:** `LAI/scripts/ops/smoke_test.py` (+198/−29) + `LAI/scripts/ops/README.md` (+30) — vm's own files.

### vm-4 — `audit_log` export / retention CLI  (roadmap 2.3 follow-up)  · easy, isolated
- **✅ DONE — committed `5abe968`.** Added `scripts/ops/audit_export.py` (asyncpg, 378 LOC) with three things: (a) CSV / JSON bulk export filtered by `--since` / `--until` / `--action` / `--org-id` / `--user-id` / `--limit` — pages through `lai.common.audit.query` (the same single read primitive the admin endpoint uses) and trims by `ts` client-side, bailing as soon as we cross below `--since` since rows are newest-first; (b) `--purge-older-than DAYS` retention that's **dry-run by default** (exits 3 with a row count) and only deletes with `--yes` via a bound-parameter `DELETE FROM audit_log WHERE ts < $1` — migration 006's trigger blocks UPDATE but intentionally leaves DELETE to a privileged retention job, which is this script; (c) README block documents the flags and adds the EU AI Act Art. 12 retention minimum-6-months callout. Same `DB_*` env as the audit writer. ruff/format clean.
- **Where:** new `LAI/scripts/ops/audit_export.py` + README block — read-only import of `lai.common.audit`, nothing modified.

### vm-5 — `gesetze-im-internet.de` statute fetcher (one law: BImSchG)  (roadmap Phase 4 feed)
- **✅ DONE — committed `3c4033b`.** **Stale-spec correction up-front:** the vm-5 brief said "no existing ingest code" — that was written before rj shipped Phase 4.3 A on 05-29 (commits `0a73f16` + `a2f975f`: `GesetzeImInternetClient`, `parse_law_xml`, `parse_toc`, the law→domain registry). I **imported and reused** those (Phase A's parsing is defusedxml-hardened and unit-tested; re-implementing it in `scripts/ingest/` would have been pure duplication and a collision risk with rj's surface). The script is therefore a thin disk-writer: per-§ JSON files under `data/statutes/<slug>/sections/NNNN_<enbez>.json` carrying `seq / law_slug / jurabk / enbez / titel / text / sha256 / fetched_at`, plus a top-level `meta.json` with `xml_sha256` as the fast-path idempotency skip key. Atomic swap via sibling temp dir + double-rename so a crash never leaves partial state under the canonical path. Default `--slug bimschg`; `--force` overrides the skip. New `scripts/ingest/README.md` explains the extension path (`--slug baugb`, `--slug eeg_2023`, …) using rj's existing `python -m lai.pipeline.statute_feed --fetch-sections` dry-run TOC tool for slug discovery. **Not run live yet** — fetches the federal portal, so first execution should be ops-coordinated (politeness throttle is already configured in the `GesetzeConfig`).
- **Where:** new `LAI/scripts/ingest/fetch_gesetze.py` (316 LOC) + `LAI/scripts/ingest/README.md` (75 LOC) + new `LAI/data/statutes/` dir.

---

## Ravi Jangid (rj) — backend/ops track (assigned 2026-05-30)

Routed to rj because each item is in his existing lane — ops/deploy, the `lai.pipeline.statute_feed` he just built, `scripts/db/migrations/`, the DDiQ analyzer/report containers. **Zero collision with our Phase 3 prep**, which lives in `LAI/training/fine_tuning/` (eval harness + playbook) and `harsh/`.

### rj-1 — Phase 4.3 Phase B: statute corpus write path + migration 007  (roadmap Phase 4 feed)  · highest value
- **✅ DONE 2026-05-30 — scope exceeded (shipped Phase B + Phase C steps 1-3).** Phase B (`bf516e5`, `b709f76`, `036bcbe`): migration 007 applied (`statute_feed_state` + `corpus_feed_id_seq` ≥ 9e9), pure ingest helpers (`doc_id` / `content_hash` / `segments_from_parsed_law` / `stable_chunk_id`), and the `--ingest <slug>` live writer — segments → `process_document` → `embed_batch` → fp16 first 4000 dims → transactional per-law DELETE+INSERT into `corpus_*`. Verified live: `bimschg` → 120 parents + 245 children in 23.9 s; re-run skipped in 1.5 s (content-hash idempotency). Phase C step 1 (`f1b9054`): `--backfill mapped` ingested 29/29 wind laws in 12.1 min → **5,762 parents + 9,133 children** across all 11 `classify.py` domains, 0 failures. Phase C step 2 (`7a0de8f`): refactored `_ingest_one(law, client)` so backfill modes share one HTTP client (no per-law TOC re-fetch); added `--backfill all [--limit N]`, `--prune-removed [--missing-days N]` (two-condition guard), `--status`. Phase C step 3 (`9a28928`): `scripts/ops/statute_feed.sh` wrapper (modes: `--status` / `--mapped` / `--full` / `--prune` / `--tail` / `--stop`) + documented daily/weekly/prune cron lines in `scripts/ops/README.md`. Blueprints: [`rj/blueprint/2026-05-29-statute-feed-phase-b.md`](../rj/blueprint/2026-05-29-statute-feed-phase-b.md) + [`rj/blueprint/2026-05-30-statute-feed-phase-c.md`](../rj/blueprint/2026-05-30-statute-feed-phase-c.md); doc: [`docs/statute_feed.md`](../LAI/docs/statute_feed.md). ⬜ Phase C step 4 (weekend full TOC sweep, ~43 h Sun 22:00 background) + cron lines installed on the box remain — both are scheduled/ops actions, not code.
- **Where:** new `LAI/scripts/db/migrations/007_*.up/.down.sql` + the write path inside `lai.pipeline.statute_feed` (his Phase-A module) + the retrieval glue. Natural follow-on to rj's `4861a10` / `0a73f16` / `a2f975f`.
- **Do:** design + ship migration 007 — either a dedicated `statute_corpus` table or an extension to `corpus_*` carrying statute provenance (`law_slug`, `enbez`, `xml_sha256`, `fetched_at`). Implement the write path so `python -m lai.pipeline.statute_feed --apply` upserts BImSchG §§ into the retrieval corpus and surfaces them to RAG. Idempotent on `(law_slug, enbez, xml_sha256)` — re-runs are no-ops when the XML hash matches.
- **Why:** Phase A is read-only; until this lands, statute freshness can't reach RAG. The whole Phase-3 architecture ("RAG = current statute, fine-tune = reasoning") depends on it.
- **Done when:** migration applies clean up+down on a scratch DB; `--apply` for BImSchG inserts/updates rows in `lai_db`; a sample RAG query against a BImSchG § returns the corpus chunk; behind a flag/scratch DB until reviewed (touches live retrieval).
- **Collision risk:** none with our Phase 3 prep — we're in `training/fine_tuning/eval/`, not `lai.pipeline.statute_feed` or `scripts/db/`. Coordinate with us only on schema column names if the eval harness ever needs to read statute corpus rows (no current dependency).

### rj-2 — Live-box end-to-end production verification + 5-line status for the boss  (production mandate)
- **✅ DONE 2026-05-31.** Ran `scripts/ops/smoke_test.py --report` against the live box (login + RAG query + DDiQ report cycle). **All green:** serve_rag `/health` loaded + retrieval_ready, reranker on `cuda:1`, query 15.1 s wall (warm) / 28 s (cold-cache first run), DDiQ report 410 s end-to-end, status=done. **Audit ledger verified live:** 3 of 5 event types (`login` / `query` / `report`) recorded by this run; the other two (`upload` / `export`) are instrumented in deployed code (`serve_rag.py:4755` + `micro-services/ddiq_report.py:3505`, both `8ddd324`) and will fire on the next real user action. **Real finding from smoke:** retrieval is 16 s cold and 4 s warm — the +9 k feed rows from this week's backfill suggest `hnsw.ef_search` is worth a half-day re-tune before the pilot demo. 5-line boss note saved at [`rj/boss-status-2026-05-31.md`](../rj/boss-status-2026-05-31.md); smoke log under `LAI/logs/host/smoke_test_2026-05-31*.log`. Reusable creds at `LAI/.env.smoke.local` (gitignored) ready for the rj-3a cron install.
- **Where:** ops only — no app code unless verification surfaces a bug. Output is a short status note (md or chat message).
- **Do:** run vm-3's `scripts/ops/smoke_test.py --report` E2E against the live box (login → seeded query → DDiQ report cycle); verify rows land in `lai_db.audit_log` for each event type (login / query / upload / report / export); confirm serve_rag healthy + reranker on `cuda:1` + DDiQ containers up. Capture one log/screenshot of a clean full-chain run. Then write **5 lines for the boss**: what's live since the *"awful"* call, what's stuck on FE deploy, what unblocks Phase 3.
- **Why:** closes the production-mandate loop ([[project_production_mandate]]) by proof-of-running rather than proof-by-claim. A lot shipped since (v2.1.0, audit live, progress fixes, smoke test, statute feed read path) — this makes it visible. Also de-risks the 2.4 pilot demo by exercising the real user path now.
- **Done when:** smoke green E2E (or specific failure captured); audit rows visible for every event type; boss has a 5-line status.
- **Collision risk:** none — exercises production paths + reads logs/DB; touches no source files we're editing.

### rj-3 — Small ops items: install smoke-test cron + fix the always-`"running"` ternary in the V2-analyzer progress path
- **✅ DONE 2026-05-31.** (a) Smoke-test cron installed daily at 08:00, sources `LAI/.env.smoke.local` inline (password not visible in `crontab -l`), `LAI_SMOKE_MAX_S=60` to absorb cold-cache retrieval, logs to `LAI/logs/host/smoke_test_cron.log`; verified end-to-end (exit 0, login + RAG query + reranker on `cuda:1`). (b)+(c) Status-callback fix shipped — the bug was actually in `serve_rag.py:_on_progress` (not DDiQ; spec hint was slightly off): the analyzer's `_emit` carries `step`/`current`/`total`/`elapsed_s`/`percent` but no `status` key, so the hardcoded `status="running"` masked the final `step="done"`/`percent=1.0` tick until the post-analyze done-write landed → FE saw a perpetual "running" chip. Re-pin status on completion so the FE transition is immediate. Applied + ruff clean + `serve_rag` restarted `SKIP_DDIQ=1` (PID 538166, healthy, reranker on `cuda:1`). No DDiQ rebuild needed.
- **Where:** shared-box `crontab` (rj's ops); + the DDiQ analyzer/report code where the V2-analyzer status ternary lives (rj's container domain — NOT `training/fine_tuning/`).
- **Do:** (a) install the daily cron line vm-3 documented in `scripts/ops/README.md` but explicitly did not install pending rj OK. (b) The collapsed-for-lint ternary that always reports `"running"` (called out in the tracker's "Next steps") — fix so status genuinely reports `"done"` when the step completes. (c) Rebuild DDiQ after (b) so the fix is live.
- **Why:** (a) the smoke test only runs on demand right now — the cron makes it actually catch regressions overnight. (b) a real logic smell — the FE never sees "done" on that path; users see a perpetual "running" chip.
- **Done when:** cron entry installed and visible in `crontab -l`; ternary returns the real status; if rebuilt, DDiQ healthy + the status transitions on a real report run.
- **Collision risk:** none — ops + DDiQ analyzer/report container; nowhere near our `training/fine_tuning/eval/` Phase-3 prep.
