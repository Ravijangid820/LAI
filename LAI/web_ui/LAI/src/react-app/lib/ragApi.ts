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
  // Backend reports which routing decision it made.
  // "chat" = no retrieval; "rag" = corpus retrieval;
  // "contract" = uses uploaded contract only;
  // "rag+contract" = both.
  mode?: "chat" | "rag" | "contract" | "rag+contract";
}

export interface ClauseIssue {
  severity: "low" | "medium" | "high";
  description: string;
  recommendation?: string;
  reason?: string;
  type?: string;
}

export interface AnalyzedClause {
  id: string;
  title: string;
  text: string;
  type: string;
  summary: string;
  issues: ClauseIssue[];
  citations: string[];
}

export interface AnalyzeResponse {
  session_id: string;
  filename: string;
  n_clauses: number;
  clauses: AnalyzedClause[];
  missing_required_clauses: ClauseIssue[];
  elapsed_s: number;
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

export async function analyzeContract(sessionId: string): Promise<AnalyzeResponse> {
  const res = await fetch(`${BACKEND_URL}/analyze-contract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `Analysis failed: ${res.status}`);
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


// ── Session rehydration (persistence across UI refreshes) ────────────────

export interface PersistedMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  mode: string | null;
  created_at: number;
}

export interface SessionDetail {
  session_id: string;
  filename: string | null;
  n_pages: number;
  uploaded_at: number | null;
  has_analysis: boolean;
  analyzer_version: string | null;
  messages: PersistedMessage[];
}

export interface SessionSummary {
  id: string;
  filename: string | null;
  n_pages: number;
  uploaded_at: number;
  updated_at: number;
  has_analysis: boolean;
  n_messages: number;
}

export async function getSession(sessionId: string): Promise<SessionDetail | null> {
  try {
    const res = await fetch(`${BACKEND_URL}/sessions/${sessionId}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`Session fetch failed: ${res.status}`);
    return res.json();
  } catch {
    return null;
  }
}

export async function listSessions(limit = 50): Promise<SessionSummary[]> {
  try {
    const res = await fetch(`${BACKEND_URL}/sessions?limit=${limit}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.sessions || [];
  } catch {
    return [];
  }
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/sessions/${sessionId}`, { method: "DELETE" });
    return res.ok;
  } catch {
    return false;
  }
}