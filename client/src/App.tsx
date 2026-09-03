import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { DiffReview } from "./components/DiffReview";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SopEditor } from "./components/SopEditor";
import { ThemeToggle } from "./components/ThemeToggle";
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
        {provider && <span className="provider">{provider}</span>}
        <ThemeToggle />
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

              {/* A diff describes one step forward in the history, so it does
                  not belong on a version it predates. Viewing v1 while the
                  latest diff runs v1 -> v3 was showing changes that have not
                  happened yet from that version's point of view. */}
              {diff && sop.version >= diff.result.new_version && (
                <ErrorBoundary resetKey={diff.id}>
                  <DiffReview
                    diffId={diff.id}
                    diff={diff.result}
                    onReviewed={() => openDocument(documentId)}
                  />
                </ErrorBoundary>
              )}

              {/* Keyed on the version so switching versions remounts the
                  editor. A single editor reused across versions would carry
                  the previous version's undo history, and undoing into it
                  then saving would write the wrong text back. */}
              <ErrorBoundary resetKey={`${sop.sop_id}:${sop.version}`}>
                <SopEditor
                  key={`${sop.sop_id}:${sop.version}`}
                  documentId={documentId}
                  sop={sop}
                  diffEntries={diff?.result.entries}
                  onSaved={() => openDocument(documentId)}
                />
              </ErrorBoundary>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
