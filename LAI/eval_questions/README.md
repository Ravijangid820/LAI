# LAI eval questions

Lawyer-blind A/B evaluation set for the Phase 3 §3.4 ship-gate (BImSchG LoRA).
Runner is `LAI/micro-services/eval_api.py` (vm-9). FE is
`LAI-UI/src/react-app/pages/EvalUI.tsx`.

## Files

- `bimschg_50.jsonl` — 50 BImSchG questions, JSONL, one row per line.
  Each row: `{id, category, question, model_a_answer, model_b_answer}`.
  Answers ship **empty** — populate them before the labelling session.
- `results.json` — created on first start of the eval API. Holds the L/R
  randomisation seed, the per-question side mapping, and the lawyer's
  scores. **Do not edit by hand mid-session.**

## Question categories

- `grundlagen` — §§ 1–6 (application, definitions, principles, basic duties)
- `genehmigung_verfahren` — §§ 6–21 (permitting, modifications, procedure)
- `ueberwachung_pflichten` — §§ 22–32, 52 (non-permitted, monitoring, info duties)
- `laerm_luft_planung` — §§ 41–50 (traffic noise, air quality, planning)

## Populate model answers

Two cleanest ways. Pick whichever fits the session:

### Option A — pre-generate offline (recommended)

For each question id, generate `model_a_answer` from the base (e.g. `Qwen3.6-27B`
served on `:8005`) and `model_b_answer` from the LoRA-tuned model. Update the
JSONL in place. The eval API loads the file once on startup; restart it after a
re-populate.

A one-shot batch script lives at `LAI/scripts/eval/generate_eval_answers.py`
(create if missing — vm-9 does not ship it). The script should:

1. Open `bimschg_50.jsonl`.
2. For each row, post `question` to two endpoints (configured via env), grab
   the completion strings.
3. Write the same rows back with `model_a_answer` and `model_b_answer` filled.

Empty answers fall back to a loud placeholder in the FE so a missed populate
step is obvious before the lawyer arrives.

### Option B — live model calls (later)

If a real-time-query mode is wanted, extend `eval_api.py` with a flag that
pulls fresh answers per `GET /eval/question/{idx}`. **Caveat:** caching is
mandatory — two consecutive GETs for the same idx must return byte-identical
answers, otherwise the lawyer is comparing two different generations and the
result is meaningless.

## Run the eval API

```bash
# From the LAI/ repo root, with the venv activated.
cd LAI/micro-services
EVAL_QUESTIONS_PATH=../eval_questions/bimschg_50.jsonl \
EVAL_STATE_PATH=../eval_questions/results.json \
uvicorn eval_api:app --host 0.0.0.0 --port 18002
```

Reproducible shuffle for tests:

```bash
EVAL_SHUFFLE_SEED=42 uvicorn eval_api:app --port 18002
```

(Drop `EVAL_SHUFFLE_SEED` in production — the auto-generated seed is what
gets persisted in `results.json` and re-used on restart.)

## Lawyer-blind invariants

1. The lawyer's browser **never** receives `model_a` / `model_b` strings.
   Only `left` and `right`. The mapping stays on the server in
   `results.json`.
2. The shuffle is fixed at the first start. A restart re-loads the same
   seed and same mapping — never re-randomises mid-session.
3. Scores are last-write-wins. A misclick is corrected by re-scoring the
   same idx; no audit log of mistakes (avoids tempting selective filtering).
4. The eval API has **no auth**. Run on a LAN the lawyer is already on.
   If you ever want to expose it beyond a LAN, lock CORS and add auth first.

## After the session

```bash
# Headline: who won.
curl http://<host>:18002/eval/results

# Full deblinded export for the §3.4 write-up.
curl http://<host>:18002/eval/export.csv > bimschg_50_eval.csv
```

The CSV columns: `idx, id, category, question, model_a_answer,
model_b_answer, left_model, lawyer_choice, choice_resolved, ts`. Pull it
into pandas / Excel and stratify by `category` to see if the win/loss is
uniform or topic-dependent.

## What this is NOT

- It is **not** an accuracy benchmark — there is no gold answer. The output
  is a relative preference of *one model vs another* on the 50-question set.
- It is **not** a substitute for the retention probe
  (`LAI/training/fine_tuning/eval/`). The retention probe is a training-loop
  stop signal for catastrophic forgetting; this set is the human-judged
  ship-gate. They answer different questions, both are required.
- It is **not** a one-off. Every Phase 3 LoRA candidate runs this gate
  before going live.
