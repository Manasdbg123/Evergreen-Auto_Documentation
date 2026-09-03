import { EditorContent, useEditor } from "@tiptap/react";
import { UndoRedo } from "@tiptap/extensions";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { extensions } from "../tiptap/schema";
import { docToSop, sopToDoc } from "../tiptap/serialize";
import type { Sop, StepDiff } from "../types";

interface Props {
  documentId: string;
  sop: Sop;
  diffEntries?: StepDiff[];
  onSaved: (version: number) => void;
}

/**
 * The editor. Structured throughout — see `tiptap/schema.ts` for why the
 * schema is closed rather than generic rich text.
 *
 * Saving is explicit rather than automatic. Every save appends a version, and
 * an autosave firing on each keystroke would bury the versions that mean
 * something under hundreds that do not.
 */
export function SopEditor({ documentId, sop, diffEntries, onSaved }: Props) {
  const [status, setStatus] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  // Kept in a ref so the editor's onUpdate closure never reads a stale SOP.
  const sopRef = useRef(sop);
  sopRef.current = sop;

  const statusByLineage: Record<string, string> = {};
  for (const entry of diffEntries ?? []) {
    if (entry.lineage_id) statusByLineage[entry.lineage_id] = entry.status;
  }

  // One editor per mounted version. App keys this component on
  // `sop_id:version`, so switching version remounts it and we get a fresh
  // editor with fresh undo history.
  //
  // It used to pass `[sop.sop_id, sop.version]` as useEditor's dependencies,
  // which destroys and rebuilds the editor in place. React then ran the effect
  // below holding the *destroyed* instance: the `if (editor)` guard passed,
  // because the object still exists, but its view was gone and reading
  // `.commands` threw. That crashed the whole component tree, so clicking an
  // older version blanked the page with no error shown anywhere.
  const editor = useEditor({
    extensions: [...extensions, UndoRedo],
    content: sopToDoc(sop, statusByLineage),
    onUpdate: () => setDirty(true),
  });

  // Only the diff overlay can change without a remount — it arrives after the
  // document loads, and it colours the steps.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editor.commands.setContent(sopToDoc(sopRef.current, statusByLineage));
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, diffEntries]);

  async function save() {
    if (!editor) return;
    setStatus("Saving…");
    try {
      const updated = docToSop(sopRef.current, editor.getJSON());
      const result = await api.saveDocument(documentId, updated);
      setDirty(false);
      setStatus(
        result.edited_fields.length
          ? `Saved v${result.version} — protected: ${result.edited_fields.join(", ")}`
          : `Saved v${result.version}`,
      );
      onSaved(result.version);
    } catch (error) {
      setStatus(`Save failed: ${(error as Error).message}`);
    }
  }

  return (
    <div className="editor">
      <div className="toolbar">
        <button onClick={save} disabled={!dirty}>
          {dirty ? "Save new version" : "Saved"}
        </button>
        <button onClick={() => editor?.commands.undo()}>Undo</button>
        <button onClick={() => editor?.commands.redo()}>Redo</button>
        <a href={api.exportUrl(documentId, "markdown")} target="_blank" rel="noreferrer">
          Markdown
        </a>
        <a href={api.exportUrl(documentId, "html")} target="_blank" rel="noreferrer">
          HTML
        </a>
        <span className="hint">{status}</span>
      </div>

      <p className="note">
        Edited text is protected: the next recording will not overwrite a field
        you changed by hand.
      </p>

      <EditorContent editor={editor} className="prose" />
    </div>
  );
}
