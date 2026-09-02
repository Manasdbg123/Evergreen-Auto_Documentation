// lib/tiptap/stepsToInitialDoc.ts
import type { JSONContent } from "@tiptap/core";
import type { Step } from "@/lib/types";

export function stepsToInitialDoc(steps: Step[]): JSONContent {
  const content: JSONContent[] = [];

  steps.forEach((step) => {
    // every step gets the same treatment now — no special-casing index 0
    content.push({
      type: "paragraph",
      content: step.title
        ? [{ type: "text", marks: [{ type: "bold" }], text: step.title }]
        : undefined,
    });

    content.push({
      type: "paragraph",
      content: step.bodyText
        ? [{ type: "text", text: step.bodyText }]
        : undefined,
    });
    if (step.screenshotUrl) {
      content.push({ type: "image", attrs: { src: step.screenshotUrl } });
    }
  });

  return {
    type: "doc",
    content: content.length ? content : [{ type: "paragraph" }],
  };
}
