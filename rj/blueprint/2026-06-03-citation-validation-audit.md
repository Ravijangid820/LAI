# Audit — `citation_validation` outcomes vs. user-facing answer quality

**Date:** 2026-06-03 · **Owner:** rj · **Status:** AUDIT FINDINGS — closes
[PROGRESS_V2.md line 421](../harsh/PROGRESS_V2.md) ("Worth a separate
audit of `citation_validation` outcomes vs. user-facing answer
quality").

## Summary

**The citation validator works correctly for its designed purpose
(catching fabricated handles).** But the original ks/as audit failure
mode it was hoped to catch — *"off-topic answer with REAL citations"* —
is **architecturally outside** what the validator can detect. That
failure mode is now closed at a different layer (UI_META routing,
shipped 2026-06-02 `0f4ce4d`).

## The validator's actual contract

[`lai.common.citation.validator.py:110`](../LAI/src/lai/common/citation/validator.py#L110):

* Inputs: `(text, allowed: set[str])`. The allowed set is `{retrieved
  source IDs} ∪ {every uploaded matter handle the session owns}`
  (serve_rag.py:3849).
* Behaviour: every handle `[C-n]` / `[M-n]` in the LLM's text that's
  NOT in `allowed` is **fabricated** — strip it, append ` (unbelegt)`
  to the containing sentence.
* Output: `CitationValidationResult` with `text` (sanitised), `emitted`
  (every handle seen), `fabricated` (handles stripped),
  `sentences_flagged` (count of sentences that got the marker).
* Wire-up: serve_rag at line 3852 runs it only on grounded turns
  (`rag_sources` non-empty), logs a `[citation]` line **only when at
  least one fabrication is detected**, and attaches the structured
  result to the API response as `CitationValidationOut` (allowed +
  emitted + fabricated + sentences_flagged).

## Production frequency — last 24 h

From `audit_log` (1779 rows in 7 d, but dominated by the hourly smoke
cron):

| Action | Count (24 h) | Notes |
|---|---|---|
| `query` | 29 | All `mode=rag`, all from the smoke user at `HH:00:15` |
| `login` | 26 | Same source (smoke cron + my 4 probes yesterday) |

From `logs/host/serve_rag.log` (since 2026-06-02 22:41 restart):

| `[citation]` events | 2 |
|---|---|
| Mode breakdown | 2 / 2 in **contract** mode (uploaded-document chat) |
| Fabricated handles per event | `['M-2','M-4']` then `['M-3','M-4','M-7','M-5','M-6']` |
| Flagged sentences | 2 and 4 |

Real-traffic sample is small because production users have been
quiet (rj 61 queries / 7d = mostly smoke; harsh 2 queries / 7d). The
two `[citation]` events both came from the same real session
`ff13887b…` two days before the restart but were still in the log
file's rotation window.

## What we found, with receipts

### Case A — validator works on its designed failure mode

Session `ff13887b…` turn 1, user *"All the pdfs got uploaded?"*. The
LLM returned a numbered list citing `[M-1]`, `[M-2]`, `[M-3]`,
`[M-4]`, `[M-5]`. Only `[M-1]`, `[M-3]`, `[M-5]` were in the allowed
set at generation time (M-2 and M-4 had not yet finished ingestion):

```
1.  **[M-1]** `05_EWE_Netzanschlussvertrag_2008.pdf` …
2. ****     `04_VRB_Darlehensvertrag_6Mio_2019.pdf` …   ← M-2 stripped
3 (unbelegt).  **[M-3]** `03_Enercon_Wartungsvertrag_2019.pdf` …
4. ****     `02_OVG_Niedersachsen_Urteil_Rueckbau_2017.pdf` …   ← M-4 stripped
5 (unbelegt).  **[M-5]** `01_Aenderungsgenehmigung_BImSchG_…` …
```

`fabricated=['M-2','M-4'], flagged_sentences=2` — exactly correct.
The `(unbelegt)` placement at items 3 and 5 is a side-effect of the
sentence-boundary regex treating `"3."` / `"5."` as terminators; the
loose split is intentional per the validator's docstring (line 14:
"abbreviation-aware splitting is intentionally NOT used here"). UX
nit, not a correctness bug.

By turn 2 (same session, ~25 min later), all 5 documents had
finished ingesting, the allowed set expanded, and `[M-2]` and
`[M-4]` were resolved cleanly — no `(unbelegt)`. The validator's
verdict is per-turn-prompt, exactly as designed.

### Case B — validator does NOT catch off-topic-with-real-citations

The exact failure mode the original ks/as audit (PROGRESS_V2:340)
spotlighted: user *"was kann ich hier tun?"* → RAG → criminal-law
lecture citing `[C-1]/[C-2]/[C-3]` to Hude/Hatten DD docs + a
2009 fraud-forum post.

Pre-UI_META trace (session `1be8d631`, ks-session-1 turn 4 from the
audit):

* User: *"was kann ich hier tun?"*
* Assistant (RAG mode): off-topic criminal-law answer
* **8 citations emitted**: `[C-1]` ×4, `[C-2]` ×1, `[C-3]` ×3 — all
  in the allowed set (they were really retrieved)
* **Only 1 sentence flagged `(unbelegt)`** — probably an extra
  `[C-?]` handle the model invented somewhere

Post-UI_META trace (session `ad0003aa`, my probe 2026-06-02 22:42):

* User: *"was kann ich hier tun?"*
* Assistant (chat mode, no RAG): friendly capabilities list in German
* **0 citations emitted**, **0 sentences flagged**

**The 7 real-but-off-topic citations in case B sailed through the
validator** because the validator's job is to catch fabricated
handles, not off-topic content. It can't be expected to evaluate
whether a chunk's content fits the question — only whether the
emitted handle resolves to a real chunk in the prompt.

## Conclusion — three crisp findings

1. **Citation validator is correctly implemented and correctly wired.**
   It catches fabricated handles per its docstring contract; the
   `[citation]` log + the `CitationValidationOut` API field both fire
   when it should.
2. **It does NOT detect the ks/as off-topic-with-real-citations
   failure mode.** That's outside its designed scope — the validator
   sees handle resolution, not topic relevance. A 600-word
   criminal-law lecture with eight real `[C-n]` handles to real fraud-
   forum chunks passes citation validation cleanly.
3. **The off-topic failure mode is now closed at a different
   layer** — the UI_META mode-router (serve_rag.py:1054, shipped
   `0f4ce4d` 2026-06-02 22:41). Post-fix, that exact query routes to
   chat mode and the validator never runs (no `rag_sources` → guard
   skips it).

## What this audit does NOT do

* Does not change the validator — it's working as specified.
* Does not propose a "topic relevance validator" — that's the
  reranker's territory and would duplicate its score signal
  imperfectly.
* Does not address `(unbelegt)` placement quirks (the `"3."`
  artefact in Case A) — UX nit, not a correctness bug, and changing
  the sentence-boundary regex risks subtle regressions.

## Recommended follow-on (if pilot turns this into a live concern)

If a pilot lawyer surfaces a real off-topic-with-real-citations
failure that survives UI_META routing, the architecturally right fix
is at the **answer-grounding layer** — a post-generation sanity
check that the retrieved chunks' topic distribution matches the
LLM's answer's topic. Could lean on the existing reranker score
(`Qwen3-Reranker-8B`'s `(query, answer)` log-odds) as a confidence
floor below which the answer is held back. **Not building until
asked for** — speculative, would add latency, and the UI_META fix
removes the canonical case.

## Verification commands (anyone can re-run)

```bash
# count [citation] log events since the last restart
grep -c '\[citation\]' LAI/logs/host/serve_rag.log

# query frequency from audit_log
psql -c "SELECT action, COUNT(*) FROM audit_log
         WHERE ts > NOW() - INTERVAL '24 hours'
         GROUP BY action;"

# repro the pre-fix off-topic case (pre-restart sessions only):
sqlite3 LAI/processed/sessions.db \
  "SELECT role, content FROM messages WHERE session_id='1be8d631-…' ORDER BY created_at;"
```
