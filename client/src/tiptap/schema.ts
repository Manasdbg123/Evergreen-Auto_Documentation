/**
 * The editor's schema — custom `step` and `screenshot` nodes.
 *
 * This is the reason the editor is TipTap and not a textarea. If the document
 * were rich text, saving it would mean parsing prose back into
 * `title` / `instruction` / `expected_result`, and any parse failure would
 * silently degrade the schema the diff engine compares. Instead the structure
 * IS the document: a step is a node with typed attributes and exactly three
 * editable text blocks, so a save is a field-for-field read, never a parse.
 *
 * The schema is deliberately closed. There is no generic paragraph, no
 * heading, no list — a user cannot create a block that has nowhere to go in
 * `models.Step`, because a block like that would be lost on the next save and
 * losing someone's writing is the one thing this product may not do.
 */

import { Node, mergeAttributes } from "@tiptap/core";
import Document from "@tiptap/extension-document";
import Text from "@tiptap/extension-text";

/**
 * The document is a list of steps and nothing else.
 *
 * `step*` rather than `step+`: a recording where no step was confirmed
 * produces a zero-step SOP, and a schema requiring at least one would throw
 * while rendering it instead of showing the (accurate, if disappointing)
 * empty document.
 */
export const StepDocument = Document.extend({
  content: "step*",
});

function textBlock(name: string, className: string, placeholder: string) {
  return Node.create({
    name,
    content: "text*",
    // Marks are stripped: the diff compares plain strings, so bold text would
    // be invisible to it and would vanish on the next regeneration anyway.
    marks: "",
    defining: true,
    parseHTML: () => [{ tag: `div[data-node="${name}"]` }],
    renderHTML: ({ HTMLAttributes }) => [
      "div",
      mergeAttributes(HTMLAttributes, {
        "data-node": name,
        class: className,
        "data-placeholder": placeholder,
      }),
      0,
    ],
  });
}

export const StepTitle = textBlock("stepTitle", "step-title", "Step title");
export const StepInstruction = textBlock(
  "stepInstruction",
  "step-instruction",
  "What the reader should do",
);
export const StepExpected = textBlock(
  "stepExpected",
  "step-expected",
  "What they should see afterwards",
);

/**
 * The screenshot. An atom: selectable and deletable as a unit, never editable,
 * because its content comes from the recording rather than from the writer.
 */
export const Screenshot = Node.create({
  name: "screenshot",
  group: "block",
  atom: true,
  draggable: false,
  addAttributes: () => ({
    src: { default: null as string | null },
    alt: { default: "" },
    ref: { default: null as string | null },
  }),
  parseHTML: () => [{ tag: "img[data-node='screenshot']" }],
  renderHTML: ({ HTMLAttributes }) => [
    "img",
    mergeAttributes(HTMLAttributes, {
      "data-node": "screenshot",
      class: "step-screenshot",
    }),
  ],
});

/**
 * One step. Every scalar field of `models.Step` that is not free text lives in
 * an attribute, so it survives a round trip through the editor untouched even
 * though nothing in the UI edits it.
 *
 * `lineageId` matters most: it is the key the diff engine uses to carry a hand
 * edit across regenerations. Drop it here and every edit is orphaned on the
 * next recording.
 */
export const StepNode = Node.create({
  name: "step",
  group: "block",
  content: "stepTitle stepInstruction stepExpected screenshot?",
  defining: true,
  addAttributes: () => ({
    // `rendered: false` keeps these in the ProseMirror model but out of the
    // DOM. They exist to survive the round trip, not to be displayed, and
    // serialising an array or an object into an HTML attribute would produce
    // "[object Object]" and lose the value on the way back.
    stepId: { default: "", rendered: false },
    lineageId: { default: "", rendered: false },
    order: { default: 0, rendered: false },
    uiType: { default: "other", rendered: false },
    uiLabel: { default: "", rendered: false },
    locationHint: { default: "", rendered: false },
    confidence: { default: "medium", rendered: false },
    screenshotRef: { default: null as string | null, rendered: false },
    prerequisites: { default: [] as string[], rendered: false },
    conflict: { default: null as string | null, rendered: false },
    editedFields: { default: [] as string[], rendered: false },
    generatedValues: { default: {} as Record<string, string>, rendered: false },
    /** Diff status. Rendered, because the stylesheet colours the step by it. */
    diffStatus: {
      default: null as string | null,
      renderHTML: (attributes: Record<string, unknown>) =>
        attributes.diffStatus
          ? { "data-diff-status": String(attributes.diffStatus) }
          : {},
    },
  }),
  parseHTML: () => [{ tag: "section[data-node='step']" }],
  renderHTML: ({ HTMLAttributes }) => [
    "section",
    mergeAttributes(HTMLAttributes, { "data-node": "step", class: "step" }),
    0,
  ],
});

export const extensions = [
  StepDocument,
  Text,
  StepNode,
  StepTitle,
  StepInstruction,
  StepExpected,
  Screenshot,
];
