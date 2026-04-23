"""
Qualitative side-by-side: base Qwen2.5-7B-Instruct vs the fine-tuned merged model.
Loads both on the same GPU, runs each picked question through both, prints
reference + base + fine-tuned answer.

Usage:
    python -m training.fine_tuning.scripts.compare_base_vs_ft \\
        --picks /tmp/eval_picks.jsonl \\
        --ft-model /data/projects/lai/models/qwen25-7b-legal-lora-v2-merged
"""

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def load_model(path: str, device: str):
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        path, torch_dtype=torch.bfloat16, device_map=device, trust_remote_code=True
    )
    model.eval()
    return tok, model


@torch.no_grad()
def generate(model, tok, messages, max_new_tokens=512):
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt").to(model.device)
    out = model.generate(
        **inp,
        max_new_tokens=max_new_tokens,
        do_sample=False,  # greedy for reproducibility
        temperature=1.0,
        repetition_penalty=1.05,
        pad_token_id=tok.pad_token_id,
    )
    # Strip the prompt echo
    gen_ids = out[0][inp.input_ids.shape[1]:]
    return tok.decode(gen_ids, skip_special_tokens=True).strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--picks", default="/tmp/eval_picks.jsonl")
    p.add_argument("--base",  default="Qwen/Qwen2.5-7B-Instruct")
    p.add_argument("--ft-model", required=True)
    p.add_argument("--max-new-tokens", type=int, default=400)
    p.add_argument("--device", default="cuda")
    args = p.parse_args()

    picks = [json.loads(l) for l in Path(args.picks).read_text().splitlines()]
    print(f"Loaded {len(picks)} picks")

    print(f"\n=== Loading BASE: {args.base} ===")
    tok_b, model_b = load_model(args.base, args.device)

    base_out = []
    for i, r in enumerate(picks):
        print(f"  generating base {i+1}/{len(picks)}...")
        msgs = [m for m in r["messages"] if m["role"] != "assistant"]
        ans = generate(model_b, tok_b, msgs, args.max_new_tokens)
        base_out.append(ans)

    # Free the base model before loading FT
    del model_b, tok_b
    torch.cuda.empty_cache()

    print(f"\n=== Loading FT: {args.ft_model} ===")
    tok_f, model_f = load_model(args.ft_model, args.device)

    ft_out = []
    for i, r in enumerate(picks):
        print(f"  generating FT {i+1}/{len(picks)}...")
        msgs = [m for m in r["messages"] if m["role"] != "assistant"]
        ans = generate(model_f, tok_f, msgs, args.max_new_tokens)
        ft_out.append(ans)

    del model_f, tok_f
    torch.cuda.empty_cache()

    # Report
    print("\n\n" + "=" * 80)
    print("COMPARISON")
    print("=" * 80)
    for i, r in enumerate(picks):
        ref = next(m["content"] for m in r["messages"] if m["role"] == "assistant")
        q   = next(m["content"] for m in r["messages"] if m["role"] == "user")
        print(f"\n\n--- [{i+1}/{len(picks)}] task={r['task_type']} ---")
        print(f"Q: {q}\n")
        print(f"[REF]\n{ref}\n")
        print(f"[BASE]\n{base_out[i]}\n")
        print(f"[FT]\n{ft_out[i]}\n")
        print("-" * 80)

    # Save for further analysis
    outp = Path(args.picks).with_name("eval_results.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(
            [{"task": r["task_type"],
              "question": next(m["content"] for m in r["messages"] if m["role"] == "user"),
              "ref":  next(m["content"] for m in r["messages"] if m["role"] == "assistant"),
              "base": base_out[i],
              "ft":   ft_out[i]}
             for i, r in enumerate(picks)],
            f, ensure_ascii=False, indent=2,
        )
    print(f"\nSaved to {outp}")


if __name__ == "__main__":
    main()
