import {
  NodeViewWrapper,
  NodeViewContent,
  type NodeViewProps,
} from "@tiptap/react";
import { GripVertical, Sparkles } from "lucide-react";
import Image from "next/image";

export default function StepNodeView({
  node,
  updateAttributes,
}: NodeViewProps) {
  const { orderIndex, title, screenshotUrl } = node.attrs;

  return (
    <NodeViewWrapper className="group relative py-3">
      {/* hover-revealed left gutter — drag handle + step number, Notion's block-handle pattern */}
      <div
        contentEditable={false}
        className="absolute -left-10 top-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity"
      >
        <button className="p-1 rounded hover:bg-neutral-100 cursor-grab text-neutral-300 hover:text-neutral-500">
          <GripVertical size={14} />
        </button>
        <span className="text-xs text-neutral-300 tabular-nums w-4 text-center">
          {orderIndex + 1}
        </span>
      </div>

      {/* title — plain contentEditable, updates the node's attrs directly, no input/border chrome */}
      <div
        contentEditable
        suppressContentEditableWarning
        onBlur={(e) =>
          updateAttributes({ title: e.currentTarget.textContent ?? "" })
        }
        className="text-base font-semibold text-neutral-900 outline-none mb-2 empty:before:content-['Step_title'] empty:before:text-neutral-300"
      >
        {title}
      </div>

      {/* screenshot — plain inline image, no frame/background/border, just rounded corners like a Notion image block */}
      {screenshotUrl && (
        <div
          contentEditable={false}
          className="relative w-full aspect-video rounded-md overflow-hidden mb-3"
        >
          <Image
            src={screenshotUrl}
            alt={title || ""}
            fill
            className="object-contain"
          />
        </div>
      )}

      {/* body text — the real ProseMirror editable region */}
      <NodeViewContent className="text-sm text-neutral-600 leading-relaxed outline-none" />

      {/* hover-revealed AI action, top-right of the block — placeholder for the rewrite/expand toolbar */}
      <button
        contentEditable={false}
        className="absolute right-0 top-3 p-1.5 rounded opacity-0 group-hover:opacity-100 transition-opacity text-neutral-300 hover:text-neutral-600 hover:bg-neutral-100"
        title="AI actions"
      >
        <Sparkles size={13} />
      </button>
    </NodeViewWrapper>
  );
}
