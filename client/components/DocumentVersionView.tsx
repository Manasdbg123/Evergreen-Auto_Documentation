// components/DocumentVersionView.tsx
import NotionEditor from "@/components/editor/NotionEditor";
import { saveDocumentContent } from "@/lib/api";
import { stepsToInitialDoc } from "@/lib/tiptap/stepsToInitialDoc";
import type { DocVersion } from "@/lib/types";
import { debounce } from "lodash";
import { useMemo } from "react";
import FloatingVideoPlayer from "./editor/FloatingVideoPlayer";
import ErrorBox from "./ErrorBox";

export default function DocumentVersionView({
  version,
  documentTitle,
  documentId,
}: {
  version: DocVersion;
  documentId: string;
  documentTitle: string;
}) {
  const debouncedSave = useMemo(
    () =>
      debounce((json: object) => {
        saveDocumentContent(documentId, version.id, json).catch(console.error);
      }, 1000),
    [documentId, version.id],
  );

  if (version.status === "processing") {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-neutral-500">
          Generating this version — extracting steps from the recording...
        </p>
      </div>
    );
  }

  if (version.status === "failed") {
    return <ErrorBox error={version.errorMessage ?? "unknown error"} />;
  }

  return (
    <div>
      <NotionEditor
        initialContent={version.contentJson ?? stepsToInitialDoc(version.steps)}
        onChange={debouncedSave}
        documentTitle={documentTitle}
      />
      {version.sourceVideoUrl && (
        <FloatingVideoPlayer videoUrl={version.sourceVideoUrl} />
      )}
    </div>
  );
}
