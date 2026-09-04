import type {
  DiffResult,
  DocumentSummary,
  JobRecord,
  Sop,
  VersionInfo,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; surfacing the raw status alone
    // turns every server-side explanation into "500".
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* not JSON — keep the status line */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean; provider: string; offline: string }>("/api/health"),

  config: () => request<Record<string, unknown>>("/api/config"),

  listDocuments: () => request<DocumentSummary[]>("/api/documents"),

  getDocument: (documentId: string, version?: number) =>
    request<{ document: DocumentSummary; versions: VersionInfo[]; sop: Sop }>(
      `/api/documents/${documentId}${version ? `?version=${version}` : ""}`,
    ),

  createDocument: (body: { job_id?: string; title?: string; app?: string }) =>
    request<{ document_id: string; version: number }>("/api/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  renameDocument: (documentId: string, body: { title?: string; app?: string }) =>
    request<DocumentSummary>(`/api/documents/${documentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  saveDocument: (documentId: string, sop: Sop) =>
    request<{ version: number; edited_fields: string[] }>(
      `/api/documents/${documentId}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sop }),
      },
    ),

  getJob: (jobId: string) => request<JobRecord>(`/api/jobs/${jobId}`),

  upload: (file: File, options: { documentId?: string; offline?: boolean } = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (options.documentId) form.append("document_id", options.documentId);
    if (options.offline) form.append("offline", "true");
    return request<{ job_id: string; document_id: string | null }>("/api/jobs", {
      method: "POST",
      body: form,
    });
  },

  runDiff: (documentId: string, jobId: string, offline = false) =>
    request<{ diff_id: string; version: number; diff: DiffResult }>(
      `/api/documents/${documentId}/diff`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, offline }),
      },
    ),

  latestDiff: (documentId: string) =>
    request<DiffResult & { diff_id: string }>(`/api/documents/${documentId}/diff`),

  review: (diffId: string, decisions: Record<string, "accepted" | "rejected" | "pending">) =>
    request<{ version: number; rejected: number }>(`/api/diffs/${diffId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ diff_id: diffId, decisions }),
    }),

  exportUrl: (documentId: string, format: "markdown" | "html") =>
    `/api/documents/${documentId}/export?format=${format}`,
};
