"""
Merge a trained LoRA adapter into the base model so inference doesn't need
to load both separately.

Loads the base in bf16 (NOT 4-bit — the merge must happen in full precision
or the adapter delta gets rounded away), merges the LoRA weights, saves to a
new directory that can be loaded like any standard causal-LM checkpoint.

Usage:
    python -m training.fine_tuning.scripts.merge_lora \\
        --adapter training/fine_tuning/output/qwen25-7b-legal-lora/checkpoint-23000 \\
        --output  models/qwen25-7b-legal-merged-v2

If --adapter is the top-level output dir (which already holds the best
checkpoint's adapter thanks to load_best_model_at_end), that also works.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

LAI_DIR = Path(__file__).resolve().parents[3]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", required=True,
                   help="Path to the adapter dir (holds adapter_config.json + adapter_model.safetensors)")
    p.add_argument("--base", default=None,
                   help="Base model path or HF id. If omitted, read from the adapter's config.")
    p.add_argument("--output", required=True, help="Where to save the merged model.")
    p.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    args = p.parse_args()

    adapter_path = Path(args.adapter).resolve()
    output_path  = Path(args.output).resolve()
    if not adapter_path.exists():
        raise SystemExit(f"Adapter not found: {adapter_path}")
    if output_path.exists():
        raise SystemExit(f"Output already exists (refusing to overwrite): {output_path}")

    # Resolve the base model
    from peft import PeftConfig
    peft_config = PeftConfig.from_pretrained(str(adapter_path))
    base_model = args.base or peft_config.base_model_name_or_path
    print(f"Base:    {base_model}")
    print(f"Adapter: {adapter_path}")
    print(f"Output:  {output_path}")
    print(f"dtype:   {args.dtype}")
    print()

    dtype = getattr(torch, args.dtype)

    print("Loading base model...")
    tok = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        dtype=dtype,
        device_map="auto",
        trust_remote_code=True,
    )

    print("Applying adapter...")
    model = PeftModel.from_pretrained(model, str(adapter_path))

    print("Merging weights...")
    model = model.merge_and_unload()

    print(f"Saving merged model to {output_path}")
    output_path.mkdir(parents=True)
    model.save_pretrained(str(output_path), safe_serialization=True)
    tok.save_pretrained(str(output_path))

    # Also copy the chat template if present
    ct = adapter_path / "chat_template.jinja"
    if ct.exists():
        shutil.copy(ct, output_path / "chat_template.jinja")

    # Sanity check
    from os import stat
    total = sum(p.stat().st_size for p in output_path.rglob("*.safetensors"))
    print(f"Done. Merged size: {total / 1024**3:.1f} GB")


if __name__ == "__main__":
    main()
