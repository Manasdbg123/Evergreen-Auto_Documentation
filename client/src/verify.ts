/**
 * Schema and round-trip check for the editor, run against the live API.
 *
 *     npm run verify
 *
 * Not a substitute for opening the app, but it covers the part most likely to
 * be silently wrong: `serialize.ts`. A round trip that drops a field does not
 * throw — it produces a valid document missing someone's writing, and the
 * damage only appears on the next regeneration when the lost `lineage_id`
 * orphans their edits. So the check asserts the schema accepts the document
 * AND that every field survives the trip back.
 */

import { getSchema } from "@tiptap/core";
import { Node as PMNode } from "@tiptap/pm/model";

import { extensions } from "./tiptap/schema";
import { docToSop, sopToDoc } from "./tiptap/serialize";
import type { Sop } from "./types";

const API = process.env.API ?? "http://127.0.0.1:8000";

function assert(condition: unknown, message: string): void {
  if (!condition) {
    console.error(`FAIL  ${message}`);
    process.exitCode = 1;
  } else {
    console.log(`ok    ${message}`);
  }
}

async function main() {
  const documents = (await (await fetch(`${API}/api/documents`)).json()) as {
    document_id: string;
  }[];
  if (!documents.length) {
    console.error("No documents on the server — nothing to verify.");
    process.exit(1);
  }

  const { sop } = (await (
    await fetch(`${API}/api/documents/${documents[0].document_id}`)
  ).json()) as { sop: Sop };

  console.log(`document "${sop.title}" v${sop.version}, ${sop.steps.length} steps\n`);

  const schema = getSchema(extensions);
  const json = sopToDoc(sop);

  // Throws if the document violates the schema — which is the whole point of
  // having a closed one.
  const node = PMNode.fromJSON(schema, json);
  assert(node.childCount === sop.steps.length, "every step became a node");

  const back = docToSop(sop, json);
  assert(back.steps.length === sop.steps.length, "every node became a step");

  for (let i = 0; i < sop.steps.length; i++) {
    const before = sop.steps[i];
    const after = back.steps[i];
    assert(after.title === before.title, `step ${i + 1} title survived`);
    assert(after.instruction === before.instruction, `step ${i + 1} instruction survived`);
    assert(
      after.expected_result === before.expected_result,
      `step ${i + 1} expected_result survived`,
    );
    // The one that matters most: lose this and every hand edit is orphaned on
    // the next recording.
    assert(
      after.meta.lineage_id === before.meta.lineage_id,
      `step ${i + 1} lineage_id survived`,
    );
    assert(
      after.ui_element.label === before.ui_element.label,
      `step ${i + 1} ui_element.label survived`,
    );
    assert(
      after.screenshot_ref === before.screenshot_ref,
      `step ${i + 1} screenshot_ref survived`,
    );
    assert(
      JSON.stringify(after.meta.edited_fields) ===
        JSON.stringify(before.meta.edited_fields),
      `step ${i + 1} edited_fields survived`,
    );
  }

  // An edit must reach the SOP, or saving would be a no-op.
  const edited = structuredClone(json);
  const firstStep = edited.content![0];
  firstStep.content![1] = {
    type: "stepInstruction",
    content: [{ type: "text", text: "Rewritten by hand." }],
  };
  const editedSop = docToSop(sop, edited);
  assert(
    editedSop.steps[0].instruction === "Rewritten by hand.",
    "an edit in the editor reaches the SOP",
  );
  assert(
    editedSop.steps[0].meta.lineage_id === sop.steps[0].meta.lineage_id,
    "editing does not change lineage",
  );

  console.log(
    process.exitCode ? "\nFAILED" : "\nAll checks passed.",
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
