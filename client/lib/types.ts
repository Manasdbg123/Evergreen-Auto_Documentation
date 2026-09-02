import { JSONContent } from "@tiptap/react";

export type DocVersionStatus = "processing" | "ready" | "failed";

export interface Step {
  id: string;
  orderIndex: number;
  title: string;
  bodyText: string;
  screenshotUrl: string;
}

export interface DocVersion {
  id: string;
  versionNumber: number;
  status: DocVersionStatus;
  sourceVideoUrl: string | null;
  errorMessage: string | null;
  createdAt: string;
  steps: Step[];
  contentJson: JSONContent | null; // saved editor state — null until the user's first edit persists
}

export interface Document {
  id: string;
  title: string;
  createdAt: string;
  versions: DocVersion[]; // all versions, sorted ascending by versionNumber
}

// lightweight shape for the sidebar list — avoids sending full step content for every doc
export interface DocumentSummary {
  id: string;
  title: string;
  createdAt: string;
  latestVersion: {
    id: string;
    versionNumber: number;
    status: DocVersionStatus;
  };
}

export interface UploadResponse {
  documentId: string;
  versionId: string;
  status: DocVersionStatus;
}

export type StepDiffType = "unchanged" | "modified" | "added" | "removed";

export interface StepDiffEntry {
  type: StepDiffType;
  oldStep: Step | null;
  newStep: Step | null;
  changedRegions?: { x: number; y: number; width: number; height: number }[];
}

export interface DiffResult {
  id: string;
  oldVersionId: string;
  newVersionId: string;
  stepDiffs: StepDiffEntry[];
}
