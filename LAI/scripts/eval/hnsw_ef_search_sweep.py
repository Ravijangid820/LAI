"""Sweep ``hnsw.ef_search`` over a range and collate Recall/MRR/latency.

Drives ``scripts.eval.retrieval_recall`` once per ``--ef-search`` value
and emits a single CSV table — what the operator stares at to pick the
production default for ``RetrievalConfig.hnsw_ef_search``.

The current production default is 100 (see
``LAI/src/lai/common/retrieval/config.py:115``). A sweep over
{40, 80, 100, 200, 400, 800} surfaces the Recall@K vs ms/query curve
across 1.5 orders of magnitude — enough resolution to see the
diminishing-returns knee that justifies the chosen value.

Modes: defaults to ``hybrid`` (production semantics). ``dense``
isolates the HNSW knob from BM25 noise — useful when debugging.

Usage
-----
::

    python -m scripts.eval.hnsw_ef_search_sweep \\
        --mode hybrid --n 200 --ef-search 40,80,100,200,400,800

Output
------
* One ``recall_<mode>_n<N>_ef<V>.json`` per sweep value (full per-row
  detail; matches the harness's own output shape).
* One ``sweep_<mode>_n<N>.csv`` summary table with columns:
  ``ef_search, n_scored, recall_at_10, recall_at_30, recall_at_100,
  mrr, embed_ms, retrieve_ms, hydrate_ms``.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = LAI_DIR / "scripts" / "eval" / "rag_eval_results"


def _parse_efs(spec: str) -> list[int]:
    return sorted({int(s.strip()) for s in spec.split(",") if s.strip()})


def _run_one(
    *,
    mode: str,
    n: int,
    ks: str,
    candidate_k: int,
    ef: int,
    out_json: Path,
    venv_python: Path,
) -> dict:
    """Spawn the harness as a subprocess and load its JSON output."""
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
        "--ef-search",
        str(ef),
        "--output",
        str(out_json),
    ]
    print(f"  ef={ef:>4}  running …", flush=True)
    res = subprocess.run(cmd, cwd=str(LAI_DIR), capture_output=True, text=True)
    if res.returncode != 0:
        # Surface the failure but don't abort the whole sweep — one
        # bad ef value shouldn't take the rest down with it.
        print(f"  ef={ef:>4}  FAILED (exit {res.returncode})", flush=True)
        if res.stderr:
            print("    stderr tail:", res.stderr.splitlines()[-1] if res.stderr.strip() else "<empty>")
        return {}
    if not out_json.exists():
        print(f"  ef={ef:>4}  FAILED (no output JSON)", flush=True)
        return {}
    return json.loads(out_json.read_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("dense", "hybrid"), default="hybrid")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--k", default="10,30,100")
    ap.add_argument("--candidate-k", type=int, default=200)
    ap.add_argument(
        "--ef-search",
        default="40,80,100,200,400,800",
        help="comma-sep ef_search values to sweep",
    )
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument(
        "--venv-python",
        type=Path,
        default=LAI_DIR / ".venv" / "bin" / "python",
        help="python interpreter (defaults to .venv) — must have lai installed",
    )
    args = ap.parse_args(argv)

    if not args.venv_python.exists():
        print(f"venv python not found at {args.venv_python}", file=sys.stderr)
        return 2

    efs = _parse_efs(args.ef_search)
    if not efs:
        print("--ef-search must list at least one integer", file=sys.stderr)
        return 2

    out_dir = args.out_dir or (DEFAULT_OUT_DIR / "hnsw_ef_search_sweep")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = out_dir / f"sweep_{args.mode}_n{args.n}.csv"

    print(f"[sweep] mode={args.mode} n={args.n} ef_search={efs}")
    rows: list[dict] = []
    for ef in efs:
        out_json = out_dir / f"recall_{args.mode}_n{args.n}_ef{ef}.json"
        result = _run_one(
            mode=args.mode,
            n=args.n,
            ks=args.k,
            candidate_k=args.candidate_k,
            ef=ef,
            out_json=out_json,
            venv_python=args.venv_python,
        )
        if not result:
            continue
        s = result.get("summary", {})
        t = result.get("timings_ms_per_query", {})
        row = {
            "ef_search": ef,
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
            f"  ef={ef:>4}  R@10={row['recall_at_10']:.3f} "
            f"R@30={row['recall_at_30']:.3f} R@100={row['recall_at_100']:.3f} "
            f"MRR={row['mrr']:.3f}  retrieve={row['retrieve_ms']:.0f}ms"
        )

    if not rows:
        print("no successful sweep rows; aborting summary", file=sys.stderr)
        return 1

    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n→ wrote {summary_csv}")
    print("\nPick the smallest ef_search whose Recall@30 is within ~0.5pp of the")
    print("plateau — that's the production default with the best recall/latency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
