import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { DiffReview } from "./components/DiffReview";
import { SopEditor } from "./components/SopEditor";
import { Upload } from "./components/Upload";
import type { DiffResult, DocumentSummary, Sop, VersionInfo } from "./types";

export default function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [sop, setSop] = useState<Sop | null>(null);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [diff, setDiff] = useState<{ id: string; result: DiffResult } | null>(null);
  const [provider, setProvider] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.health().then((h) => setProvider(`${h.provider} (offline: ${h.offline})`)).catch(() => {});
  }, []);

  const refreshDocuments = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refreshDocuments();
  }, [refreshDocuments]);

  const openDocument = useCallback(async (id: string, version?: number) => {
    setError("");
    try {
      const body = await api.getDocument(id, version);
      setDocumentId(id);
      setSop(body.sop);
      setVersions(body.versions);
      try {
        const latest = await api.latestDiff(id);
        setDiff({ id: latest.diff_id, result: latest });
      } catch {
        setDiff(null); // no diff recorded yet — the normal case for a new doc
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  /** A first recording: create the document from the finished job. */
  const onFirstUpload = useCallback(
    async (jobId: string) => {
      try {
        const created = await api.createDocument({ job_id: jobId });
        await refreshDocuments();
        await openDocument(created.document_id);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [openDocument, refreshDocuments],
  );

  /** A re-recording: diff it against the open document. This is the demo. */
  const onReupload = useCallback(
    async (jobId: string) => {
      if (!documentId) return;
      try {
        const result = await api.runDiff(documentId, jobId);
        setDiff({ id: result.diff_id, result: result.diff });
        await openDocument(documentId);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [documentId, openDocument],
  );

  return (
    <div className="app">
      <header>
        <h1>Evergreen</h1>
        <span className="hint">
          Screen recordings in, structured SOPs out — with a reviewable diff when
          the workflow is recorded again.
        </span>
        {provider && <span className="hint">model: {provider}</span>}
      </header>

      {error && <p className="error">{error}</p>}

      <div className="layout">
        <aside>
          <h2>Documents</h2>
          <Upload label="New recording" onComplete={onFirstUpload} />
          <ul className="doclist">
            {documents.map((doc) => (
              <li key={doc.document_id}>
                <button
                  className={doc.document_id === documentId ? "on" : ""}
                  onClick={() => openDocument(doc.document_id)}
                >
                  {doc.title}
                  <span className="hint"> v{doc.latest_version ?? 0}</span>
                </button>
              </li>
            ))}
          </ul>
          {documents.length === 0 && (
            <p className="note">
              No documents yet. Upload a screen recording to make one.
            </p>
          )}
        </aside>

        <main>
          {!sop || !documentId ? (
            <p className="note">Select a document, or upload a recording.</p>
          ) : (
            <>
              <div className="dochead">
                <h2>{sop.title}</h2>
                <p className="hint">{sop.summary}</p>
                <div className="versions">
                  {versions.map((v) => (
                    <button
                      key={v.version_id}
                      className={v.version === sop.version ? "on" : ""}
                      onClick={() => openDocument(documentId, v.version)}
                      title={v.source}
                    >
                      v{v.version}
                    </button>
                  ))}
                </div>
                <Upload
                  documentId={documentId}
                  label="Re-record this workflow"
                  onComplete={onReupload}
                />
              </div>

              {diff && (
                <DiffReview
                  diffId={diff.id}
                  diff={diff.result}
                  onReviewed={() => openDocument(documentId)}
                />
              )}

              <SopEditor
                documentId={documentId}
                sop={sop}
                diffEntries={diff?.result.entries}
                onSaved={() => openDocument(documentId)}
              />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
