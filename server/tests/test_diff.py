"""Diff engine graded against the known-correct answer.

The diff is the product, so these are the tests that matter most. They run
offline — lexical and local embeddings only, no API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import load_config  # noqa: E402
from app.models import Confidence, DiffStatus, SOP, Step, UiElement  # noqa: E402
from app.pipeline.diff import (COSMETIC_FIELDS, DiffEngine, field_changes,  # noqa: E402
                               preserve_edits, weighted_increasing_subsequence)
from sop_fixtures import sop_v1, sop_v2  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def diffed(cfg):
    """Diff the fixtures once, keeping the exact SOP instances that were used.

    step_id is a fresh uuid on every construction, so the SOP objects must be
    held rather than rebuilt — otherwise nothing can be looked up by id.
    """
    v1, v2 = sop_v1(), sop_v2()
    result, merged = DiffEngine(cfg).run(v1, v2)
    return result, merged, v1, v2


def _entry(diffed, fragment: str):
    """Find the entry whose old or new step title contains `fragment`."""
    result, _, v1, v2 = diffed
    titles = {s.step_id: s.title for s in list(v1.steps) + list(v2.steps)}
    for e in result.entries:
        title = titles.get(e.new_step_id) or titles.get(e.old_step_id) or ""
        if fragment.lower() in title.lower():
            return e
    raise AssertionError(f"no diff entry for {fragment!r}")


# --------------------------------------------------------------------------
# The demo: does it get the right answer?
# --------------------------------------------------------------------------


def test_added_step_is_detected(diffed):
    """2FA appears in v2 and nothing in v1 resembles it."""
    assert _entry(diffed, "two-factor").status == DiffStatus.added


def test_removed_step_is_detected(diffed):
    """The attach-receipt step is gone from v2."""
    assert _entry(diffed, "attach").status == DiffStatus.removed


def test_button_rename_is_modified_not_remove_plus_add(diffed):
    """The single most important case in the whole demo.

    Save -> Submit must be reported as one modified step. Reporting it as a
    removal plus an unrelated addition would lose the connection between the
    two versions, and with it every hand edit on that step.
    """
    e = _entry(diffed, "expense details")
    assert e.status == DiffStatus.modified
    labels = [c for c in e.field_changes if c.field == "ui_element.label"]
    assert labels, "the button rename must be reported as a field change"
    assert labels[0].old == "Save" and labels[0].new == "Submit"


def test_rewording_alone_is_not_a_change(diffed):
    """Every regeneration rewrites the prose. If that reads as "modified",
    the reviewer sees a wall of false positives and stops trusting the diff."""
    for fragment in ("admin console", "dashboard", "finish"):
        e = _entry(diffed, fragment)
        assert e.status == DiffStatus.unchanged, (
            f"{fragment!r} was only reworded but reported {e.status.value}"
        )


def test_summary_matches_ground_truth(diffed):
    summary = diffed[0].summary
    assert summary.get("added") == 1
    assert summary.get("removed") == 1
    assert summary.get("modified") == 1
    assert summary.get("unchanged") == 4


def test_the_move_is_reported_somewhere(diffed):
    """Form and review swapped. Which of the two is called the mover is
    genuinely ambiguous, but silence is not an acceptable answer."""
    moved = [e for e in diffed[0].entries
             if e.also_reordered or e.status == DiffStatus.reordered]
    assert moved, "a step changed relative position and nothing reported it"


def test_cosmetic_changes_are_shown_but_do_not_drive_status(diffed):
    """location_hint differences are surfaced to the reviewer, but a step
    whose only difference is a rephrased position is not "modified"."""
    e = _entry(diffed, "admin console")
    assert e.status == DiffStatus.unchanged
    assert any(c.field in COSMETIC_FIELDS for c in e.field_changes)
    assert "wording" in e.rationale


# --------------------------------------------------------------------------
# The hard requirement: edits must survive
# --------------------------------------------------------------------------


def test_manual_edits_survive_regeneration(cfg):
    """Hard requirement from the spec.

    A person edits an instruction on v1. v2 is generated and rewrites that
    instruction. The human's text must win.
    """
    v1 = sop_v1()
    edited = v1.steps[1]
    edited.instruction = "Click the big orange New request button. NB: only visible to admins."
    edited.meta.edited_by_human = True
    edited.meta.edited_fields = ["instruction"]

    _, merged = DiffEngine(cfg).run(v1, sop_v2())

    survivor = [s for s in merged.steps if s.meta.lineage_id == edited.meta.lineage_id]
    assert survivor, "the edited step lost its lineage and could not be tracked"
    assert survivor[0].instruction == edited.instruction, (
        "regeneration overwrote a hand-written instruction"
    )


def test_lineage_survives_across_two_regenerations(cfg):
    """Edit preservation must not break on the *second* update.

    A lineage that is minted fresh each run works once and then silently
    stops, which is worse than not working at all.
    """
    v1 = sop_v1()
    v1.steps[0].meta.edited_by_human = True
    v1.steps[0].meta.edited_fields = ["title"]
    v1.steps[0].title = "Log in (use your SSO account)"
    lineage = v1.steps[0].meta.lineage_id

    engine = DiffEngine(cfg)
    _, gen2 = engine.run(v1, sop_v2())
    gen2.version = 2
    v3 = sop_v2()
    v3.version = 3
    _, gen3 = engine.run(gen2, v3)

    survivors = [s for s in gen3.steps if s.meta.lineage_id == lineage]
    assert survivors, "lineage was lost on the second regeneration"
    assert survivors[0].title == "Log in (use your SSO account)"


def test_preserved_edit_is_not_reported_as_a_change(cfg):
    """If we kept the human's text, the document did not change."""
    v1 = sop_v1()
    v1.steps[5].expected_result = "You land back on the dashboard listing."
    v1.steps[5].meta.edited_by_human = True
    v1.steps[5].meta.edited_fields = ["expected_result"]

    v2 = sop_v2()
    result, merged = DiffEngine(cfg).run(v1, v2)
    e = _entry((result, merged, v1, v2), "finish")
    assert "expected_result" in e.preserved_edits
    assert not [c for c in e.field_changes if c.field == "expected_result"]


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def test_weighted_subsequence_prefers_confident_pairs():
    """The tie-break that decides which step gets accused of moving."""
    values = [0, 2, 4, 3, 5]
    # Two length-4 subsequences exist: keeping index 2, or keeping index 3.
    low = weighted_increasing_subsequence(values, [0.9, 0.9, 0.5, 0.9, 0.9])
    assert 2 not in low and 3 in low, "should anchor the higher-confidence pair"

    high = weighted_increasing_subsequence(values, [0.9, 0.9, 0.99, 0.5, 0.9])
    assert 2 in high and 3 not in high


def test_empty_subsequence():
    assert weighted_increasing_subsequence([]) == set()


def test_identity_fields_are_never_smoothed_away():
    """A label change must be reported even at a permissive prose threshold."""
    def mk(label):
        return Step(order=1, title="Save the form", instruction="Click the button.",
                    ui_element=UiElement(type="button", label=label),
                    expected_result="Saved.", confidence=Confidence.high)

    changes = field_changes(mk("Save"), mk("Submit"), {}, rewrite_threshold=0.0)
    assert any(c.field == "ui_element.label" for c in changes)


def test_diff_against_empty_previous_version(cfg):
    """First upload: everything is an addition, nothing crashes."""
    empty = SOP(job_id="j", document_id="d", version=0, steps=[])
    result, merged = DiffEngine(cfg).run(empty, sop_v1())
    assert result.summary.get("added") == 6
    assert len(merged.steps) == 6


def test_diff_when_new_version_is_empty(cfg):
    empty = SOP(job_id="j", document_id="d", version=2, steps=[])
    result, _ = DiffEngine(cfg).run(sop_v1(), empty)
    assert result.summary.get("removed") == 6


def test_identical_sops_report_no_changes(cfg):
    result, _ = DiffEngine(cfg).run(sop_v1(), sop_v1())
    assert result.summary.get("unchanged") == 6
    assert not any(e.status != DiffStatus.unchanged for e in result.entries)
