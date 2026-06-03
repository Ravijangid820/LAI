# Pilot readiness self-check — honest internal view

**Status:** INTERNAL. Read this before promising anything to a firm.
The point is to make sure the boss doesn't claim something in a
pitch meeting that the engineering side hasn't shipped.

This extends the EU AI Act open-gaps list at `harsh/EU_AI_ACT.md`
with operational specifics — response time, throughput, data
segregation, what "production-ready" actually means today.

---

## Day 1 of a pilot — what works in production

These I have personally verified live against the running `:18000` in
the last 24 hours (post the 23:00 restart). Citing them is safe.

### Core retrieval + generation
* ✅ **End-to-end RAG query path** — login → seed session → query →
  reranker → LLM → cited answer with stable `[C-n]` handles. Working
  smoke at 13.9 s wall on the canonical BImSchG question.
* ✅ **Contract mode** — uploaded matter docs surface as `[M-n]`
  handles, isolated per session. Verified on a real session (T1-T5
  of `a9cc9f0e`).
* ✅ **Mode routing** — UI/meta questions short-circuit to chat;
  file-access capability questions also short-circuit (today's fix).
  Verified live 4/4 + 4/4 probes.
* ✅ **Language detection** — German questions get German answers;
  English questions get English. Verified live.
* ✅ **Citation validator** — strips fabricated handles, marks the
  sentence `(unbelegt)`. Working on its designed contract per the
  citation audit shipped today.

### Document handling
* ✅ **Upload pipeline** — resumable uploads via tus 1.0, multi-file
  matter view, per-document status tracking
* ✅ **Document analysis** — Qwen3.6-27B analyzer running on `:8005`,
  loaded since 2026-05-27 (week-long uptime before today's restart
  did NOT touch it)
* ✅ **DDiQ report generator** — structured DD output, 410 s end-to-
  end on a real contract (`LA KG_Enercon_Wartungsvertrag`)
* ✅ **DOCX export** — German labels, firm-letterhead placeholder
  (you customise per pilot firm)

### Audit + compliance
* ✅ **Audit log (EU AI Act Art. 12)** — every login, query, upload,
  report, export append-only logged. 6-month minimum retention
  configurable.
* ✅ **CSV/JSON export for DPO** — `scripts/ops/audit_export.py`
  filtered by date, action, user, org
* ✅ **Append-only at the DB layer** — Postgres trigger blocks
  UPDATE, only privileged DELETE for retention
* ✅ **Admin UI** — `/dashboard/admin/audit` with action filter +
  paging, admin-gated

### Operations
* ✅ **Hourly smoke cron** — catches process outages within 1 hour
  (was 24 h until yesterday's tightening)
* ✅ **Persistent sessions** — sessions survive `restart_serve_rag.sh`
  (verified twice: 126 → 126 and 152 → 152 across the last two
  restarts)
* ✅ **Statute feed** — daily refresh of 29 wind-relevant federal
  laws + weekly full TOC sweep + weekly prune. Cron lines installed
  on the box; first weekly-full sweep fires Sun 2026-06-07 22:00.
* ✅ **Lawyer-blind A/B eval infrastructure** — `/eval` route + 50
  BImSchG seed questions + pre-generated answers ready. Not yet run
  with a real lawyer.

## Day 1 — what we have but doesn't run live yet

These exist in code or staging; activating them requires a finite
amount of work I can quote, but they're not currently in the
production path.

| Capability | What it is | What's needed to activate | Effort estimate |
|---|---|---|---|
| systemd-managed serve_rag | Auto-restart on crash + auto-start at boot | `sudo bash LAI/scripts/ops/systemd/install.sh` | ks_admin's sudo, 5 min |
| Phase 3 LoRA training | BImSchG-specialised fine-tune of Qwen3.6-27B | Pilot training data + 5-6 GPU hours | 5-7 days post-pilot data |
| Mode-router Phase 2 (embedding classifier) | Replaces brittle regex with a real classifier | Labelled training set; only if regex breaks in pilot | 2-3 days |
| On-prem deployment | Docker compose targeting customer infrastructure | Pilot firm's IT-sicherheit cooperation + 2 weeks setup buffer | 2 weeks per first deployment |

## Day 30 of a pilot — known unknowns

Things we'll learn we need only when a real lawyer uses the system.
**Do not claim these are ready** in the pitch:

* **Print/PDF rendering of DDiQ reports** — DOCX export works; PDF
  export and print layout are untested in adversarial conditions
* **Concurrency under load** — verified 600-req synth burst (1225
  req/s, p99 48 ms); have NOT tested 5 lawyers × 100 queries × parallel
* **Long-document handling** — chunks at 3000 chars work; 500-page
  contracts are sandbox-tested but not pilot-validated
* **Mixed-language matters** — German contract with English annex is
  handled by the language detector at query level; document-level
  language mixing under uncertain testing
* **PII / personal data redaction in audit logs** — audit log
  captures `payload` JSON; if a query contains PII, it gets logged
  verbatim. Pilot firm's DPO needs to know this; we may need to
  add masking before commercial use
* **Failover and disaster recovery** — sessions persist; if pgvector
  or SQLite goes down mid-query, the user sees a 500 with no graceful
  message. Hardening pre-commercial.

## Day 90 — explicit Phase 2 gaps (the EU AI Act 9-list, restated)

Per `harsh/EU_AI_ACT.md`, these are pre-commercial blockers, NOT
pilot blockers:

* No formal conformity assessment (EU AI Act high-risk system
  obligation)
* No user-facing model card (Art. 13 transparency)
* No data-quality register for the corpus
* No FE decision-support disclaimer in the chat UI
* No daily refusal probe on the deployed model
* No `system_change` audit action (e.g. when admin changes the
  retrieval config)
* No operator kill-switch / emergency stop
* No formal red-team exercise
* No formal model card published

We can pilot WITHOUT these. We cannot sign a commercial contract for
production legal work without them. The pilot conversation should
make that clear so the prospect's general counsel doesn't surprise
us at week 6.

## Hard-limit calibration — what NOT to commit to in a pitch

Per the engineering work to date, here's where the bar lives:

| Don't claim | Because |
|---|---|
| "Industry-leading accuracy" | We've measured one metric (R@30 = 0.49) on one val set; "industry-leading" needs benchmarks. |
| "Trained on German wind-energy law" | Phase 3 isn't done yet. The base model is Qwen3.6-27B, not LoRA-tuned. |
| "Full BImSchG coverage" | We have 29 laws in the corpus; "BImSchG" is one of them but a real corpus would include hundreds. |
| "Zero hallucinations" | Citation validator catches handle hallucinations; not concept hallucinations. |
| "GDPR-compliant out of the box" | We have audit logs; we don't have a formal DPIA, DPA template, or sub-processor agreement. |
| "Available 24/7 with 99.99 % uptime" | We have hourly smoke cron + manual restart. 99 % is plausible; 99.99 % requires HA setup we don't have. |
| "Replaces lawyer judgment" | We assist; the lawyer signs. Day 1 messaging matters. |

## What boss IS safe to say

These are claims I'd defend in a hostile general-counsel conversation:

* "We measured our retrieval ceiling at Recall@30 = 0.49 on 200 real
  BImSchG questions and we know the failure modes."
* "Every login, query, and report export is append-only audit logged
  per EU AI Act Art. 12 — we can show the database schema and the
  retention trigger."
* "We've found and fixed three production routing bugs in the last
  72 hours; the fix lifecycle is repository-visible."
* "Phase 3 LoRA training is engineered and ready; we deliberately
  haven't trained yet because we don't want to train on synthetic
  data when a pilot firm's real matter docs would produce a much
  better fine-tune."
* "We deploy in the EU; on-prem option available for IT-sicherheit-
  strict firms."
* "We're a small team. The pilot lawyer who reports a bug talks to
  the engineer who fixes it. That's not going to scale forever, but
  it's the right shape for a pilot."

## When to STOP a pilot conversation

If any of these come up, escalate before continuing:

* Firm wants source-code escrow → commercial contract, not pilot
* Firm wants AI-warranty against lawyer mis-advice → can't sign that
* Firm wants exclusivity (no other pilots) → declined
* Firm wants to bring their own model → not our product
* Firm wants formal conformity assessment IN the pilot → not in scope

Most prospects will not raise these. If one does, the conversation
has shifted from "pilot" to "commercial procurement", and the
pricing + terms work changes accordingly.
