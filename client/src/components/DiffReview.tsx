import { useState } from "react";

import { api } from "../api";
import type { DiffResult, StepDiff } from "../types";

interface Props {
  diffId: string;
  diff: DiffResult;
  onReviewed: () => void;
}

const LABEL: Record<string, string> = {
  unchanged: "Unchanged",
  modified: "Changed",
  added: "New step",
  removed: "Removed",
  reordered: "Moved",
};

/**
 * Per-step review. Each change is accepted or rejected on its own — a single
 * "apply everything" button would make the reviewer's only real choice
 * all-or-nothing, which for a document that is mostly correct means either
 * re-doing the edits or shipping the wrong text.
 *
 * Rejecting restores the previous version's values on the server; it does not
 * merely dismiss the row.
 *
 * A reviewed diff becomes read-only history. The decision is recorded per
 * entry on the server, so re-showing the buttons would invite a second verdict
 * on a question already answered — and because "reject" writes the old text
 * back, answering twice against a document that has since moved on would
 * reintroduce text the user had already accepted away.
 */
export function DiffReview({ diffId, diff, onReviewed }: Props) {
  const [decisions, setDecisions] = useState<
    Record<string, "accepted" | "rejected" | "pending">
  >({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  // Unchanged steps are not decisions — showing them as pending review would
  // bury the handful of things that actually need attention.
  //
  // Except when the step carried a hand edit through the regeneration. That is
  // the product's central promise, and a preserved edit lands on a step that is
  // by definition unchanged, so filtering on status alone hid the one reassurance
  // the user most needs to see. Those rows are shown, with nothing to decide.
  const shown = diff.entries.filter(
    (e) => e.status !== "unchanged" || e.preserved_edits.length > 0,
  );
  const actionable = shown.filter((e) => e.status !== "unchanged");
  const unchanged = diff.entries.length - actionable.length;
  const preservedCount = diff.entries.filter((e) => e.preserved_edits.length > 0).length;

  const undecided = actionable.filter((e) => e.review === "pending");
  const settled = actionable.length > 0 && undecided.length === 0;
  const accepted = actionable.filter((e) => e.review === "accepted").length;
  const rejected = actionable.filter((e) => e.review === "rejected").length;

  function decide(entry: StepDiff, value: "accepted" | "rejected") {
    setDecisions((prev) => ({
      ...prev,
      [entry.diff_id]: prev[entry.diff_id] === value ? "pending" : value,
    }));
  }

  async function apply() {
    setBusy(true);
    setMessage("");
    try {
      const result = await api.review(diffId, decisions);
      setMessage(
        `Applied. ${result.rejected} change(s) rejected and reverted — now v${result.version}.`,
      );
      onReviewed();
    } catch (error) {
      setMessage(`Failed: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const decided = Object.values(decisions).filter((d) => d !== "pending").length;

  const summaryLine = (
    <span className="hint">
      {Object.entries(diff.summary)
        .map(([k, v]) => `${v} ${k}`)
        .join(", ")}
      {diff.llm_judgements_used > 0 && ` · ${diff.llm_judgements_used} adjudicated`}
      {diff.visual_comparisons_used > 0 &&
        ` · ${diff.visual_comparisons_used} compared visually`}
    </span>
  );

  const entryRows = shown.map((entry) => (
    <div key={entry.diff_id} className={`entry entry-${entry.status}`}>
      <div className="entry-head">
        <span className={`badge badge-${entry.status}`}>
          {LABEL[entry.status] ?? entry.status}
        </span>
        <span className="pos">
          {entry.old_order !== null && entry.new_order !== null
            ? `step ${entry.old_order} → ${entry.new_order}`
            : entry.new_order !== null
              ? `step ${entry.new_order}`
              : `was step ${entry.old_order}`}
        </span>
        {entry.also_reordered && <span className="tag">also moved</span>}
        {entry.similarity !== null && (
          <span className="tag">
            {entry.similarity.toFixed(2)} · {entry.decided_by}
          </span>
        )}
        {entry.review !== "pending" && (
          <span className={`verdict verdict-${entry.review}`}>
            {entry.review === "accepted" ? "Accepted" : "Rejected"}
          </span>
        )}
      </div>

      {entry.rationale && <p className="rationale">{entry.rationale}</p>}

      {entry.preserved_edits.length > 0 && (
        <p className="preserved">
          Your edit to {entry.preserved_edits.join(", ")} was kept.
        </p>
      )}

      {entry.field_changes.map((change, i) => (
        <div className="change" key={i}>
          <span className="field">{change.field}</span>
          <span className="old">{String(change.old ?? "")}</span>
          <span className="arrow">→</span>
          <span className="new">{String(change.new ?? "")}</span>
        </div>
      ))}

      {entry.status !== "unchanged" && entry.review === "pending" && (
        <div className="actions">
          <button
            className={decisions[entry.diff_id] === "accepted" ? "on" : ""}
            onClick={() => decide(entry, "accepted")}
          >
            Accept
          </button>
          <button
            className={decisions[entry.diff_id] === "rejected" ? "on danger" : ""}
            onClick={() => decide(entry, "rejected")}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  ));

  // Already reviewed: keep the record, drop the form. Collapsed, because the
  // question it answered is closed and the document below is what matters now.
  if (settled) {
    return (
      <details className="review settled">
        <summary>
          <strong>
            v{diff.old_version} &rarr; v{diff.new_version}
          </strong>
          <span className="hint">
            reviewed · {accepted} accepted, {rejected} rejected
          </span>
        </summary>
        <div className="review-head">{summaryLine}</div>
        {entryRows}
      </details>
    );
  }

  return (
    <div className="review">
      <div className="review-head">
        <strong>
          v{diff.old_version} &rarr; v{diff.new_version}
        </strong>
        {summaryLine}
      </div>

      {actionable.length === 0 ? (
        <p className="note">
          Nothing changed. All {unchanged} steps matched the previous version.
        </p>
      ) : (
        <p className="note">
          {undecided.length} change(s) to review; {unchanged} step(s) unchanged.
          {preservedCount > 0 &&
            ` ${preservedCount} step(s) kept your edits through the regeneration.`}
        </p>
      )}

      {entryRows}

      {undecided.length > 0 && (
        <div className="review-foot">
          <button onClick={apply} disabled={busy || decided === 0}>
            {busy ? "Applying…" : `Apply ${decided} decision(s)`}
          </button>
          <span className="hint">
            Rejecting a change puts the previous version's text back.
          </span>
          {message && <span className="hint">{message}</span>}
        </div>
      )}
    </div>
  );
}
