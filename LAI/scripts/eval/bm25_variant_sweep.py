"""Run every BM25 variant through the recall harness and collate the table.

Drives ``scripts.eval.retrieval_recall`` once per variant by setting
``LAI_BM25_VARIANT`` in the subprocess env (the dispatcher in
``lai.search.eval._bm25_match_expr`` then picks the implementation).
Production stays on ``v1`` because the env var is never set in the
serve_rag process.

Modes
-----
Defaults to ``--mode hybrid`` so the measurement matches what users see
in serve_rag (BM25 alone is never user-facing — it's always RRF-fused
with dense). Use ``--mode bm25`` to isolate the BM25-only effect.

Decision rule
-------------
From ``rj/blueprint/2026-06-02-bm25-retune-empirical.md``:

* Drop any variant whose Recall@30 is more than 1 pp below v1.
* From the survivors, pick the one with lowest ``retrieve_ms``.
* On a 5 % latency tie, prefer the smaller code change.

The summary CSV is exactly the input that rule consumes.

Usage
-----
::

    python -m scripts.eval.bm25_variant_sweep \\
        --mode hybrid --n 200 --variants v1,v2,v3,v5,v6,v7
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
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
) -> dict:
    """Spawn the harness with ``LAI_BM25_VARIANT`` set in the env."""
    env = os.environ.copy()
    env["LAI_BM25_VARIANT"] = variant
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
    print(f"  {variant:<4} running …", flush=True)
    res = subprocess.run(cmd, cwd=str(LAI_DIR), capture_output=True, text=True, env=env)
    if res.returncode != 0:
        print(f"  {variant:<4} FAILED (exit {res.returncode})", flush=True)
        if res.stderr:
            print("    stderr tail:", res.stderr.splitlines()[-1] if res.stderr.strip() else "<empty>")
        return {}
    if not out_json.exists():
        print(f"  {variant:<4} FAILED (no output JSON)", flush=True)
        return {}
    return json.loads(out_json.read_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("bm25", "hybrid"), default="hybrid")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--k", default="10,30,100")
    ap.add_argument("--candidate-k", type=int, default=200)
    ap.add_argument(
        "--variants",
        default="v1,v2,v3,v5,v6,v7",
        help=(
            "comma-sep variant tags from `lai.search.eval._BM25_VARIANTS`. "
            "v1 is the control (current production). v4 is a duplicate of "
            "v1 — skipped by default."
        ),
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

    out_dir = args.out_dir or (DEFAULT_OUT_DIR / "bm25_variant_sweep")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = out_dir / f"sweep_{args.mode}_n{args.n}.csv"

    print(f"[sweep] mode={args.mode} n={args.n} variants={variants}")
    rows: list[dict] = []
    for variant in variants:
        out_json = out_dir / f"recall_{args.mode}_n{args.n}_{variant}.json"
        result = _run_one(
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
        }
        rows.append(row)
        print(
            f"  {variant:<4} R@10={row['recall_at_10']:.3f} "
            f"R@30={row['recall_at_30']:.3f} R@100={row['recall_at_100']:.3f} "
            f"MRR={row['mrr']:.3f}  retrieve={row['retrieve_ms']:.0f}ms"
        )

    if not rows:
        print("no successful variant rows; aborting summary", file=sys.stderr)
        return 1

    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n→ wrote {summary_csv}")

    # ── Apply the decision rule from the blueprint ──────────────────────
    control = next((r for r in rows if r["variant"] == "v1"), None)
    if control is None:
        print("(no v1 in run; skipping decision rule)")
        return 0
    print("\n=== decision rule (vs v1 control) ===")
    survivors: list[dict] = []
    for r in rows:
        if r["variant"] == "v1":
            continue
        d_recall = r["recall_at_30"] - control["recall_at_30"]
        d_retrieve = (r["retrieve_ms"] or 0) - (control["retrieve_ms"] or 0)
        verdict = "DROP" if d_recall < -0.01 else "KEEP"
        print(
            f"  {r['variant']:<4} ΔR@30={d_recall:+.3f} "
            f"Δretrieve={d_retrieve:+.0f}ms  → {verdict}"
        )
        if verdict == "KEEP":
            survivors.append(r)
    if not survivors:
        print("\nno variant clears the recall gate — keep v1 in production.")
        return 0
    winner = min(survivors, key=lambda r: r["retrieve_ms"] or 0)
    print(
        f"\n→ winner: {winner['variant']}  "
        f"(R@30={winner['recall_at_30']:.3f} vs v1={control['recall_at_30']:.3f}, "
        f"retrieve={winner['retrieve_ms']:.0f}ms vs v1={control['retrieve_ms']:.0f}ms)"
    )
    print("Make it the new default by updating the dispatcher in lai.search.eval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
