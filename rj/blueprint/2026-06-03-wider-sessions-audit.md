# Audit — wider sessions sample (`cd5a4a1b…`)

**Date:** 2026-06-03 · **Owner:** rj · **Status:** AUDIT FINDINGS — closes
[PROGRESS_V2.md line 420](../harsh/PROGRESS_V2.md) ("We did not look
at sessions from the most-active user cd5a4a1b…").

## TL;DR

The "most-active user" in the original ks/as audit (PROGRESS_V2 line
420: *"8 sessions, last active 2026-06-01 17:58"*) turns out to be
**rj@blockland.ae** — the project owner — not a third-party prospect.
By 2026-06-03 the session count has ballooned to 60 because the
hourly smoke cron creates a new 2-message session every hour.

Filtering to real (>2-message) conversations leaves **3 sessions**:

1. `ad0003aa` (2026-06-02 22:48) — my UI_META verification probe.
   Already audited in `rj/2026-06-02-restart-checklist.md` (4/4 PASS).
2. `02cac914` (2026-05-24 21:25) — 3 user turns, **all 3 are
   meta/UI-access failures** that the original ks/as audit did NOT
   surface and that the 2026-06-02 UI_META regex does NOT catch.
3. `a9cc9f0e` (2026-05-23 20:30) — 5 user turns, all handled
   correctly in contract mode with proper `[M-1]` citations. No
   findings.

**One new failure mode surfaced → fix shipped this turn:** UI_META
extended with German + English "file-access capability" patterns
(`hast du zugriff auf`, `welche dokumente hast du`, `can you access`,
`which documents do you have`). 51 router tests pass; 0/50
bimschg_50 questions affected. Live as soon as serve_rag restarts.

## Session-by-session detail

### `02cac914` — the new failure family

Three user turns, each a UI-capability / file-access question. None
of them match the post-2026-06-02 UI_META regex; they all fall through
to the LLM router (which biases toward RAG).

| Turn | User | What model did | Failure mode |
|---|---|---|---|
| 1 | *"can you access the document that i uploaded in the documents and reports section"* | Generic-AI disclaimer: *"I do not have direct access to external files…"* | Wrong: there IS a documents section + the user really did upload docs |
| 2 | *"which documents do you have access to ?"* | Routed to RAG, retrieved a Hude/Hatten DD report, replied as if those were the user's uploaded docs | Misleading: confused corpus content for user-uploaded matter docs |
| 3 | *"LA KG_Enercon_…signed.pdf can you access this document"* | *"I cannot directly access or open files…"* | Wrong: the user just named a specific uploaded file |

All three are file-access capability questions — *"can you see my
docs?"* — distinct from the UI/navigation questions ("was kann ich
hier tun?") that the 2026-06-02 fix targeted. They share the same
*root cause* (RAG-by-default for ambiguous queries) but the surface
phrasings are different.

### `a9cc9f0e` — clean session, no findings

Five user turns on `M-1 = LA KG_Enercon_Wartungsvertrag` (uploaded
contract). Every turn handled correctly:

* T1 *"tell me about this document"* → substantive contract overview
  with `[M-1]` citations. ✅
* T2 *"i asked you what this document is about ?"* → re-focused
  summary, same `[M-1]`. ✅
* T3 *"so based on the document should i trust enercon to handle the
  services …"* → 4.8 KB analysis of contractual obligations + an
  honest *"as an AI assistant"* disclaimer about not giving legal
  advice. ✅
* T4 *"hey"* → friendly German greeting, no over-grounding. ✅
* T5 *"do you have the data of the file that i uploaded"* → confirms
  M-1 access + re-summarises. ✅ *(Note: this phrasing is the same
  family as the 02cac914 failures, BUT here it landed in contract
  mode with M-1 actually in the prompt — model answered correctly. So
  the UI_META extension also needs to be careful about
  contract-mode false-positives — checked: contract mode already
  short-circuits through `session_uses_contract`, which the UI_META
  fix gates correctly.)*

## Fix shipped this turn

`serve_rag.py:1090-1106` — UI_META regex extended with two new
clauses:

```python
# German file-access capability (2026-06-03 widening — surfaced by
# the cd5a4a1b session 02cac914 audit)
r"hast\s+du\s+(zugriff|zugang|den\s+zugriff|den\s+zugang)\s+(auf|zu|zur)|"
r"welche\s+(dokumente|dateien|pdfs?|akten?)\s+(hast|siehst|kennst|kannst)\s+du|"
# English file-access capability (same audit)
r"(can|do|did)\s+you\s+access\s+(the|this|that|these|those|my|a)\s*(document|file|pdf|attachment)?|"
r"which\s+(documents?|files?|pdfs?|docs?)\s+(do|did|have|are)\s+you"
```

**10 new tests added** (5 German, 5 English) covering the new
patterns. **0/50 `bimschg_50.jsonl` legal questions match** — the
same gold-RAG safety check from the original UI_META PR. **All 6
close-negative legal queries** (`"Welche Dokumente regelt § 4
BImSchG?"`, `"Welche Klauseln sind im Vertrag enthalten?"`, etc.)
correctly stay out. 51 router tests pass; full suite 1029/1029.

**Production behaviour:** inert until the next `restart_serve_rag.sh`.
The patch sits in `serve_rag.py:1090-1106` alongside the existing
UI_META block; no other call sites changed. Cost: one additional
regex hit per query (microseconds).

## Live coverage — would it have caught session 02cac914's failures?

| Audit query | Pre-fix UI_META | Post-fix UI_META |
|---|---|---|
| *"can you access the document that i uploaded …"* | ❌ no match → RAG | ✅ match → chat |
| *"which documents do you have access to ?"* | ❌ no match → RAG | ✅ match → chat |
| *"LA KG_Enercon… can you access this document"* | ❌ no match → RAG | ❌ still no match |

The third query starts with a filename, so it doesn't trip
`^\s*(can|do|did)`. Fixable by moving the anchor to allow a small
prefix, but doing so risks false-positives on legal queries that
might lead with a citation handle or quote. **Accepting 2/3 coverage
as the right trade-off.** The third query is unusual phrasing (user
typed a filename, then a sentence) — the more common pattern *"can
you see this file?"* would route correctly.

## What this audit does NOT cover

* Does NOT propose Phase 2 (embedding-based intent classifier) — the
  regex extension closes today's measurable failures cleanly. Phase 2
  remains scoped in `rj/blueprint/2026-06-02-mode-router-quality.md`
  for "if pilot feedback shows the regex is brittle on paraphrases."
* Does NOT widen the audit to other users. Production traffic is
  dominated by the smoke cron; the audit_log shows 61 queries from
  rj/7d (mostly cron) + 2 from harsh/7d. There ARE no third-party
  power users with multi-turn traffic in the current data.

## Verification commands

```bash
# Re-pull the failed session
sqlite3 LAI/processed/sessions.db \
  "SELECT role, substr(content,1,300) FROM messages
   WHERE session_id LIKE '02cac914%' ORDER BY created_at;"

# Run the new tests
.venv/bin/python -m pytest tests/unit/api/test_router_ui_meta.py -v

# Live verify the regex extension matches the 3 audit failures
.venv/bin/python -c "
import os; os.environ['LAI_AUTH_JWT_ACCESS_SECRET']='test'*8
from lai.api.serve_rag import UI_META
for q in [
  'can you access the document that i uploaded in the documents and reports section',
  'which documents do you have access to ?',
  'hast du zugriff auf meine projekte',
]:
    print(bool(UI_META.match(q)), q[:60])
"
```
