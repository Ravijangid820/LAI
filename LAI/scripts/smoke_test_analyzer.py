"""Smoke tests for the V2 analyzer.

Two phases:
  Phase A — direct-to-vLLM checks: /v1/models, thinking mode, JSON-guided decoding.
  Phase B — end-to-end pipeline against a real contract PDF, bypassing serve_rag.
            Uses Docling directly + analyzer_pipeline.analyze().

Run:
    cd /data/projects/lai/LAI
    ANALYZER_LLM_API_URL=http://localhost:8005 \
    ANALYZER_LLM_MODEL=qwen3.6-27b \
    .venv/bin/python scripts/smoke_test_analyzer.py [pdf_path]

Default pdf: WP Altmark UW-Nutzungsvertrag (smallest, most focused).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

LAI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAI / "src"))
sys.path.insert(0, str(LAI / "scripts"))

from lai.analyzer import llm_client, pipeline as analyzer_pipeline  # noqa: E402

DEFAULT_PDF = Path(
    "/data/projects/lai/VDRs/WP Altmark/UW-Nutzungsvertrag/UW-Nutzungsvertrag Windpark Altmark.pdf"
)


# ---------------------------------------------------------------------------
# Phase A — direct vLLM checks
# ---------------------------------------------------------------------------

def phase_a(api_url: str, model: str) -> bool:
    print("\n=== Phase A — direct vLLM checks ===\n")

    # A1: /v1/models reachable
    print("  [A1] /v1/models …", end=" ", flush=True)
    r = httpx.get(f"{api_url.rstrip('/')}/v1/models", timeout=10)
    r.raise_for_status()
    models = r.json().get("data", [])
    served = [m["id"] for m in models]
    print(f"OK — served={served}")
    assert any(model in s or s in model for s in served), f"{model} not served"

    cfg = llm_client.AnalyzerLLMConfig(api_url=api_url, model=model)

    # A2: thinking mode produces reasoning_content (or stripped <think>)
    print("  [A2] thinking-mode call …", end=" ", flush=True)
    t0 = time.time()
    out, thinking_tokens = llm_client.call(
        cfg,
        system="Du bist ein Mathe-Tutor. Antworte knapp.",
        user="Wie viel ist 17 * 23? Erkläre deinen Gedankengang in höchstens 30 Wörtern.",
        enable_thinking=True,
        max_thinking_tokens=2048,
        max_new_tokens=300,
        temperature=0.0,
    )
    print(f"OK — {time.time()-t0:.1f}s, thinking_tokens≈{thinking_tokens}")
    print(f"      content: {out[:200]}")
    assert "391" in out, f"expected 17*23=391 in answer, got: {out!r}"

    # A3: JSON-guided decoding returns valid JSON
    print("  [A3] JSON-guided decoding …", end=" ", flush=True)
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "country": {"type": "string"},
            "population_millions": {"type": "number"},
        },
        "required": ["city", "country", "population_millions"],
        "additionalProperties": False,
    }
    t0 = time.time()
    out, _ = llm_client.call(
        cfg,
        system="Antworte ausschließlich mit JSON nach dem vorgegebenen Schema.",
        user="Hauptstadt von Deutschland?",
        json_schema=schema,
        enable_thinking=False,
        max_new_tokens=200,
        temperature=0.0,
    )
    print(f"OK — {time.time()-t0:.1f}s")
    print(f"      content: {out}")
    parsed = json.loads(out)
    assert "berlin" in parsed["city"].lower()
    assert isinstance(parsed["population_millions"], (int, float))

    print("\n  Phase A: PASS")
    return True


# ---------------------------------------------------------------------------
# Phase B — end-to-end against a real contract
# ---------------------------------------------------------------------------

def phase_b(api_url: str, model: str, pdf_path: Path) -> bool:
    print(f"\n=== Phase B — end-to-end against {pdf_path.name} ===\n")

    if not pdf_path.exists():
        print(f"  SKIP — {pdf_path} not found")
        return False

    # Reuse serve_rag's docling_convert; cheaper than a separate import path.
    print("  [B1] Docling extraction …", end=" ", flush=True)
    t0 = time.time()
    from serve_rag import docling_convert  # noqa: PLC0415
    md, n_pages, tables = docling_convert(pdf_path.read_bytes(), pdf_path.name)
    print(f"OK — {time.time()-t0:.1f}s, {len(md):,} chars, {n_pages} pages, {len(tables)} tables")

    # Cheap structural segmentation — split by markdown headings; if none,
    # fall back to one block per ~3000 chars. Avoids needing the V1 LLM.
    print("  [B2] structural segmentation …", end=" ", flush=True)
    clauses = _segment_simple(md)
    print(f"OK — {len(clauses)} clauses")

    cfg = llm_client.AnalyzerLLMConfig(api_url=api_url, model=model)

    print("  [B3] analyzer_pipeline.analyze() …", flush=True)
    t0 = time.time()
    result = analyzer_pipeline.analyze(
        contract_text=md,
        cfg=cfg,
        clauses_input=clauses[:8],   # Cap to keep smoke run < 5 min
        docling_tables=tables,
    )
    elapsed = time.time() - t0
    print(f"      OK — total {elapsed:.1f}s")

    # Pretty-print key fields
    print("\n  --- Result summary ---")
    print(f"  contract_type:           {result.contract_type}")
    print(f"  metadata.parties:        {result.metadata.parties}")
    print(f"  parcels:                 {len(result.parcels)}")
    for p in result.parcels[:3]:
        print(f"    - Gem={p.gemarkung!r} Flur={p.flur!r} Flst={p.flurstueck!r} m²={p.groesse_m2}")
    print(f"  financial_tables:        {len(result.financial_tables)}")
    print(f"  reconciliation_findings: {len(result.reconciliation_findings)}")
    for f in result.reconciliation_findings[:3]:
        print(f"    - {f.severity:<6} {f.kind:<20} {f.note[:120]}")
    print(f"  clauses analyzed:        {len(result.clauses)}")
    n_issues = sum(len(c.issues) for c in result.clauses)
    print(f"  total issues:            {n_issues}")
    for c in result.clauses[:3]:
        print(f"    - [{c.id}] type={c.type!r}  issues={len(c.issues)}")
        for i in c.issues[:2]:
            print(f"        sev={i.severity}  {i.title[:80]}")
    print(f"  cross_clause_findings:   {len(result.cross_clause_findings)}")
    print(f"  missing_required:        {len(result.missing_required_clauses)}")
    for m in result.missing_required_clauses[:5]:
        print(f"    - sev={m.severity} {m.title}")

    # Persist full output for inspection
    out_dir = LAI / "scripts" / "rag_eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"smoke_v2_{pdf_path.stem.replace(' ', '_')[:40]}.json"
    out_path.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Wrote full result → {out_path}")

    print("\n  Phase B: PASS")
    return True


def _segment_simple(text: str) -> list[dict]:
    import re
    # Try markdown headings first
    parts = re.split(r"(?m)^#{1,3}\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 4:
        out = []
        for i, p in enumerate(parts, 1):
            first_line = p.split("\n", 1)[0][:80]
            out.append({"id": str(i), "title": first_line, "text": p})
        return out
    # Fallback — paragraph windows of ~3000 chars
    out = []
    cur = 0
    while cur < len(text):
        end = min(cur + 3000, len(text))
        back = text.rfind("\n\n", cur, end)
        if back > cur + 1500:
            end = back
        chunk = text[cur:end].strip()
        if chunk:
            out.append({"id": str(len(out) + 1), "title": chunk[:80].split("\n")[0], "text": chunk})
        cur = end
    return out


# ---------------------------------------------------------------------------

def main() -> int:
    api_url = os.environ.get("ANALYZER_LLM_API_URL", "http://localhost:8005")
    model = os.environ.get("ANALYZER_LLM_MODEL", "qwen3.6-27b")
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF

    try:
        phase_a(api_url, model)
    except Exception as e:
        print(f"\n  Phase A FAILED: {type(e).__name__}: {e}")
        return 1

    try:
        phase_b(api_url, model, pdf)
    except Exception as e:
        print(f"\n  Phase B FAILED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return 2

    print("\nALL PHASES PASS\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
