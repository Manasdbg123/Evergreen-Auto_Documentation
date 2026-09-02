"""Stage 8 — the diff engine. This is the product.

Aligns the steps of a newly generated SOP against the previously stored one
and classifies each as unchanged / modified / added / removed / reordered.

Three decisions define this module:

**Optimal assignment, not greedy matching.** A greedy pass lets the first old
step to claim a new step win even when a later old step matches it far better,
and one bad early match then cascades through the rest of the document. The
Hungarian algorithm minimises total assignment cost across the whole document
instead, so a single ambiguous pair cannot poison its neighbours.

**Reorder is decided by longest increasing subsequence.** See
`_classify_order` for the heuristic and why it resolves the
reorder-vs-remove+add ambiguity.

**Human edits win.** A regenerated step never silently overwrites a field a
person edited. This is a hard requirement: a documentation tool that destroys
hand-written notes on every re-record is worse than no tool.

Cost: tiers 1 and 2 are free and offline. Only pairs left in the ambiguous
band reach an LLM, and screenshots are consulted only when even that is
inconclusive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Config
from ..models import (SOP, DiffResult, DiffStatus, FieldChange, Step, StepDiff)
from .similarity import similarity_matrix

#: Fields compared to decide "modified", in report order.
DIFF_FIELDS = ["title", "instruction", "ui_element", "expected_result", "prerequisites"]


@dataclass
class Pair:
    old_index: int
    new_index: int
    score: float
    decided_by: str = "lexical"
    rationale: str = ""


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------


def optimal_assignment(matrix: list[list[float]]) -> list[tuple[int, int]]:
    """Maximise total similarity across all pairs simultaneously.

    Falls back to a greedy best-first pass if scipy is unavailable. Greedy is
    strictly worse — it is order-dependent — so this is a degradation, not an
    equivalent path, and it says so out loud.
    """
    if not matrix or not matrix[0]:
        return []
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        cost = 1.0 - np.array(matrix)
        rows, cols = linear_sum_assignment(cost)
        return list(zip(rows.tolist(), cols.tolist()))
    except ImportError:
        print("[diff] scipy unavailable — falling back to greedy matching (less accurate)")
        return _greedy_assignment(matrix)


def _greedy_assignment(matrix: list[list[float]]) -> list[tuple[int, int]]:
    scored = sorted(
        ((matrix[i][j], i, j) for i in range(len(matrix)) for j in range(len(matrix[0]))),
        reverse=True,
    )
    used_old: set[int] = set()
    used_new: set[int] = set()
    out: list[tuple[int, int]] = []
    for _, i, j in scored:
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        out.append((i, j))
    return sorted(out)


def weighted_increasing_subsequence(
    values: list[int], weights: list[float] | None = None
) -> set[int]:
    """Indices forming the increasing subsequence of greatest total weight.

    Weight is match confidence. This matters because plain longest-subsequence
    has ties, and the tie-break decides which step gets accused of moving.

    Concretely, on the fixture the new-order sequence is [0, 2, 4, 3, 5]:
    two subsequences of length 4 exist, one keeping the form step in place and
    one keeping the review step. Unweighted LIS picked arbitrarily (by
    iteration order) and dropped the *higher confidence* pair. Weighting keeps
    the pairs we are most sure are the same step anchored, and attributes the
    move to the least certain one — which is both more defensible and stable
    across runs.

    O(n^2), which is irrelevant at SOP scale (tens of steps, not thousands).
    """
    n = len(values)
    if n == 0:
        return set()
    w = weights or [1.0] * n

    best = list(w)
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if values[j] < values[i] and best[j] + w[i] > best[i]:
                best[i] = best[j] + w[i]
                prev[i] = j

    end = max(range(n), key=lambda i: best[i])
    out: set[int] = set()
    while end != -1:
        out.add(end)
        end = prev[end]
    return out


# --------------------------------------------------------------------------
# Field comparison
# --------------------------------------------------------------------------


#: Fields whose *wording* is regenerated every run and therefore drifts even
#: when nothing about the workflow changed. Compared for meaning, not bytes.
PROSE_FIELDS = {"title", "instruction", "expected_result", "ui_element.location_hint"}

#: Fields that carry semantic identity. Compared exactly — "Save" becoming
#: "Submit" is the entire point of the product and must never be smoothed away.
EXACT_FIELDS = {"ui_element.label", "ui_element.type", "prerequisites"}

#: Reported to the reviewer, but never sufficient on their own to call a step
#: modified. `location_hint` is a loose positional description ("bottom right
#: of the panel" / "lower right of the card") that the model rephrases freely;
#: measured, two descriptions of the *same* position scored as low as 0.550,
#: below several genuinely different pairs. Letting it drive status would mark
#: most of the document modified on every regeneration.
COSMETIC_FIELDS = {"ui_element.location_hint"}


def field_pairs(old: Step, new: Step) -> list[tuple[str, Any, Any]]:
    """Flatten both steps into comparable (field, old, new) triples."""
    a, b = old.diff_payload(), new.diff_payload()
    out: list[tuple[str, Any, Any]] = []
    for field in DIFF_FIELDS:
        av, bv = a.get(field), b.get(field)
        if field == "ui_element":
            for key in ("type", "label", "location_hint"):
                out.append((f"ui_element.{key}",
                            (av or {}).get(key, ""), (bv or {}).get(key, "")))
        else:
            out.append((field, av, bv))
    return out


def field_changes(
    old: Step, new: Step, prose_scores: dict[str, float] | None = None,
    rewrite_threshold: float = 1.0,
) -> list[FieldChange]:
    """Exactly which fields differ. This is why ui_element is structured.

    With a prose blob we could only say "this step changed"; with named fields
    the UI says "button label: Save -> Submit" and the reviewer accepts or
    rejects that specific change.

    Prose fields get a tolerance. Every regeneration rewrites the SOP from
    scratch, so "Click Save" becomes "Press the Save button" with no change to
    the workflow at all. Comparing those byte-for-byte reported all six fixture
    steps as modified when only one had really changed — noise that would make
    the review UI useless. Above `rewrite_threshold` a prose difference is
    treated as a rewording and dropped.

    Identity fields are never given that tolerance.
    """
    out: list[FieldChange] = []
    for field, av, bv in field_pairs(old, new):
        if _normalise(av) == _normalise(bv):
            continue
        if field in PROSE_FIELDS and prose_scores is not None:
            score = prose_scores.get(f"{field}\x00{av}\x00{bv}")
            if score is not None and score >= rewrite_threshold:
                continue  # same meaning, different words
        out.append(FieldChange(field=field, old=av, new=bv))
    return out


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.lower().split())
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Edit preservation
# --------------------------------------------------------------------------


def preserve_edits(old: Step, new: Step) -> tuple[Step, list[str]]:
    """Carry a human's work forward onto the regenerated step.

    Hard requirement: regeneration must not destroy hand-written text. For
    every field the user edited on the old step, the old value wins and the
    freshly generated value is discarded from the document — it is still
    reported as a field change so the reviewer can choose to take it.

    The lineage id is inherited too, so edits survive not just the next
    regeneration but every one after it.
    """
    merged = new.model_copy(deep=True)
    merged.meta.lineage_id = old.meta.lineage_id
    merged.meta.edited_by_human = old.meta.edited_by_human
    merged.meta.edited_fields = list(old.meta.edited_fields)

    preserved: list[str] = []
    for field in old.meta.edited_fields:
        if not hasattr(old, field):
            continue
        setattr(merged, field, getattr(old, field))
        preserved.append(field)
    return merged, preserved


# --------------------------------------------------------------------------
# The stage
# --------------------------------------------------------------------------


class DiffEngine:
    """Callable outside the Stage machinery, because the diff compares two
    SOPs rather than two points in one job's pipeline."""

    def __init__(self, cfg: Config, judge=None, visual=None):
        self.cfg = cfg
        #: Optional callable(old_step, new_step) -> (score, rationale).
        self.judge = judge
        #: Optional callable(old_step, new_step) -> (iou, rationale).
        self.visual = visual

    def run(self, old_sop: SOP, new_sop: SOP) -> tuple[DiffResult, SOP]:
        """Returns the diff and the merged new SOP with edits preserved."""
        old, new = old_sop.steps, new_sop.steps
        d = self.cfg.diff

        result = DiffResult(
            document_id=old_sop.document_id or new_sop.document_id,
            old_version=old_sop.version,
            new_version=new_sop.version,
        )

        if not old:
            merged = new_sop.model_copy(deep=True)
            result.entries = [
                StepDiff(status=DiffStatus.added, new_step_id=s.step_id,
                         new_order=s.order, lineage_id=s.meta.lineage_id,
                         decided_by="trivial", rationale="no previous version")
                for s in new
            ]
            result.summary = _summarise(result.entries)
            return result, merged
        if not new:
            result.entries = [
                StepDiff(status=DiffStatus.removed, old_step_id=s.step_id,
                         old_order=s.order, lineage_id=s.meta.lineage_id,
                         decided_by="trivial", rationale="new version has no steps")
                for s in old
            ]
            result.summary = _summarise(result.entries)
            return result, new_sop.model_copy(deep=True)

        matrix, used_embeddings = similarity_matrix(self.cfg, old, new)
        print(
            f"[diff] {len(old)} old x {len(new)} new steps | "
            f"tier 2 embeddings {'used' if used_embeddings else 'unavailable'}"
        )

        pairs = [
            Pair(i, j, matrix[i][j],
                 decided_by="embedding" if used_embeddings else "lexical")
            for i, j in optimal_assignment(matrix)
        ]

        pairs = self._adjudicate(pairs, old, new, result)
        matched = [p for p in pairs if p.score >= d.match_threshold]

        entries, merged_steps = self._classify(matched, old, new)
        result.entries = entries
        result.summary = _summarise(entries)

        merged_sop = new_sop.model_copy(deep=True)
        merged_sop.steps = merged_steps

        s = result.summary
        print(
            f"[diff] {s.get('unchanged', 0)} unchanged, {s.get('modified', 0)} modified, "
            f"{s.get('added', 0)} added, {s.get('removed', 0)} removed, "
            f"{s.get('reordered', 0)} reordered"
        )
        return result, merged_sop

    # ------------------------------------------------------------------

    def _adjudicate(self, pairs: list[Pair], old, new, result: DiffResult) -> list[Pair]:
        """Escalate only pairs the offline tiers could not settle.

        Everything outside the ambiguous band is already decided, for free.
        """
        lo, hi = self.cfg.diff.ambiguous_band
        ambiguous = [p for p in pairs if lo <= p.score < hi]
        if not ambiguous:
            return pairs

        print(f"[diff] {len(ambiguous)} of {len(pairs)} pairs are ambiguous ({lo}-{hi})")

        for p in ambiguous:
            if self.judge and self.cfg.similarity.use_llm_judge:
                try:
                    score, why = self.judge(old[p.old_index], new[p.new_index])
                    p.score, p.decided_by, p.rationale = score, "llm_judge", why
                    result.llm_judgements_used += 1
                    continue
                except Exception as exc:
                    print(f"[diff] judge failed ({exc}) — keeping the offline score")

            # Visual fallback: last resort, hard-capped, and only when text
            # genuinely could not decide.
            if (
                self.visual
                and self.cfg.diff.use_visual_fallback
                and result.visual_comparisons_used < self.cfg.diff.max_visual_comparisons
            ):
                try:
                    iou, why = self.visual(old[p.old_index], new[p.new_index])
                    result.visual_comparisons_used += 1
                    if iou >= self.cfg.diff.visual_same_screen_iou:
                        p.score = max(p.score, hi)
                    elif iou <= self.cfg.diff.visual_different_screen_iou:
                        p.score = min(p.score, lo - 0.01)
                    p.decided_by, p.rationale = "visual", why
                except Exception as exc:
                    print(f"[diff] visual comparison failed ({exc})")
        return pairs

    def _prose_scores(self, matched_sorted, old, new) -> dict[str, float]:
        """Batch-score every differing prose field across all matched pairs.

        One encode call for the whole document rather than one per field.
        """
        from .similarity import prose_equivalence

        keys: list[str] = []
        pairs: list[tuple[str, str]] = []
        for p in matched_sorted:
            merged, _ = preserve_edits(old[p.old_index], new[p.new_index])
            for field, av, bv in field_pairs(old[p.old_index], merged):
                if field in PROSE_FIELDS and _normalise(av) != _normalise(bv):
                    keys.append(f"{field}\x00{av}\x00{bv}")
                    pairs.append((str(av or ""), str(bv or "")))
        if not pairs:
            return {}
        return dict(zip(keys, prose_equivalence(self.cfg, pairs)))

    def _classify(self, matched: list[Pair], old: list[Step], new: list[Step]):
        """Assign a status to every step on both sides, and merge edits."""
        d = self.cfg.diff
        matched_sorted = sorted(matched, key=lambda p: p.old_index)
        prose_scores = self._prose_scores(matched_sorted, old, new)
        in_order = _classify_order(matched_sorted, d.reorder_min_similarity)

        entries: list[StepDiff] = []
        merged_by_new: dict[int, Step] = {}
        matched_old = {p.old_index for p in matched}
        matched_new = {p.new_index for p in matched}

        for pos, p in enumerate(matched_sorted):
            o, n = old[p.old_index], new[p.new_index]
            merged, preserved = preserve_edits(o, n)
            merged_by_new[p.new_index] = merged

            # Compare against the merged step: a field the human edited and we
            # preserved is not a change to the document.
            changes = field_changes(o, merged, prose_scores, d.field_rewrite_threshold)

            # Cosmetic differences are shown to the reviewer but do not by
            # themselves make a step "modified".
            material = [c for c in changes if c.field not in COSMETIC_FIELDS]
            moved = pos not in in_order
            # Content changes outrank a move: "the Save button is now Submit"
            # is what the reviewer must act on, and reporting only "this moved"
            # would hide it. The move is preserved in `also_reordered`.
            if material:
                status = DiffStatus.modified
            elif moved:
                status = DiffStatus.reordered
            else:
                status = DiffStatus.unchanged

            entries.append(StepDiff(
                status=status,
                also_reordered=moved,
                lineage_id=merged.meta.lineage_id,
                old_step_id=o.step_id, new_step_id=n.step_id,
                old_order=o.order, new_order=n.order,
                similarity=round(p.score, 4),
                field_changes=changes,
                decided_by=p.decided_by,
                rationale=p.rationale or _order_rationale(status, moved, changes, o, n),
                preserved_edits=preserved,
            ))

        for j, s in enumerate(new):
            if j not in matched_new:
                merged_by_new[j] = s
                entries.append(StepDiff(
                    status=DiffStatus.added, lineage_id=s.meta.lineage_id,
                    new_step_id=s.step_id, new_order=s.order,
                    decided_by="assignment",
                    rationale="no step in the previous version matched above threshold",
                ))

        for i, s in enumerate(old):
            if i not in matched_old:
                entries.append(StepDiff(
                    status=DiffStatus.removed, lineage_id=s.meta.lineage_id,
                    old_step_id=s.step_id, old_order=s.order,
                    decided_by="assignment",
                    rationale="no step in the new version matched above threshold",
                ))

        merged_steps = [merged_by_new[j] for j in sorted(merged_by_new)]
        for order, step in enumerate(merged_steps, start=1):
            step.order = order

        entries.sort(key=lambda e: (e.new_order if e.new_order is not None else 1e6,
                                    e.old_order or 0))
        return entries, merged_steps


def _classify_order(matched_sorted: list[Pair], min_similarity: float) -> set[int]:
    """Which matched pairs kept their relative order.

    **The reorder-vs-remove+add heuristic.**

    Given pairs sorted by their position in the old document, take the
    longest strictly increasing subsequence of their positions in the new
    document. Pairs inside that subsequence kept their relative order.
    Pairs outside it moved, and are reported as `reordered`.

    Why LIS and not "position changed": in a document where one step moves
    from the end to the front, every other step's absolute index shifts by
    one. Comparing indices directly would report the whole document as
    reordered. LIS reports the *minimum* set of steps that must have moved
    to explain the new ordering, which is the answer a human would give.

    The ambiguity this resolves: a step that vanished from position 2 and a
    similar-looking step that appeared at position 7 could be either one step
    that moved, or a removal plus an unrelated addition. We call it a move
    only when the pair matched above `reorder_min_similarity` — a bar set
    higher than the ordinary match threshold, because claiming a move asserts
    identity across a distance. Below that bar the pair is left to the normal
    matching rules, which will report remove + add.
    """
    if not matched_sorted:
        return set()

    # Every matched pair participates in establishing the ordering. An earlier
    # version excluded low-confidence pairs from the subsequence entirely,
    # which hid real moves: the pair involved in a swap is often the one that
    # also changed, so it scores lower, and removing it made the remaining
    # sequence look perfectly ordered. The confidence gate belongs on the
    # *accusation*, not on the evidence.
    kept = weighted_increasing_subsequence(
        [p.new_index for p in matched_sorted],
        [p.score for p in matched_sorted],
    )
    in_order = set(kept)
    # A pair too weak to assert identity across a distance is never accused of
    # moving; it degrades to the ordinary matching rules instead.
    in_order |= {
        i for i, p in enumerate(matched_sorted) if p.score < min_similarity
    }
    return in_order


def _order_rationale(status: DiffStatus, moved: bool, changes: list, old: Step, new: Step) -> str:
    move = f"moved from position {old.order} to {new.order}"
    if status == DiffStatus.reordered:
        return move
    if status == DiffStatus.unchanged:
        # Distinguish "identical" from "reworded but equivalent" — the reviewer
        # is shown the cosmetic differences either way and should know which.
        return "wording differs, meaning unchanged" if changes else "no field differences"
    return f"field values differ; also {move}" if moved else "field values differ"


def _summarise(entries: list[StepDiff]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        out[e.status.value] = out.get(e.status.value, 0) + 1
    return out
