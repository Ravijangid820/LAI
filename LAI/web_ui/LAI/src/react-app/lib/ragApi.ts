// src/react-app/lib/ragApi.ts

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://192.168.178.82:8000";

export interface Chunk {
  text: string;
  section: string;
  law_refs: string[];
  sources: string[];       // ["vector"] or ["vector", "bm25"]
  similarity: number;
  rerank_score: number;
}

export interface Timings {
  embed_s: number;
  retrieve_s: number;
  rerank_s: number;
  generate_s: number;
  total_s: number;
}

export interface RAGResponse {
  answer: string;
  chunks: Chunk[];
  timings: Timings;
  tokens: {
    prompt: number;
    completion: number;
  };
  session_id: string;
}

export interface UploadResponse {
  session_id: string;
  filename: string;
  pages: number;
  chunks: number;
  message: string;
}

export async function queryRAG(question: string, sessionId: string | null = null): Promise<RAGResponse> {
  const res = await fetch(`${BACKEND_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `Server error: ${res.status}`);
  }

  return res.json();
}

export async function uploadDocument(file: File, sessionId: string | null = null): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  const res = await fetch(`${BACKEND_URL}/upload`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `Upload failed: ${res.status}`);
  }

  return res.json();
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { method: "GET" });
    return res.ok;
  } catch {
    return false;
  }
}