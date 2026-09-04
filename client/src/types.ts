// Mirrors server/app/models.py. The step schema is the contract the diff
// engine depends on, so these names track the Python field for field.

export type Confidence = "high" | "medium" | "low";

export type DiffStatus =
  | "unchanged"
  | "modified"
  | "added"
  | "removed"
  | "reordered";

export interface UiElement {
  type: string;
  label: string;
  location_hint: string;
}

export interface StepMeta {
  lineage_id: string;
  source_frame_ts: number | null;
  candidate_id: string | null;
  transcript_span: [number, number] | null;
  phash: string | null;
  edited_by_human: boolean;
  edited_fields: string[];
  generated_values: Record<string, string>;
  conflict: string | null;
}

export interface Step {
  step_id: string;
  order: number;
  title: string;
  instruction: string;
  ui_element: UiElement;
  expected_result: string;
  screenshot_ref: string | null;
  /** Added by the API so the browser has something it can actually fetch. */
  screenshot_url?: string;
  prerequisites: string[];
  confidence: Confidence;
  meta: StepMeta;
}

export interface Sop {
  sop_id: string;
  job_id: string;
  document_id: string | null;
  version: number;
  title: string;
  summary: string;
  steps: Step[];
  generated_from_transcript: boolean;
}

export interface FieldChange {
  field: string;
  old: unknown;
  new: unknown;
}

export interface StepDiff {
  diff_id: string;
  status: DiffStatus;
  lineage_id: string | null;
  old_step_id: string | null;
  new_step_id: string | null;
  old_order: number | null;
  new_order: number | null;
  similarity: number | null;
  field_changes: FieldChange[];
  also_reordered: boolean;
  decided_by: string;
  rationale: string;
  preserved_edits: string[];
  review: "pending" | "accepted" | "rejected";
}

export interface DiffResult {
  document_id: string | null;
  old_version: number;
  new_version: number;
  entries: StepDiff[];
  summary: Record<string, number>;
  visual_comparisons_used: number;
  llm_judgements_used: number;
}

export interface DocumentSummary {
  document_id: string;
  title: string;
  /** The product this procedure belongs to. "" means ungrouped. */
  app: string;
  created_at: number;
  updated_at: number;
  version_count: number;
  latest_version: number | null;
}

export interface VersionInfo {
  version_id: string;
  version: number;
  job_id: string | null;
  source: string;
  created_at: number;
  /** The title the SOP carried at this version — i.e. which workflow it documents. */
  title: string;
}

export interface JobRecord {
  job_id: string;
  document_id: string | null;
  status: string;
  stage: string | null;
  error: string | null;
  source_name: string | null;
  created_at: number;
  updated_at: number;
  stages?: Record<string, { elapsed_sec?: number; count?: number; mode?: string } | null>;
  spend_usd?: number;
}
