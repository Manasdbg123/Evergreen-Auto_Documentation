"""The data contracts every pipeline stage reads and writes.

These schemas are the product. The diff engine can only be precise about what
changed because a step is a set of named fields rather than a paragraph of
prose, so treat every field here as load-bearing.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Stage 2 — transcript
# --------------------------------------------------------------------------


class Word(BaseModel):
    text: str
    start: float
    end: float
    probability: float | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = []


class Transcript(BaseModel):
    """Optional enrichment. The pipeline is fully functional with this empty."""

    available: bool = False
    language: str | None = None
    duration: float = 0.0
    segments: list[TranscriptSegment] = []
    # Why transcription produced nothing, when it produced nothing.
    note: str | None = None

    def text_between(self, start: float, end: float) -> str:
        parts = [s.text.strip() for s in self.segments if s.end >= start and s.start <= end]
        return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Stage 3/4 — frames and change events
# --------------------------------------------------------------------------


class SampledFrame(BaseModel):
    index: int
    timestamp: float
    path: str
    phash: str
    # Similarity to the previous sampled frame. 1.0 for the first frame.
    ssim_prev: float = 1.0
    phash_dist_prev: int = 0


class ChangeEvent(BaseModel):
    """A moment where the screen genuinely changed.

    `stable_timestamp` is the point we actually screenshot: the first frame
    after the change whose similarity to its successor has recovered, so we
    capture a settled UI rather than a mid-transition blur.
    """

    event_id: str = Field(default_factory=lambda: new_id("ev"))
    change_timestamp: float
    stable_timestamp: float
    stable_frame_index: int
    stable_frame_path: str
    phash: str
    # How large the change was: 1.0 - ssim at the drop. Higher = more visual churn.
    magnitude: float
    # How cleanly it settled: ssim between the stable frame and its successor.
    stability: float
    settled: bool = True
    merged_from: int = 1


class CandidateFrame(BaseModel):
    """A change event promoted to the (small) set that may reach a vision call."""

    candidate_id: str = Field(default_factory=lambda: new_id("cand"))
    event_id: str
    order: int
    timestamp: float
    frame_path: str
    llm_image_path: str | None = None
    phash: str
    magnitude: float
    stability: float
    score: float
    transcript_text: str = ""


# --------------------------------------------------------------------------
# Stage 5 — step detection
# --------------------------------------------------------------------------


class DetectedStep(BaseModel):
    """Haiku's verdict: this candidate is a real step, not scroll/hover noise."""

    candidate_id: str
    is_step: bool
    order: int | None = None
    reason: str = ""
    provisional_title: str = ""


# --------------------------------------------------------------------------
# Stage 6 — the structured SOP
# --------------------------------------------------------------------------


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class UiElement(BaseModel):
    """Structured on purpose.

    A string here would force an LLM call to explain every modification. As
    fields, "Save" -> "Submit" is a diff the engine reports for free.
    """

    type: Literal[
        "button", "link", "field", "menu", "tab", "toggle",
        "checkbox", "dropdown", "dialog", "icon", "other",
    ] = "other"
    label: str = ""
    location_hint: str = ""


class StepMeta(BaseModel):
    """Provenance and edit tracking. Kept out of the diffable core."""

    # Carried across regenerations when the aligner matches this step to a
    # previous one. This — not step_id — is what makes hand edits survive.
    lineage_id: str = Field(default_factory=lambda: new_id("ln"))
    source_frame_ts: float | None = None
    candidate_id: str | None = None
    transcript_span: tuple[float, float] | None = None
    phash: str | None = None
    edited_by_human: bool = False
    edited_fields: list[str] = []
    #: What the model wrote before a human overwrote it, per edited field.
    #: Kept so step *identity* can be judged on generated text on both sides —
    #: see `Step.identity_view`. Never shown to the reader.
    generated_values: dict[str, str] = {}
    # Set when narration and frame disagreed; the frame won.
    conflict: str | None = None


class Step(BaseModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    order: int
    title: str
    instruction: str
    ui_element: UiElement = Field(default_factory=UiElement)
    expected_result: str = ""
    screenshot_ref: str | None = None
    prerequisites: list[str] = []
    confidence: Confidence = Confidence.medium
    meta: StepMeta = Field(default_factory=StepMeta)

    def diff_payload(self) -> dict[str, Any]:
        """The fields that define step identity for comparison purposes."""
        return {
            "title": self.title,
            "instruction": self.instruction,
            "ui_element": self.ui_element.model_dump(),
            "expected_result": self.expected_result,
            "prerequisites": list(self.prerequisites),
        }

    def identity_view(self) -> dict[str, str | None]:
        """The fields used to decide *which step this is*, with edits neutralised.

        A human's edit must never be evidence about identity. Comparing a
        person's hand-written instruction against freshly generated prose
        compares two different kinds of text, and the effect is perverse: the
        more carefully someone edits a step, the less likely the engine is to
        recognise it on the next recording — so regeneration discards exactly
        the work that was most valuable. That inverts the hard requirement this
        product rests on.

        So for any field a human edited, identity is judged on what the model
        wrote *before* they touched it (`meta.generated_values`), keeping both
        sides machine-generated and comparable. When that original was not
        captured, the field is dropped rather than trusted — an unknown edit is
        not evidence, and pretending otherwise is what caused the failure.

        The human's text is of course still what the document shows, and is
        still compared by `field_changes`. It is only barred from deciding
        which step is which.

        `None` means "unknown" and is distinct from `""`, which means the field
        is genuinely empty. The caller must not compare an unknown field — it
        should abstain. Collapsing the two would make a suppressed field score
        as a mismatch, which reintroduces the same penalty by a different
        route.
        """
        edited = set(self.meta.edited_fields)
        original = self.meta.generated_values

        def value(field: str, actual: str) -> str | None:
            if field not in edited:
                return actual
            return original.get(field)  # None when no baseline was captured

        view: dict[str, str | None] = {
            "title": value("title", self.title),
            "instruction": value("instruction", self.instruction),
            "label": value("ui_element.label", self.ui_element.label),
        }
        # If every identity field was suppressed there is nothing left to match
        # on. Fall back to the live values: a degraded comparison still beats
        # abstaining on everything and matching nothing.
        if all(v is None for v in view.values()):
            return {"title": self.title, "instruction": self.instruction,
                    "label": self.ui_element.label}
        return view

    def similarity_text(self) -> str:
        """What the embedder reads to decide *which step this is*.

        `expected_result` is deliberately excluded, for the same reason
        `ui_element.label` is weighted at 0.05 in `lexical_similarity`:
        it is a change signal, not an identity signal.

        A step's expected result describes its *consequence*, which changes
        whenever the workflow changes around it — without the step itself
        changing at all. Measured on the two real generated SOPs: inserting a
        2FA screen after sign-in rewrote sign-in's expected_result from "the
        dashboard loads" to "the Two-factor verification screen appears", and
        the sign-in pair — identical title, identical "Log in" label — fell to
        0.500, under the 0.62 match threshold, and was reported as
        remove + add.

        That was not a tuning problem. Including the field, correct pairs
        bottomed out at 0.500 while incorrect pairs reached 0.508: the
        distributions *overlap*, so no threshold separates them. Excluding it,
        correct pairs bottom out at 0.744 and incorrect ones top out at 0.395 —
        a 0.349 margin with the threshold sitting inside it.

        The field is still compared in full by `field_changes`, which is where
        a changed consequence belongs: reported as a change, never used as
        evidence that the step is a different step.
        """
        view = self.identity_view()
        return " ".join(
            b for b in (view["title"], view["instruction"], view["label"]) if b
        ).strip()


class SOP(BaseModel):
    sop_id: str = Field(default_factory=lambda: new_id("sop"))
    job_id: str
    document_id: str | None = None
    version: int = 1
    title: str = "Untitled procedure"
    summary: str = ""
    steps: list[Step] = []
    generated_from_transcript: bool = False


# --------------------------------------------------------------------------
# Stage 8 — the diff
# --------------------------------------------------------------------------


class DiffStatus(str, Enum):
    unchanged = "unchanged"
    modified = "modified"
    added = "added"
    removed = "removed"
    reordered = "reordered"


class FieldChange(BaseModel):
    field: str
    old: Any = None
    new: Any = None


class StepDiff(BaseModel):
    diff_id: str = Field(default_factory=lambda: new_id("d"))
    status: DiffStatus
    lineage_id: str | None = None
    old_step_id: str | None = None
    new_step_id: str | None = None
    old_order: int | None = None
    new_order: int | None = None
    similarity: float | None = None
    field_changes: list[FieldChange] = []
    #: A step can both move and change content. `status` reports the more
    #: actionable of the two (content), so this carries the move separately
    #: rather than letting one finding mask the other.
    also_reordered: bool = False
    # How the verdict was reached: lexical | embedding | llm_judge | visual
    decided_by: str = "lexical"
    rationale: str = ""
    # True when the old step carried human edits that were preserved.
    preserved_edits: list[str] = []
    # Per-step accept/reject in the review UI.
    review: Literal["pending", "accepted", "rejected"] = "pending"


class DiffResult(BaseModel):
    document_id: str | None = None
    old_version: int
    new_version: int
    entries: list[StepDiff] = []
    summary: dict[str, int] = {}
    visual_comparisons_used: int = 0
    llm_judgements_used: int = 0
