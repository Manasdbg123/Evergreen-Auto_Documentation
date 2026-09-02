"use client";

import { useState } from "react";
import { Trash2, X, Check } from "lucide-react";
import { deleteVersion } from "@/lib/api";
import type { DocVersion } from "@/lib/types";
import { useUnauthorizedDialog } from "./UnauthorizedDialogContext";

interface Props {
  documentId: string;
  versions: DocVersion[];
  activeVersionId: string;
  onSelect: (versionId: string) => void;
  onVersionDeleted: () => void; // parent refetches the document after a successful delete
}

export default function VersionTabs({
  documentId,
  versions,
  activeVersionId,
  onSelect,
  onVersionDeleted,
}: Props) {
  const [confirmingVersionId, setConfirmingVersionId] = useState<string | null>(
    null,
  );

  const { showUnauthorizedDialog } = useUnauthorizedDialog();
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (versions.length <= 1) return null;

  const latestVersion = versions[versions.length - 1];

  async function handleConfirmDelete(versionId: string) {
    setIsDeleting(true);
    setError(null);
    try {
      await deleteVersion(documentId, versionId, showUnauthorizedDialog);
      setConfirmingVersionId(null);
      onVersionDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete version");
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className="border-b border-neutral-200">
      <div className="flex items-center gap-1 px-6 bg-white">
        {versions.map((v) => {
          const isActive = v.id === activeVersionId;
          const isLatest = v.id === latestVersion.id;
          const isConfirming = confirmingVersionId === v.id;

          return (
            <div key={v.id} className="flex items-center">
              {isConfirming ? (
                <div className="flex items-center gap-1.5 px-2 py-1.5 text-xs">
                  <span className="text-neutral-500">
                    {isLatest
                      ? `Delete v${v.versionNumber}?`
                      : `Delete v${v.versionNumber}? This may break the diff for later versions.`}
                  </span>
                  <button
                    onClick={() => handleConfirmDelete(v.id)}
                    disabled={isDeleting}
                    className="p-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40"
                    aria-label="Confirm delete"
                  >
                    <Check size={13} />
                  </button>
                  <button
                    onClick={() => setConfirmingVersionId(null)}
                    disabled={isDeleting}
                    className="p-1 rounded text-neutral-400 hover:bg-neutral-100"
                    aria-label="Cancel delete"
                  >
                    <X size={13} />
                  </button>
                </div>
              ) : (
                <div className="group flex items-center">
                  <button
                    onClick={() => onSelect(v.id)}
                    className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                      isActive
                        ? "border-neutral-900 text-neutral-900"
                        : "border-transparent text-neutral-400 hover:text-neutral-600"
                    }`}
                  >
                    v{v.versionNumber}
                    {v.status === "processing" && (
                      <span className="ml-1.5 w-1.5 h-1.5 rounded-full bg-amber-500 inline-block animate-pulse" />
                    )}
                  </button>

                  {/* only the latest version is deletable — matches backend constraint */}
                  {versions.length > 1 && (
                    <button
                      onClick={() => setConfirmingVersionId(v.id)}
                      className="p-1 mr-1 rounded text-neutral-300 opacity-0 group-hover:opacity-100 hover:text-red-600 hover:bg-red-50 transition-opacity"
                      aria-label={`Delete version ${v.versionNumber}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {error && <p className="px-6 pb-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
