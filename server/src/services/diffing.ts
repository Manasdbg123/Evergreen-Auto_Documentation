import { cosineSimilarity } from "./embeddings.js";
import { diffScreenshots } from "./screenshotDiff.js";
import type { BoundingBox, StepDiffEntry } from "../types.js";
import { downloadFromCloudinaryToTemp } from "./storage.js";
import { Step } from "../../generated/prisma/client.js";
import fs from "fs";
import path from "path";

const TMP_DIFF_DIR = path.resolve("tmp/diff-downloads");

const SIMILARITY_MATCH_THRESHOLD = 0.75; // below this, treat as no match (added/removed)
const SIMILARITY_IDENTICAL_THRESHOLD = 0.97; // above this, treat as unchanged

export async function generateStepDiffs(
  oldSteps: Step[],
  newSteps: Step[],
): Promise<StepDiffEntry[]> {
  const matches = matchSteps(oldSteps, newSteps);
  const stepDiffs: StepDiffEntry[] = [];

  for (const match of matches) {
    if (match.type === "added") {
      stepDiffs.push({
        type: "added",
        oldStepId: null,
        newStepId: match.newStep!.id,
      });
      continue;
    }

    if (match.type === "removed") {
      stepDiffs.push({
        type: "removed",
        oldStepId: match.oldStep!.id,
        newStepId: null,
      });
      continue;
    }

    if (match.type === "unchanged") {
      stepDiffs.push({
        type: "unchanged",
        oldStepId: match.oldStep!.id,
        newStepId: match.newStep!.id,
      });
      continue;
    }

    // modified — actually diff the screenshots
    if (match.type === "modified") {
      let changedRegions: BoundingBox[] = [];
      let oldLocalPath: string | undefined;
      let newLocalPath: string | undefined;

      try {
        const results = await Promise.allSettled([
          downloadFromCloudinaryToTemp(
            match.oldStep!.screenshotUrl,
            TMP_DIFF_DIR,
          ),
          downloadFromCloudinaryToTemp(
            match.newStep!.screenshotUrl,
            TMP_DIFF_DIR,
          ),
        ]);

        if (results[0].status === "fulfilled") oldLocalPath = results[0].value;
        if (results[1].status === "fulfilled") newLocalPath = results[1].value;

        if (oldLocalPath && newLocalPath) {
          changedRegions = await diffScreenshots(oldLocalPath, newLocalPath);
        } else {
          throw new Error("One or both screenshot downloads failed");
        }
      } catch (err) {
        console.error(
          "Screenshot diff failed, falling back to no regions:",
          err,
        );
      } finally {
        [oldLocalPath, newLocalPath].forEach((p) => {
          if (p) {
            try {
              fs.unlinkSync(p);
            } catch {
              /* already gone */
            }
          }
        });
      }

      stepDiffs.push({
        type: "modified",
        oldStepId: match.oldStep!.id,
        newStepId: match.newStep!.id,
        changedRegions,
      });
      continue;
    }
  }

  return stepDiffs;
}

type MatchType = "unchanged" | "modified" | "added" | "removed";
interface StepMatch {
  type: MatchType;
  oldStep: Step | null;
  newStep: Step | null;
}

function matchSteps(oldSteps: Step[], newSteps: Step[]): StepMatch[] {
  const oldEmbeddings = oldSteps.map(
    (s) => (s.embedding as number[] | null) ?? [],
  );
  const newEmbeddings = newSteps.map(
    (s) => (s.embedding as number[] | null) ?? [],
  );

  const usedNewIndices = new Set<number>();
  const matches: StepMatch[] = [];

  for (let i = 0; i < oldSteps.length; i++) {
    let bestJ = -1;
    let bestScore = -1;

    for (let j = 0; j < newSteps.length; j++) {
      if (usedNewIndices.has(j)) continue;
      if (oldEmbeddings[i].length === 0 || newEmbeddings[j].length === 0)
        continue;

      const score = cosineSimilarity(oldEmbeddings[i], newEmbeddings[j]);
      if (score > bestScore) {
        bestScore = score;
        bestJ = j;
      }
    }

    if (bestJ !== -1 && bestScore > SIMILARITY_MATCH_THRESHOLD) {
      usedNewIndices.add(bestJ);
      const matchType: MatchType =
        bestScore > SIMILARITY_IDENTICAL_THRESHOLD ? "unchanged" : "modified";
      matches.push({
        type: matchType,
        oldStep: oldSteps[i],
        newStep: newSteps[bestJ],
      });
    } else {
      matches.push({ type: "removed", oldStep: oldSteps[i], newStep: null });
    }
  }

  for (let j = 0; j < newSteps.length; j++) {
    if (!usedNewIndices.has(j)) {
      matches.push({ type: "added", oldStep: null, newStep: newSteps[j] });
    }
  }

  return matches;
}
