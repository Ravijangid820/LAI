# EU AI Act — coverage map (vm-7, 2026-05-31)

**Tracks:** [ROADMAP_2026Q3.md](./ROADMAP_2026Q3.md) §4.2 · [PROGRESS_V2.md](./PROGRESS_V2.md)
**Audience:** boss + 2.4 pilot conversations.
**Promise:** every "we already have X" line below points to a specific commit / file / migration. No vague claims. Gaps are listed honestly at the bottom.

Disclaimer up front: this is an engineering self-map of what we've shipped, not a formal legal AI-Act conformity assessment. The pilot firm's compliance counsel should review the categorisation (e.g. our system is most likely a **high-risk** AI system under Annex III §8(a) "AI systems intended to be used for administration of justice and democratic processes" — assisting legal advice — but that finding belongs to them). The four articles below are the ones that apply to *any* high-risk system; we map them to shipped code so the pilot can see hard evidence.

---

## Art. 12 — Record-keeping (logging)

> *"High-risk AI systems shall technically allow for the automatic recording of events ('logs') over the duration of the lifetime of the system."*

**Status: shipped + deployed.**

| Requirement (Art. 12 §1–§3) | What we shipped |
|---|---|
| Automatic event recording across lifetime | `audit_log` table + writers in **migration 006** (`LAI/scripts/db/migrations/006_audit_log.up.sql`). One shared `lai_db` table; all components write to it — `auth_router` (asyncpg) for login, `serve_rag` (psycopg2) for query + upload, DDiQ worker (psycopg2) for report + export. |
| Tamper-evident records | BEFORE-UPDATE trigger in migration 006 rejects every UPDATE → an admin who later wants to "edit" an audit row gets a database-level error. DELETE is intentionally left open to a privileged retention job (next row). |
| Retention | EU AI Act Art. 12 minimum is **6 months**; longer is a policy decision. `scripts/ops/audit_export.py` (`5abe968`, vm-4) ships the retention CLI — dry-run by default, deletes only with `--yes`, parameter-bound `DELETE FROM audit_log WHERE ts < $1`. README block (`LAI/scripts/ops/README.md`) carries the 6-month callout so an ops engineer hitting `--purge-older-than 180` understands what 180 is grounded in. |
| Identification of natural person who provided input / verified result | `user_id` + `org_id` columns on every row (nullable so failed-login probes and anonymous events still log); `ON DELETE SET NULL` keeps the trail outliving the account. See migration 006 header comment. |
| Period during which the system operated | `ts TIMESTAMPTZ NOT NULL DEFAULT NOW()` on every row → wall-clock for every event without trusting the writer. |
| Logs for verification of the operation (transparency to authorities) | Admin read endpoint `GET /admin/audit` (`d9ed39a`) + FE table at `/dashboard/admin/audit` (`c554842`) — super_admin sees every org; firm admin is scoped server-side to their own org. Filter by `action`, paginated. Bulk export for an auditor: `scripts/ops/audit_export.py` (CSV + JSON, date / action / org / user filters). |
| Best-effort writer (logging never breaks the user flow) | `lai.common.audit` (`src/lai/common/audit.py`) is best-effort: a logging failure is logged and swallowed, the request continues. Tested at 98% cov, async + sync writers. |
| Live in production | Migration 006 applied to `lai_db` 2026-05-29 14:25; serve_rag + DDiQ restarted with the audit code; `49431d8` (rj-2) verified live — login/query/report rows landed inside a 15-min window during the end-to-end smoke; upload + export wired (`8ddd324`) and fire on the next real user action. |

Instrumentation map (file:line — for the auditor who wants to see the call sites):

- login → `auth_router` (asyncpg, async writer)
- query → `src/lai/api/serve_rag.py:3970`
- upload → `src/lai/api/serve_rag.py:4772`
- report + export → `LAI/micro-services/ddiq_report.py:2212/2221/2231` (and the explicit `POST /ddiq/report/{id}/export` audit hook around line 3488)

**Gap (Art. 12 §2 b):** "events that may result in a substantial modification of the AI system" — model swaps + LoRA-adapter changes + retrieval-corpus migrations are not currently audit-logged. We have git history for code/model files, but a row in `audit_log` for "model X swapped to model Y at time T" doesn't exist yet. **Note:** add a `system_change` action type at the point of cutover.

---

## Art. 13 — Transparency & provision of information to users

> *"High-risk AI systems shall be designed and developed in such a way that their operation is sufficiently transparent to enable users to interpret a system's output and use it appropriately."*

**Status: partial.**

| Requirement | What we have | What we don't yet |
|---|---|---|
| Capabilities and limitations of the system | The retention probe (`abc15d1`, `190d371`) catches exactly the *limitation* legal users care about most: confident fabrication of a non-existent statute. vm-6 (2026-05-31) widened the sentinel from 1 fictional probe to 8 (`refusal_003` … `refusal_010`), covering Bundesgesetze, Landesgesetze, EU-Verordnungen, German + English. The hard-stop policy is conservative on purpose (only fires on the worst regression: `\d+ (Jahre\|Monate\|…)` shape in a non-existent-§ answer with no calibration phrase). | A user-facing model-card / capabilities page is **not** built yet. The retention findings live in `MODEL_COMPARISON.md` (engineer-facing). vm-7 (this doc) is the first reader-facing artifact; a pilot-facing one-pager is still ⬜. |
| Refusal calibration | `refusal_001` / `refusal_002` (harmful-act prompts) + 8 fictional-statute probes test that the model says "I don't know" when it should. Verified failure mode on the prior FT (v1 == v2 fabrication on § 999); the callback now hard-stops a training run that reproduces it. | Refusal calibration on **deployed model** is unverified — the callback only catches it during a training run. A standing daily probe on the deployed analyzer would close this; not built. |
| Foreseeable misuse warned about | DDiQ report writes carry an Ampel (red / amber / green) for risk and a `refusal_guard` step that surfaces ambiguous outputs to the user rather than burying them in confident prose (`884ea24`). | Standard "this is a decision support tool, not a substitute for legal advice" disclaimer in the FE is **not present**. Should be one line on chat + report screens. |
| Logging that supports interpretability ex-post | Slow-query telemetry (`9d516dc`): every query above `LAI_SLOW_QUERY_S` emits one JSON line with `embed/retrieve/rerank/generate/total` ms — so a slow or wrong answer can be reproduced from logs. | Per-answer provenance chain (which chunks fed the answer) is in the chat stream but not durably stored for ex-post audit. A pilot will surface this. |

---

## Art. 14 — Human oversight

> *"High-risk AI systems shall be designed and developed in such a way … that they can be effectively overseen by natural persons during the period in which the AI system is in use."*

**Status: partial; ship-gate scoped, UI in progress.**

- **Lawyer-labelled blind A/B (roadmap §3.4) is the ship-gate.** The Phase 3 LoRA does *not* go live to users unless a lawyer-blind labelling session over 50 BImSchG questions prefers it (or ties) vs the base. The criterion is in `ROADMAP_2026Q3.md` §3.4.
- **vm-9 (this session)** ships the artifact that makes §3.4 runnable: a blind A/B FE + backend (`LAI-UI/src/react-app/pages/EvalUI.tsx` + `LAI/micro-services/eval_api.py`) so the lawyer can run the 50-question session start-to-finish without seeing model names; L/R randomisation lives server-side and is never sent to the client. Until §3.4 has a pass result, no LoRA model is routed for real users.
- **Human-in-the-loop in the running product:** DDiQ reports are *drafts* surfaced for lawyer review; the chat UI keeps citations inline so the answer is checkable; there is no autonomous action / no agent that writes back to a CRM. The product is read-only from the user's matter perspective.

**Gap (Art. 14 §4 d):** an "intervene or interrupt" control mid-generation. We have FE stream cancellation (the user can close the request), but no operator-level kill-switch dashboard. A `super_admin` should be able to disable a model or route at runtime without a deploy. **Note:** small `/admin/models` toggle behind the same admin gate as `/admin/audit`.

**Gap (Art. 14 §4 e):** systematic "automation bias" warning. We don't tell the lawyer *"the model is right ~X% of the time on questions of class Y"* because we don't have those numbers yet — that's exactly what §3.4 produces. Once §3.4 lands, those numbers go on the chat screen as a calibrated confidence reminder.

---

## Art. 15 — Accuracy, robustness, cybersecurity

> *"High-risk AI systems shall be designed and developed in such a way that they achieve, in light of their intended purpose, an appropriate level of accuracy, robustness and cybersecurity."*

**Status: scaffolding in place, ground-truth numbers pending §3.4.**

### Accuracy

- **Recipe correction folded in (`MODEL_COMPARISON.md → "Recommended fine-tune recipe (playbook)"`).** The prior on-box FT (`output/qwen25-7b-legal-lora`) failed exactly the Art. 15 spirit — val_loss looked great while general capability collapsed and confident-fabrication on a non-existent § shipped. The new playbook (r=16–32, attention-only, LR ≤1e-4, 1 epoch, 30–50k curated, **5–10 % replay**, retention probe as stop signal) is the correction.
- **Retention probe (`abc15d1` + `190d371` + vm-6 widening).** 32 probes across `de_general / en_general / de_legal_other / de_legal_bimschg / instruct_format / refusal / reasoning` — with 8 specifically-crafted fictional-statute probes. Greedy decode, reproducible deltas, hard-stop on detector failures during training. This is not a benchmark; it's a regression sentinel.
- **Phase 3 §3.4 lawyer A/B is the accuracy ground-truth.** Until 50 BImSchG questions are labelled by a real lawyer, accuracy is an unverified claim. vm-9 builds the runner; the session itself is the answer.

### Robustness

- **In-domain retrieval freshness.** Phase 4.3 statute-feed (`bf516e5`, `b709f76`, `036bcbe`, `f1b9054`, `7a0de8f`, `9a28928`) keeps the BImSchG corpus tied to gesetze-im-internet.de upstream — content-hash idempotent, transactional per-law DELETE+INSERT. The Phase 3 architecture is **RAG = current statute, fine-tune = reasoning**; statute drift is bounded by what the feed lets through.
- **Hardened parsing.** XML is parsed with `defusedxml` (CI gate green, `16b31f2`); the connector layer uses `httpx + tenacity` retry + structured-log metrics (`0a73f16`). Avoids XXE + entity-expansion + retry-on-flaky behaviour as basic robustness primitives.
- **Smoke test on the live box.** `scripts/ops/smoke_test.py` (vm-1 `b7c141c` + vm-3 `290bb25`) hits `/health` → login → seeded `/query` → DDiQ report cycle, asserts < `LAI_SMOKE_MAX_S` budget AND reranker `on cuda`. Distinct exit codes for cron alerting. Installed daily via `49431d8` cron line.

### Cybersecurity

- **CI security gate (`16b31f2`).** Bandit on `lai.common` is green (14 findings → 0; B608 audited-safe, XML hardened with defusedxml).
- **Append-only audit (above)** is the breach-evidence primitive: a row that records an event cannot be silently mutated to hide a later compromise.
- **Postgres trigger + parameter-bound writes.** The whole audit write path uses bound parameters (asyncpg + psycopg2). vm-4's retention `DELETE` uses `$1` for the cutoff, not string interpolation.

**Gap (Art. 15 §3):** "appropriate level of accuracy" — we don't have a published accuracy metric yet because §3.4 has not run. That is the load-bearing missing number for an Art. 15 statement.
**Gap (Art. 15 §4):** the deployed model behind the API is not behind formal red-teaming. The retention probe is a unit-test-style sentinel, not adversarial.

---

## Open gaps (honest)

The items below are real and known. Listing them here so the pilot conversation can address them head-on instead of having them surface as surprises.

1. **No published model card.** The FT-recipe playbook, base-model rationale, and retention findings are all in `harsh/` markdown for engineers — not the pilot. **Owner:** harsh or boss; one-pager from `MODEL_COMPARISON.md`.
2. **No data-quality register for the training corpus.** Phase 3 prep folded the prior-attempt analysis (190k synthetic Q&A, no replay, no OOD eval) into a playbook, but a per-dataset card listing source, filter step, quality-score threshold, replay ratio, lawyer-spot-check rate, etc. does not exist. **Owner:** Phase 3 lead at training kickoff.
3. **No "this is decision-support, not legal advice" disclaimer in the FE.** Chat and report screens render answers as-is. One line of FE copy on each. **Owner:** LAI-UI maintainer; bundle with the next FE deploy.
4. **No automation-bias note alongside answers.** Pending §3.4 numbers (per-category accuracy).
5. **No daily refusal-calibration probe on the deployed model.** Retention probe is a training-time sentinel; we don't currently run a small daily probe against the live `:8005` analyzer. ~5 prompts × 1× daily would catch silent retrieval/serving regressions early.
6. **No `system_change` audit action.** Art. 12 §2 b — see Art. 12 gap above.
7. **No operator kill-switch dashboard.** Art. 14 §4 d — see Art. 14 gap above.
8. **No formal red-team.** Art. 15 §4 — pending pilot.
9. **No formal AI-Act conformity assessment.** This doc is engineering self-mapping. The pilot firm's compliance counsel needs to (a) confirm high-risk classification under Annex III §8 and (b) sign off the Art. 9 risk-management system, Art. 10 data governance, Art. 11 technical documentation, Art. 16 obligations of providers. Items in this doc feed *into* that work; they don't replace it.

---

## Pointers (for the engineer reading this doc later)

- **Audit subsystem:** `LAI/scripts/db/migrations/006_audit_log.up.sql` · `LAI/src/lai/common/audit.py` · `LAI/src/lai/api/serve_rag.py:3970, 4772` · `LAI/micro-services/ddiq_report.py:2212` · `LAI/scripts/ops/audit_export.py` · admin endpoint `GET /admin/audit` (`d9ed39a`) · FE `/dashboard/admin/audit` (`c554842`).
- **Retention sentinel:** `LAI/training/fine_tuning/eval/probes/retention_probes.jsonl` · `LAI/training/fine_tuning/eval/detectors.py` · `LAI/training/fine_tuning/eval/retention_callback.py` · `LAI/training/fine_tuning/eval/test_detectors.py`.
- **Statute freshness:** `LAI/scripts/db/migrations/007_statute_feed_state.up.sql` · `LAI/src/lai/pipeline/statute_feed.py` · `LAI/docs/statute_feed.md` · `LAI/scripts/ingest/fetch_gesetze.py` (vm-5 offline-fetch).
- **Smoke + cron:** `LAI/scripts/ops/smoke_test.py` · `LAI/scripts/ops/README.md` · cron installed via `49431d8`.
- **Recipe + base-model rationale:** `harsh/MODEL_COMPARISON.md`.
- **Lawyer A/B runner (when it lands):** `LAI/micro-services/eval_api.py` + `LAI-UI/src/react-app/pages/EvalUI.tsx` (vm-9 — sibling task this session).
