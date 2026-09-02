import { Node, mergeAttributes } from "@tiptap/core";
import { ReactNodeViewRenderer } from "@tiptap/react";
import StepNodeView from "@/components/editor/StepNodeView";

export interface StepAttrs {
  stepId: string;
  orderIndex: number;
  title: string;
  screenshotUrl: string;
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    stepBlock: {
      insertStep: (attrs: StepAttrs) => ReturnType;
    };
  }
}

export const StepTitle = Node.create({
  name: "stepTitle",
  content: "text*",
  group: "block",
  parseHTML: () => [{ tag: 'div[data-type="step-title"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    "div",
    mergeAttributes(HTMLAttributes, { "data-type": "step-title" }),
    0,
  ],
});

export const StepImage = Node.create({
  name: "stepImage",
  group: "block",
  atom: true, // opaque, single unit — cursor treats it as one "character," can't edit into it
  selectable: true, // so it can be selected and deleted as its own unit, not swallowing neighbors
  isolating: true, // backspace/join operations stop at its boundary instead of reaching through it
  addAttributes() {
    return { screenshotUrl: { default: "" } };
  },
  parseHTML: () => [{ tag: 'img[data-type="step-image"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    "img",
    mergeAttributes(HTMLAttributes, {
      "data-type": "step-image",
      src: HTMLAttributes.screenshotUrl,
    }),
  ],
});

export const StepBody = Node.create({
  name: "stepBody",
  content: "inline*",
  group: "block",
  parseHTML: () => [{ tag: 'div[data-type="step-body"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    "div",
    mergeAttributes(HTMLAttributes, { "data-type": "step-body" }),
    0,
  ],
});

export const StepNode = Node.create({
  name: "step",
  group: "block",
  content: "stepImage stepTitle stepBody", // must match what stepsToDoc produces
  draggable: true,
  addAttributes() {
    return {
      stepId: { default: null },
      orderIndex: { default: 0 },
      screenshotUrl: { default: "" },
    };
  },
  parseHTML: () => [{ tag: 'div[data-type="step"]' }],
  renderHTML: ({ HTMLAttributes }) => [
    "div",
    mergeAttributes(HTMLAttributes, { "data-type": "step" }),
    0,
  ],
  addNodeView() {
    return ReactNodeViewRenderer(StepNodeView);
  },
});
