"""A deterministic stand-in for the two LLM stages, for when there is no key.

This exists so that the parts of the product that are *not* the LLM — frame
selection, the diff engine, edit preservation, the editor, export — can be
built, tested and demonstrated end to end without spending anything or waiting
on credentials.

It is a harness, not a feature, and it is careful not to pretend otherwise:

* Every instruction it writes is prefixed `[offline]`.
* Every step it produces is `confidence: low`.
* `sop.summary` says plainly that no vision model saw these frames.

The one thing it does faithfully is *shape*. Output validates against the same
schema the real stage returns, so anything downstream that works here works
unchanged once a key is set. It cannot read a button label off a screenshot,
so `ui_element.label` is left empty rather than guessed — a fabricated label
would flow straight into the diff and be reported as a real UI change.
"""

from __future__ import annotations

import re
from typing import Any

from ..config import Config
from ..models import CandidateFrame, Transcript

BANNER = ("Generated without a vision model (no ANTHROPIC_API_KEY). Step text is "
          "derived from timing and narration only; screenshots were not read.")

_SENTENCE_END = re.compile(r"[.!?]")


def detect_steps(
    cfg: Config, candidates: list[CandidateFrame]
) -> list[dict[str, Any]]:
    """Accept candidates on the signals stage 4 already computed.

    Stage 4's ranking score is a blend of visual magnitude, settle quality,
    narration overlap and temporal spread — a reasonable proxy for "was this a
    real step", which is exactly what the classifier is asked. Candidates
    scoring in the bottom fifth are rejected as noise.
    """
    if not candidates:
        return []

    scores = sorted(c.score for c in candidates)
    cut = scores[max(int(len(scores) * 0.2) - 1, 0)] if len(scores) > 4 else -1.0

    decisions: list[dict[str, Any]] = []
    accepted = 0
    for c in candidates:
        keep = c.score > cut or accepted < cfg.steps.min_count
        accepted += keep
        decisions.append({
            "candidate_id": c.candidate_id,
            "is_step": bool(keep),
            "reason": ("offline heuristic: rank score above the noise cut"
                       if keep else
                       "offline heuristic: rank score in the bottom fifth"),
            "provisional_title": _title_for(c),
        })

    # Respect the same ceiling the real stage does — the cap is a cost control
    # in production and a scope control here.
    if accepted > cfg.steps.max_count:
        keepers = sorted(
            (d for d in decisions if d["is_step"]),
            key=lambda d: -_score_of(d["candidate_id"], candidates),
        )[cfg.steps.max_count:]
        for d in keepers:
            d["is_step"] = False
            d["reason"] = "offline heuristic: over steps.max_count"

    return decisions


def structure(
    cfg: Config, confirmed: list[CandidateFrame], transcript: Transcript
) -> dict[str, Any]:
    """Build a schema-valid SOP from timing and narration."""
    steps = []
    for c in confirmed:
        narration = c.transcript_text.strip()
        steps.append({
            "candidate_id": c.candidate_id,
            "title": _title_for(c),
            "instruction": (
                f"[offline] {_first_sentence(narration)}" if narration
                else f"[offline] The screen changed at {c.timestamp:.1f}s. "
                     f"Open the screenshot and describe the action."
            ),
            # Empty, not guessed. A made-up label would be indistinguishable
            # from a real one to the diff engine.
            "ui_element": {"type": "other", "label": "", "location_hint": ""},
            "expected_result": "",
            "prerequisites": [],
            "confidence": "low",
            "conflict": "",
        })

    return {
        "title": _document_title(transcript),
        "summary": BANNER,
        "steps": steps,
    }


# --------------------------------------------------------------------------


def _title_for(c: CandidateFrame) -> str:
    narration = c.transcript_text.strip()
    if narration:
        words = _first_sentence(narration).split()
        if words:
            return " ".join(words[:6]).rstrip(",;:")
    return f"Screen at {c.timestamp:.1f}s"


def _first_sentence(text: str) -> str:
    match = _SENTENCE_END.search(text)
    sentence = text[: match.end()] if match else text
    return " ".join(sentence.split())


def _document_title(transcript: Transcript) -> str:
    if transcript.available and transcript.segments:
        opening = _first_sentence(transcript.segments[0].text.strip())
        if opening:
            return " ".join(opening.split()[:8]).rstrip(".,;:")
    return "Untitled procedure"


def _score_of(candidate_id: str, candidates: list[CandidateFrame]) -> float:
    return next((c.score for c in candidates if c.candidate_id == candidate_id), 0.0)
