"""Stage 3 — sample the video and measure frame-to-frame similarity.

This stage does no interpretation. It walks the video once at the configured
rate, saves each sampled frame, and records pHash + SSIM against the previous
sample. Everything downstream reads that timeline rather than the video, which
is what makes the later stages cheap to re-run.
"""

from __future__ import annotations

from typing import Any

import cv2

from ..models import SampledFrame
from .base import JobPaths, Stage
from .video import phash_of, save_frame, ssim, to_analysis_gray


class FramesStage(Stage):
    name = "frames"
    depends_on = ["ingest"]

    def config_slice(self) -> dict[str, Any]:
        return {
            "frames": self.cfg.frames.model_dump(),
            # Cropping affects the recorded SSIM, so it belongs to this stage's key.
            "ignore_top_pct": self.cfg.change_detection.ignore_top_pct,
            "ignore_bottom_pct": self.cfg.change_detection.ignore_bottom_pct,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        meta = inputs["ingest"]
        src = job.abs(meta["source_path"])
        duration = float(meta["duration"])
        step = 1.0 / self.cfg.frames.sample_fps

        for stale in job.sampled.glob("*"):
            stale.unlink()

        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV could not open {src}")

        frames: list[SampledFrame] = []
        prev_gray = None
        prev_hash = None
        index = 0
        t = 0.0

        try:
            while t < duration:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None:
                    # Seeking past the last decodable frame; stop cleanly rather
                    # than emitting a run of black frames at the tail.
                    break

                gray = to_analysis_gray(frame, self.cfg)
                h = phash_of(frame)

                if prev_gray is None:
                    sim, dist = 1.0, 0
                else:
                    sim = ssim(prev_gray, gray)
                    dist = int(prev_hash - h)

                path = job.sampled / f"f{index:05d}.jpg"
                save_frame(frame, path)

                frames.append(SampledFrame(
                    index=index,
                    timestamp=round(t, 3),
                    path=job.rel(path),
                    phash=str(h),
                    ssim_prev=round(sim, 5),
                    phash_dist_prev=dist,
                ))

                prev_gray, prev_hash = gray, h
                index += 1
                t += step
        finally:
            cap.release()

        if not frames:
            raise RuntimeError(f"No frames could be decoded from {src.name}")

        sims = [f.ssim_prev for f in frames[1:]] or [1.0]
        print(
            f"[frames] sampled {len(frames)} frames at {self.cfg.frames.sample_fps}fps "
            f"over {duration:.1f}s | ssim min={min(sims):.3f} "
            f"mean={sum(sims)/len(sims):.3f}"
        )

        return {
            "sample_fps": self.cfg.frames.sample_fps,
            "duration": duration,
            "count": len(frames),
            "frames": [f.model_dump() for f in frames],
        }
