"""Working out what a human actually changed when they save.

The editor sends back a whole document. Which fields were hand-edited is
derived here, on the server, by comparing against the stored version — never
taken from the client. Two reasons, and both are load-bearing:

* `meta.edited_fields` is what protects a person's writing from the next
  regeneration. A client that forgot to set it would silently opt the user out
  of the guarantee the whole product rests on.
* `meta.generated_values` must hold what the *model* last wrote, which the
  client has no reliable way to know after a few rounds of editing.

Steps are matched by `lineage_id`, not `step_id`, because `step_id` is minted
fresh on every regeneration by design.
"""

from __future__ import annotations

from .models import SOP, Step

#: Fields a person can edit in the editor and that the diff compares.
EDITABLE_FIELDS = ["title", "instruction", "expected_result"]


def apply_edits(previous: SOP, incoming: SOP) -> SOP:
    """Return `incoming` with edit provenance filled in from `previous`.

    Editing is cumulative: a field edited two versions ago and left alone since
    is still an edit, and must still survive the next regeneration. So marks
    are carried forward and only ever added to, never silently cleared by a
    save that happened not to touch that field.
    """
    by_lineage = {s.meta.lineage_id: s for s in previous.steps}
    by_step_id = {s.step_id: s for s in previous.steps}

    result = incoming.model_copy(deep=True)
    for step in result.steps:
        old = by_lineage.get(step.meta.lineage_id) or by_step_id.get(step.step_id)
        if old is None:
            continue  # a step the user added by hand; nothing to compare against
        _mark_edits(old, step)
    return result


def _mark_edits(old: Step, new: Step) -> None:
    edited = set(old.meta.edited_fields)
    generated = dict(old.meta.generated_values)

    for field in EDITABLE_FIELDS:
        before, after = getattr(old, field, ""), getattr(new, field, "")
        if _same(before, after):
            continue
        # First time this field is touched, the value being replaced is the
        # model's. On later edits the model's original is already recorded and
        # must not be overwritten with the user's previous draft — otherwise
        # `identity_view` ends up comparing hand-written text after all.
        generated.setdefault(field, str(before or ""))
        edited.add(field)

    new.meta.edited_fields = sorted(edited)
    new.meta.generated_values = generated
    new.meta.edited_by_human = bool(edited)
    new.meta.lineage_id = old.meta.lineage_id
    # Provenance belongs to the recording, not to the edit — a person rewriting
    # the wording does not change which frame the step came from.
    new.meta.source_frame_ts = old.meta.source_frame_ts
    new.meta.candidate_id = old.meta.candidate_id
    new.meta.phash = old.meta.phash
    if new.screenshot_ref is None:
        new.screenshot_ref = old.screenshot_ref


def _same(a: object, b: object) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.split()) == " ".join(b.split())
    return a == b
