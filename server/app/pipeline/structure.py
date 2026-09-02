"""Stage 7 — turn confirmed frames into the structured SOP.

The output of this stage is the product's real artifact. Everything after it —
the editor, the diff, both exporters — reads `models.SOP` and nothing else, so
this is the last point at which the document is allowed to be prose-shaped.

No markdown is produced here, deliberately. A step is a set of named fields
because that is what lets the diff say "the button label changed from Save to
Submit" instead of "this paragraph changed". Collapsing the schema into
formatted text at this stage would take the core capability with it.
"""

from __future__ import annotations

import shutil
from typing import Any

from ..llm import offline as offline_impl
from ..llm.client import ToolSpec, image_block, text_block
from ..llm.prompts import (structure_system, structure_tool_schema,
                           structure_user_intro)
from ..models import (SOP, CandidateFrame, Confidence, DetectedStep, Step,
                      StepMeta, Transcript, UiElement)
from .base import JobPaths, Stage
from .detect_steps import load_transcript
from .llm_stage import LLMStage


class StructureStage(LLMStage):
    name = "structure"
    depends_on = ["select_candidates", "detect_steps"]

    def config_slice(self) -> dict[str, Any]:
        return {
            "writing": self.cfg.writing.model_dump(),
            "steps": self.cfg.steps.model_dump(),
            "structure_model": self.cfg.models.structure,
            "screenshot_format": self.cfg.frames.screenshot_format,
            "offline": self.offline,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        candidates = {
            c["candidate_id"]: CandidateFrame.model_validate(c)
            for c in inputs["select_candidates"]["candidates"]
        }
        decisions = [
            DetectedStep.model_validate(d)
            for d in inputs["detect_steps"]["decisions"]
        ]
        confirmed = [
            candidates[d.candidate_id]
            for d in decisions
            if d.is_step and d.candidate_id in candidates
        ]
        confirmed.sort(key=lambda c: c.timestamp)

        if not confirmed:
            empty = SOP(job_id=job.job_id, title="Untitled procedure",
                        summary="No steps were confirmed in this recording.")
            return {"count": 0, "mode": "skipped", "sop": empty.model_dump(),
                    "note": "detect_steps confirmed no steps"}

        transcript = load_transcript(job)
        mode = self.resolve_mode(job)

        if mode == "offline":
            raw = offline_impl.structure(self.cfg, confirmed, transcript)
        else:
            raw = self._write_sop(job, confirmed, transcript)

        screenshots = self._place_screenshots(job, confirmed)
        sop = self._to_sop(job, raw, confirmed, screenshots, transcript)

        conflicts = [s for s in sop.steps if s.meta.conflict]
        low = [s for s in sop.steps if s.confidence == Confidence.low]
        print(f"[structure] {len(sop.steps)} steps — \"{sop.title}\"")
        for s in sop.steps:
            flag = "!" if s.confidence == Confidence.low else " "
            label = f" [{s.ui_element.label}]" if s.ui_element.label else ""
            print(f"  {flag} {s.order:2d}. {s.title}{label}")
        if conflicts:
            print(f"[structure] {len(conflicts)} step(s) where narration and frame "
                  f"disagreed — frame used, flagged for review")
        if low:
            print(f"[structure] {len(low)} step(s) at low confidence")

        return {
            "count": len(sop.steps),
            "mode": mode,
            "low_confidence": len(low),
            "conflicts": len(conflicts),
            "sop": sop.model_dump(),
        }

    # ------------------------------------------------------------------

    def _write_sop(self, job: JobPaths, confirmed, transcript) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            text_block(structure_user_intro(len(confirmed), transcript.available))
        ]
        for position, c in enumerate(confirmed, start=1):
            narration = c.transcript_text.strip()
            content.append(text_block(
                f"\ncandidate_id: {c.candidate_id}\n"
                f"step position: {position} of {len(confirmed)}\n"
                f"timestamp: {c.timestamp:.2f}s\n"
                + (f"narration recorded here: \"{narration}\"\n" if narration else
                   "no narration at this timestamp\n")
            ))
            content.append(image_block(job.abs(c.llm_image_path or c.frame_path)))

        return self.client(job).structured(
            stage=self.name,
            model=self.cfg.models.structure,
            system=structure_system(self.cfg),
            content=content,
            tool=ToolSpec(
                name="write_procedure",
                description="Write the structured SOP for this workflow, one step "
                            "per confirmed frame.",
                input_schema=structure_tool_schema(self.cfg),
            ),
        )

    def _place_screenshots(self, job: JobPaths, confirmed) -> dict[str, str]:
        """Copy the full-resolution stable frames into screenshots/.

        The LLM saw a 1568px copy; the document ships the original. Names are
        positional and stable so a regenerated SOP overwrites rather than
        accumulates.
        """
        for stale in job.screenshots.glob("*"):
            stale.unlink()

        out: dict[str, str] = {}
        ext = self.cfg.frames.screenshot_format
        for position, c in enumerate(confirmed, start=1):
            src = job.abs(c.frame_path)
            dst = job.screenshots / f"step_{position:02d}{src.suffix or '.' + ext}"
            shutil.copyfile(src, dst)
            out[c.candidate_id] = job.rel(dst)
        return out

    def _to_sop(
        self, job: JobPaths, raw: dict[str, Any], confirmed: list[CandidateFrame],
        screenshots: dict[str, str], transcript: Transcript,
    ) -> SOP:
        """Bind the model's answer back to the frames it was written from.

        Provenance is attached here rather than trusted from the response:
        timestamp, source frame and transcript span come from our own records,
        so a step can always be traced to the second of video that produced it
        even if the model garbles a candidate_id.
        """
        by_id = {c.candidate_id: c for c in confirmed}
        returned = raw.get("steps") or []

        # Positional fallback: the prompt asks for one step per frame in order,
        # so when an id does not resolve, position is a better guess than
        # dropping the step and losing a screenshot from the document.
        steps: list[Step] = []
        for position, item in enumerate(returned):
            if not isinstance(item, dict):
                continue
            cand = by_id.get(item.get("candidate_id"))
            if cand is None and position < len(confirmed):
                cand = confirmed[position]
            if cand is None:
                continue

            ui = item.get("ui_element") or {}
            conflict = (item.get("conflict") or "").strip()
            confidence = _confidence(item.get("confidence"), conflict)

            steps.append(Step(
                order=len(steps) + 1,
                title=str(item.get("title", "")).strip() or f"Step {len(steps) + 1}",
                instruction=str(item.get("instruction", "")).strip(),
                ui_element=UiElement(
                    type=_ui_type(ui.get("type")),
                    label=str(ui.get("label", "")).strip(),
                    location_hint=str(ui.get("location_hint", "")).strip(),
                ),
                expected_result=str(item.get("expected_result", "")).strip(),
                screenshot_ref=screenshots.get(cand.candidate_id),
                prerequisites=[str(p).strip() for p in (item.get("prerequisites") or [])
                               if str(p).strip()],
                confidence=confidence,
                meta=StepMeta(
                    source_frame_ts=cand.timestamp,
                    candidate_id=cand.candidate_id,
                    transcript_span=((cand.timestamp - 2.5, cand.timestamp + 2.5)
                                     if cand.transcript_text else None),
                    phash=cand.phash,
                    conflict=conflict or None,
                ),
            ))

        return SOP(
            job_id=job.job_id,
            title=str(raw.get("title", "")).strip() or "Untitled procedure",
            summary=str(raw.get("summary", "")).strip(),
            steps=steps,
            generated_from_transcript=transcript.available,
        )


def _confidence(value: Any, conflict: str) -> Confidence:
    """The conflict rule, enforced in code rather than hoped for in the prompt.

    A step where narration and frame disagreed is low confidence by definition,
    whatever the model claimed, because that step is exactly the one a reviewer
    must look at.
    """
    if conflict:
        return Confidence.low
    try:
        return Confidence(str(value).lower())
    except ValueError:
        return Confidence.medium


def _ui_type(value: Any) -> str:
    allowed = UiElement.model_fields["type"].annotation.__args__
    text = str(value or "").lower().strip()
    return text if text in allowed else "other"
