"""The diff, and the per-step review that follows it.

The review is the point. A diff nobody can act on is a report; a diff where
each change is accepted or rejected individually is a tool. Accepting and
rejecting are both applied against the *stored* document, so a rejected change
leaves the previous text in place rather than merely hiding a row.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import load_config
from ..models import SOP, DiffResult, DiffStatus, Step
from ..pipeline.base import JobPaths
from ..pipeline.diff import DIFF_FIELDS
from ..pipeline.diff_stage import DiffStage

router = APIRouter(prefix="/api", tags=["diff"])


class RunDiff(BaseModel):
    #: The job holding the new recording.
    job_id: str
    #: Compare against this version. Defaults to the document's latest.
    against_version: int | None = None
    offline: bool = False


class ReviewDecision(BaseModel):
    diff_id: str
    decisions: dict[str, Literal["accepted", "rejected", "pending"]]


@router.post("/documents/{document_id}/diff")
def run_diff(document_id: str, body: RunDiff) -> dict[str, Any]:
    """Diff a freshly processed recording against a stored document."""
    cfg = load_config()
    if db.get_document(cfg, document_id) is None:
        raise HTTPException(404, f"No document '{document_id}'")

    old_sop = db.get_version(cfg, document_id, body.against_version)
    if old_sop is None:
        raise HTTPException(400, "That document has no version to diff against")
    if not old_sop.job_id:
        raise HTTPException(400, "The stored version has no job to resolve its "
                                 "screenshots against")

    stage = DiffStage(cfg, old_job_id=old_sop.job_id, offline=body.offline)
    try:
        data = stage.run(body.job_id, force=True)
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")

    result = DiffResult.model_validate(data["diff"])
    merged = SOP.model_validate(data["merged_sop"])

    # The merged SOP — the one carrying preserved edits — becomes the new
    # current version immediately. Review then adjusts it per step, rather than
    # the user having to accept everything before seeing a document at all.
    db.attach_job_to_document(cfg, body.job_id, document_id)
    version = db.save_version(cfg, document_id, merged, source="merged",
                              job_id=body.job_id)
    result.document_id = document_id
    result.new_version = version
    diff_id = db.save_diff(cfg, document_id, result)

    return {"diff_id": diff_id, "document_id": document_id,
            "version": version, "summary": result.summary,
            "diff": result.model_dump()}


@router.get("/diffs/{diff_id}")
def get_diff(diff_id: str) -> dict[str, Any]:
    cfg = load_config()
    result = db.get_diff(cfg, diff_id)
    if result is None:
        raise HTTPException(404, f"No diff '{diff_id}'")
    return result.model_dump()


@router.get("/documents/{document_id}/diff")
def latest_diff(document_id: str) -> dict[str, Any]:
    cfg = load_config()
    found = db.latest_diff(cfg, document_id)
    if found is None:
        raise HTTPException(404, f"No diff recorded for '{document_id}'")
    diff_id, result = found
    return {"diff_id": diff_id, **result.model_dump()}


@router.post("/diffs/{diff_id}/review")
def review_diff(diff_id: str, body: ReviewDecision) -> dict[str, Any]:
    """Accept or reject individual changes, and write the outcome back.

    Rejecting is the interesting half. It does not just dismiss a row — it puts
    the previous version's values back into the current document, so "reject"
    means the change is undone rather than merely acknowledged.
    """
    cfg = load_config()
    result = db.get_diff(cfg, diff_id)
    if result is None:
        raise HTTPException(404, f"No diff '{diff_id}'")
    if not result.document_id:
        raise HTTPException(400, "This diff is not attached to a document")

    current = db.get_version(cfg, result.document_id)
    previous = db.get_version(cfg, result.document_id, result.old_version)
    if current is None or previous is None:
        raise HTTPException(400, "Cannot resolve both versions of the document")

    for entry in result.entries:
        decision = body.decisions.get(entry.diff_id)
        if decision:
            entry.review = decision

    revised = _apply_rejections(result, previous, current)
    version = db.save_version(cfg, result.document_id, revised, source="edited")
    db.update_diff(cfg, diff_id, result)

    rejected = sum(1 for e in result.entries if e.review == "rejected")
    return {"diff_id": diff_id, "version": version, "rejected": rejected,
            "summary": result.summary}


def _apply_rejections(result: DiffResult, previous: SOP, current: SOP) -> SOP:
    """Undo every rejected change against the current document.

    Rejections are resolved by `lineage_id`, the only id stable across
    versions. `step_id` is minted fresh on every regeneration, so matching on
    it would silently no-op.
    """
    revised = current.model_copy(deep=True)
    by_lineage = {s.meta.lineage_id: s for s in revised.steps}
    old_by_lineage = {s.meta.lineage_id: s for s in previous.steps}

    for entry in result.entries:
        if entry.review != "rejected" or not entry.lineage_id:
            continue
        old = old_by_lineage.get(entry.lineage_id)

        if entry.status == DiffStatus.added:
            # Rejecting an added step removes it from the document.
            revised.steps = [s for s in revised.steps
                             if s.meta.lineage_id != entry.lineage_id]
        elif entry.status == DiffStatus.removed and old is not None:
            # Rejecting a removal puts the step back.
            revised.steps.append(old.model_copy(deep=True))
        elif old is not None:
            # Rejecting a modification restores the previous field values —
            # only the ones this entry actually reported, so an unrelated
            # change in the same step is left alone.
            step = by_lineage.get(entry.lineage_id)
            if step is not None:
                _restore(step, old, [c.field for c in entry.field_changes])

    revised.steps.sort(key=lambda s: s.order)
    for order, step in enumerate(revised.steps, start=1):
        step.order = order
    return revised


def _restore(step: Step, old: Step, fields: list[str]) -> None:
    for field in fields:
        if field.startswith("ui_element."):
            key = field.split(".", 1)[1]
            setattr(step.ui_element, key, getattr(old.ui_element, key))
        elif field in DIFF_FIELDS:
            setattr(step, field, getattr(old, field))
