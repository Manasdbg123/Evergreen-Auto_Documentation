import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { DiffReview } from "./components/DiffReview";
import { DocumentName } from "./components/DocumentName";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SopEditor } from "./components/SopEditor";
import { ThemeToggle } from "./components/ThemeToggle";
import { Upload } from "./components/Upload";
import type { DiffResult, DocumentSummary, Sop, VersionInfo } from "./types";

/**
 * What kind of version this is, in the user's words rather than the database's.
 *
 * Three labels, because there are only three things that can produce a
 * version: a video, a person, or a video merged over a person's work. An
 * earlier version of this also distinguished the first recording from later
 * ones, which said nothing the card's position in the row did not already say.
 */
function versionLabel(source: string): string {
  if (source === "edited") return "Your edit";
  if (source === "merged") return "After update";
  return "From recording";
}

function versionDate(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [sop, setSop] = useState<Sop | null>(null);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [diff, setDiff] = useState<{ id: string; result: DiffResult } | null>(null);
  const [provider, setProvider] = useState("");
  const [error, setError] = useState("");
  const [editingName, setEditingName] = useState(false);
  /** Which app the next recording joins. Follows the open document, and can
   *  be typed over to start a new one. */
  const [newApp, setNewApp] = useState("");

  const openDoc = documents.find((d) => d.document_id === documentId) ?? null;
  const openApp = openDoc?.app ?? "";

  // Opening a document points the field at its app, so recording several
  // workflows for one website needs no retyping. Typing over it is what
  // starts a new one.
  useEffect(() => {
    if (openApp) setNewApp(openApp);
  }, [openApp]);

  /** Documents by app, groups alphabetical, ungrouped last. */
  const groups = useMemo(() => {
    const by = new Map<string, DocumentSummary[]>();
    for (const doc of documents) {
      const key = doc.app || "";
      (by.get(key) ?? by.set(key, []).get(key)!).push(doc);
    }
    return [...by.entries()].sort(([a], [b]) => {
      if (!a) return 1;
      if (!b) return -1;
      return a.localeCompare(b);
    });
  }, [documents]);

  const knownApps = useMemo(
    () => [...new Set(documents.map((d) => d.app).filter(Boolean))].sort(),
    [documents],
  );

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

  /** A first recording: create the document from the finished job.
   *
   *  It lands in whichever app is currently open, because recording several
   *  workflows for one product is the normal case and re-filing each one
   *  afterwards would be busywork. */
  const onFirstUpload = useCallback(
    async (jobId: string) => {
      try {
        const created = await api.createDocument({
          job_id: jobId,
          app: newApp.trim(),
        });
        await refreshDocuments();
        await openDocument(created.document_id);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [newApp, openDocument, refreshDocuments],
  );

  const saveName = useCallback(
    async (title: string, app: string) => {
      if (!documentId) return;
      try {
        await api.renameDocument(documentId, { title, app });
        setEditingName(false);
        await refreshDocuments();
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [documentId, refreshDocuments],
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

          {/* Which website the next recording belongs to. Named here rather
              than afterwards, because "I am about to document Salesforce" is
              the thought that comes first. A group still has no existence of
              its own — typing a new name here creates one the moment the
              recording lands, so there is never an empty group to manage. */}
          <label className="target-app">
            <span>Website or app</span>
            <input
              value={newApp}
              list="known-apps"
              placeholder="e.g. LeetCode"
              onChange={(e) => setNewApp(e.target.value)}
            />
            <datalist id="known-apps">
              {knownApps.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
          </label>

          <Upload
            label={
              newApp.trim() ? `New recording in ${newApp.trim()}` : "New recording"
            }
            onComplete={onFirstUpload}
          />

          {/* Grouped by app, because a library covering several products is a
              flat list of names otherwise — and the names are procedures, so
              nothing in them says which product they belong to. */}
          {groups.map(([app, docs]) => (
            <section className="group" key={app || "__none__"}>
              <h3 className="group-name">{app || "Not grouped"}</h3>
              <ul className="doclist">
                {docs.map((doc) => (
                  <li key={doc.document_id}>
                    <button
                      className={doc.document_id === documentId ? "on" : ""}
                      onClick={() => openDocument(doc.document_id)}
                    >
                      {doc.title}
                      <span className="hint">v{doc.latest_version ?? 0}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}

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
                <DocumentName
                  title={openDoc?.title ?? sop.title}
                  app={openApp}
                  knownApps={knownApps}
                  editing={editingName}
                  onEdit={() => setEditingName(true)}
                  onCancel={() => setEditingName(false)}
                  onSave={saveName}
                />
                <p className="hint">{sop.summary}</p>
                <div className="history">
                  <span className="history-label">History</span>
                  <div className="versions">
                    {/* Oldest first, so the row reads left to right as the
                        document's story: generated, then edited, then updated
                        from a new recording. */}
                    {[...versions]
                      .sort((a, b) => a.version - b.version)
                      .map((v) => (
                        <button
                          key={v.version_id}
                          className={v.version === sop.version ? "on" : ""}
                          onClick={() => openDocument(documentId, v.version)}
                          title={`Version ${v.version} — ${versionLabel(v.source)}${
                            v.title ? `\n${v.title}` : ""
                          }${v.job_id ? `\nfrom ${v.job_id}` : ""}`}
                        >
                          <span className="v-n">{v.version}</span>
                          {/* The workflow this version documents. Normally the
                              same across a document; when one differs, two
                              different workflows were recorded into it. */}
                          <span className="v-what">
                            {v.title || versionLabel(v.source)}
                          </span>
                          <span className="v-when">
                            {versionLabel(v.source)} · {versionDate(v.created_at)}
                          </span>
                        </button>
                      ))}
                  </div>
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
