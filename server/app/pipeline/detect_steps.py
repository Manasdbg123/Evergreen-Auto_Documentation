"""Stage 6 — separate genuine steps from noise.

The cheap model's job, and the reason it runs before the expensive one: every
candidate rejected here is a Sonnet vision call not made in stage 7. Haiku
input is a fifth of Sonnet's, so spending it to cull the set is strictly
cheaper than letting Sonnet see everything, and the estimator bears that out.

Only the classification lives here. No instruction text is written at this
stage, because a step that turns out to be an alt-tab should never have cost
anything to describe.
"""

from __future__ import annotations

from typing import Any

from ..llm import offline as offline_impl
from ..llm.client import ToolSpec, image_block, text_block
from ..llm.prompts import (detect_steps_system, detect_steps_tool_schema,
                           detect_steps_user_intro)
from ..models import CandidateFrame, DetectedStep, Transcript
from .base import JobPaths, Stage, read_stage
from .llm_stage import LLMStage


class DetectStepsStage(LLMStage):
    name = "detect_steps"
    depends_on = ["select_candidates"]

    def config_slice(self) -> dict[str, Any]:
        return {
            "steps": self.cfg.steps.model_dump(),
            "classify_model": self.cfg.models.classify,
            "offline": self.offline,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        candidates = [
            CandidateFrame.model_validate(c)
            for c in inputs["select_candidates"]["candidates"]
        ]
        if not candidates:
            return {"count": 0, "accepted": 0, "decisions": [], "mode": "skipped",
                    "note": "no candidate frames to classify"}

        transcript = load_transcript(job)
        mode = self.resolve_mode(job)

        if mode == "offline":
            raw = offline_impl.detect_steps(self.cfg, candidates)
        else:
            raw = self._classify(job, candidates, transcript)

        decisions = self._to_models(raw, candidates)
        accepted = [d for d in decisions if d.is_step]

        print(f"[detect_steps] {len(accepted)} of {len(candidates)} candidates are "
              f"genuine steps ({len(candidates) - len(accepted)} rejected as noise)")
        for d in decisions:
            mark = "keep" if d.is_step else "drop"
            print(f"    {mark}  {d.provisional_title or '-':<34} {d.reason}")

        return {
            "count": len(decisions),
            "accepted": len(accepted),
            "mode": mode,
            "decisions": [d.model_dump() for d in decisions],
        }

    # ------------------------------------------------------------------

    def _classify(self, job: JobPaths, candidates, transcript) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            text_block(detect_steps_user_intro(
                self.cfg, len(candidates), transcript.available))
        ]
        for c in candidates:
            narration = c.transcript_text.strip()
            content.append(text_block(
                f"\ncandidate_id: {c.candidate_id}\n"
                f"position: {c.order} of {len(candidates)}\n"
                f"timestamp: {c.timestamp:.2f}s\n"
                + (f"narration nearby: \"{narration}\"\n" if narration else "")
            ))
            content.append(image_block(job.abs(c.llm_image_path or c.frame_path)))

        result = self.client(job).structured(
            stage=self.name,
            model=self.cfg.models.classify,
            system=detect_steps_system(self.cfg),
            content=content,
            tool=ToolSpec(
                name="report_step_boundaries",
                description="Report, for every candidate frame, whether it is a "
                            "genuine step in the procedure or recording noise.",
                input_schema=detect_steps_tool_schema(),
            ),
        )
        return result.get("decisions", [])

    def _to_models(self, raw: list[dict[str, Any]], candidates) -> list[DetectedStep]:
        """Reconcile the model's answer with the candidates we actually sent.

        A missing decision is treated as a rejection, not as an error. The
        alternative — failing the stage — throws away a paid call over one
        omitted entry, and a candidate the classifier declined to mention is
        not one it was confident about.
        """
        by_id = {d.get("candidate_id"): d for d in raw if isinstance(d, dict)}
        out: list[DetectedStep] = []
        order = 0
        for c in candidates:
            d = by_id.get(c.candidate_id)
            if d is None:
                out.append(DetectedStep(
                    candidate_id=c.candidate_id, is_step=False,
                    reason="no decision returned for this candidate",
                ))
                continue
            is_step = bool(d.get("is_step"))
            if is_step:
                order += 1
            out.append(DetectedStep(
                candidate_id=c.candidate_id,
                is_step=is_step,
                order=order if is_step else None,
                reason=str(d.get("reason", "")),
                provisional_title=str(d.get("provisional_title", "")),
            ))
        return out


def load_transcript(job: JobPaths) -> Transcript:
    """Enrichment, never a dependency. Absent is the normal case."""
    raw = read_stage(job, "transcribe")
    if not raw:
        return Transcript()
    try:
        return Transcript.model_validate(raw.get("transcript", {}))
    except Exception:
        return Transcript()
