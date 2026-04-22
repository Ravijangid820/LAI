"""
LoRA fine-tune of Qwen2.5-7B-Instruct on the German legal dataset.

Input:
    training/fine_tuning/data/train.jsonl
    training/fine_tuning/data/val.jsonl
    (produced by training/fine_tuning/scripts/export_training_data.py)

Output:
    training/fine_tuning/output/qwen25-7b-legal-lora/
        - adapter_model.safetensors  (the LoRA adapter)
        - checkpoint-<step>/         (intermediate)
        - trainer_state.json

Standard TRL SFTTrainer + PEFT LoRA. No Unsloth dep. Works with a single
RTX Pro 6000 (97 GB): 4-bit base + bf16 LoRA sits in ~18 GB.

Usage (single GPU):
    CUDA_VISIBLE_DEVICES=0 \\
    python -m training.fine_tuning.scripts.run_lora --epochs 1

Common flags:
    --base-model Qwen/Qwen2.5-7B-Instruct
    --max-seq-len 4096
    --per-device-batch 2 --grad-accum 8  (effective batch = 16)
    --lr 2e-4 --lora-r 128 --lora-alpha 256
    --output-dir ./training/fine_tuning/output/qwen25-7b-legal-lora
    --resume  (from the latest checkpoint)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer


LAI_DIR     = Path(__file__).resolve().parents[3]
DATA_DIR    = LAI_DIR / "training" / "fine_tuning" / "data"
DEFAULT_OUT = LAI_DIR / "training" / "fine_tuning" / "output" / "qwen25-7b-legal-lora"


def _pick_attn_impl() -> str:
    """Return the fastest attention impl actually available.

    flash-attn > sdpa > eager. flash-attn requires a separate compiled
    package; we don't pin it because it's fragile across CUDA versions.
    """
    try:
        import flash_attn  # noqa: F401
        return "flash_attention_2"
    except ImportError:
        return "sdpa"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--base-model", default="Qwen/Qwen2.5-7B-Instruct")
    p.add_argument("--train-file", default=str(DATA_DIR / "train.jsonl"))
    p.add_argument("--val-file",   default=str(DATA_DIR / "val.jsonl"))
    p.add_argument("--output-dir", default=str(DEFAULT_OUT))
    p.add_argument("--max-seq-len", type=int, default=4096)

    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--per-device-batch", type=int, default=2)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--eval-batch", type=int, default=8,
                   help="Per-device eval batch size. Larger is safe because eval "
                        "uses no gradients/optimizer state; bumping this from 2 to "
                        "8+ makes eval ~4x faster, which matters a lot when evals "
                        "happen every N train steps.")
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--warmup-ratio", type=float, default=0.03)

    p.add_argument("--lora-r", type=int, default=128)
    p.add_argument("--lora-alpha", type=int, default=256)
    p.add_argument("--lora-dropout", type=float, default=0.05)

    p.add_argument("--save-steps",   type=int, default=500)
    p.add_argument("--eval-steps",   type=int, default=1000,
                   help="Eval every N training steps. Each eval currently takes "
                        "~2-3 min at --eval-batch=8 on 10K val samples, so eval "
                        "every 1000 is a reasonable balance vs. total training "
                        "time (~20 evals over a 24K-step run).")
    p.add_argument("--log-steps",    type=int, default=25)
    p.add_argument("--seed",         type=int, default=42)
    p.add_argument("--no-4bit", action="store_true",
                   help="Disable 4-bit base load (uses bf16 for debugging).")
    p.add_argument("--no-grad-ckpt", action="store_true",
                   help="Disable gradient checkpointing — faster but uses more VRAM. "
                        "Default is on; turn off only when you have headroom "
                        "(RTX Pro 6000 with batch<=8, seq<=4k has plenty).")
    p.add_argument("--resume", action="store_true",
                   help="Resume from the latest checkpoint in --output-dir.")
    p.add_argument("--limit", type=int, default=0,
                   help="If >0, use only the first N train rows (for smoke tests).")
    return p.parse_args()


def main():
    args = parse_args()

    # ---- tokenizer ----
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # ---- datasets ----
    data_files = {"train": args.train_file, "val": args.val_file}
    ds = load_dataset("json", data_files=data_files)
    if args.limit > 0:
        ds["train"] = ds["train"].select(range(min(args.limit, len(ds["train"]))))

    def to_text(example):
        # Render ChatML from the stored messages
        example["text"] = tok.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False
        )
        return example

    ds = ds.map(to_text, remove_columns=[c for c in ds["train"].column_names if c != "text"])

    print(f"Train rows: {len(ds['train']):,}")
    print(f"Val rows:   {len(ds['val']):,}")

    # ---- base model (optionally 4-bit) ----
    if args.no_4bit:
        model = AutoModelForCausalLM.from_pretrained(
            args.base_model,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
            # transformers picks the best available attention impl; use
            # flash_attention_2 only if the flash-attn package is installed.
            attn_implementation=_pick_attn_impl(),
        )
    else:
        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.base_model,
            quantization_config=bnb,
            trust_remote_code=True,
            # transformers picks the best available attention impl; use
            # flash_attention_2 only if the flash-attn package is installed.
            attn_implementation=_pick_attn_impl(),
        )
    # Prefill cache unused during training; save VRAM
    model.config.use_cache = False

    # ---- LoRA config ----
    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )

    # load_best_model_at_end requires save_steps to be a multiple of eval_steps;
    # simplest contract is to save at every eval.
    save_steps = args.eval_steps

    # ---- SFT config ----
    sft = SFTConfig(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.per_device_batch,
        per_device_eval_batch_size=args.eval_batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type="cosine",
        optim="paged_adamw_8bit" if not args.no_4bit else "adamw_torch",
        bf16=True,
        tf32=True,
        gradient_checkpointing=not args.no_grad_ckpt,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=args.log_steps,
        save_steps=save_steps,
        save_total_limit=3,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        # Pick the checkpoint with the lowest val loss at the end. save_total_limit=3
        # keeps only the 3 best checkpoints on disk, so old worse ones are pruned.
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        max_length=args.max_seq_len,
        packing=False,                 # packing + chat template can corrupt labels
        dataset_text_field="text",
        report_to=[],                  # no W&B / mlflow by default; opt-in later
        seed=args.seed,
        dataloader_num_workers=2,
        dataloader_pin_memory=True,
    )

    trainer = SFTTrainer(
        model=model,
        args=sft,
        train_dataset=ds["train"],
        eval_dataset=ds["val"],
        peft_config=peft_config,
        processing_class=tok,
    )

    print()
    print("=" * 70)
    print(f"Base:         {args.base_model}")
    print(f"Output:       {args.output_dir}")
    print(f"Effective bs: {args.per_device_batch} * {args.grad_accum}"
          f" = {args.per_device_batch * args.grad_accum}")
    print(f"LoRA:         r={args.lora_r}, alpha={args.lora_alpha}")
    print(f"Epochs:       {args.epochs}")
    print(f"Max seq len:  {args.max_seq_len}")
    print("=" * 70)

    resume = args.resume and os.path.isdir(args.output_dir) and \
             any(p.startswith("checkpoint-") for p in os.listdir(args.output_dir))
    trainer.train(resume_from_checkpoint=resume or None)
    trainer.save_model(args.output_dir)
    print(f"\nSaved final adapter to {args.output_dir}")


if __name__ == "__main__":
    main()
