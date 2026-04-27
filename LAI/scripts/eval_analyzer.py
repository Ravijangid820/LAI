"""Eval harness for Contract Analyzer V1 vs V2.

Runs both versions against a small gold set and reports per-metric scores.
Designed to be invoked as a script — no test framework dependency.

Gold-set layout:
    LAI/eval/contracts/
        <slug>.pdf          — input contract
        <slug>.gold.json    — annotations (parties, parcels, required clauses,
                              top issues, table totals)

Run:
    cd /data/projects/lai/LAI
    python scripts/eval_analyzer.py [--server http://localhost:18000]
                                    [--versions 1,2]
                                    [--out scripts/rag_eval_results/analyzer_compare.md]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

LAI = Path(__file__).resolve().parents[1]
GOLD_DIR = LAI / "eval" / "contracts"
DEFAULT_OUT = LAI / "scripts" / "rag_eval_results" / "analyzer_compare.md"


# ---------------------------------------------------------------------------
# Gold-set helpers
# ---------------------------------------------------------------------------

def load_gold_set() -> list[dict]:
    if not GOLD_DIR.exists():
        return []
    items = []
    for gold in sorted(GOLD_DIR.glob("*.gold.json")):
        slug = gold.stem.removesuffix(".gold")
        pdf = GOLD_DIR / f"{slug}.pdf"
        if not pdf.exists():
            print(f"[warn] missing PDF for {slug}", file=sys.stderr)
            continue
        items.append({
            "slug": slug,
            "pdf": pdf,
            "gold": json.loads(gold.read_text(encoding="utf-8")),
        })
    return items


# ---------------------------------------------------------------------------
# Server interaction
# ---------------------------------------------------------------------------

def upload(server: str, pdf: Path) -> str:
    with pdf.open("rb") as f:
        r = httpx.post(
            f"{server.rstrip('/')}/upload",
            files={"file": (pdf.name, f, "application/pdf")},
            timeout=300.0,
        )
    r.raise_for_status()
    return r.json()["session_id"]


def analyze(server: str, session_id: str, version: str) -> tuple[dict, float]:
    t0 = time.time()
    r = httpx.post(
        f"{server.rstrip('/')}/analyze-contract",
        json={"session_id": session_id, "version": version},
        timeout=600.0,
    )
    elapsed = time.time() - t0
    r.raise_for_status()
    return r.json(), elapsed


def fetch_full_v2(server: str, session_id: str) -> Optional[dict]:
    r = httpx.get(
        f"{server.rstrip('/')}/analyze-contract/full",
        params={"session_id": session_id},
        timeout=30.0,
    )
    if r.status_code != 200:
        return None
    return r.json()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


def required_clause_recall(gold: dict, response: dict, full: Optional[dict]) -> float:
    """How many of the gold-flagged required clauses were either present
    (in response.clauses) or surfaced as missing_required_clauses?"""
    required = gold.get("required_clauses") or []
    if not required:
        return float("nan")
    present_types = {_norm(c.get("type", "")) for c in response.get("clauses", [])}
    missing_titles = {
        _norm(m.get("description", "") + " " + (m.get("type") or ""))
        for m in response.get("missing_required_clauses", [])
    }
    hit = 0
    for topic in required:
        nt = _norm(topic)
        if any(nt in p for p in present_types) or any(nt in t for t in missing_titles):
            hit += 1
    return hit / len(required)


def parcel_f1(gold: dict, full: Optional[dict]) -> Optional[float]:
    if full is None:
        return None
    gold_parcels = gold.get("parcels") or []
    if not gold_parcels:
        return None
    pred = full.get("parcels", [])

    def key(p: dict) -> tuple:
        return (
            _norm(p.get("gemarkung") or ""),
            _norm(p.get("flur") or ""),
            _norm(p.get("flurstueck") or ""),
        )

    gold_keys = {key(p) for p in gold_parcels if any(p.values())}
    pred_keys = {key(p) for p in pred if any([p.get("gemarkung"), p.get("flur"), p.get("flurstueck")])}
    if not gold_keys or not pred_keys:
        return 0.0
    tp = len(gold_keys & pred_keys)
    if tp == 0:
        return 0.0
    precision = tp / len(pred_keys)
    recall = tp / len(gold_keys)
    return 2 * precision * recall / (precision + recall)


def issue_recall(gold: dict, response: dict) -> Optional[float]:
    gold_issues = gold.get("top_issues") or []
    if not gold_issues:
        return None
    found = 0
    flat: list[str] = []
    for c in response.get("clauses", []):
        for i in c.get("issues", []):
            flat.append(_norm(i.get("description", "")))
    flat += [_norm(i.get("description", "")) for i in response.get("missing_required_clauses", [])]
    blob = " ".join(flat)
    for needle in gold_issues:
        n = _norm(needle.get("keyword", needle if isinstance(needle, str) else ""))
        if n and n in blob:
            found += 1
    return found / len(gold_issues)


def reconciliation_precision(gold: dict, full: Optional[dict]) -> Optional[float]:
    if full is None:
        return None
    expected = gold.get("table_discrepancies", [])
    flagged = full.get("reconciliation_findings", [])
    if not flagged:
        return float("nan") if not expected else 0.0
    real = 0
    for f in flagged:
        # Treat low/info as auto-correct (rounding) — only count med/high
        if f.get("severity") in ("medium", "high"):
            real += 1
    if real == 0:
        return float("nan")
    expected_real = sum(1 for e in expected if e.get("severity") in ("medium", "high"))
    return min(1.0, expected_real / real)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if x != x:  # NaN
            return "n/a"
        return f"{x:.2f}"
    return str(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:18000")
    ap.add_argument("--versions", default="1,2",
                    help="Comma-separated analyzer versions to run")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    items = load_gold_set()
    if not items:
        print(f"[err] no gold contracts under {GOLD_DIR}", file=sys.stderr)
        print("       create <slug>.pdf + <slug>.gold.json files first", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for it in items:
        sid = upload(args.server, it["pdf"])
        for v in versions:
            try:
                resp, elapsed = analyze(args.server, sid, v)
            except Exception as e:
                print(f"[err] {it['slug']} v{v}: {e}", file=sys.stderr)
                continue
            full = fetch_full_v2(args.server, sid) if v == "2" else None
            rows.append({
                "slug": it["slug"],
                "version": v,
                "elapsed_s": elapsed,
                "n_clauses": resp.get("n_clauses"),
                "required_clause_recall": required_clause_recall(it["gold"], resp, full),
                "issue_recall": issue_recall(it["gold"], resp),
                "parcel_f1": parcel_f1(it["gold"], full),
                "recon_precision": reconciliation_precision(it["gold"], full),
            })
            print(f"  {it['slug']:30} v{v}  "
                  f"req={fmt(rows[-1]['required_clause_recall'])} "
                  f"iss={fmt(rows[-1]['issue_recall'])} "
                  f"parcel={fmt(rows[-1]['parcel_f1'])} "
                  f"recon={fmt(rows[-1]['recon_precision'])} "
                  f"t={elapsed:.1f}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("# Analyzer V1 vs V2 — Eval Results\n\n")
        f.write(f"_Run at_: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("| Slug | Version | Required-clause recall | Issue recall | Parcel F1 | Recon precision | Elapsed (s) | # clauses |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['slug']} | v{r['version']} | "
                    f"{fmt(r['required_clause_recall'])} | "
                    f"{fmt(r['issue_recall'])} | "
                    f"{fmt(r['parcel_f1'])} | "
                    f"{fmt(r['recon_precision'])} | "
                    f"{r['elapsed_s']:.1f} | "
                    f"{r['n_clauses']} |\n")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
