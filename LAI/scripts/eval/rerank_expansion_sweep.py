"""Sweep query-augmentation variants through the post-reranker harness.

Drives ``scripts.eval.retrieval_recall --rerank`` once per
``LAI_RERANK_QUERY_VARIANT`` value (none/q1/q2/q3), collates Recall@K
+ MRR + rerank_ms + retrieve_ms into one CSV, and applies the
blueprint's decision rule (R@30 ≥ control − 1 pp AND rerank_ms ≤
control × 1.30; pick highest R@30 among survivors).

BM25 base stays at v5 (current production); only the string handed to
the cross-encoder reranker changes.

Usage
-----
::

    python -m scripts.eval.rerank_expansion_sweep --n 200 \\
        --variants none,q1,q2,q3 --rerank-top-n 30

LAI_RERANK_DEVICE is forwarded into each subprocess so the reranker
lands on a consistent GPU (default ``cuda:1`` to coexist with the
vLLM analyzer on cuda:0). Expansions are LLM-cached from the
2026-06-02 BM25-rewrite sweep — re-runs cost ~zero on the LLM side.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = LAI_DIR / "scripts" / "eval" / "rag_eval_results"


def _parse_variants(spec: str) -> list[str]:
    return [s.strip() for s in spec.split(",") if s.strip()]


def _run_one(
    *,
    variant: str,
    n: int,
    ks: str,
    candidate_k: int,
    rerank_top_n: int,
    out_json: Path,
    venv_python: Path,
    rerank_device: str,
) -> tuple[dict, float]:
    env = os.environ.copy()
    env["LAI_RERANK_QUERY_VARIANT"] = variant
    env["LAI_RERANK_DEVICE"] = rerank_device
    env["LAI_BM25_VARIANT"] = env.get("LAI_BM25_VARIANT", "v5")
    cmd = [
        str(venv_python),
        "-m",
        "scripts.eval.retrieval_recall",
        "--mode",
        "hybrid",
        "--n",
        str(n),
        "--k",
        ks,
        "--candidate-k",
        str(candidate_k),
        "--rerank",
        "--rerank-top-n",
        str(rerank_top_n),
        "--output",
        str(out_json),
    ]
    print(f"  variant={variant:<4} running …", flush=True)
    t0 = time.monotonic()
    res = subprocess.run(cmd, cwd=str(LAI_DIR), capture_output=True, text=True, env=env)
    wall = time.monotonic() - t0
    if res.returncode != 0:
        print(f"  variant={variant:<4} FAILED (exit {res.returncode})", flush=True)
        if res.stderr:
            print("    stderr tail:", res.stderr.splitlines()[-1] if res.stderr.strip() else "<empty>")
        return {}, wall
    if not out_json.exists():
        print(f"  variant={variant:<4} FAILED (no output JSON)", flush=True)
        return {}, wall
    return json.loads(out_json.read_text()), wall


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--k", default="10,30,100")
    ap.add_argument("--candidate-k", type=int, default=200)
    ap.add_argument("--rerank-top-n", type=int, default=30)
    ap.add_argument(
        "--variants",
        default="none,q1,q2,q3",
        help=(
            "comma-sep variant tags from lai.search.rerank_query.augment. "
            "none = control (original query); q1 = synonyms; q2 = morphology; "
            "q3 = both."
        ),
    )
    ap.add_argument(
        "--rerank-device",
        default="cuda:1",
        help="forwarded to the harness subprocess via LAI_RERANK_DEVICE",
    )
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument(
        "--venv-python",
        type=Path,
        default=LAI_DIR / ".venv" / "bin" / "python",
    )
    args = ap.parse_args(argv)

    if not args.venv_python.exists():
        print(f"venv python not found at {args.venv_python}", file=sys.stderr)
        return 2

    variants = _parse_variants(args.variants)
    if not variants:
        print("--variants must list at least one tag", file=sys.stderr)
        return 2

    out_dir = args.out_dir or (DEFAULT_OUT_DIR / "rerank_expansion_sweep")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = out_dir / f"sweep_n{args.n}_top{args.rerank_top_n}.csv"

    print(
        f"[sweep] n={args.n} top_n={args.rerank_top_n} variants={variants} "
        f"device={args.rerank_device}"
    )
    rows: list[dict] = []
    for variant in variants:
        out_json = out_dir / f"recall_n{args.n}_top{args.rerank_top_n}_{variant}.json"
        result, wall = _run_one(
            variant=variant,
            n=args.n,
            ks=args.k,
            candidate_k=args.candidate_k,
            rerank_top_n=args.rerank_top_n,
            out_json=out_json,
            venv_python=args.venv_python,
            rerank_device=args.rerank_device,
        )
        if not result:
            continue
        s = result.get("summary", {})
        t = result.get("timings_ms_per_query", {})
        row = {
            "variant": variant,
            "n_scored": s.get("n", 0),
            "recall_at_10": s.get("recall_at_k", {}).get("10"),
            "recall_at_30": s.get("recall_at_k", {}).get("30"),
            "recall_at_100": s.get("recall_at_k", {}).get("100"),
            "mrr": s.get("mrr"),
            "embed_ms": t.get("embed"),
            "retrieve_ms": t.get("retrieve"),
            "hydrate_ms": t.get("hydrate"),
            "rerank_ms": t.get("rerank"),
            "rerank_query_chars_avg": result.get("rerank_query_chars_avg"),
            "wall_seconds": round(wall, 1),
        }
        rows.append(row)
        print(
            f"  variant={variant:<4} R@10={row['recall_at_10']:.3f} "
            f"R@30={row['recall_at_30']:.3f} R@100={row['recall_at_100']:.3f} "
            f"MRR={row['mrr']:.3f}  retrieve={row['retrieve_ms']:.0f}ms "
            f"rerank={row['rerank_ms']:.0f}ms qchars={row['rerank_query_chars_avg']:.0f}"
        )

    if not rows:
        print("no successful sweep rows; aborting summary", file=sys.stderr)
        return 1

    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n→ wrote {summary_csv}")

    # ── Apply the blueprint decision rule ───────────────────────────────
    control = next((r for r in rows if r["variant"] == "none"), None)
    if control is None:
        print("(no none-variant in run; skipping decision rule)")
        return 0
    print("\n=== decision rule (vs none-variant baseline) ===")
    print("  drop if R@30 < control_R@30 - 0.01 OR rerank_ms > control_rerank_ms * 1.30")
    rerank_budget = (control["rerank_ms"] or 0) * 1.30
    recall_floor = (control["recall_at_30"] or 0) - 0.01
    survivors: list[dict] = []
    for r in rows:
        if r["variant"] == "none":
            continue
        d_recall = r["recall_at_30"] - control["recall_at_30"]
        d_rerank = (r["rerank_ms"] or 0) - (control["rerank_ms"] or 0)
        recall_ok = (r["recall_at_30"] or 0) >= recall_floor
        latency_ok = (r["rerank_ms"] or 0) <= rerank_budget
        verdict = "KEEP" if (recall_ok and latency_ok) else "DROP"
        print(
            f"  {r['variant']:<4} ΔR@30={d_recall:+.3f}  "
            f"Δrerank={d_rerank:+.0f}ms  "
            f"abs_R@30={r['recall_at_30']:.3f}  "
            f"abs_rerank={r['rerank_ms']:.0f}ms → {verdict}"
        )
        if verdict == "KEEP":
            survivors.append(r)
    if not survivors:
        print("\nno variant clears the recall+latency gate — keep 'none' (original query).")
        return 0
    winner = max(survivors, key=lambda r: r["recall_at_30"] or 0)
    print(
        f"\n→ winner: {winner['variant']}  "
        f"(R@30={winner['recall_at_30']:.3f} vs none={control['recall_at_30']:.3f}, "
        f"rerank={winner['rerank_ms']:.0f}ms vs none={control['rerank_ms']:.0f}ms)"
    )
    print(
        "Make it the new default by flipping LAI_RERANK_QUERY_VARIANT in "
        "lai.search.rerank_query.augment (or set the env in serve_rag startup)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
