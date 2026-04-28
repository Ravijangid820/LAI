// src/react-app/lib/ddiqApi.ts
// DDiQ Report API — connects to the lai-backend microservice's /ddiq/* endpoints.
//
// The DDiQ backend lives on a SEPARATE port from the conversational
// backend (`serve_rag` on :18000). Default :18001 here. Override with
// VITE_DDIQ_URL for non-default deployments.

const BACKEND_URL =
  import.meta.env.VITE_DDIQ_URL ||
  // Fallback derives sibling port from VITE_BACKEND_URL (same host, swap port).
  (import.meta.env.VITE_BACKEND_URL || "http://192.168.178.82:18001").replace(
    /:(?:\d+)$/,
    ":18001",
  );

// ─── Types (match backend Pydantic models exactly) ──────────────────────────

import type {
    DDiQReportData,
    DocumentItem,
} from "@/react-app/lib/ddiqDemoData";

export interface DocumentListResponse {
    documents: DocumentItem[];
    total: number;
}

export interface UploadDocResponse {
    id: string;
    filename: string;
    pages: number;
    chunks: number;
    status: string;
    message: string;
}

export interface GenerateReportResponse {
    report_id: string;
    report: DDiQReportData;
    timings: Record<string, number>;
}

export interface GenerateReportRequest {
    document_ids: string[];
    preset?: string;
    project_name?: string;
    prepared_for?: string;
}

// ─── API Functions ──────────────────────────────────────────────────────────

/** List all uploaded DDiQ documents */
export async function fetchDocuments(): Promise<DocumentListResponse> {
    const res = await fetch(`${BACKEND_URL}/ddiq/documents`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Failed to fetch documents: ${res.status}`);
    }
    return res.json();
}

/** Upload a PDF for DDiQ analysis */
export async function uploadDDiQDocument(
    file: File,
    category: string = "Uncategorized",
    sessionId?: string,
): Promise<UploadDocResponse> {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("category", category);
    if (sessionId) formData.append("session_id", sessionId);

    const res = await fetch(`${BACKEND_URL}/ddiq/documents/upload`, {
        method: "POST",
        body: formData,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Upload failed: ${res.status}`);
    }
    return res.json();
}

/** Generate a DDiQ report from selected documents */
export async function generateReport(
    req: GenerateReportRequest,
): Promise<GenerateReportResponse> {
    const res = await fetch(`${BACKEND_URL}/ddiq/report/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Report generation failed: ${res.status}`);
    }
    return res.json();
}

/** Retrieve a previously generated report */
export async function fetchReport(reportId: string): Promise<{
    report_id: string;
    created_at: string;
    project_name: string;
    report: DDiQReportData;
}> {
    const res = await fetch(`${BACKEND_URL}/ddiq/report/${reportId}`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Report not found: ${res.status}`);
    }
    return res.json();
}