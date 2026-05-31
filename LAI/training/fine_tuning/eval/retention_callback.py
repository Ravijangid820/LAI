"""
retention_callback.py — TRL / HF-Trainer callback that runs the retention probe at every
``on_save`` and treats hard regressions (output degeneration, confident statute fabrication)
as **training-loop stop conditions**.

Why this exists: the prior on-box LoRA iterated v1 → v2 with **bit-identical fabrication**
on a non-existent § 999 ("Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des
Gesetzes"). Both runs' eval-loss looked monotonically great because the val set shared the
train distribution. The team saved a "best" checkpoint that confidently invents statute
citations — exactly the failure mode a legal product cannot ship. This callback turns the
post-hoc retention probe into a real-time training signal so the next run *can't* save a
checkpoint past the same failure unseen.

Design choices:
- Generation is greedy (``do_sample=False``) so the stop signal is reproducible.
- The base side is **precomputed once** (via ``retention_probe.py --save-base-answers``) and
  loaded from JSON — we never re-load the base into VRAM during training.
- FT generation reuses the trainer's in-memory model (no extra weights loaded). We
  temporarily ``.eval()`` it during generation and restore train mode afterwards.
- Detectors are intentionally narrow / false-positive-averse:
    - **degeneracy** (token loops / output collapse): unique 5-gram ratio < 0.20 on
      answers ≥ 30 chars. Triggers a hard stop only for ``de_general`` probes — out-of-template
      style failures are exactly what the prior attempt's val_loss couldn't see.
    - **fictional-§ fabrication**: applies only to probe IDs in ``fictional_probe_ids``
      (default ``["refusal_003"]``). Hard-stops if the answer contains a ``Frist`` duration
      (``\\d+ (Jahre|Monate|…)``) **without** any calibration phrase (``fiktiv``, ``existiert
      nicht``, …). Catches the exact v1/v2 ship-blocker.

Usage from a TRL/HF training script:

    from training.fine_tuning.eval.retention_callback import RetentionProbeCallback
    cb = RetentionProbeCallback(
        probes_path       = "./training/fine_tuning/eval/probes/retention_probes.jsonl",
        base_answers_path = "./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json",
        out_dir           = "./training/fine_tuning/eval/reports/qwen36-27b-bimschg-lora",
        max_new_tokens    = 256,
        early_stop        = True,
    )
    trainer = SFTTrainer(..., callbacks=[cb])

Generate the base answers once (per ``base × probes`` combo) via:

    python -m training.fine_tuning.eval.retention_probe \\
        --base   Qwen/Qwen3.6-27B \\
        --probes ./training/fine_tuning/eval/probes/retention_probes.jsonl \\
        --save-base-answers ./training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from transformers import TrainerCallback, TrainerControl, TrainerState, TrainingArguments

from training.fine_tuning.eval.detectors import is_degenerate, looks_like_fabricated_frist
from training.fine_tuning.eval.retention_probe import Probe, _probes_sha256, ascii_ratio, load_probes


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


@dataclass
class _Issue:
    probe_id: str
    category: str
    flags: list[str]


class RetentionProbeCallback(TrainerCallback):
    """Runs the retention probe at every ``on_save`` and optionally early-stops on
    hard regressions. See module docstring for the full design rationale."""

    def __init__(
        self,
        *,
        probes_path: str | Path,
        base_answers_path: str | Path,
        out_dir: str | Path,
        max_new_tokens: int = 256,
        early_stop: bool = True,
        fictional_probe_ids: tuple[str, ...] = ("refusal_003",),
    ) -> None:
        self.probes_path = Path(probes_path)
        self.base_answers_path = Path(base_answers_path)
        self.out_dir = Path(out_dir)
        self.max_new_tokens = max_new_tokens
        self.early_stop = early_stop
        self.fictional_probe_ids = set(fictional_probe_ids)

        # Populated in on_train_begin so a misconfigured callback fails loudly *before*
        # the run burns GPU time.
        self.probes: list[Probe] = []
        self.base_answers: dict[str, dict[str, Any]] = {}
        # Lifted from the base-answers meta — keeps base + FT generation paths in sync
        # (e.g. Qwen3 enable_thinking). Empty dict = use the tokenizer's default chat template.
        self.chat_template_kwargs: dict[str, Any] = {}

    # ---- lifecycle hooks ----

    def on_train_begin(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        **kwargs: Any,
    ) -> None:
        if not self.probes_path.exists():
            raise FileNotFoundError(f"probes file not found: {self.probes_path}")
        if not self.base_answers_path.exists():
            raise FileNotFoundError(
                f"base-answers file not found: {self.base_answers_path} — "
                f"run `python -m training.fine_tuning.eval.retention_probe --save-base-answers ...` first"
            )

        self.probes = load_probes(self.probes_path)
        if not self.probes:
            raise ValueError(f"no valid probes loaded from {self.probes_path}")

        base = json.loads(self.base_answers_path.read_text(encoding="utf-8"))
        self.base_answers = base.get("answers", {})
        base_meta = base.get("meta", {})

        # Validate the probes file hasn't changed since the base answers were computed.
        # Without this check, a silent probes-edit would invalidate every delta.
        recorded_sha = base_meta.get("probes_sha256")
        current_sha = _probes_sha256(self.probes_path)
        if recorded_sha and recorded_sha != current_sha:
            raise ValueError(
                f"probes file SHA mismatch — base answers were computed against a different "
                f"version of {self.probes_path}. Re-run --save-base-answers to refresh.\n"
                f"  recorded: {recorded_sha}\n  current:  {current_sha}"
            )

        missing = [p.id for p in self.probes if p.id not in self.base_answers]
        if missing:
            raise ValueError(
                f"{len(missing)} probe IDs missing from base answers (first 5: {missing[:5]}). "
                f"Re-run --save-base-answers to refresh."
            )

        # Lift the chat-template kwargs from the base-answers meta so FT generations
        # use the exact same prompt-formatting (e.g. Qwen3 enable_thinking) the base
        # was computed with. Silent drift here would make every delta uninterpretable.
        meta_ctk = base_meta.get("chat_template_kwargs")
        if isinstance(meta_ctk, dict):
            self.chat_template_kwargs = meta_ctk

        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Quantization / thinking-mode reported for traceability — written by the
        # extended --save-base-answers in retention_probe.py.
        q = base_meta.get("quantization", "unknown")
        et = base_meta.get("enable_thinking", "unknown")
        print(
            f"[retention-probe] armed: {len(self.probes)} probes, "
            f"early_stop={self.early_stop}, "
            f"base_quantization={q}, enable_thinking={et}, "
            f"chat_template_kwargs={self.chat_template_kwargs}, "
            f"out={self.out_dir}",
            flush=True,
        )

    def on_save(
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        **kwargs: Any,
    ) -> None:
        model = kwargs.get("model")
        tokenizer = kwargs.get("processing_class") or kwargs.get("tokenizer")
        if model is None or tokenizer is None:
            print("[retention-probe] WARN: model or tokenizer missing in on_save; skipping", flush=True)
            return

        step = state.global_step
        step_dir = self.out_dir / f"step-{step:06d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()

        was_training = model.training
        model.eval()
        try:
            ft_answers = [self._generate(model, tokenizer, p.prompt) for p in self.probes]
        finally:
            if was_training:
                model.train()

        issues, hard_stop, reasons = self._detect(ft_answers)
        self._write_report(step_dir, step, ft_answers, issues, reasons, elapsed=time.time() - t0)

        # Surface a one-line summary into the trainer's log_history so it shows up alongside
        # eval_loss in trainer_state.json — makes the probe outcome inspectable later.
        n_flagged = sum(1 for it in issues if it.flags)
        print(
            f"[retention-probe] step {step}: {n_flagged}/{len(issues)} probes flagged; "
            f"hard_stop={hard_stop}; {time.time() - t0:.1f}s",
            flush=True,
        )

        if hard_stop and self.early_stop:
            print(
                f"\n[retention-probe] !! STOPPING TRAINING at step {step} on hard regression:\n  - "
                + "\n  - ".join(reasons)
                + f"\n  see {step_dir / 'report.md'}\n",
                flush=True,
            )
            control.should_training_stop = True

    # ---- internals ----

    @torch.no_grad()
    def _generate(self, model: Any, tokenizer: Any, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        # Use the same chat_template_kwargs the base side was computed with — lifted
        # from the base-answers meta in on_train_begin. Empty dict for tokenizers /
        # baselines that don't need any (Qwen2.5, or precomputes using 'default').
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, **self.chat_template_kwargs
        )
        inp = tokenizer(text, return_tensors="pt").to(model.device)
        out = model.generate(
            **inp,
            max_new_tokens=self.max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
        gen = out[0][inp.input_ids.shape[1] :]
        return tokenizer.decode(gen, skip_special_tokens=True).strip()

    def _detect(self, ft_answers: list[str]) -> tuple[list[_Issue], bool, list[str]]:
        issues: list[_Issue] = []
        hard_stop = False
        reasons: list[str] = []
        for probe, ft in zip(self.probes, ft_answers, strict=True):
            flags: list[str] = []
            if is_degenerate(ft):
                flags.append("degenerate")
                # Stop only on de_general — that's the out-of-template category the prior
                # attempt actually collapsed on. Degeneracy on, say, de_legal_bimschg might
                # be a legitimate "short and on-template" answer.
                if probe.category == "de_general":
                    hard_stop = True
                    reasons.append(f"{probe.id} ({probe.category}): degenerate output")
            if probe.id in self.fictional_probe_ids and looks_like_fabricated_frist(ft):
                flags.append("fictional_fabrication")
                hard_stop = True
                reasons.append(f"{probe.id}: fabricated Frist for a non-existent statute")
            issues.append(_Issue(probe_id=probe.id, category=probe.category, flags=flags))
        return issues, hard_stop, reasons

    def _write_report(
        self,
        step_dir: Path,
        step: int,
        ft_answers: list[str],
        issues: list[_Issue],
        hard_stop_reasons: list[str],
        elapsed: float,
    ) -> None:
        # JSON: machine-readable, paired with base.
        results = []
        for probe, ft, issue in zip(self.probes, ft_answers, issues, strict=True):
            base_rec = self.base_answers.get(probe.id, {})
            results.append(
                {
                    "id": probe.id,
                    "category": probe.category,
                    "language": probe.language,
                    "prompt": probe.prompt,
                    "base_answer": base_rec.get("answer", ""),
                    "ft_answer": ft,
                    "base_len": base_rec.get("len", 0),
                    "ft_len": len(ft),
                    "base_ascii_ratio": base_rec.get("ascii_ratio", 1.0),
                    "ft_ascii_ratio": round(ascii_ratio(ft), 3),
                    "flags": issue.flags,
                }
            )
        (step_dir / "report.json").write_text(
            json.dumps(
                {
                    "step": step,
                    "elapsed_seconds": round(elapsed, 1),
                    "hard_stop": bool(hard_stop_reasons),
                    "hard_stop_reasons": hard_stop_reasons,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # MD: scannable. Lead with hard-stop reasons (if any) and flagged probes.
        lines: list[str] = []
        lines.append(f"# Retention probe — step {step}\n\n")
        lines.append(f"- elapsed: {elapsed:.1f}s\n")
        flagged = [r for r in results if r["flags"]]
        lines.append(f"- flagged probes: **{len(flagged)}/{len(results)}**\n")
        if hard_stop_reasons:
            lines.append("\n## HARD STOP\n\n")
            for r in hard_stop_reasons:
                lines.append(f"- {r}\n")
        if flagged:
            lines.append("\n## Flagged\n")
            for r in flagged:
                lines.append(f"\n### `{r['id']}` · {r['category']} — flags: `{', '.join(r['flags'])}`\n\n")
                lines.append(f"**Prompt:** {r['prompt']}\n\n")
                lines.append(f"**Base:**\n\n```\n{r['base_answer']}\n```\n\n")
                lines.append(f"**FT:**\n\n```\n{r['ft_answer']}\n```\n")
        (step_dir / "report.md").write_text("".join(lines), encoding="utf-8")
