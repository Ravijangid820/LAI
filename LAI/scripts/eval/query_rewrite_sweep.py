"""Sweep query-rewriting variants through the recall harness.

Drives ``scripts.eval.retrieval_recall`` once per ``LAI_QUERY_REWRITE_VARIANT``
value, collates Recall@K + retrieve_ms + rewrite_ms into one CSV, and
applies the blueprint's decision rule (R@30 ≥ 0.500 AND retrieve_ms
≤ 3700; pick lowest total chat-path latency among survivors).

Baseline BM25 stays at ``v5`` (the current production default) so the
sweep isolates the rewriting effect from BM25 token-selection.

Usage
-----
::

    python -m scripts.eval.query_rewrite_sweep --mode hybrid --n 200

The cache at ``scripts/eval/_rewrite_cache/`` means re-runs cost only
the BM25/dense/hydrate work, not the LLM expansions. First run pays
the LLM cost (~12 min for r1 + r3 LLM calls amortised over the sweep).
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
    mode: str,
    n: int,
    ks: str,
    candidate_k: int,
    out_json: Path,
    venv_python: Path,
) -> tuple[dict, float]:
    """Spawn the harness with ``LAI_QUERY_REWRITE_VARIANT`` set. Returns
    (result dict, wall_clock_seconds_for_full_subprocess)."""
    env = os.environ.copy()
    env["LAI_QUERY_REWRITE_VARIANT"] = variant
    # Force v5 BM25 base in case the parent shell had a different setting
    env["LAI_BM25_VARIANT"] = env.get("LAI_BM25_VARIANT", "v5")
    cmd = [
        str(venv_python),
        "-m",
        "scripts.eval.retrieval_recall",
        "--mode",
        mode,
        "--n",
        str(n),
        "--k",
        ks,
        "--candidate-k",
        str(candidate_k),
        "--output",
        str(out_json),
    ]
    print(f"  rewrite={variant:<4} running …", flush=True)
    t0 = time.monotonic()
    res = subprocess.run(cmd, cwd=str(LAI_DIR), capture_output=True, text=True, env=env)
    wall = time.monotonic() - t0
    if res.returncode != 0:
        print(f"  rewrite={variant:<4} FAILED (exit {res.returncode})", flush=True)
        if res.stderr:
            print("    stderr tail:", res.stderr.splitlines()[-1] if res.stderr.strip() else "<empty>")
        return {}, wall
    if not out_json.exists():
        print(f"  rewrite={variant:<4} FAILED (no output JSON)", flush=True)
        return {}, wall
    return json.loads(out_json.read_text()), wall


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("bm25", "hybrid"), default="hybrid")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--k", default="10,30,100")
    ap.add_argument("--candidate-k", type=int, default=200)
    ap.add_argument(
        "--variants",
        default="none,r1,r2,r3",
        help="comma-sep variant tags (none is the control / current production)",
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

    out_dir = args.out_dir or (DEFAULT_OUT_DIR / "query_rewrite_sweep")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = out_dir / f"sweep_{args.mode}_n{args.n}.csv"

    print(f"[sweep] mode={args.mode} n={args.n} rewrite variants={variants}")
    rows: list[dict] = []
    for variant in variants:
        out_json = out_dir / f"recall_{args.mode}_n{args.n}_rewrite_{variant}.json"
        result, wall = _run_one(
            variant=variant,
            mode=args.mode,
            n=args.n,
            ks=args.k,
            candidate_k=args.candidate_k,
            out_json=out_json,
            venv_python=args.venv_python,
        )
        if not result:
            continue
        s = result.get("summary", {})
        t = result.get("timings_ms_per_query", {})
        # rewrite_ms estimated by subtracting the within-process per-query
        # timings (embed + retrieve + hydrate) from the subprocess wall
        # clock and dividing by n_scored — this captures both the LLM
        # rewrite call and any per-query Python overhead the harness
        # itself doesn't measure.
        n_scored = s.get("n", 1) or 1
        sum_inproc_ms = (t.get("embed", 0) + t.get("retrieve", 0) + t.get("hydrate", 0)) * n_scored
        wall_ms = wall * 1000.0
        rewrite_ms_est = max(0.0, (wall_ms - sum_inproc_ms) / n_scored)
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
            "rewrite_ms_est": round(rewrite_ms_est, 1),
            "wall_seconds": round(wall, 1),
        }
        rows.append(row)
        print(
            f"  rewrite={variant:<4} R@10={row['recall_at_10']:.3f} "
            f"R@30={row['recall_at_30']:.3f} R@100={row['recall_at_100']:.3f} "
            f"MRR={row['mrr']:.3f}  retrieve={row['retrieve_ms']:.0f}ms "
            f"rewrite_est={row['rewrite_ms_est']:.0f}ms wall={row['wall_seconds']}s"
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
    print("  drop if R@30 < 0.500 OR retrieve_ms > 3700")
    survivors: list[dict] = []
    for r in rows:
        if r["variant"] == "none":
            continue
        d_recall = r["recall_at_30"] - control["recall_at_30"]
        d_retrieve = (r["retrieve_ms"] or 0) - (control["retrieve_ms"] or 0)
        recall_ok = (r["recall_at_30"] or 0) >= 0.500
        latency_ok = (r["retrieve_ms"] or 0) <= 3700
        verdict = "KEEP" if (recall_ok and latency_ok) else "DROP"
        print(
            f"  {r['variant']:<4} ΔR@30={d_recall:+.3f}  "
            f"Δretrieve={d_retrieve:+.0f}ms  "
            f"abs_R@30={r['recall_at_30']:.3f}  "
            f"abs_retrieve={r['retrieve_ms']:.0f}ms → {verdict}"
        )
        if verdict == "KEEP":
            survivors.append(r)
    if not survivors:
        print("\nno variant clears the recall+latency gate — keep none in production.")
        return 0
    winner = min(survivors, key=lambda r: (r["retrieve_ms"] or 0) + (r["rewrite_ms_est"] or 0))
    print(
        f"\n→ winner: {winner['variant']}  "
        f"(R@30={winner['recall_at_30']:.3f} vs none={control['recall_at_30']:.3f}, "
        f"retrieve+rewrite={(winner['retrieve_ms']+winner['rewrite_ms_est']):.0f}ms "
        f"vs none={(control['retrieve_ms']+control['rewrite_ms_est']):.0f}ms)"
    )
    print("Make it the new default by flipping LAI_QUERY_REWRITE_VARIANT in lai.search.eval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
