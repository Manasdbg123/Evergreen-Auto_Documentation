import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { Extension } from "@tiptap/react";

export const DiffHighlightExtension = Extension.create({
  name: "diffHighlight",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey("diffHighlight"),
        props: {
          decorations(state) {
            const decorations: Decoration[] = [];
            state.doc.descendants((node, pos) => {
              if (
                node.type.name === "step" &&
                node.attrs.diffStatus === "modified"
              ) {
                decorations.push(
                  Decoration.node(pos, pos + node.nodeSize, {
                    class: "diff-modified",
                  }),
                );
              }
            });
            return DecorationSet.create(state.doc, decorations);
          },
        },
      }),
    ];
  },
});
