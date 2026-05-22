"""Verify the parallelized VLM OCR path on the real scanned contract that
took ~40 min on the chat upload. Runs the *edited* serve_rag._vlm_ocr_pdf
sequentially (workers=1) then in parallel (workers=5), against the live
:8005 vision LLM — no serve_rag restart, no GPU model load (import is
side-effect-light; models load only in the startup function)."""

from __future__ import annotations

import time

from lai.api import serve_rag as sr

# Point at the same vision endpoint the live serve_rag uses (startup default).
sr.STATE["llm_api_url"] = "http://localhost:8005"
sr.STATE["llm_model_name"] = "qwen3.6-27b"

PDF = ("/data/projects/lai/harsh/testing_vdr_pdfs/"
       "05_Lamstedt_Nutzungsvertrag_GemeindeLamstedt_10pg.pdf")
data = open(PDF, "rb").read()
print(f"file: {len(data)} bytes")


def run(workers: int) -> tuple[float, int, int]:
    sr._VLM_OCR_WORKERS = workers
    seen = []
    def prog(done: int, total: int) -> None:
        if done:  # skip the initial (0, total)
            seen.append((done, time.time()))
            print(f"    progress {done}/{total}", flush=True)
    t0 = time.time()
    md, n, _ = sr._vlm_ocr_pdf(data, on_progress=prog)
    dt = time.time() - t0
    # page markers must be present and in order
    order_ok = all(f"<!-- Seite {i} -->" in md for i in range(1, n + 1))
    print(f"  workers={workers}: {n} pages in {dt:.1f}s, {len(md)} chars, "
          f"page-order-ok={order_ok}, progress-ticks={len(seen)}")
    return dt, n, len(md)


print("=== SEQUENTIAL (workers=1) ===")
seq_dt, seq_n, seq_len = run(1)
print("=== PARALLEL (workers=5) ===")
par_dt, par_n, par_len = run(5)

print("\n=== RESULT ===")
print(f"sequential: {seq_dt:.1f}s   parallel: {par_dt:.1f}s   "
      f"speedup: {seq_dt / par_dt:.1f}x")
print(f"pages match: {seq_n == par_n} ({seq_n})   "
      f"text length similar: {seq_len} vs {par_len}")
