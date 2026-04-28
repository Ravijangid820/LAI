#!/usr/bin/env python3
"""
FastAPI Backend — Legal AI RAG
Runs on SSH server, called by React frontend on local machine
"""
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional
from collections import defaultdict
import os, time, requests, psycopg2, re, uuid
import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image
import io
import logging
from ddiq_report import router as ddiq_router

logger = logging.getLogger("lai_api")

load_dotenv()

app = FastAPI(title="Legal AI RAG API", version="1.0.0")

app.include_router(ddiq_router, prefix="/ddiq")

# CORS — environment-aware: production origins from env, localhost only in dev
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] if _cors_origins_env else [
    "https://lai-beta.vercel.app",
    "https://lai-pied.vercel.app",
    "https://lai-ashen.vercel.app",
    "http://192.168.178.82:5173",
]
# Only allow localhost in development
if os.getenv("ENVIRONMENT", "development") == "development":
    _cors_origins.extend(["http://localhost:3000", "http://localhost:5173"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
LLM_URL        = os.getenv("LLM_URL", "http://localhost:8001/v1")
LLM_MODEL      = os.getenv("LLM_MODEL", "legal-lora")
EMBEDDING_URL  = os.getenv("EMBEDDING_URL", "http://localhost:8002")
RERANKER_URL   = os.getenv("RERANKER_URL", "http://localhost:8004")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "lai_db"),
    "user":     os.getenv("DB_USER", "lai_user"),
    "password": os.getenv("DB_PASSWORD", "lai_test_password_2024"),
}

# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Du bist ein erfahrener Fachanwalt für deutsches Energierecht.\n\n"
    "## Anweisungen:\n"
    "1. Beantworte die Frage AUSSCHLIESSLICH auf Basis der unten stehenden Kontextdokumente.\n"
    "2. CRITICAL: Detect the language of the user's question and reply in that EXACT language. English question → English answer. German question → German answer. Never mix languages.\n"
    "3. Zitiere konkret die Paragraphen, Absätze und Sätze.\n"
    "4. Wenn mehrere Dokumente relevante Informationen enthalten, fasse sie zusammen.\n"
    "5. Wenn der Kontext die Frage NICHT beantwortet, sage das explizit.\n"
    "6. Erfinde KEINE Informationen, die nicht im Kontext stehen.\n"
    "7. Strukturiere deine Antwort klar.\n\n"
    "## Kontextdokumente:\n{context}\n"
)

GREETING_SYSTEM_PROMPT = (
    "You are LAI, an AI assistant specialized in German energy law and legal due diligence for wind energy projects. "
    "CRITICAL RULE: You MUST detect the language of the user's message and reply in that EXACT language. "
    "If the user writes in English → reply in English only. "
    "If the user writes in German → reply in German only. "
    "Never switch languages. "
    "Greet the user warmly and briefly explain you can help with wind energy permits, contracts, BImSchG, EEG, and legal compliance."
)

# ─────────────────────────────────────────────
# Greeting / small-talk detection
# ─────────────────────────────────────────────
GREETING_PATTERNS = [
    r"^\s*(hi|hello|hey|hallo|guten\s*(morgen|tag|abend)|moin|servus|grüß\s*gott)\s*[!.,]?\s*$",
    r"^\s*(wie geht|how are you|what's up|sup|yo)\s*",
    r"^\s*(danke|thanks|thank you|merci|thx)\s*[!.]?\s*$",
    r"^\s*(bye|tschüss|auf wiedersehen|ciao)\s*[!.]?\s*$",
    r"^\s*(ok|okay|alright|gut|prima|super|cool)\s*[!.]?\s*$",
    r"^\s*(ja|nein|yes|no)\s*[!.]?\s*$",
    r"^\s*[!?.]+\s*$",
]

def is_greeting(text: str) -> bool:
    """Returns True if the message is a greeting or small talk, not a legal question."""
    cleaned = text.strip().lower()
    # Very short messages (under 4 words) that aren't questions
    word_count = len(cleaned.split())
    if word_count <= 3 and "?" not in cleaned and not any(
        kw in cleaned for kw in ["§", "gesetz", "recht", "wind", "energie", "bimsch", "eeg", "baug"]
    ):
        return True
    # Match greeting patterns
    for pattern in GREETING_PATTERNS:
        if re.match(pattern, cleaned, re.IGNORECASE):
            return True
    return False

# ─────────────────────────────────────────────
# Conversation Memory (in-memory store)
# ─────────────────────────────────────────────
conversation_store: dict[str, list[dict]] = defaultdict(list)
MAX_HISTORY = 10  # Keep last 10 messages per session

# ─────────────────────────────────────────────
# Document Store (in-memory, per session)
# ─────────────────────────────────────────────
# Stores uploaded document chunks with embeddings per session
document_store: dict[str, list[dict]] = defaultdict(list)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# ─────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None  # For conversation memory

class ChunkInfo(BaseModel):
    text: str
    section: str
    law_refs: list[str]
    sources: list[str]
    similarity: float
    rerank_score: float

class QueryResponse(BaseModel):
    answer: str
    chunks: list[ChunkInfo]
    timings: dict
    tokens: dict
    is_greeting: bool = False   # lets frontend know no RAG was used
    session_id: str = ""        # Return session_id for conversation continuity

class UploadResponse(BaseModel):
    session_id: str
    filename: str
    pages: int
    chunks: int
    message: str

# ─────────────────────────────────────────────
# Pipeline functions
# ─────────────────────────────────────────────
# Embedding service: try vLLM's OpenAI-compatible /v1/embeddings first
# (current LAI runtime), fall back to HuggingFace TEI's /embed if the
# server speaks that older shape instead.
def _embed_via_openai(texts: list[str], timeout: int = 120) -> list[list[float]]:
    resp = requests.post(
        f"{EMBEDDING_URL}/v1/embeddings",
        json={"model": "Qwen/Qwen3-Embedding-8B", "input": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json().get("data", [])]


def _embed_via_tei(texts: list[str], timeout: int = 120) -> list[list[float]]:
    resp = requests.post(
        f"{EMBEDDING_URL}/embed",
        json={"inputs": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def embed_texts(texts: list[str], batch_size: int = 8) -> list[list]:
    """Embed multiple texts in batches to avoid payload size limits."""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            all_embeddings.extend(_embed_via_openai(batch, timeout=120))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                all_embeddings.extend(_embed_via_tei(batch, timeout=120))
            else:
                raise
    return all_embeddings


def embed_query(text: str) -> list:
    return embed_texts([text])[0]


def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    """Extract text from PDF bytes. Uses OCR as fallback for scanned PDFs. Returns (full_text, page_count)."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []

    for page in doc:
        # Try normal text extraction first
        text = page.get_text().strip()

        # If no text found, try OCR
        if not text or len(text) < 50:
            # Render page to image
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))

            # OCR with German + English
            text = pytesseract.image_to_string(img, lang="deu+eng")

        if text.strip():
            pages.append(text.strip())

    doc.close()
    return "\n\n".join(pages), len(pages)


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    chunk_id = 0
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append({
                "id": chunk_id,
                "text": chunk_text.strip(),
                "start": start,
                "end": end,
            })
            chunk_id += 1
        start += chunk_size - overlap
    return chunks


def retrieve_hybrid(embedding: list, query: str, top_k: int = 30) -> list:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    emb_str = "[" + ",".join(str(x) for x in embedding) + "]"

    cur.execute("""
        SELECT id, text_clean, law_refs, section, doc_type,
               1 - (embedding <=> %s::vector) AS similarity
        FROM chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (emb_str, emb_str, top_k))
    vector_rows = cur.fetchall()

    cur.execute("""
        SELECT id, text_clean, law_refs, section, doc_type,
               ts_rank_cd(search_vector, query) AS bm25_score
        FROM chunks, plainto_tsquery('german', %s) query
        WHERE search_vector @@ query
          AND embedding IS NOT NULL
        ORDER BY ts_rank_cd(search_vector, query) DESC
        LIMIT %s
    """, (query, top_k))
    bm25_rows = cur.fetchall()

    cur.close()
    conn.close()

    k = 60
    scores = {}

    for rank, r in enumerate(vector_rows):
        cid = str(r[0])
        scores[cid] = {
            "id": r[0], "text": r[1],
            "law_refs": r[2] or [], "section": r[3] or "",
            "doc_type": r[4] or "",
            "similarity": round(r[5], 4),
            "rrf_score": 0.7 / (k + rank + 1),
            "sources": ["vector"],
        }

    for rank, r in enumerate(bm25_rows):
        cid = str(r[0])
        rrf = 0.3 / (k + rank + 1)
        if cid in scores:
            scores[cid]["rrf_score"] += rrf
            scores[cid]["sources"].append("bm25")
        else:
            scores[cid] = {
                "id": r[0], "text": r[1],
                "law_refs": r[2] or [], "section": r[3] or "",
                "doc_type": r[4] or "",
                "similarity": 0, "rrf_score": rrf,
                "sources": ["bm25"],
            }

    return sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)[:top_k]


def search_uploaded_docs(session_id: str, query_embedding: list, top_k: int = 5) -> list:
    """Search within uploaded documents for this session using vector similarity."""
    docs = document_store.get(session_id, [])
    if not docs:
        return []

    # Calculate cosine similarity
    query_vec = np.array(query_embedding)

    scored = []
    for doc in docs:
        doc_vec = np.array(doc["embedding"])
        # Cosine similarity
        similarity = float(np.dot(query_vec, doc_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(doc_vec)))
        scored.append({
            "text": doc["text"],
            "section": f"Uploaded: {doc['filename']}",
            "law_refs": [],
            "doc_type": "uploaded",
            "similarity": round(similarity, 4),
            "sources": ["uploaded"],
        })

    # Sort by similarity and return top_k
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def rerank_chunks(query: str, chunks: list, top_k: int = 3) -> list:
    texts = [c["text"] for c in chunks]
    try:
        resp = requests.post(
            f"{RERANKER_URL}/rerank",
            json={"query": query, "texts": texts, "truncate": True},
            timeout=30,
        )
        resp.raise_for_status()
        ranked = sorted(resp.json(), key=lambda x: x["score"], reverse=True)[:top_k]
        return [{**chunks[item["index"]], "rerank_score": round(item["score"], 4)} for item in ranked]
    except Exception:
        return [{**c, "rerank_score": 0.0} for c in chunks[:top_k]]


def generate_answer(messages: list) -> dict:
    start = time.time()
    resp = requests.post(
        f"{LLM_URL}/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.1,
            "frequency_penalty": 0.5,
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "content": data["choices"][0]["message"]["content"].strip(),
        "latency_s": round(time.time() - start, 2),
        "prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0),
        "completion_tokens": data.get("usage", {}).get("completion_tokens", 0),
    }

# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "model": LLM_MODEL}


@app.post("/upload", response_model=UploadResponse)
def upload_document(
    file: UploadFile = File(...),
    session_id: Optional[str] = None,
):
    """Upload a PDF document for analysis.

    Sync handler — FastAPI dispatches to a threadpool. The underlying
    SpooledTemporaryFile (`file.file`) is readable synchronously, so we
    don't need `await file.read()` (which only works in an async def).
    """
    # Validate file type
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Read file
    file_bytes = file.file.read()

    # Check file size
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB")

    # Get or create session
    sid = session_id or str(uuid.uuid4())

    try:
        # Extract text from PDF
        full_text, page_count = extract_pdf_text(file_bytes)

        if not full_text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")

        # Chunk the text
        chunks = chunk_text(full_text)

        if not chunks:
            raise HTTPException(status_code=400, detail="No text chunks could be created from PDF")

        # Embed all chunks
        chunk_texts = [c["text"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        # Store in document_store
        for chunk, embedding in zip(chunks, embeddings):
            document_store[sid].append({
                "filename": file.filename,
                "text": chunk["text"],
                "embedding": embedding,
                "chunk_id": chunk["id"],
            })

        return UploadResponse(
            session_id=sid,
            filename=file.filename,
            pages=page_count,
            chunks=len(chunks),
            message=f"Successfully processed {file.filename}: {page_count} pages, {len(chunks)} chunks",
        )

    except Exception as e:
        logger.error(f"Error processing PDF '{file.filename}': {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing PDF. Please ensure the file is valid.")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    t_total = time.time()

    # Get or create session
    session_id = req.session_id or str(uuid.uuid4())
    history = conversation_store[session_id]

    # ── Greeting path — skip RAG entirely ──────────────────────────────
    if is_greeting(req.question) and not history:
        # Only treat as greeting if no conversation history
        messages = [
            {"role": "system", "content": GREETING_SYSTEM_PROMPT},
            {"role": "user",   "content": req.question},
        ]
        result = generate_answer(messages)

        # Store in history
        history.append({"role": "user", "content": req.question})
        history.append({"role": "assistant", "content": result["content"]})
        if len(history) > MAX_HISTORY * 2:
            conversation_store[session_id] = history[-MAX_HISTORY * 2:]

        return QueryResponse(
            answer=result["content"],
            chunks=[],
            timings={"total_s": round(time.time() - t_total, 2)},
            tokens={"prompt": result["prompt_tokens"], "completion": result["completion_tokens"]},
            is_greeting=True,
            session_id=session_id,
        )

    # ── RAG path — full hybrid pipeline ────────────────────────────────
    try:
        t0 = time.time()
        embedding = embed_query(req.question)
        embed_s = round(time.time() - t0, 2)

        t1 = time.time()
        # Check if session has uploaded documents - search those FIRST
        uploaded_chunks = search_uploaded_docs(session_id, embedding, top_k=5)

        if uploaded_chunks:
            # Use uploaded documents primarily
            chunks = uploaded_chunks
            retrieve_s = round(time.time() - t1, 2)
            t2 = time.time()
            reranked = rerank_chunks(req.question, chunks, top_k=5)
            rerank_s = round(time.time() - t2, 2)
        else:
            # Fall back to database search
            chunks = retrieve_hybrid(embedding, req.question)
            retrieve_s = round(time.time() - t1, 2)
            t2 = time.time()
            reranked = rerank_chunks(req.question, chunks)
            rerank_s = round(time.time() - t2, 2)

        context = "\n\n".join([
            f"[Dokument {i+1}]"
            f"(Abschnitt: {c['section']}; Refs: {', '.join(c.get('law_refs', [])[:3])}):\n{c['text'][:800]}"
            for i, c in enumerate(reranked)
        ])

        # Build messages with conversation history
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
        ]
        # Add recent history (last 4 exchanges = 8 messages)
        recent_history = history[-8:] if history else []
        messages.extend(recent_history)
        messages.append({"role": "user", "content": req.question})

        result = generate_answer(messages)

        # Store in history
        history.append({"role": "user", "content": req.question})
        history.append({"role": "assistant", "content": result["content"]})
        if len(history) > MAX_HISTORY * 2:
            conversation_store[session_id] = history[-MAX_HISTORY * 2:]

        return QueryResponse(
            answer=result["content"],
            chunks=[
                ChunkInfo(
                    text=c["text"][:400],
                    section=c.get("section", ""),
                    law_refs=c.get("law_refs", []),
                    sources=c.get("sources", []),
                    similarity=c.get("similarity", 0.0),
                    rerank_score=c.get("rerank_score", 0.0),
                )
                for c in reranked
            ],
            timings={
                "embed_s":    embed_s,
                "retrieve_s": retrieve_s,
                "rerank_s":   rerank_s,
                "generate_s": result["latency_s"],
                "total_s":    round(time.time() - t_total, 2),
            },
            tokens={
                "prompt":     result["prompt_tokens"],
                "completion": result["completion_tokens"],
            },
            session_id=session_id,
        )

    except requests.RequestException as e:
        logger.error(f"Backend service error: {str(e)}")
        raise HTTPException(status_code=503, detail="Backend service temporarily unavailable")
    except psycopg2.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(status_code=503, detail="Database service temporarily unavailable")