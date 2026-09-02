"use client";

import type { Editor } from "@tiptap/react";
import { Download } from "lucide-react";
import { jsonToMarkdown } from "@/lib/tiptap/jsonToMarkdown";
import { downloadMarkdown } from "@/lib/downloadMarkdown";

export default function Toolbar({
  editor,
  documentTitle,
}: {
  editor: Editor;
  documentTitle: string;
}) {
  function handleDownload() {
    const markdown = jsonToMarkdown(editor.getJSON());
    downloadMarkdown(markdown, documentTitle);
  }

  return (
    <div className="fixed bottom-21 right-6 z-50 flex flex-col items-end gap-3">
      <button
        onClick={handleDownload}
        className="w-12 h-12 rounded-full bg-neutral-900 hover:bg-neutral-800 text-white flex items-center justify-center shadow-lg transition-colors shrink-0"
      >
        <Download size={18} />
      </button>
    </div>
  );
}
