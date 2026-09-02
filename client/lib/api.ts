const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:4000";

import type {
  Document,
  DocumentSummary,
  UploadResponse,
  DiffResult,
} from "./types";

export async function listDocuments(): Promise<DocumentSummary[]> {
  const res = await fetch(`${API_BASE}/api/v1/documents`, {
    cache: "no-store",
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(`Failed to list documents: ${res.status}`);
  return res.json();
}

export async function fetchDocument(
  documentId: string,
  showUnauthorizedDialog: () => void,
): Promise<Document> {
  const res = await fetch(`${API_BASE}/api/v1/documents/${documentId}`, {
    cache: "no-store",
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (res.status === 401) showUnauthorizedDialog();
  if (!res.ok) throw new Error(`Failed to fetch document: ${res.status}`);
  return res.json();
}

export async function createDocument(
  file: File,
  title: string,
  showUnauthorizedDialog: () => void,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("video", file);
  formData.append("title", title);

  const res = await fetch(`${API_BASE}/api/v1/documents`, {
    method: "POST",
    body: formData,
    credentials: "include",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (res.status === 401) showUnauthorizedDialog();
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
}

// separate endpoint from createDocument — this appends a new version to an EXISTING document
export async function regenerateDocument(
  documentId: string,
  file: File,
  showUnauthorizedDialog: () => void,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("video", file);

  const res = await fetch(
    `${API_BASE}/api/v1/documents/${documentId}/versions`,
    {
      method: "POST",
      body: formData,
      credentials: "include",
      headers: { "ngrok-skip-browser-warning": "true" },
    },
  );
  if (res.status === 401) showUnauthorizedDialog();
  if (!res.ok) throw new Error(`Regenerate failed: ${res.status}`);
  return res.json();
}

export async function fetchDiff(
  documentId: string,
  versionId: string,
  showUnauthorizedDialog: () => void,
): Promise<DiffResult> {
  const res = await fetch(
    `${API_BASE}/api/v1/documents/${documentId}/versions/${versionId}/diff`,
    {
      cache: "no-store",
      credentials: "include",
      headers: { "ngrok-skip-browser-warning": "true" },
    },
  );
  if (res.status === 401) showUnauthorizedDialog();
  if (!res.ok) throw new Error(`Failed to fetch diff: ${res.status}`);
  return res.json();
}

// lib/api.ts
export async function deleteVersion(
  documentId: string,
  versionId: string,
  showUnauthorizedDialog: () => void,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/v1/documents/${documentId}/versions/${versionId}`,
    {
      method: "DELETE",
      credentials: "include",
      headers: { "ngrok-skip-browser-warning": "true" },
    },
  );
  if (res.status === 401) showUnauthorizedDialog();
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Delete failed: ${res.status}`);
  }
}

export type AiActionType = "rewrite" | "expand" | "simplify";

export async function callAiRewriteApi(
  text: string,
  action: AiActionType,
): Promise<string> {
  const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:4000";

  const res = await fetch(`${API_BASE}/api/v1/ai/rewrite`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    body: JSON.stringify({ text, action }),
    credentials: "include",
  });

  if (!res.ok) {
    throw new Error(`AI rewrite failed: ${res.status}`);
  }

  const data = await res.json();
  return data.newText as string;
}

export async function saveDocumentContent(
  documentId: string,
  versionId: string,
  contentJson: object,
): Promise<void> {
  const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:4000";

  const res = await fetch(
    `${API_BASE}/api/v1/documents/${documentId}/versions/${versionId}/content`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify({ content: contentJson }),
      credentials: "include",
    },
  );

  if (!res.ok) {
    throw new Error(`Failed to save: ${res.status}`);
  }
}
