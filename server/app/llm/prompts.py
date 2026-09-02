"""Prompts and response schemas for the two LLM stages.

Kept in one file, separate from the stages that run them, for a reason that is
part of the pitch: everything a client would want to change about *how the SOP
reads* is either a config value interpolated here or a line of this file. No
prompt text is buried in pipeline code.

The schemas are not free-form JSON. They mirror `models.Step` field for field,
because the diff engine compares named fields — a schema drift here silently
degrades the product's core capability rather than raising an error.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..models import UiElement

#: Pulled off the model so the prompt and the schema can never disagree about
#: which control types exist.
UI_ELEMENT_TYPES = list(UiElement.model_fields["type"].annotation.__args__)

GRANULARITY_GUIDANCE = {
    "coarse": "Group related interactions. A whole form — fill several fields, "
              "then submit — is one step.",
    "normal": "One step per meaningful user action that changes the screen. "
              "Filling several fields of the same form is one step; submitting "
              "it is another.",
    "fine": "One step per individual interaction, including each field entry.",
}

TONE_GUIDANCE = {
    "neutral": "Plain and factual.",
    "friendly": "Warm and encouraging, but never chatty.",
    "formal": "Formal register, no contractions.",
    "terse": "As short as possible while still unambiguous.",
}


# --------------------------------------------------------------------------
# Stage 6 — step detection (Haiku)
# --------------------------------------------------------------------------


def detect_steps_system(cfg: Config) -> str:
    return f"""You classify frames from a screen recording of a software workflow.

You are given candidate frames in chronological order. Each was captured at a
moment the screen changed. Your only job is to decide which frames represent a
genuine step a person would follow, and which are noise.

Noise, to be rejected:
- scrolling within a page that is already shown by another candidate
- hover states, tooltips, focus rings, cursor movement
- alt-tab, window switching, notifications, taskbar and OS chrome
- loading spinners, skeletons, and mid-transition frames
- returning to a screen an earlier candidate already shows

A genuine step, to be accepted:
- a new screen, page, dialog, or panel the user reached
- a form submitted, a record saved, a state changed
- a distinct decision point the reader must act on

{GRANULARITY_GUIDANCE.get(cfg.steps.granularity, GRANULARITY_GUIDANCE['normal'])}

Aim for between {cfg.steps.min_count} and {cfg.steps.max_count} accepted
frames. Prefer rejecting a doubtful frame: a missing step is repaired by the
reviewer, but a fabricated step teaches somebody the wrong procedure.

Narration, when present, is a hint about intent only. The frames are the record
of what happened. Never accept a frame because narration mentions something you
cannot see in it."""


def detect_steps_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "description": "One entry per candidate, in the order given.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    # Strict mode validates only what the schema constrains,
                    # so every property is required — an optional key is an
                    # unconstrained key.
                    "required": ["candidate_id", "is_step", "reason",
                                 "provisional_title"],
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "is_step": {"type": "boolean"},
                        "reason": {
                            "type": "string",
                            "description": "One short clause. If rejecting, name the "
                                           "kind of noise.",
                        },
                        "provisional_title": {
                            "type": "string",
                            "description": "Five words or fewer describing the action. "
                                           "Empty when is_step is false.",
                        },
                    },
                },
            }
        },
    }


def detect_steps_user_intro(cfg: Config, count: int, has_transcript: bool) -> str:
    source = ("Frames and narration follow." if has_transcript
              else "Frames follow. This recording has no narration — judge from "
                   "the images alone.")
    return (f"{count} candidate frames from one screen recording. {source}\n"
            f"Return exactly {count} decisions, one per candidate_id, in order.")


# --------------------------------------------------------------------------
# Stage 7 — structuring (Sonnet, vision)
# --------------------------------------------------------------------------


def structure_system(cfg: Config) -> str:
    w = cfg.writing
    person = ("Address the reader directly in the imperative: \"Click Save\", not "
              "\"The user clicks Save\"." if w.person == "second"
              else "Write in the third person.")
    return f"""You write standard operating procedures from screen recordings.

You are given the confirmed step frames of one workflow, in order, with any
narration recorded near each frame. Produce one step per frame, in the same
order, plus a title and a one-sentence summary for the document.

Audience: {w.audience}
Tone: {TONE_GUIDANCE.get(w.tone, TONE_GUIDANCE['neutral'])}
{person}
Keep each instruction to {w.max_instruction_words} words or fewer.

Read the actual text in each screenshot. Button labels, field names and menu
items must be copied exactly as they appear on screen, including capitalisation.
Do not paraphrase a label, and do not invent one you cannot read.

THE CONFLICT RULE — this one matters more than the others:
The video is the record of what happened. The narration is a hint about intent
and naming, and speakers misremember their own product. When the narration and
the frame disagree — the speaker says "Save" and the button reads "Submit" —
the frame wins. Use the label you can see, use the narration only for the
surrounding explanation, set confidence to "low", and describe the disagreement
in the `conflict` field. A reviewer will then check that step.

Set confidence:
- high    the frame shows the action and its result unambiguously
- medium  the action is clear but the result or a label is inferred
- low     narration and frame disagree, or the frame is hard to read

`prerequisites` lists conditions that must already hold before this step, and
only when they are not simply "you completed the previous step". Most steps
have none; an empty list is the normal answer.

`expected_result` is what the reader should see after acting, so they can tell
whether it worked. If the next frame shows the outcome, describe that."""


def structure_tool_schema(cfg: Config) -> dict[str, Any]:
    step_props = {
        "candidate_id": {
            "type": "string",
            "description": "The candidate_id of the frame this step describes.",
        },
        "title": {"type": "string", "description": "Six words or fewer."},
        "instruction": {
            "type": "string",
            "description": f"What to do, in {cfg.writing.max_instruction_words} "
                           f"words or fewer.",
        },
        "ui_element": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "label", "location_hint"],
            "properties": {
                "type": {"type": "string", "enum": UI_ELEMENT_TYPES},
                "label": {
                    "type": "string",
                    "description": "The control's on-screen text, copied exactly. "
                                   "Empty if the step involves no single control.",
                },
                "location_hint": {
                    "type": "string",
                    "description": "Where on screen it sits, e.g. 'top right of "
                                   "the sidebar'.",
                },
            },
        },
        "expected_result": {"type": "string"},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "conflict": {
            "type": "string",
            "description": "Only when narration and frame disagreed: state both, "
                           "and that the frame was used. Otherwise empty.",
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "summary", "steps"],
        "properties": {
            "title": {
                "type": "string",
                "description": "The procedure, named as a task: 'Submit an expense "
                               "claim'.",
            },
            "summary": {"type": "string", "description": "One sentence."},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["candidate_id", "title", "instruction",
                                 "ui_element", "expected_result", "prerequisites",
                                 "confidence", "conflict"],
                    "properties": step_props,
                },
            },
        },
    }


def structure_user_intro(count: int, has_transcript: bool) -> str:
    narration = ("Narration recorded near each frame is included."
                 if has_transcript
                 else "This recording has no narration. Write from the frames alone.")
    return (f"{count} confirmed step frames from one workflow, in order. "
            f"{narration}\nReturn exactly {count} steps, one per candidate_id, "
            f"in the same order.")
