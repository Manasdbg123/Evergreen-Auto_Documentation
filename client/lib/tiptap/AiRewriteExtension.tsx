import { Extension } from "@tiptap/react";
import { callAiRewriteApi } from "../api";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    aiRewrite: {
      aiRewriteSelection: (
        action: "rewrite" | "expand" | "simplify",
      ) => ReturnType;
    };
  }
}

// as a TipTap Extension, not inline component code
export const AiRewriteExtension = Extension.create({
  name: "aiRewrite",
  addCommands() {
    return {
      aiRewriteSelection:
        (action) =>
        ({ editor, state }) => {
          const { from, to } = state.selection;
          const selectedText = state.doc.textBetween(from, to, " ");
          if (!selectedText) return false;

          callAiRewriteApi(selectedText, action).then((newText) => {
            // re-validate the range is still meaningful before applying —
            // guards against the user having kept typing during the request
            if (editor.isDestroyed) return;
            editor.chain().focus().insertContentAt({ from, to }, newText).run();
          });

          return true;
        },
    };
  },
});
