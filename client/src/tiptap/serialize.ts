/**
 * SOP <-> ProseMirror document.
 *
 * Both directions are total and lossless for every field the diff engine
 * reads. Fields the editor does not expose (provenance, phash, prerequisites)
 * ride along in node attributes rather than being dropped and regenerated,
 * because regenerating them would quietly sever a step from the frame it came
 * from — and from its lineage, which is what makes edits survive.
 */

import type { JSONContent } from "@tiptap/core";
import type { Sop, Step } from "../types";

/** A text block holding a single string, or an empty block for "". */
function textBlock(type: string, value: string): JSONContent {
  const text = (value ?? "").trim();
  return text ? { type, content: [{ type: "text", text }] } : { type };
}

function readText(node: JSONContent | undefined): string {
  if (!node?.content) return "";
  return node.content
    .map((child) => (child.type === "text" ? (child.text ?? "") : ""))
    .join("")
    .trim();
}

export function sopToDoc(
  sop: Sop,
  statusByLineage: Record<string, string> = {},
): JSONContent {
  return {
    type: "doc",
    content: sop.steps.map((step) => stepToNode(step, statusByLineage)),
  };
}

function stepToNode(step: Step, statusByLineage: Record<string, string>): JSONContent {
  const content: JSONContent[] = [
    textBlock("stepTitle", step.title),
    textBlock("stepInstruction", step.instruction),
    textBlock("stepExpected", step.expected_result),
  ];

  const src = step.screenshot_url ?? null;
  if (src) {
    content.push({
      type: "screenshot",
      attrs: { src, alt: step.title, ref: step.screenshot_ref },
    });
  }

  return {
    type: "step",
    attrs: {
      stepId: step.step_id,
      lineageId: step.meta.lineage_id,
      order: step.order,
      uiType: step.ui_element.type,
      uiLabel: step.ui_element.label,
      locationHint: step.ui_element.location_hint,
      confidence: step.confidence,
      screenshotRef: step.screenshot_ref,
      prerequisites: step.prerequisites,
      conflict: step.meta.conflict,
      editedFields: step.meta.edited_fields,
      generatedValues: step.meta.generated_values,
      diffStatus: statusByLineage[step.meta.lineage_id] ?? null,
    },
    content,
  };
}

/**
 * Read the editor back into an SOP.
 *
 * `original` supplies everything the editor never touched. Rebuilding a step
 * from the node alone would mean inventing provenance the editor does not
 * carry, so the original step is the base and only the edited fields are laid
 * over it. A step whose lineage is not in `original` is one the user created
 * by hand, and is built from defaults.
 */
export function docToSop(sop: Sop, doc: JSONContent): Sop {
  const byLineage = new Map(sop.steps.map((s) => [s.meta.lineage_id, s]));
  const nodes = (doc.content ?? []).filter((n) => n.type === "step");

  const steps: Step[] = nodes.map((node, index) => {
    const attrs = (node.attrs ?? {}) as Record<string, unknown>;
    const lineageId = String(attrs.lineageId ?? "");
    const base = byLineage.get(lineageId);

    const blocks = node.content ?? [];
    const title = readText(blocks.find((b) => b.type === "stepTitle"));
    const instruction = readText(blocks.find((b) => b.type === "stepInstruction"));
    const expected = readText(blocks.find((b) => b.type === "stepExpected"));

    if (base) {
      return {
        ...base,
        order: index + 1,
        title,
        instruction,
        expected_result: expected,
      };
    }

    // A hand-added step. The server assigns edit provenance on save; an empty
    // lineage_id tells it this step has no previous version to compare against.
    return {
      step_id: String(attrs.stepId ?? ""),
      order: index + 1,
      title,
      instruction,
      ui_element: {
        type: String(attrs.uiType ?? "other"),
        label: String(attrs.uiLabel ?? ""),
        location_hint: String(attrs.locationHint ?? ""),
      },
      expected_result: expected,
      screenshot_ref: (attrs.screenshotRef as string | null) ?? null,
      prerequisites: (attrs.prerequisites as string[]) ?? [],
      confidence: "medium",
      meta: {
        lineage_id: lineageId,
        source_frame_ts: null,
        candidate_id: null,
        transcript_span: null,
        phash: null,
        edited_by_human: true,
        edited_fields: [],
        generated_values: {},
        conflict: null,
      },
    } as Step;
  });

  return { ...sop, steps };
}
