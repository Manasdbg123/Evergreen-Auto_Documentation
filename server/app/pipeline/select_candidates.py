"""Stage 5 — cull change events down to the handful that may reach a vision call.

Cost gate. A five-minute recording sampled at 2fps produces 600 frames; sending
those to Sonnet would cost more than the entire build budget. This stage caps
the set at `candidates.max_frames` before a single token is spent, and writes
LLM-ready copies capped at 1568px on the long edge.

Ranking blends four signals so the survivors are spread across the workflow
rather than clustered in whichever 20 seconds happened to be busiest.
"""

from __future__ import annotations

from typing import Any

from ..models import CandidateFrame, ChangeEvent, Transcript
from .base import JobPaths, Stage, read_stage
from .video import resize_for_llm


class SelectCandidatesStage(Stage):
    name = "select_candidates"
    depends_on = ["detect_changes"]

    def config_slice(self) -> dict[str, Any]:
        return {
            "candidates": self.cfg.candidates.model_dump(),
            "llm_max_edge_px": self.cfg.frames.llm_max_edge_px,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        cc = self.cfg.candidates
        events = [ChangeEvent.model_validate(e) for e in inputs["detect_changes"]["events"]]
        transcript = self._load_transcript(job)

        if not events:
            return {"count": 0, "candidates": [], "note": "no change events to select from"}

        duration = max(e.stable_timestamp for e in events) or 1.0
        scored = [(self._score(e, events, transcript, duration), e) for e in events]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        chosen: list[tuple[float, ChangeEvent]] = []
        min_gap = cc.min_spacing_ms / 1000.0
        for score, e in scored:
            if len(chosen) >= cc.max_frames:
                break
            if any(abs(e.stable_timestamp - c.stable_timestamp) < min_gap for _, c in chosen):
                continue
            chosen.append((score, e))

        # Under the floor, relax spacing rather than hand the LLM two frames
        # and expect a coherent procedure out of it.
        if len(chosen) < min(cc.min_frames, len(events)):
            taken = {id(c) for _, c in chosen}
            for score, e in scored:
                if len(chosen) >= min(cc.min_frames, len(events)):
                    break
                if id(e) not in taken:
                    chosen.append((score, e))

        chosen.sort(key=lambda pair: pair[1].stable_timestamp)

        for stale in job.llm_frames.glob("*"):
            stale.unlink()

        candidates: list[CandidateFrame] = []
        for order, (score, e) in enumerate(chosen, start=1):
            src = job.abs(e.stable_frame_path)
            llm_path = job.llm_frames / f"c{order:03d}.jpg"
            resize_for_llm(src, llm_path, self.cfg.frames.llm_max_edge_px)
            candidates.append(CandidateFrame(
                event_id=e.event_id,
                order=order,
                timestamp=e.stable_timestamp,
                frame_path=e.stable_frame_path,
                llm_image_path=job.rel(llm_path),
                phash=e.phash,
                magnitude=e.magnitude,
                stability=e.stability,
                score=round(score, 5),
                transcript_text=self._narration_around(transcript, e.stable_timestamp),
            ))

        total_kb = sum(
            job.abs(c.llm_image_path).stat().st_size for c in candidates
        ) / 1024
        print(
            f"[select_candidates] {len(events)} events -> {len(candidates)} candidates "
            f"(cap {cc.max_frames}) | {total_kb:.0f}KB of images prepared for vision"
        )
        for c in candidates:
            snippet = (c.transcript_text[:60] + "…") if len(c.transcript_text) > 60 else c.transcript_text
            print(f"    #{c.order:02d} t={c.timestamp:7.2f}s score={c.score:.3f} {snippet}")

        return {
            "count": len(candidates),
            "from_events": len(events),
            "candidates": [c.model_dump() for c in candidates],
        }

    # ----------------------------------------------------------------------

    def _score(self, e: ChangeEvent, all_events, transcript: Transcript, duration: float) -> float:
        w = self.cfg.candidates.rank_weights
        mags = [x.magnitude for x in all_events] or [1.0]
        span = max(mags) - min(mags)
        norm_mag = (e.magnitude - min(mags)) / span if span > 1e-9 else 1.0

        narration = 1.0 if self._narration_around(transcript, e.stable_timestamp) else 0.0
        # Reward events that sit far from their nearest already-known neighbour,
        # so coverage wins over a cluster of near-identical churn.
        others = [x.stable_timestamp for x in all_events if x.event_id != e.event_id]
        nearest = min((abs(e.stable_timestamp - o) for o in others), default=duration)
        spread = min(nearest / max(duration, 1e-6) * 4, 1.0)

        return (
            w.visual_magnitude * norm_mag
            + w.stability * e.stability
            + w.transcript_overlap * narration
            + w.temporal_spread * spread
        )

    @staticmethod
    def _narration_around(transcript: Transcript, ts: float, window: float = 2.5) -> str:
        if not transcript.available:
            return ""
        return transcript.text_between(ts - window, ts + window)

    def _load_transcript(self, job: JobPaths) -> Transcript:
        """Transcript is enrichment, never a dependency. Absent is normal."""
        raw = read_stage(job, "transcribe")
        if not raw:
            return Transcript()
        try:
            return Transcript.model_validate(raw.get("transcript", {}))
        except Exception:
            return Transcript()
