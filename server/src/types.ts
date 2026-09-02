export interface StructuredStep {
  title: string;
  body_text: string;
  frame_index: number;
}

export interface StepWithScreenshot extends StructuredStep {
  screenshotUrl: string;
}

export type ImageContentBlock =
  | { type: "text"; text: string }
  | {
      type: "image";
      source: { type: "base64"; media_type: "image/png"; data: string };
    };

export type DocVersionStatus = "processing" | "ready" | "failed";

export interface StructuredStep {
  title: string;
  body_text: string;
  frame_index: number;
}

export interface StepWithScreenshot extends StructuredStep {
  screenshotUrl: string;
}

export type StepDiffType = "unchanged" | "modified" | "added" | "removed";

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface StepDiffEntry {
  type: StepDiffType;
  oldStepId: string | null;
  newStepId: string | null;
  changedRegions?: BoundingBox[];
}
