#!/usr/bin/env python3
"""
FastAPI Backend — Legal AI RAG
Runs on SSH server, called by React frontend on local machine
"""
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional
import os, time, requests, psycopg2, psycopg2.extras, re, uuid
import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image
import io
import logging
from ddiq_report import router as ddiq_router

# Auth — every protected route depends on ``get_current_user`` (4a).
# The dependency lives in a shared module so api.py and the imported
# ddiq_router resolve to the same TokenIssuer/secret instance.
from auth_dep import get_current_user
from lai.common.auth import CurrentUser
from lai.api.auth_router import register_auth_exception_handlers

logger = logging.getLogger("lai_api")

load_dotenv()

app = FastAPI(title="Legal AI RAG API", version="1.0.0")

# Translate auth-module exceptions into 401s app-wide. Must run before
# include_router so the ddiq sub-router inherits the same handler.
register_auth_exception_handlers(app)

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
    _cors_origins.extend(["http://localhost:3000", "http://localhost:5173","http://192.168.178.82:5173"])

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
# Persistence — AUTH_PLAN §6.1
# ─────────────────────────────────────────────
# Conversation history and per-session uploaded documents live in
# Postgres, keyed by ``user_id`` (from the JWT) and ``conversation_id``
# (server-issued). The previous in-memory ``conversation_store`` and
# ``document_store`` dicts are gone — they had no tenant binding and
# were lost on restart.
MAX_HISTORY = 10  # Keep last 10 messages per conversation in the prompt window
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB (bumped from 50 — real binders hit the cap)


def _ensure_conversation(user_id, conversation_id):
    """Resolve-or-create a conversation owned by ``user_id``.

    Returns the conversation UUID as a string. If ``conversation_id`` is
    provided but does not belong to ``user_id``, returns ``None``
    (caller maps to 404 — no leaking existence of other users' rows).
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            if conversation_id:
                cur.execute(
                    "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
                    (conversation_id, str(user_id)),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
            cur.execute(
                "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id",
                (str(user_id),),
            )
            return str(cur.fetchone()[0])
    finally:
        conn.close()


def _load_history(conversation_id):
    """Return the last ``MAX_HISTORY * 2`` messages in chronological order."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (conversation_id, MAX_HISTORY * 2),
            )
            rows = cur.fetchall()
        # Reverse to chronological order.
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    finally:
        conn.close()


def _append_message(conversation_id, role, content):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO messages (conversation_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (conversation_id, role, content),
            )
            cur.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                (conversation_id,),
            )
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    # Conversation handle (server-issued). Optional — a missing value
    # creates a fresh conversation owned by the caller. The name is
    # kept as ``session_id`` to preserve the existing client contract
    # while the field semantics change to "conversation id".
    session_id: Optional[str] = None

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
    session_id: str = ""        # echoes the (possibly newly created) conversation id

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


def search_uploaded_docs(
    user_id: str,
    conversation_id: str,
    query_embedding: list,
    top_k: int = 5,
) -> list:
    """Search this user's uploaded chunks for the given conversation.

    Uses pgvector's cosine distance operator (``<=>``). The join on
    ``ddiq_documents`` enforces the tenant filter — even if a caller
    fabricates a ``conversation_id``, only their own documents come
    back.
    """
    emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.text, d.filename,
                       1 - (c.embedding <=> %s::vector) AS similarity
                FROM ddiq_doc_chunks c
                JOIN ddiq_documents d ON d.id = c.doc_id
                WHERE d.user_id = %s
                  AND d.session_id = %s
                  AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (emb_str, str(user_id), conversation_id, emb_str, top_k),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "text": r[0],
            "section": f"Uploaded: {r[1]}",
            "law_refs": [],
            "doc_type": "uploaded",
            "similarity": round(float(r[2]), 4),
            "sources": ["uploaded"],
        }
        for r in rows
    ]


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
    user: CurrentUser = Depends(get_current_user),
):
    """Upload a PDF document for chat-context augmentation.

    Sync handler — FastAPI dispatches to a threadpool. The underlying
    SpooledTemporaryFile (``file.file``) is readable synchronously.

    Tenant isolation (AUTH_PLAN G1/G3): the row's ``user_id`` is taken
    from the JWT, never from the request body. The optional
    ``session_id`` parameter is interpreted as a conversation id and
    is validated to belong to the calling user — an unknown or
    other-user id 404s.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_bytes = file.file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB",
        )

    conv_id = _ensure_conversation(user.id, session_id)
    if conv_id is None:
        # session_id was supplied but does not belong to this user.
        raise HTTPException(status_code=404, detail="conversation not found")

    try:
        full_text, page_count = extract_pdf_text(file_bytes)
        if not full_text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")

        chunks = chunk_text(full_text)
        if not chunks:
            raise HTTPException(status_code=400, detail="No text chunks could be created from PDF")

        chunk_texts = [c["text"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ddiq_documents (
                        user_id, filename, size_bytes, status, category,
                        full_text, chunk_count, session_id
                    )
                    VALUES (%s, %s, %s, 'ready', 'Chat upload', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        str(user.id), file.filename, len(file_bytes),
                        full_text, len(chunks), conv_id,
                    ),
                )
                doc_id = cur.fetchone()[0]
                # Batch-insert chunks. ``psycopg2.extras.execute_values``
                # is materially faster than per-row INSERTs once N > ~20.
                values = [
                    (str(doc_id), c["id"], c["text"], "[" + ",".join(str(x) for x in emb) + "]")
                    for c, emb in zip(chunks, embeddings)
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO ddiq_doc_chunks (doc_id, chunk_idx, text, embedding)
                    VALUES %s
                    """,
                    values,
                    template="(%s, %s, %s, %s::vector)",
                )
        finally:
            conn.close()

        return UploadResponse(
            session_id=conv_id,
            filename=file.filename,
            pages=page_count,
            chunks=len(chunks),
            message=f"Successfully processed {file.filename}: {page_count} pages, {len(chunks)} chunks",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing PDF '{file.filename}': {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing PDF. Please ensure the file is valid.")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    t_total = time.time()

    # Resolve or create the conversation. AUTH_PLAN G4: the conversation
    # id alone is no longer a capability — the JWT must agree.
    conv_id = _ensure_conversation(user.id, req.session_id)
    if conv_id is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    history = _load_history(conv_id)

    # ── Greeting path — skip RAG entirely ──────────────────────────────
    if is_greeting(req.question) and not history:
        messages = [
            {"role": "system", "content": GREETING_SYSTEM_PROMPT},
            {"role": "user",   "content": req.question},
        ]
        result = generate_answer(messages)

        _append_message(conv_id, "user", req.question)
        _append_message(conv_id, "assistant", result["content"])

        return QueryResponse(
            answer=result["content"],
            chunks=[],
            timings={"total_s": round(time.time() - t_total, 2)},
            tokens={"prompt": result["prompt_tokens"], "completion": result["completion_tokens"]},
            is_greeting=True,
            session_id=conv_id,
        )

    # ── RAG path — full hybrid pipeline ────────────────────────────────
    try:
        t0 = time.time()
        embedding = embed_query(req.question)
        embed_s = round(time.time() - t0, 2)

        t1 = time.time()
        # Search this user's uploaded chunks for THIS conversation first.
        uploaded_chunks = search_uploaded_docs(user.id, conv_id, embedding, top_k=5)

        if uploaded_chunks:
            chunks = uploaded_chunks
            retrieve_s = round(time.time() - t1, 2)
            t2 = time.time()
            reranked = rerank_chunks(req.question, chunks, top_k=5)
            rerank_s = round(time.time() - t2, 2)
        else:
            # Fall back to shared corpus (no PII; same data for all users).
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

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
        ]
        # Last 4 exchanges (8 messages) from the persisted history.
        messages.extend(history[-8:])
        messages.append({"role": "user", "content": req.question})

        result = generate_answer(messages)

        _append_message(conv_id, "user", req.question)
        _append_message(conv_id, "assistant", result["content"])

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
            session_id=conv_id,
        )

    except requests.RequestException as e:
        logger.error(f"Backend service error: {str(e)}")
        raise HTTPException(status_code=503, detail="Backend service temporarily unavailable")
    except psycopg2.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(status_code=503, detail="Database service temporarily unavailable")