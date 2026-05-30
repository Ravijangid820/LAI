# Retention-eval scaffold (Phase 3 prep)

This directory holds the **base-vs-fine-tune retention probe** — the missing piece
from the prior on-box LoRA attempt at `output/qwen25-7b-legal-lora/`. See
[`harsh/MODEL_COMPARISON.md`](../../../../harsh/MODEL_COMPARISON.md) — *"Prior
on-box attempt — what we learned"* — for the full analysis. TL;DR of the gap this
fills:

> In-domain `val_loss` is **blind to catastrophic forgetting**. The prior attempt's
> val_loss looked great (0.977 → 0.553, still falling) while the model was
> qualitatively collapsing toward the narrow Windenergie-Recht training template.
> A small fixed probe across general capabilities and German fluency *outside* the
> training distribution would have caught it.

## What's here

| | |
|---|---|
| `retention_probe.py` | Runner. Loads base + FT (PEFT adapter or merged checkpoint), runs the fixed prompt set through both with greedy decoding, writes `report.json` + `report.md` side-by-side with simple deltas. |
| `probes/retention_probes.jsonl` | The fixed probe set — **25 prompts**, see categories below. |
| `reports/` | Output dir (created at first run). One sub-dir per probe run. |

### Probe categories (25 prompts total)

| Category | n | What it catches |
|---|---|---|
| `de_general` | 5 | German general knowledge / style / translation — non-legal. **Catches:** DE→EN language drift, fluency loss outside the legal template. |
| `en_general` | 3 | English instructions. **Catches:** loss of English competence. |
| `de_legal_other` | 5 | BauGB / EEG / BGB / StGB — legal but **not** BImSchG. **Catches:** the FT over-narrowed to BImSchG and lost adjacent areas. |
| `de_legal_bimschg` | 3 | BImSchG-adjacent — the *target* domain. Sanity check, not the win condition (that's the §3.4 A/B). |
| `instruct_format` | 3 | Structured output (JSON / Markdown / numbered list). **Catches:** instruction-following overwritten. |
| `refusal` | 3 | Should refuse or admit insufficient context, including a **non-existent fictional statute** (`refusal_003`). **Catches:** lost "I don't know" calibration → confident fabrication. |
| `reasoning` | 3 | Simple multi-step reasoning (DE arithmetic, DE date math, EN logic). **Catches:** narrowed style that can no longer follow general logical structure. |

## Usage

PEFT adapter (preferred — no merge step needed):

```bash
python -m training.fine_tuning.eval.retention_probe \
    --base Qwen/Qwen3.6-27B \
    --ft-adapter ./training/fine_tuning/output/qwen36-27b-bimschg-lora \
    --probes    ./training/fine_tuning/eval/probes/retention_probes.jsonl \
    --out       ./training/fine_tuning/eval/reports/qwen36-bimschg-lora-step-NNNN
```

Already-merged FT model:

```bash
python -m training.fine_tuning.eval.retention_probe \
    --base Qwen/Qwen3.6-27B \
    --ft-model ./training/fine_tuning/output/qwen36-27b-bimschg-lora-merged \
    --probes ... --out ...
```

Useful flags: `--max-new-tokens` (default 256), `--dtype {bfloat16,float16,float32}` (default bf16),
`--device {cuda,cpu,cuda:0,...}` (default cuda).

Exit codes: `0` ok, `2` bad args / missing probes, `3` model load failure.

## What to look for in `report.md`

- **`de_general` answers that drift toward English** — the *DE ascii drift (FT-base)*
  column in the summary table going positive on `de_general` / `de_legal_*` is the
  clearest forgetting signal (FT replies in English to German prompts).
- **`de_legal_other` answers that get worse than base** — over-narrowing to BImSchG.
- **`instruct_format` answers that violate the requested format** — instruction
  following was overwritten by the narrow training template.
- **`refusal` answers that confidently fabricate** — especially `refusal_003`
  (a non-existent fictional statute). A FT that *invents* a § 999 answer has lost
  its calibration; that's a ship-blocker.
- **`reasoning` answers that collapse** — the FT learned a templated answer shape
  and can no longer follow general logical structure.

## How to use this in the training loop

Run the probe **every 1–2K training steps** (alongside `eval_loss`) and treat
regressions in `de_general` / `de_legal_other` / `refusal` as **stop conditions**,
not just metrics to log. The prior attempt's `load_best_model_at_end=True` chose
the lowest `val_loss` checkpoint — which on a 7B with 190K in-domain examples is
often the *worst* on retention. Either:

1. Pick `best_checkpoint` by a composite of `val_loss` + retention deltas, or
2. Stop training before retention deltas grow large (early stop on the *probe*,
   not just on val_loss).

## What this is *not*

A benchmark. 25 prompts can't rank a model — they can only catch obvious
capability collapse. For the quantitative Phase-3 ship/no-ship gate (roadmap
§3.4), use the 50 real BImSchG questions from matter logs, lawyer-labelled.
Retention probe and §3.4 A/B serve different jobs:

| Tool | Purpose | When |
|---|---|---|
| `eval_loss` (existing in `run_lora.py`) | in-domain fit | every train eval (1K steps) |
| **retention probe** (this) | catch forgetting *outside* the training distribution | every 1–2K steps |
| §3.4 A/B (50 lawyer-Qs) | quantitative ship / no-ship decision | end of run, base-Qwen vs LoRA-Qwen vs base-Gemma-4-27B |

## Why the probes are German-heavy

The product serves a German law firm on German wind/BImSchG cases. The base
model's German fluency outside the training template is the most-likely first
casualty of an over-aggressive in-domain LoRA, so the probe weights German
prompts (15 / 25) and includes non-BImSchG German legal areas (BauGB, EEG, BGB,
StGB). English (4 / 25) is kept as a sanity check that the FT didn't *lose*
English competence in the process.
