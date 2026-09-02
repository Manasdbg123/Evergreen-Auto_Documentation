"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useDocument } from "@/hooks/useDocument";
import VersionTabs from "@/components/VersionTabs";
import DocumentVersionView from "@/components/DocumentVersionView";
import DiffView from "@/components/DiffView";
import ErrorBox from "@/components/ErrorBox";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { document, error, refresh } = useDocument(id);
  const [activeVersionId, setActiveVersionId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"document" | "diff">("document");

  // default to the latest version whenever the doc loads or a new version appears
  useEffect(() => {
    if (!document) return;
    const latest = document.versions[document.versions.length - 1];
    // if the previously active version no longer exists (was deleted), fall back to latest
    const stillExists = document.versions.some((v) => v.id === activeVersionId);
    if (!activeVersionId || !stillExists) {
      setActiveVersionId(latest.id);
      setViewMode("document");
    }
  }, [document, activeVersionId]);

  if (error) return <ErrorBox error={error} />;
  if (!document) return <CenteredMessage text="Loading..." />;

  const activeVersion =
    document.versions.find((v) => v.id === activeVersionId) ??
    document.versions[document.versions.length - 1];
  const activeVersionIndex = document.versions.findIndex(
    (v) => v.id === activeVersion.id,
  );
  const canShowDiff =
    activeVersionIndex > 0 && activeVersion.status === "ready";

  return (
    <div className="flex flex-col h-full">
      <nav className="flex items-center justify-between px-6 py-[14.5px] border-b border-neutral-200 mt-2">
        <h1 className="text-sm font-semibold text-neutral-900">
          {document.title}
        </h1>
        <Link
          href={`/document/${id}/regenerate`}
          className="px-3 py-1.5 mr-21 rounded-lg bg-[#DA5CC7] text-white text-xs font-medium hover:bg-[#DA5CC7]/90"
        >
          Generate new version
        </Link>
      </nav>
      <VersionTabs
        documentId={id}
        versions={document.versions}
        activeVersionId={activeVersion.id}
        onSelect={(vid) => {
          setActiveVersionId(vid);
          setViewMode("document");
        }}
        onVersionDeleted={refresh}
      />

      {canShowDiff && (
        <div className="flex gap-1 px-6 py-3 bg-white shadow border-b border-neutral-200">
          <ToggleButton
            active={viewMode === "document"}
            onClick={() => setViewMode("document")}
            label="Document"
          />
          <ToggleButton
            active={viewMode === "diff"}
            onClick={() => setViewMode("diff")}
            label="What changed"
          />
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {viewMode === "diff" && canShowDiff ? (
          <DiffView documentId={id} versionId={activeVersion.id} />
        ) : (
          <DocumentVersionView
            version={activeVersion}
            documentTitle={document.title}
            documentId={document.id}
          />
        )}
      </div>
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-xs font-medium rounded-md ${
        active
          ? "bg-neutral-900 text-white"
          : "text-neutral-500 hover:bg-neutral-100"
      }`}
    >
      {label}
    </button>
  );
}

function CenteredMessage({
  text,
  isError = false,
}: {
  text: string;
  isError?: boolean;
}) {
  return (
    <div className="h-full flex items-center justify-center">
      <p className={`text-sm ${isError ? "text-red-600" : "text-neutral-500"}`}>
        {text}
      </p>
    </div>
  );
}
