"""
retention_probe.py — base-vs-fine-tune capability probe against a fixed set.

WHY this exists: in-domain ``val_loss`` (the only metric the prior on-box LoRA tracked)
is blind to catastrophic forgetting. A fine-tune can drive val_loss down while losing
general capability, German fluency outside the training template, instruction
following, and refusal calibration. This probe complements val_loss by running a
small, fixed, curated prompt set through BOTH the base and the fine-tuned model
with **greedy decoding** (reproducible) and reporting side-by-side answers plus
simple deltas.

This is **not** a generative-quality benchmark — it's a regression sentinel:
"did the FT model lose anything obvious vs base on capabilities outside the
training distribution?"

See ./README.md for the rationale, the prior-attempt analysis it addresses, and
how to read the report.

Usage:

    # PEFT adapter (preferred — no merge step):
    python -m training.fine_tuning.eval.retention_probe \\
        --base Qwen/Qwen3.6-27B \\
        --ft-adapter ./output/qwen36-27b-bimschg-lora \\
        --probes ./training/fine_tuning/eval/probes/retention_probes.jsonl \\
        --out    ./training/fine_tuning/eval/reports/qwen36-bimschg-lora

    # Already-merged FT model:
    python -m training.fine_tuning.eval.retention_probe \\
        --base Qwen/Qwen3.6-27B \\
        --ft-model ./output/qwen36-27b-bimschg-lora-merged \\
        --probes ... --out ...

Exit codes:
    0 — probe completed, report written.
    2 — bad arguments / missing probes file / no valid probes.
    3 — model load failure.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


@dataclass
class Probe:
    id: str
    category: str
    prompt: str
    language: str
    notes: str = ""
    # When True, the RetentionProbeCallback treats this probe as a
    # "fictional-statute" probe and runs the looks_like_fabricated_frist
    # detector on it. Lets vm-6 add refusal_004..N without code changes —
    # just set "fictional": true in the JSONL row.
    fictional: bool = False


@dataclass
class ProbeResult:
    id: str
    category: str
    language: str
    prompt: str
    base_answer: str
    ft_answer: str
    base_len: int
    ft_len: int
    equal: bool
    base_ascii_ratio: float
    ft_ascii_ratio: float


def load_probes(path: Path) -> list[Probe]:
    probes: list[Probe] = []
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                d = json.loads(s)
                probes.append(
                    Probe(
                        id=d["id"],
                        category=d["category"],
                        prompt=d["prompt"],
                        language=d["language"],
                        notes=d.get("notes", ""),
                        fictional=bool(d.get("fictional", False)),
                    )
                )
            except Exception as e:
                print(f"WARN: bad probe at line {i + 1}: {e}", file=sys.stderr)
    return probes


def ascii_ratio(s: str) -> float:
    if not s:
        return 1.0
    return sum(1 for ch in s if ord(ch) < 128) / len(s)


def _bnb_4bit_config(compute_dtype: torch.dtype) -> BitsAndBytesConfig:
    """Match the QLoRA config used in ``scripts/run_lora.py`` so the precomputed
    base baseline is loaded the same way the eventual FT training loads its base."""
    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )


def _load_base(
    path_or_name: str,
    device: str,
    dtype: torch.dtype,
    *,
    load_in_4bit: bool = False,
):
    tok = AutoTokenizer.from_pretrained(path_or_name, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    kwargs: dict[str, Any] = {"device_map": device, "trust_remote_code": True}
    if load_in_4bit:
        kwargs["quantization_config"] = _bnb_4bit_config(dtype)
    else:
        kwargs["torch_dtype"] = dtype
    model = AutoModelForCausalLM.from_pretrained(path_or_name, **kwargs)
    model.eval()
    return tok, model


def _load_ft_adapter(
    base: str,
    adapter_dir: str,
    device: str,
    dtype: torch.dtype,
    *,
    load_in_4bit: bool = False,
):
    # Lazy import: PEFT only needed in the adapter path.
    from peft import PeftModel

    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    kwargs: dict[str, Any] = {"device_map": device, "trust_remote_code": True}
    if load_in_4bit:
        kwargs["quantization_config"] = _bnb_4bit_config(dtype)
    else:
        kwargs["torch_dtype"] = dtype
    base_model = AutoModelForCausalLM.from_pretrained(base, **kwargs)
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    model.eval()
    return tok, model


@torch.no_grad()
def generate_one(
    tok,
    model,
    prompt: str,
    max_new_tokens: int,
    *,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> str:
    """Generate one greedy answer.

    ``chat_template_kwargs`` are forwarded to ``tok.apply_chat_template`` so the
    caller can toggle e.g. Qwen3's ``enable_thinking`` flag. Unknown keys are
    silently ignored by tokenizers whose template doesn't reference them
    (Qwen2.5 ignores ``enable_thinking``), so the same kwarg dict is safe across
    base / FT pairs."""
    messages = [{"role": "user", "content": prompt}]
    template_kwargs = chat_template_kwargs or {}
    text = tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, **template_kwargs
    )
    inp = tok(text, return_tensors="pt").to(model.device)
    out = model.generate(
        **inp,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        pad_token_id=tok.pad_token_id,
    )
    return tok.decode(out[0][inp.input_ids.shape[1] :], skip_special_tokens=True).strip()


def run_side(
    tok,
    model,
    probes: list[Probe],
    max_new_tokens: int,
    label: str,
    *,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> list[str]:
    out: list[str] = []
    t0 = time.time()
    for i, p in enumerate(probes):
        if i == 0 or (i + 1) % 5 == 0:
            print(f"  [{label}] {i + 1}/{len(probes)} ({time.time() - t0:.1f}s)", flush=True)
        out.append(
            generate_one(tok, model, p.prompt, max_new_tokens, chat_template_kwargs=chat_template_kwargs)
        )
    print(f"  [{label}] done in {time.time() - t0:.1f}s", flush=True)
    return out


def write_reports(
    probes: list[Probe],
    base_ans: list[str],
    ft_ans: list[str],
    outdir: Path,
    meta: dict,
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    results: list[ProbeResult] = []
    for p, b, f in zip(probes, base_ans, ft_ans, strict=True):
        results.append(
            ProbeResult(
                id=p.id,
                category=p.category,
                language=p.language,
                prompt=p.prompt,
                base_answer=b,
                ft_answer=f,
                base_len=len(b),
                ft_len=len(f),
                equal=(b.strip() == f.strip()),
                base_ascii_ratio=round(ascii_ratio(b), 3),
                ft_ascii_ratio=round(ascii_ratio(f), 3),
            )
        )

    # Machine-readable
    (outdir / "report.json").write_text(
        json.dumps({"meta": meta, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Human-scannable markdown
    lines: list[str] = []
    lines.append(f"# Retention probe — {meta.get('base', '?')} vs FT\n\n")
    lines.append(f"- probes: **{len(results)}**\n")
    lines.append(f"- generated: {meta.get('timestamp', '')}\n")
    lines.append(f"- max_new_tokens: {meta.get('max_new_tokens', '?')}\n")
    lines.append(f"- dtype: {meta.get('dtype', '?')}\n\n")

    # Category summary
    cats: dict[str, list[ProbeResult]] = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)
    lines.append("## Summary by category\n\n")
    lines.append("| category | n | equal | avg len base | avg len FT | DE ascii drift (FT-base) |\n")
    lines.append("|---|---|---|---|---|---|\n")
    for c, rs in sorted(cats.items()):
        n = len(rs)
        eq = sum(1 for r in rs if r.equal)
        avg_b = sum(r.base_len for r in rs) / n
        avg_f = sum(r.ft_len for r in rs) / n
        de = [r for r in rs if r.language == "de"]
        drift = (
            f"{(sum(r.ft_ascii_ratio - r.base_ascii_ratio for r in de) / len(de)):+.3f}"
            if de
            else "—"
        )
        lines.append(f"| {c} | {n} | {eq}/{n} | {avg_b:.0f} | {avg_f:.0f} | {drift} |\n")

    # Per-probe paired
    lines.append("\n## Per probe\n")
    for r in results:
        lines.append(f"\n### `{r.id}` · {r.category} · `{r.language}`\n\n")
        lines.append(f"**Prompt:**\n\n> {r.prompt}\n\n")
        lines.append(f"**Base:**\n\n```\n{r.base_answer}\n```\n\n")
        lines.append(f"**FT:**\n\n```\n{r.ft_answer}\n```\n")

    (outdir / "report.md").write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {outdir / 'report.json'}")
    print(f"Wrote {outdir / 'report.md'}")


def _probes_sha256(path: Path) -> str:
    """SHA-256 of the probes file. Used to validate precomputed base answers
    were generated against the same prompt set the callback will reuse."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def write_base_answers(
    probes: list[Probe],
    base_ans: list[str],
    out_path: Path,
    meta: dict,
) -> None:
    """Write a compact base-only artifact for the RetentionProbeCallback to read.

    Schema (small and stable so the callback can rely on it):
        {"meta": {...}, "answers": {probe_id: {"answer": str, "len": int, "ascii_ratio": float}}}
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "answers": {
            p.id: {
                "answer": ans,
                "len": len(ans),
                "ascii_ratio": round(ascii_ratio(ans), 3),
            }
            for p, ans in zip(probes, base_ans, strict=True)
        },
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Base-vs-FT retention probe — see ./README.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base", required=True, help="Base model name or path (e.g. Qwen/Qwen3.6-27B)")
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--ft-adapter", help="PEFT adapter dir (loaded on top of --base)")
    g.add_argument("--ft-model", help="Merged FT model dir (loaded directly)")
    p.add_argument("--probes", required=True, help="Path to retention_probes.jsonl")
    p.add_argument(
        "--out",
        help="Output dir (writes report.json + report.md). Required UNLESS --save-base-answers is set.",
    )
    p.add_argument(
        "--save-base-answers",
        metavar="PATH",
        help=(
            "Run BASE only and write a compact base-answers JSON to PATH. "
            "Used by RetentionProbeCallback to avoid re-loading the base every save step. "
            "When set, --ft-adapter/--ft-model are ignored and --out is not required."
        ),
    )
    p.add_argument("--max-new-tokens", type=int, default=256)
    p.add_argument("--device", default="cuda")
    p.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    p.add_argument(
        "--load-in-4bit",
        action="store_true",
        help=(
            "Load base + FT in 4-bit (nf4 + double-quant, bf16 compute) — matches the QLoRA "
            "config in scripts/run_lora.py. Fits a 27B model in ~14 GB VRAM, so the precompute "
            "can run alongside production on a single 96 GB Blackwell. Persisted in the "
            "base-answers meta so the callback knows the baseline was 4-bit."
        ),
    )
    p.add_argument(
        "--enable-thinking",
        choices=("default", "on", "off"),
        default="default",
        help=(
            "Qwen3 chat-template thinking-mode toggle (passed via chat_template_kwargs to "
            "apply_chat_template). 'default' = the tokenizer's built-in (on for Qwen3). For "
            "the retention probe baseline, prefer 'off': cleaner outputs, fits in "
            "--max-new-tokens, faster, and the detectors are calibrated against direct "
            "answers (not <think> traces). Persisted in the base-answers meta so the callback "
            "applies the same setting to the FT side at training time. No-op for tokenizers "
            "whose template doesn't reference this variable (e.g., Qwen2.5)."
        ),
    )
    args = p.parse_args()

    # Build the chat-template kwargs dict that will be (a) used here for generation
    # and (b) persisted in the base-answers meta so the callback re-applies it.
    chat_template_kwargs: dict[str, Any] = {}
    if args.enable_thinking == "on":
        chat_template_kwargs["enable_thinking"] = True
    elif args.enable_thinking == "off":
        chat_template_kwargs["enable_thinking"] = False
    # "default" → empty dict → tokenizer default behaviour

    base_only = bool(args.save_base_answers)
    if not base_only and not (args.ft_adapter or args.ft_model):
        print(
            "ERROR: pass --ft-adapter PATH or --ft-model PATH (or use --save-base-answers PATH "
            "to write a base-only artifact).",
            file=sys.stderr,
        )
        return 2
    if not base_only and not args.out:
        print("ERROR: --out is required unless --save-base-answers is set.", file=sys.stderr)
        return 2

    probes_path = Path(args.probes)
    if not probes_path.exists():
        print(f"ERROR: probes file not found: {probes_path}", file=sys.stderr)
        return 2
    probes = load_probes(probes_path)
    if not probes:
        print("ERROR: no valid probes loaded", file=sys.stderr)
        return 2
    print(f"Loaded {len(probes)} probes")

    dtype = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }[args.dtype]

    quantization = "4bit_nf4" if args.load_in_4bit else "none"

    print(f"\n=== Loading BASE: {args.base} ({quantization}) ===")
    try:
        tok_b, model_b = _load_base(args.base, args.device, dtype, load_in_4bit=args.load_in_4bit)
    except Exception as e:
        print(f"ERROR loading base: {e}", file=sys.stderr)
        return 3
    base_ans = run_side(
        tok_b, model_b, probes, args.max_new_tokens, "base",
        chat_template_kwargs=chat_template_kwargs,
    )
    del model_b, tok_b
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    if base_only:
        meta = {
            "base": args.base,
            "probes_path": str(probes_path),
            "probes_sha256": _probes_sha256(probes_path),
            "max_new_tokens": args.max_new_tokens,
            "dtype": args.dtype,
            "quantization": quantization,
            "enable_thinking": args.enable_thinking,
            "chat_template_kwargs": chat_template_kwargs,
            "n_probes": len(probes),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_base_answers(probes, base_ans, Path(args.save_base_answers), meta)
        print("\nBase-only run complete. Skipped FT side.")
        return 0

    if args.ft_adapter:
        print(f"\n=== Loading FT (adapter on base): {args.ft_adapter} ({quantization}) ===")
        try:
            tok_f, model_f = _load_ft_adapter(
                args.base, args.ft_adapter, args.device, dtype, load_in_4bit=args.load_in_4bit
            )
        except Exception as e:
            print(f"ERROR loading FT adapter: {e}", file=sys.stderr)
            return 3
    else:
        print(f"\n=== Loading FT (merged): {args.ft_model} ({quantization}) ===")
        try:
            tok_f, model_f = _load_base(
                args.ft_model, args.device, dtype, load_in_4bit=args.load_in_4bit
            )
        except Exception as e:
            print(f"ERROR loading FT model: {e}", file=sys.stderr)
            return 3
    ft_ans = run_side(
        tok_f, model_f, probes, args.max_new_tokens, "ft",
        chat_template_kwargs=chat_template_kwargs,
    )
    del model_f, tok_f
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    meta = {
        "base": args.base,
        "ft_adapter": args.ft_adapter,
        "ft_model": args.ft_model,
        "max_new_tokens": args.max_new_tokens,
        "dtype": args.dtype,
        "quantization": quantization,
        "enable_thinking": args.enable_thinking,
        "chat_template_kwargs": chat_template_kwargs,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_reports(probes, base_ans, ft_ans, Path(args.out), meta)
    print(
        "\nDone. Inspect report.md — focus on (a) DE ascii drift in `de_general` / `de_legal_*`, "
        "(b) `de_legal_other` getting worse than base (over-narrowing), "
        "(c) `refusal` confidently fabricating (lost 'I don't know')."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
