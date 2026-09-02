"use client";

import { useEditor, EditorContent } from "@tiptap/react";
import type { JSONContent } from "@tiptap/core";
import { documentExtensions } from "@/lib/tiptap/documentExtensions";
import Toolbar from "./Toolbar";

interface NotionEditorProps {
  initialContent?: JSONContent | null;
  onChange?: (json: JSONContent) => void;
  documentTitle: string;
}

export default function NotionEditor({
  initialContent,
  onChange,
  documentTitle,
}: NotionEditorProps) {
  const editor = useEditor({
    extensions: documentExtensions,
    content: initialContent ?? {
      type: "doc",
      content: [
        { type: "heading", attrs: { level: 1 } },
        { type: "paragraph" },
      ],
    },
    onUpdate: ({ editor, transaction }) => {
      if (!transaction.docChanged) return;
      onChange?.(editor.getJSON());
    },
    editorProps: {
      attributes: {
        class: "focus:outline-none prose-headings:font-semibold",
      },
    },
    immediatelyRender: false,
  });

  if (!editor) return null;

  return (
    <div>
      <Toolbar editor={editor} documentTitle={documentTitle} />
      <div className="max-w-2xl mx-auto p-8">
        <EditorContent
          editor={editor}
          className="[&_h1]:text-3xl [&_h1]:font-bold [&_h1]:mb-4 [&_p]:text-sm [&_p]:leading-relaxed [&_p]:text-neutral-700 [&_p]:mb-3"
        />
        <AddImageButton editor={editor} />
      </div>
    </div>
  );
}

function AddImageButton({
  editor,
}: {
  editor: import("@tiptap/react").Editor;
}) {
  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    // simplest possible path for now — object URL, swap for a real upload
    // (e.g. your existing Cloudinary route) before persisting
    const localUrl = URL.createObjectURL(file);
    editor.chain().focus().setImage({ src: localUrl }).run();
  }

  return (
    <label className="inline-flex items-center gap-1.5 mt-2 text-xs text-neutral-400 hover:text-neutral-600 cursor-pointer">
      + Add image
      <input
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFileSelect}
      />
    </label>
  );
}
