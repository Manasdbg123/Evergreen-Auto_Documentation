"use client";

import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Color from "@tiptap/extension-color";
import { TextStyle } from "@tiptap/extension-text-style";
import Highlight from "@tiptap/extension-highlight";
import { useMemo } from "react";
import debounce from "lodash/debounce";
import type { JSONContent } from "@tiptap/core";

import {
  StepBody,
  StepImage,
  StepNode,
  StepTitle,
} from "@/lib/tiptap/StepNode";
import { AiRewriteExtension } from "@/lib/tiptap/AiRewriteExtension";
// import SelectionToolbar from "./SelectionToolbar";
import { saveDocumentContent } from "@/lib/api";
import type { Step } from "@/lib/types";

interface ArticleEditorProps {
  documentId: string;
  versionId: string;
  steps: Step[];
  initialContent?: JSONContent | null; // saved ProseMirror JSON, if this version was edited before
}

function stepsToDoc(steps: Step[]): JSONContent {
  return {
    type: "doc",
    content: steps.map((step) => ({
      type: "step",
      attrs: {
        stepId: step.id,
        orderIndex: step.orderIndex,
        title: step.title,
        screenshotUrl: step.screenshotUrl,
      },
      content: [{ type: "text", text: step.bodyText || " " }],
    })),
  };
}
export default function ArticleEditor({
  documentId,
  versionId,
  steps,
  initialContent,
}: ArticleEditorProps) {
  // stable across re-renders per document version — recreated only if the
  // user navigates to a different document/version
  const debouncedSave = useMemo(
    () =>
      debounce((json: JSONContent) => {
        saveDocumentContent(documentId, versionId, json).catch((err) => {
          console.error("Autosave failed:", err);
        });
      }, 1000),
    [documentId, versionId],
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: false }),
      TextStyle, // required by Color — Color piggybacks on the textStyle mark
      Color,
      Highlight,
      StepImage,
      StepTitle.configure({
        HTMLAttributes: {
          class: "step-title",
        },
      }), // ← must be registered, not just StepNode
      StepBody,
      StepNode,
      AiRewriteExtension,
    ],
    // prefer previously-saved content if it exists, otherwise build fresh
    // from the generated steps (first time this version is opened)
    content: initialContent ?? stepsToDoc(steps),
    onUpdate: ({ editor, transaction }) => {
      if (!transaction.docChanged) return; // ignore selection-only transactions
      debouncedSave(editor.getJSON());
    },
    editorProps: {
      attributes: {
        class: "focus:outline-none marker:text-neutral-900",
      },
    },
    immediatelyRender: false, // required for Next.js SSR — avoids hydration mismatch
  });

  if (!editor)
    return null; // useEditor returns null on first render; toolbars need a real instance
  else {
    return <EditorContent editor={editor} />;
  }
}
