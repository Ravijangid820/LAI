"""
Lightweight RAG service that matches the LAI web_ui frontend contract.

Implements:
    GET  /health
    POST /query    -> {answer, chunks[], timings, tokens, session_id}
    POST /upload   -> stub (returns success without persisting)

The frontend (src/react-app/lib/ragApi.ts) expects this shape exactly.

Runtime:
    - Loads ~127 GB of child embeddings into RAM (one-time, ~25 min)
    - Loads Qwen3-Reranker-8B into GPU (~16 GB)
    - Loads Qwen2.5-7B-Instruct into GPU (~16 GB)
    - Reuses the running lai_embedding container (port 8003) for query encoding

Usage:
    cd /data/projects/lai/LAI
    CUDA_VISIBLE_DEVICES=1 .venv/bin/python scripts/serve_rag.py [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

LAI_DIR = Path(__file__).resolve().parents[1]
DB      = LAI_DIR / "processed" / "pipeline_local.db"

# Reuse the proven retriever + generator
sys.path.insert(0, str(LAI_DIR / "scripts"))
from rag_eval import (  # type: ignore[import-not-found]
    Corpus, load_embeddings, ensure_bm25, embed_query,
    retrieve_dense, retrieve_bm25, rrf_fuse, Reranker,
    load_parent_texts, dedupe_by_parent,
)


# Globals filled in lifespan startup
STATE: dict = {
    "corpus": None,
    "conn": None,
    "parent_text": None,
    "reranker": None,
    "lm": None,
    "tok": None,
}


RAG_SYSTEM = (
    "Du bist ein juristischer KI-Assistent für deutsches Windenergie- und "
    "Due-Diligence-Recht. Beantworte die Nutzerfrage ausschließlich auf "
    "Grundlage der unten bereitgestellten Rechtstexte. Zitiere bei jeder "
    "Aussage den entsprechenden Quellabschnitt (z.B. [Quelle 1]). "
    "Wenn die Frage mit den Quellen nicht eindeutig beantwortet werden "
    "kann, gib das ehrlich an."
)


def build_rag_messages(question: str, sources: list[str]) -> list[dict]:
    src_block = "\n\n".join(
        f"[Quelle {i+1}]\n{s}" for i, s in enumerate(sources)
    )
    user = f"Rechtstexte:\n{src_block}\n\nFrage: {question}"
    return [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user",   "content": user},
    ]


@torch.no_grad()
def llm_generate(messages: list[dict], max_new_tokens: int = 400) -> tuple[str, int, int]:
    tok = STATE["tok"]
    model = STATE["lm"]
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt", truncation=True, max_length=8192).to(model.device)
    prompt_tokens = int(inp.input_ids.shape[1])
    out = model.generate(
        **inp,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        temperature=1.0,
        repetition_penalty=1.05,
        pad_token_id=tok.pad_token_id,
    )
    gen_ids = out[0][inp.input_ids.shape[1]:]
    completion_tokens = int(gen_ids.shape[0])
    return tok.decode(gen_ids, skip_special_tokens=True).strip(), prompt_tokens, completion_tokens


# -----------------------------------------------------------------------------
# Lifespan: load everything once at startup
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] loading embeddings...", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(DB), check_same_thread=False)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    corpus = load_embeddings(conn)
    ensure_bm25(corpus, conn)
    parent_text = load_parent_texts(conn)
    print(f"[startup]   embeddings + bm25 + parent_text: {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    reranker = Reranker("Qwen/Qwen3-Reranker-8B")
    print(f"[startup]   reranker: {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-7B-Instruct", trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    lm = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen2.5-7B-Instruct",
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        trust_remote_code=True,
    ).eval()
    print(f"[startup]   LLM: {time.time()-t0:.1f}s", flush=True)

    STATE.update(corpus=corpus, conn=conn, parent_text=parent_text,
                 reranker=reranker, lm=lm, tok=tok)
    print("[startup] READY", flush=True)
    yield
    # No cleanup needed; process exit drops everything


# -----------------------------------------------------------------------------
# Schemas (mirroring src/react-app/lib/ragApi.ts)
# -----------------------------------------------------------------------------

class QueryReq(BaseModel):
    question: str
    session_id: Optional[str] = None
    top_k: int = 3
    candidate_k: int = 30


class ChunkOut(BaseModel):
    text: str
    section: str
    law_refs: list[str]
    sources: list[str]
    similarity: float
    rerank_score: float


class TimingsOut(BaseModel):
    embed_s: float
    retrieve_s: float
    rerank_s: float
    generate_s: float
    total_s: float


class TokensOut(BaseModel):
    prompt: int
    completion: int


class QueryResp(BaseModel):
    answer: str
    chunks: list[ChunkOut]
    timings: TimingsOut
    tokens: TokensOut
    session_id: str


class UploadResp(BaseModel):
    session_id: str
    filename: str
    pages: int
    chunks: int
    message: str


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "loaded": STATE["lm"] is not None}


@app.post("/query", response_model=QueryResp)
def query(req: QueryReq):
    if STATE["lm"] is None:
        raise HTTPException(503, "Service still loading")

    corpus: Corpus = STATE["corpus"]
    parent_text = STATE["parent_text"]
    reranker = STATE["reranker"]

    t_total0 = time.time()

    # 1. Embed query (HTTP to lai_embedding container)
    t0 = time.time()
    qvec = embed_query(req.question, with_prefix=True)
    embed_s = time.time() - t0

    # 2. Dense + BM25 retrieve, RRF fuse
    t0 = time.time()
    d_idx, d_sims = retrieve_dense(qvec, corpus, req.candidate_k)
    b_idx, b_sims = retrieve_bm25(req.question, corpus, req.candidate_k)
    fused = rrf_fuse([d_idx, b_idx])[:req.candidate_k]
    cand_idx = [c for c, _ in fused]
    retrieve_s = time.time() - t0

    # 3. Rerank
    t0 = time.time()
    pairs = [(req.question, parent_text.get(int(corpus.parent_ids[c]), "")[:2000])
             for c in cand_idx]
    rerank_scores = reranker.score(pairs)
    order = np.argsort(-np.asarray(rerank_scores))
    reranked = [cand_idx[j] for j in order]
    top_parents = dedupe_by_parent(reranked, corpus, req.top_k)
    rerank_s = time.time() - t0

    # Build chunks for response (top_k unique parents)
    chunks_out: list[ChunkOut] = []
    seen_pids = set()
    for j, idx in enumerate(reranked):
        pid = int(corpus.parent_ids[idx])
        if pid in seen_pids or pid not in top_parents:
            continue
        seen_pids.add(pid)
        text = parent_text.get(pid, "")[:1500]
        chunks_out.append(ChunkOut(
            text=text,
            section=f"Parent {pid}",
            law_refs=[],
            sources=["dense", "bm25"] if idx in d_idx and idx in b_idx else (
                ["dense"] if idx in d_idx else ["bm25"]
            ),
            similarity=float(rerank_scores[order[j]]),
            rerank_score=float(rerank_scores[order[j]]),
        ))
        if len(chunks_out) >= req.top_k:
            break

    # 4. Generate
    t0 = time.time()
    sources = [parent_text.get(int(p), "")[:1500] for p in top_parents]
    msgs = build_rag_messages(req.question, sources)
    answer, prompt_tokens, completion_tokens = llm_generate(msgs)
    generate_s = time.time() - t0

    total_s = time.time() - t_total0
    sid = req.session_id or str(uuid.uuid4())

    return QueryResp(
        answer=answer,
        chunks=chunks_out,
        timings=TimingsOut(
            embed_s=round(embed_s, 3),
            retrieve_s=round(retrieve_s, 3),
            rerank_s=round(rerank_s, 3),
            generate_s=round(generate_s, 3),
            total_s=round(total_s, 3),
        ),
        tokens=TokensOut(prompt=prompt_tokens, completion=completion_tokens),
        session_id=sid,
    )


@app.post("/upload", response_model=UploadResp)
async def upload(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """Stub: accepts an upload but does not yet ingest. Returns plausible
    metadata so the frontend remains functional."""
    sid = session_id or str(uuid.uuid4())
    contents = await file.read()
    return UploadResp(
        session_id=sid,
        filename=file.filename or "uploaded.pdf",
        pages=0,
        chunks=0,
        message=f"Received {len(contents)} bytes. Ingestion not yet implemented.",
    )


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
