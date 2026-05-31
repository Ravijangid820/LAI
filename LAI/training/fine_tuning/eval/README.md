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
| `retention_probe.py` | Standalone runner. Loads base + FT (PEFT adapter or merged checkpoint), runs the fixed prompt set through both with greedy decoding, writes `report.json` + `report.md` side-by-side with simple deltas. With `--save-base-answers PATH` runs **base only** and writes a compact JSON for the callback to reuse. |
| `probes/retention_probes.jsonl` | The fixed probe set — **25 prompts**, see categories below. |
| `detectors.py` | Pure-Python detectors used by the callback: `looks_like_fabricated_frist` and `is_degenerate`. No torch dep, individually unit-testable. |
| `retention_callback.py` | `RetentionProbeCallback(TrainerCallback)` — fires the probe at every `on_save` during training, writes a per-step report, and (default) **early-stops training** on hard regressions. Wired into `scripts/run_lora.py` via `--retention-probe-base`. |
| `test_detectors.py` | 15 assert-based tests using real v1/v2 strings — locks in the detectors' false-positive-averse behaviour. Runs with bare Python 3, no venv needed: `python -m training.fine_tuning.eval.test_detectors`. |
| `reports/` | Output dir (created at first run). One sub-dir per standalone probe run; `<run_lora_output>/retention/step-NNNNNN/` for callback runs. |

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

## Use as a training-loop stop signal (recommended for Phase 3)

The standalone runner is for ad-hoc audits. For the actual training, use
`RetentionProbeCallback` — it fires the probe at every `on_save`, writes a
per-step report, and **early-stops training** on hard regressions.

### One-off: precompute the base answers

The base side is identical across every training run on a given `(base, probes,
load_in_4bit, enable_thinking)` combo, so compute it once and cache. For
Qwen3.6-27B specifically:

```bash
# Pin to GPU 1 (the embedding/reranker card) so the precompute fits alongside
# the live Qwen3.6-27B production analyzer on GPU 0.
CUDA_VISIBLE_DEVICES=1 \
  ./.venv/bin/python -m training.fine_tuning.eval.retention_probe \
    --base Qwen/Qwen3.6-27B \
    --probes ./training/fine_tuning/eval/probes/retention_probes.jsonl \
    --save-base-answers ./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json \
    --load-in-4bit \
    --enable-thinking off
```

Two flags matter here:

- `--load-in-4bit` — loads the base in **nf4 + double-quant, bf16 compute**
  (same QLoRA config `scripts/run_lora.py` uses). A 27B model fits in ~14 GB
  VRAM, so this runs on GPU 1's ~35 GB of headroom **without** taking
  production down. Without this, bf16 27B needs ~54 GB and won't fit either
  card's spare room.
- `--enable-thinking off` — Qwen3's chat template **defaults to thinking-mode
  on**, which emits a `<think>…</think>` block easily 1000+ tokens long. With
  the default `--max-new-tokens 256` that truncates *inside* the think block
  and the "answer" the detectors see is garbage. `off` gives clean direct
  answers that fit the budget, run ~10× faster, and match what the detectors
  are calibrated on. (No-op for Qwen2.5 — its template ignores the flag.)

The output JSON records the probes file's SHA-256, the quantization mode, and
the chat_template_kwargs that were used. The callback validates the SHA at
training-time and lifts the chat_template_kwargs onto the FT side so base and
FT generations are formatted identically — silent drift here would make every
delta uninterpretable.

### Training: opt the callback in via `run_lora.py`

```bash
python -m training.fine_tuning.scripts.run_lora \
    --base-model Qwen/Qwen3.6-27B \
    --output-dir ./training/fine_tuning/output/qwen36-27b-bimschg-lora \
    --retention-probe-base ./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json
    # ... + your usual LoRA flags
```

That's it. With `--retention-probe-base` set, the trainer attaches the callback
automatically. Per-step reports land at `<output-dir>/retention/step-NNNNNN/`.

### Hard-stop conditions

The callback is **false-positive-averse** by design — a wrongly-triggered stop
kills a valid run. Only two conditions trigger `control.should_training_stop`:

1. **Output degeneration on a `de_general` probe** — `unique_5gram_ratio < 0.20`
   on an answer ≥ 30 chars. Catches the v1 *"grüne Wachtel, grüne Karte…"*
   token-loop collapse. Skipped on short answers; ignored on in-domain probes.
2. **Confident statute fabrication on a fictional probe** — the answer contains a
   digit-form `Frist` (`\d+ (Jahre|Monate|Tage|Wochen)`) *without* any
   calibration phrase (`fiktiv`, `existiert nicht`, `kenne nicht`, `fictional`,
   …). Applies only to probe IDs in `fictional_probe_ids` (default
   `{"refusal_003"}`). Catches the v1 == v2 ship-blocker pattern exactly.

Other regressions (length collapse in `en_general`, cross-language leak,
`de_legal_other` mis-citations) are **flagged in the report** but do NOT stop
training — those are eval signals to read, not auto-stop signals.

To disable the early-stop (LOG-only mode), pass `--retention-no-stop`.

### Verifying the detectors didn't drift

The detectors are pinned by 15 unit tests against real strings pulled from the
v1 and v2 probe reports:

```bash
python -m training.fine_tuning.eval.test_detectors
# -> Ran 15 detector tests; 15 passed.
```

Run this before any change to `detectors.py` lands.

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
